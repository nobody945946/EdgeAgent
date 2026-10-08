"""EdgeAgent-owned, restartable installation of model assets and inference binaries."""

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
from collections.abc import Callable
from pathlib import Path

from edge_agent import hardware
from edge_agent.runtime import (
    AGENT_ADAPTER,
    CHARACTER_ADAPTER,
    CORE_ROOT,
    DEFAULT_MODEL,
    NODE_VERSION,
    QWEN_MODEL,
)

LLAMA_TAG = "b11193"
LLAMA_ROOT = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}"
MODEL_URL = (
    "https://huggingface.co/unsloth/gemma-4-E4B-it-qat-GGUF/resolve/"
    "8c5a9e4fd5482e2be20fe0bf013b4c262a8f4265/" + DEFAULT_MODEL
)
MODEL_SHA = "df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3"
QWEN_MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/"
    "e87f176479d0855a907a41277aca2f8ee7a09523/" + QWEN_MODEL
)
QWEN_MODEL_SHA = "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"
# Adapters that ship with the code (edge_agent/adapters), by filename.
ADAPTERS = {AGENT_ADAPTER: "85b224ecec76572e2fb153b10d4b7d16f015bf69f0e6a63731a774c54e21566e",
            CHARACTER_ADAPTER: "99ea1581f8f1f0e0f46d6da9b04d444b941c6395b93343b173073e6ade6d29d9"}
PROJECTOR_ASSETS = {
    DEFAULT_MODEL: (
        "gemma4",
        "mmproj-gemma-4-E4B-it-Q8_0.gguf",
        (
            "https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/"
            "b8093469224f83f5c38f691eb906c380e9e63114/mmproj-gemma-4-E4B-it-Q8_0.gguf"
        ),
        "197f49a93027f9843772bd24a6a9e0be2a32a788de5a3def330e9c585d86edd1",
    ),
    QWEN_MODEL: (
        "qwen35",
        "mmproj-Qwen3.5-4B-F16.gguf",
        QWEN_MODEL_URL.rsplit("/", 1)[0] + "/mmproj-F16.gguf",
        "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864",
    ),
}


def ensure_projector(model_path: Path, architecture: str) -> Path | None:
    """Install only the pinned public projector belonging to a shipped base."""
    asset = PROJECTOR_ASSETS.get(model_path.name)
    if asset is None or architecture != asset[0]:
        return None
    target = model_path.parent / asset[1]
    download(asset[2], target, asset[3])
    return target


NODE_SHA = "2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71"
# The Agent's shell is the PowerShell its training was recorded and measured
# in (bench/Dockerfile), not Windows PowerShell 5.1: 5.1 lacks 7's syntax and
# writes Chinese in the ANSI code page (laptop 2026-10-07: no PowerShell 7).
POWERSHELL_VERSION = "7.6.6"
POWERSHELL = (
    ("https://github.com/PowerShell/PowerShell/releases/download/"
    f"v{POWERSHELL_VERSION}/PowerShell-{POWERSHELL_VERSION}-win-x64.zip"),
    "02fe458be20493fbdf43f61ea20610b811ee6c738ab1676c61b9cfcd1a33c860",
)
SEARCH_TOOLS = {
    "rg": (
        (
            "https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/"
            "ripgrep-15.2.0-x86_64-pc-windows-msvc.zip"
        ),
        "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5",
    ),
    "fd": (
        (
            "https://github.com/sharkdp/fd/releases/download/v10.5.0/"
            "fd-v10.5.0-x86_64-pc-windows-msvc.zip"
        ),
        "a227701b8551c35a9931d9f6da75503cf86d88e182d71fb849a70864c5d57cd7",
    ),
}
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


def download(
    url: str,
    target: Path,
    expected: str,
    *,
    attempts: int = 3,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Resume a partial transfer; publish only a verified complete file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if verified(target, expected):
        if progress:
            progress(target.stat().st_size, target.stat().st_size)
        print(f"[OK] {target.name}", flush=True)
        return
    partial = target.with_name(target.name + ".part")
    for attempt in range(attempts):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "EdgeAgent/2.0"}
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
                    total = (
                        int(response.headers.get("Content-Length", "0")) + transferred
                    )
                    if progress:
                        progress(transferred, total)
                    last_report = transferred
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        transferred += len(chunk)
                        if progress:
                            progress(transferred, total)
                        if transferred - last_report >= 64 * 1024 * 1024:
                            print(f"  {transferred // (1024 * 1024)} MiB", flush=True)
                            last_report = transferred
                    if total and transferred < total:
                        raise RuntimeError("download ended before the advertised size")
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
    command = hardware.nvidia_smi()
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


def install_search_tools(root: Path) -> None:
    """Pi runs offline; deploy verified tools instead of relying on system PATH."""
    destination = root / ".runtime/tools"
    destination.mkdir(parents=True, exist_ok=True)
    for tool, (url, digest) in SEARCH_TOOLS.items():
        archive = root / ".runtime/downloads" / url.rsplit("/", 1)[-1]
        download(url, archive, digest)
        with zipfile.ZipFile(archive) as bundle:
            members = [
                item
                for item in bundle.infolist()
                if Path(item.filename).name == tool + ".exe"
            ]
            if len(members) != 1:
                raise RuntimeError(f"verified release must contain one {tool}.exe")
            content = bundle.read(members[0])
        target = destination / (tool + ".exe")
        if (
            not target.is_file()
            or sha256(target) != hashlib.sha256(content).hexdigest()
        ):
            temporary = target.with_suffix(".exe.part")
            temporary.write_bytes(content)
            temporary.replace(target)
        subprocess.run(
            [str(target), "--version"],
            check=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def install_powershell(root: Path) -> None:
    """Portable PowerShell 7 in .runtime/tools/pwsh (runtime.tool_paths puts
    it first on PATH), extracted again when the pinned release changes."""
    url, digest = POWERSHELL
    archive = root / ".runtime/downloads" / url.rsplit("/", 1)[-1]
    download(url, archive, digest)
    destination = root / ".runtime/tools/pwsh"
    receipt = destination / ".installed.sha256"
    pwsh = destination / "pwsh.exe"
    if not pwsh.is_file() or not receipt.is_file() or receipt.read_text() != digest:
        shutil.rmtree(destination, ignore_errors=True)
        extract_zip(archive, destination)
        receipt.write_text(digest, encoding="ascii")
    subprocess.run(
        [str(pwsh), "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        check=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def install_agent_runtime(root: Path) -> None:
    """Install pinned upstream Pi and portable Node using npm's lockfile, and
    the tools the Agent calls: its PowerShell, rg and fd."""
    install_powershell(root)
    install_search_tools(root)
    node_root = root / ".runtime/node"
    name = f"node-v{NODE_VERSION}-win-x64"
    archive = node_root / (name + ".zip")
    download(f"https://nodejs.org/dist/v{NODE_VERSION}/{name}.zip", archive, NODE_SHA)
    node = node_root / name / "node.exe"
    npm = node.parent / "node_modules/npm/bin/npm-cli.js"
    if not node.is_file() or not npm.is_file():
        extract_zip(archive, node_root)
    subprocess.run([str(node), "--version"], check=True, timeout=30)
    destination = root / ".runtime/pi"
    destination.mkdir(parents=True, exist_ok=True)
    sources = Path(__file__).parent
    lock = sources / "pi-runtime-lock.json"
    receipt = destination / ".installed.sha256"
    digest = sha256(lock)
    package = destination / "node_modules/@earendil-works/pi-coding-agent/dist/index.js"
    probe = [
        str(node),
        "--input-type=module",
        "-e",
        f"await import({json.dumps(package.as_uri())})",
    ]
    installed = (
        package.is_file() and receipt.is_file() and receipt.read_text() == digest
    )
    if installed:
        try:
            subprocess.run(
                probe, cwd=destination, capture_output=True, check=True, timeout=30
            )
            return
        except (OSError, subprocess.SubprocessError):
            installed = False
    if not installed:
        shutil.copyfile(sources / "pi-runtime.json", destination / "package.json")
        shutil.copyfile(lock, destination / "package-lock.json")
        subprocess.run(
            [str(node), str(npm), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=destination,
            env={
                **os.environ,
                "PATH": str(node.parent) + os.pathsep + os.environ.get("PATH", ""),
            },
            check=True,
            timeout=600,
        )
    # Probe imports as well as executable presence after interrupted installations.
    subprocess.run(
        probe,
        cwd=destination,
        check=True,
        timeout=30,
    )
    receipt.write_text(digest, encoding="ascii")


def install_adapters(root: Path) -> None:
    """Copy the adapters that ship with the code into models/, checked by
    hash, so the model page and SilentTalk's lease find them by name."""
    for name, digest in ADAPTERS.items():
        source = Path(__file__).with_name("adapters") / name
        target = root / "models" / name
        if verified(target, digest):
            continue
        if sha256(source) != digest:
            raise RuntimeError(f"bundled adapter {name} does not match its hash")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        shutil.copyfile(source, partial)
        partial.replace(target)


def install(root: Path = CORE_ROOT, backend: str = "auto") -> dict[str, str]:
    root = root.resolve()
    cache = root / ".runtime/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    # The machine spirit's public base, for the Agent and SilentTalk alike,
    # and the Agent's adapter for it. Other models come from the model page.
    download(MODEL_URL, root / "models" / DEFAULT_MODEL, MODEL_SHA)
    install_adapters(root)
    install_agent_runtime(root)
    selected = "cuda" if backend == "auto" and has_nvidia() else backend
    if selected == "auto":
        selected = "cpu"
    directory = root / ".runtime" / f"llama-{LLAMA_TAG}-{selected}"
    for name, digest in BINARIES[selected]:
        archive = cache / name
        download(f"{LLAMA_ROOT}/{name}", archive, digest)
        # Extract once after full hash validation; retries complete unfinished installs.
        marker = directory / f".{name}.installed"
        complete = marker.exists()
        if complete:
            with zipfile.ZipFile(archive) as bundle:
                complete = all(
                    item.is_dir()
                    or (
                        (directory / item.filename).is_file()
                        and (directory / item.filename).stat().st_size == item.file_size
                    )
                    for item in bundle.infolist()
                )
        if not complete:
            extract_zip(archive, directory)
            marker.write_text(digest, encoding="ascii")
    executable = next(directory.rglob("llama-server.exe"), None)
    if executable is None:
        raise RuntimeError("llama-server is missing from the verified release")
    probe = subprocess.run(
        [str(executable), "--help"], capture_output=True, timeout=30, check=False
    )
    if probe.returncode != 0 or b"--models-preset" not in probe.stdout + probe.stderr:
        if selected == "cuda" and backend == "auto":
            print("CUDA cannot initialize on this driver; installing the CPU backend.")
            return install(root, "cpu")
        raise RuntimeError("llama-server cannot run or does not support model routing")
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
        print(f"[OK] EdgeAgent runtime ({result['backend']})")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
