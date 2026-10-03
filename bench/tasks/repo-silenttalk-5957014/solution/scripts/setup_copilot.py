from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from silenttalk.config import load_effective_tables

RUNTIME_DIR = PROJECT_DIR / ".runtime"
ASR_DIR = RUNTIME_DIR / "models" / "asr"
SENSEVOICE_DIR = RUNTIME_DIR / "models" / "sensevoice"
VAD_DIR = RUNTIME_DIR / "models" / "vad"

ASSETS = (
    (
        "ASR encoder INT8",
        "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/encoder.int8.onnx?download=true",
        ASR_DIR / "encoder.int8.onnx",
        "81a70226a8934e6ed92aa1d4fc486b428b5398e2f2619ed4897b7294cab90e9a",
        150_000_000,
    ),
    (
        "ASR decoder INT8",
        "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/decoder.int8.onnx?download=true",
        ASR_DIR / "decoder.int8.onnx",
        "f3cca9f77bb9d93c8fcbfb63ae617b6b1ee96818df3aa3b151c40658fe38594f",
        65_000_000,
    ),
    (
        "ASR tokens",
        "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/tokens.txt?download=true",
        ASR_DIR / "tokens.txt",
        "59aba8873a2ed1e122c25fee421e25f283b63290efbde85c1f01a853d83cb6e6",
        50_000,
    ),
)

# Only downloaded when the effective settings select copilot.stt_engine = "sensevoice".
# min_size is 95% of the verified byte count.
SENSEVOICE_ASSETS = (
    (
        "SenseVoice INT8",
        "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx?download=true",
        SENSEVOICE_DIR / "model.int8.onnx",
        "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        227_272_148,  # 239,233,841 bytes
    ),
    (
        "SenseVoice tokens",
        "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt?download=true",
        SENSEVOICE_DIR / "tokens.txt",
        "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        300_099,  # 315,894 bytes
    ),
    (
        "Silero VAD",
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
        VAD_DIR / "silero_vad.onnx",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        611_661,  # 643,854 bytes
    ),
)


def _copilot_section() -> dict:
    raw = load_effective_tables(PROJECT_DIR)
    section = raw.get("copilot", {})
    return section if isinstance(section, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_ready(path: Path, expected_sha256: str | None, min_size: int) -> bool:
    if not path.is_file() or path.stat().st_size < min_size:
        return False
    if expected_sha256 is None:
        return True

    marker = path.with_name(path.name + ".sha256")
    actual = _sha256(path)
    if actual != expected_sha256:
        print(f"Existing {path.name} failed SHA256; downloading a clean copy.")
        return False

    marker.write_text(expected_sha256 + "\n", encoding="ascii")
    return True


def _download(
    label: str,
    url: str,
    path: Path,
    expected_sha256: str | None,
    min_size: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_ready(path, expected_sha256, min_size):
        print(f"[OK] {label}")
        return

    temp = path.with_name(path.name + ".part")
    temp.unlink(missing_ok=True)
    marker = path.with_name(path.name + ".sha256")
    marker.unlink(missing_ok=True)

    print(f"Downloading {label}...")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SilentTalk/1.0"},
    )
    digest = hashlib.sha256()
    received = 0
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temp.open("wb") as out,
        ):
            total = int(response.headers.get("Content-Length", "0") or "0")
            last_percent = -1
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if total:
                    percent = int(received * 100 / total)
                    if percent >= last_percent + 5:
                        print(f"  {percent:3d}%")
                        last_percent = percent
    except (OSError, urllib.error.URLError) as exc:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"{label} download failed: {exc}") from exc

    if received < min_size:
        temp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{label} download is unexpectedly small ({received} bytes)."
        )

    actual_sha256 = digest.hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        temp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{label} SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )

    temp.replace(path)
    if expected_sha256 is not None:
        marker.write_text(expected_sha256 + "\n", encoding="ascii")
    print(f"[OK] {label}")


def check_runtime(section: dict) -> None:
    """Verify native E4B, character leasing and return-to-base without chat data."""
    from silenttalk import edge_ai

    previous = edge_ai.service_status()
    if previous and previous.get("mode") == "character":
        raise RuntimeError(
            "E4B is already leased; finish the active session before --check"
        )
    owner = os.getpid()
    acquired = False
    try:
        base = edge_ai.ensure_service()
        if base.get("mode") != "base":
            raise RuntimeError("Native E4B is not in base mode")
        configuration = {
            "model": section.get("llm_model", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
            "lora": section.get("character_lora", "character-e4b-lora-v3.q8_0.gguf"),
            "port": int(section.get("llama_port", 18088)),
            "context_size": int(section.get("llm_context_size", 32768)),
            "gpu_layers": int(section.get("llm_gpu_layers", 99)),
            "low_vram": bool(section.get("llm_low_vram", True)),
        }
        result = edge_ai.post_json(
            "/character", {"owner_pid": owner, "character": configuration}, timeout=360
        )
        acquired = True
        if result.get("mode") != "character" or result.get("owner_pid") != owner:
            raise RuntimeError("Character lease validation failed")
        returned = edge_ai.release_character(owner)
        acquired = False
        if returned.get("mode") != "base":
            raise RuntimeError("E4B did not return to native base mode")
        print("[OK] Native E4B / character lease / native restore")
    finally:
        if acquired:
            edge_ai.release_character(owner)
        if previous is None and edge_ai.service_status() is not None:
            edge_ai.post_json("/shutdown", {}, timeout=30, require_ready=False)


def check_speech(section: dict) -> None:
    from silenttalk.copilot.stt import StreamingTranscriber
    from silenttalk.copilot.stt_offline import OfflineTranscriber

    engine = str(section.get("stt_engine", "paraformer")).strip().lower()
    if engine == "sensevoice":
        transcriber = OfflineTranscriber(
            SENSEVOICE_DIR, lambda *args: None, lambda error: None, vad_dir=VAD_DIR
        )
    else:
        transcriber = StreamingTranscriber(
            ASR_DIR, lambda *args: None, lambda error: None
        )
    try:
        transcriber.start()
        print("[OK] Speech model loaded")
    finally:
        transcriber.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install SilentTalk speech assets")
    parser.add_argument("--check-runtime", action="store_true")
    args = parser.parse_args(argv)
    section = _copilot_section()
    if not bool(section.get("enabled", True)):
        print("Copilot is disabled; speech model deployment skipped.")
        if args.check_runtime:
            try:
                check_runtime(section)
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
        return 0

    stt_engine = str(section.get("stt_engine", "paraformer")).strip().lower()
    stt_assets = SENSEVOICE_ASSETS if stt_engine == "sensevoice" else ()

    print("Checking SilentTalk speech models...")
    try:
        for asset in (*ASSETS, *stt_assets):
            _download(*asset)
        if args.check_runtime:
            check_speech(section)
            check_runtime(section)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("SilentTalk speech models are ready; reply models belong to E4B Core.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
