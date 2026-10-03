from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from silenttalk.config import load_effective_tables

RUNTIME_DIR = PROJECT_DIR / ".runtime"
ASR_DIR = RUNTIME_DIR / "models" / "asr"
LLM_DIR = RUNTIME_DIR / "models" / "llm"
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
        None,
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


# Known reply models, keyed by the file name used in config.toml copilot.llm_model.
# Qwen's official repos only publish Q8_0 for 0.6B/1.7B, so Q4_K_M comes from unsloth.
# Qwen3-0.6B is deliberately absent: in benchmarks it ignored the four-line
# format on 29 of 32 lanes and mostly parroted the transcript back.
# Default since 2026-09-27: Gemma 4 E2B (Apache-2.0) matched Qwen3.5-4B on
# the persona eval of the time (voice 3.54 / stance 3.25 / natural 3.01) while
# needing ~1.4 GB of VRAM like Qwen3-1.7B; its per-layer embeddings stay in RAM.
LLM_MODELS = {
    "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf": (
        "Gemma 4 E2B QAT Q4_K_XL",
        "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf?download=true",
        "e531007218dfab990486a5de7676a6932d6ea8dea233d1f698d7c21cf8a16889",
        2_600_000_000,
    ),
    # Twice E2B's size: better options on the dev box, speed on a 3050 Ti
    # still to be measured (scripts/dev/bench_hw.py --model ...).
    "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf": (
        "Gemma 4 E4B QAT Q4_K_XL",
        "https://huggingface.co/unsloth/gemma-4-E4B-it-qat-GGUF/resolve/main/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf?download=true",
        "df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3",
        4_200_000_000,
    ),
    "Qwen3.5-4B-Q4_K_M.gguf": (
        "Qwen3.5-4B Q4_K_M",
        "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf?download=true",
        "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
        2_700_000_000,
    ),
    "Qwen3-1.7B-Q4_K_M.gguf": (
        "Qwen3-1.7B Q4_K_M",
        "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf?download=true",
        "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
        1_100_000_000,
    ),
    "Qwen3-4B-Q4_K_M.gguf": (
        "Qwen3-4B Q4_K_M",
        "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf?download=true",
        "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
        2_490_000_000,
    ),
}
DEFAULT_LLM_MODEL = "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"


# Small files that ship in the repo (models/) and are copied into the runtime
# model folder: the character LoRA trained on the dev box has no download URL.
BUNDLED = (
    (
        "Character LoRA v1 (q8_0)",
        PROJECT_DIR / "models" / "character-e2b-lora-v1.q8_0.gguf",
        LLM_DIR / "character-e2b-lora-v1.q8_0.gguf",
        "83655e8502a1c83b5d15dd1adc2bb4b908ddde6506d436f9a93eb491c566f957",
    ),
)


def _install_bundled(label: str, source: Path, target: Path, expected_sha256: str) -> None:
    if target.is_file() and _sha256(target) == expected_sha256:
        print(f"[OK] {label}")
        return
    if not source.is_file() or _sha256(source) != expected_sha256:
        raise RuntimeError(f"{label}: {source} is missing or damaged; run git pull again")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"[OK] {label} (installed)")


def _copilot_section() -> dict:
    raw = load_effective_tables(PROJECT_DIR)
    section = raw.get("copilot", {})
    return section if isinstance(section, dict) else {}


def _llm_asset(model_name: str) -> tuple[str, str, Path, str | None, int] | None:
    known = LLM_MODELS.get(model_name)
    if known is None:
        return None
    label, url, sha256, min_size = known
    return label, url, LLM_DIR / model_name, sha256, min_size


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
    if marker.is_file() and marker.read_text(encoding="ascii").strip() == expected_sha256:
        return True

    actual = _sha256(path)
    if actual != expected_sha256:
        print(f"Existing {path.name} failed SHA256; downloading a clean copy.")
        path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
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
        with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as out:
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


def main() -> int:
    # `setup_copilot.py <model file>` also fetches another known reply model,
    # e.g. to benchmark it, without changing config.toml.
    extra = [name for name in sys.argv[1:] if not name.startswith("-")]
    unknown = [name for name in extra if name not in LLM_MODELS]
    if unknown:
        print(f"ERROR: unknown model {unknown}; known: " + ", ".join(LLM_MODELS), file=sys.stderr)
        return 1
    section = _copilot_section()
    if not bool(section.get("enabled", True)):
        print("Copilot is disabled in the effective settings; model deployment skipped.")
        return 0

    model_name = str(section.get("llm_model", DEFAULT_LLM_MODEL)).strip()
    llm_asset = _llm_asset(model_name)
    if llm_asset is None and not (LLM_DIR / model_name).is_file():
        print(
            f"ERROR: copilot.llm_model = {model_name!r} is not a known download; "
            f"place it at {LLM_DIR / model_name} or use one of: "
            + ", ".join(LLM_MODELS),
            file=sys.stderr,
        )
        return 1

    stt_engine = str(section.get("stt_engine", "paraformer")).strip().lower()
    stt_assets = SENSEVOICE_ASSETS if stt_engine == "sensevoice" else ()

    print("Checking SilentTalk Copilot models...")
    try:
        extra_assets = [_llm_asset(name) for name in extra]
        for asset in (*ASSETS, *stt_assets, *([llm_asset] if llm_asset else []), *extra_assets):
            _download(*asset)
        for bundled in BUNDLED:
            _install_bundled(*bundled)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("SilentTalk Copilot models are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
