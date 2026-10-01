"""E4B-owned, restartable installation of model assets and inference binaries."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from e4b_core.runtime import CORE_ROOT, DEFAULT_MODEL

LLAMA_TAG = "b11193"
LLAMA_ROOT = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}"
MODEL_URL = (
    "https://huggingface.co/unsloth/gemma-4-E4B-it-qat-GGUF/resolve/"
    "8c5a9e4fd5482e2be20fe0bf013b4c262a8f4265/" + DEFAULT_MODEL
)
MODEL_SHA = "df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3"
LORA_NAME = "character-e4b-lora-v3.q8_0.gguf"
LORA_SHA = "bf7fd9e3189fe0be7c15f8b79e5ccaa45bbbd5d5f9670c695a428c5bb88ebac0"
ASSET_RELEASE = "runtime-assets-v1"
BINARIES = {
    "cpu": (
        (
            f"llama-{LLAMA_TAG}-bin-win-cpu-x64.zip",
            "1110e183589e0e2ac5b365fb5c3c4a7e149e0760c1989cac18ee426b08e5813a",
        ),
    ),
    "cuda": (
        (
            f"llama-{LLAMA_TAG}-bin-win-cuda-12.4-x64.zip",
            "7ccec8500973f49725b4a43b7487cb9e782a8f62beb330c4b4490a2f3be3f28d",
        ),
        (
            "cudart-llama-bin-win-cuda-12.4-x64.zip",
            "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
        ),
    ),
}


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verified(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False
    # Cache only hashes we calculated, tied to exact file metadata.
    stat = path.stat()
    marker = path.with_name(path.name + ".verified.json")
    signature = {"sha256": expected, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    try:
        if json.loads(marker.read_text(encoding="utf-8")) == signature:
            return True
    except (OSError, ValueError):
        pass
    if sha256(path) != expected:
        return False
    marker.write_text(json.dumps(signature), encoding="utf-8")
    return True


def download(url: str, target: Path, expected: str, *, attempts: int = 3) -> None:
    """Resume a partial transfer; publish only a verified complete file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if verified(target, expected):
        print(f"[OK] {target.name}", flush=True)
        return
    partial = target.with_name(target.name + ".part")
    for attempt in range(attempts):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "E4BCore/1.1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            print(f"Downloading {target.name} (resume: {offset} bytes)", flush=True)
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                resumed = response.status == 206 and offset > 0
                if resumed and not response.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                ):
                    raise RuntimeError("download returned an invalid byte range")
                with partial.open("ab" if resumed else "wb") as output:
                    transferred = offset if resumed else 0
                    last_report = transferred
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        transferred += len(chunk)
                        if transferred - last_report >= 64 * 1024 * 1024:
                            print(f"  {transferred // (1024 * 1024)} MiB", flush=True)
                            last_report = transferred
            if sha256(partial) != expected:
                # Keep damaged data for inspection, but do not resume it again.
                partial.rename(
                    partial.with_name(partial.name + f".invalid.{time.time_ns()}")
                )
                raise RuntimeError(f"SHA256 mismatch for {target.name}")
            if target.exists():
                target.rename(
                    target.with_name(target.name + f".invalid.{time.time_ns()}")
                )
            partial.replace(target)
            verified(target, expected)
            return
        except (
            OSError,
            urllib.error.URLError,
            RuntimeError,
            http.client.HTTPException,
        ) as exc:
            if (
                isinstance(exc, urllib.error.HTTPError)
                and exc.code == 416
                and partial.exists()
            ):
                partial.rename(
                    partial.with_name(partial.name + f".invalid.{time.time_ns()}")
                )
            if attempt + 1 == attempts:
                raise RuntimeError(
                    f"Cannot install {target.name}; rerun setup to resume"
                ) from exc
            time.sleep(attempt + 1)


def extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            path = (root / item.filename).resolve()
            if (
                not path.is_relative_to(root)
                or (item.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise RuntimeError("unsafe archive path")
        bundle.extractall(root)


def has_nvidia() -> bool:
    command = shutil.which("nvidia-smi")
    if not command:
        candidate = (
            Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe"
        )
        command = str(candidate) if candidate.is_file() else None
    if not command:
        return False
    try:
        result = subprocess.run(
            [command, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def install(root: Path = CORE_ROOT, backend: str = "auto") -> dict[str, str]:
    root = root.resolve()
    assets = root / "models"
    cache = root / ".runtime/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    download(MODEL_URL, assets / DEFAULT_MODEL, MODEL_SHA)
    lora = assets / LORA_NAME
    if not verified(lora, LORA_SHA):
        gh = shutil.which("gh")
        if not gh:
            raise RuntimeError(
                "GitHub CLI is required to fetch the private character adapter"
            )
        staging = cache / f"adapter-{time.time_ns()}"
        staging.mkdir()
        subprocess.run(
            [
                gh,
                "release",
                "download",
                ASSET_RELEASE,
                "--repo",
                "nobody945946/E4BCore",
                "--pattern",
                LORA_NAME,
                "--dir",
                str(staging),
            ],
            check=True,
        )
        downloaded = staging / LORA_NAME
        if sha256(downloaded) != LORA_SHA:
            raise RuntimeError("Character adapter SHA256 mismatch")
        if lora.exists():
            lora.rename(lora.with_name(lora.name + f".invalid.{time.time_ns()}"))
        assets.mkdir(parents=True, exist_ok=True)
        downloaded.replace(lora)
        verified(lora, LORA_SHA)
    selected = "cuda" if backend == "auto" and has_nvidia() else backend
    if selected == "auto":
        selected = "cpu"
    directory = root / ".runtime" / f"llama-{LLAMA_TAG}-{selected}"
    for name, digest in BINARIES[selected]:
        archive = cache / name
        download(f"{LLAMA_ROOT}/{name}", archive, digest)
        # Extract once after full hash validation; retries complete unfinished installs.
        marker = directory / f".{name}.installed"
        if not marker.exists():
            extract_zip(archive, directory)
            marker.write_text(digest, encoding="ascii")
    executable = next(directory.rglob("llama-server.exe"), None)
    if executable is None:
        raise RuntimeError("llama-server is missing from the verified release")
    probe = subprocess.run(
        [str(executable), "--help"], capture_output=True, timeout=30, check=False
    )
    if probe.returncode != 0 or b"--tools" not in probe.stdout + probe.stderr:
        if selected == "cuda" and backend == "auto":
            print("CUDA cannot initialize on this driver; installing the CPU backend.")
            return install(root, "cpu")
        raise RuntimeError("llama-server cannot run or does not support agent tools")
    result = {
        "server": str(executable.relative_to(root)),
        "backend": selected,
        "version": LLAMA_TAG,
    }
    marker = root / ".runtime/deployment.json"
    temp = marker.with_suffix(".tmp")
    temp.write_text(json.dumps(result), encoding="utf-8")
    temp.replace(marker)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CORE_ROOT)
    parser.add_argument("--backend", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args(argv)
    try:
        result = install(args.root, args.backend)
        print(f"[OK] E4B native runtime ({result['backend']})")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
