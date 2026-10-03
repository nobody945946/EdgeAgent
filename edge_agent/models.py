"""Model discovery and Hub metadata; inference and downloads keep their owners."""

from __future__ import annotations

import json
import re
import struct
import urllib.request
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote


def model_filename(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(char in value for char in "/\\:")
        or any(ord(char) < 32 for char in value)
        or not value.lower().endswith(".gguf")
    ):
        raise ValueError("請選擇 models 目錄中的 GGUF 模型檔")
    return value


def model_metadata(path: Path) -> dict[str, object]:
    path = path.resolve()
    stat = path.stat()
    return dict(_read_model_metadata(path, stat.st_size, stat.st_mtime_ns))


@lru_cache(maxsize=128)
def _read_model_metadata(path: Path, size: int, mtime_ns: int) -> dict[str, object]:
    """Read GGUF metadata without loading weights or tokenizer arrays into RAM."""
    sizes = {
        0: "B",
        1: "b",
        2: "H",
        3: "h",
        4: "I",
        5: "i",
        6: "f",
        7: "?",
        10: "Q",
        11: "q",
        12: "d",
    }
    with path.open("rb") as stream:

        def read(length: int) -> bytes:
            if length < 0 or length > 16 * 1024 * 1024:
                raise ValueError("GGUF metadata too large")
            data = stream.read(length)
            if len(data) != length:
                raise ValueError("Incomplete GGUF metadata")
            return data

        def number(fmt: str):
            return struct.unpack("<" + fmt, read(struct.calcsize(fmt)))[0]

        def skip(length: int) -> None:
            if length < 0 or stream.tell() + length > size:
                raise ValueError("Incomplete GGUF metadata")
            stream.seek(length, 1)

        def value(kind: int, keep: bool = False):
            if kind in sizes:
                if keep:
                    return number(sizes[kind])
                skip(struct.calcsize(sizes[kind]))
            elif kind == 8:
                length = number("Q")
                if keep:
                    return read(length).decode("utf-8")
                skip(length)
            elif kind == 9:
                element, count = number("I"), number("Q")
                if element == 9 or count > 10_000_000:
                    raise ValueError("Invalid GGUF array")
                if element in sizes:
                    skip(count * struct.calcsize(sizes[element]))
                else:
                    for _ in range(count):
                        value(element)
            else:
                raise ValueError("Unknown GGUF metadata type")
            return None

        if read(4) != b"GGUF" or number("I") not in (2, 3):
            raise ValueError("Unsupported GGUF file")
        tensors, count = number("Q"), number("Q")
        if count > 100_000 or tensors == 0:
            raise ValueError("Invalid GGUF model header")
        metadata = {}
        for _ in range(count):
            key = read(number("Q")).decode("utf-8")
            keep = key in {
                "general.architecture",
                "general.name",
                "general.type",
                "adapter.type",
                "split.count",
                "tokenizer.chat_template",
                "clip.has_vision_encoder",
                "clip.has_audio_encoder",
            } or key.endswith(".context_length")
            item = value(number("I"), keep)
            if keep:
                metadata[key] = item
    architecture = str(metadata.get("general.architecture", ""))
    template = str(metadata.get("tokenizer.chat_template", ""))
    return {
        "filename": path.name,
        "name": metadata.get("general.name") or path.stem,
        "bytes": size,
        "architecture": architecture,
        "projector": architecture == "clip",
        "vision": metadata.get("clip.has_vision_encoder") is True,
        "audio": metadata.get("clip.has_audio_encoder") is True,
        "context_length": metadata.get(architecture + ".context_length", 0),
        "adapter": metadata.get("general.type") == "adapter"
        or "adapter.type" in metadata,
        "split": int(metadata.get("split.count", 1) or 1) > 1,
        # This describes template support, not a claim about agent ability.
        "tool_template": "tools" in template or "tool_calls" in template,
        "error": "",
    }


def list_models(directory: Path) -> list[dict[str, object]]:
    result = []
    for path in sorted(directory.glob("*.gguf"), key=lambda item: item.name.casefold()):
        if path.resolve().parent != directory.resolve():
            continue
        try:
            item = model_metadata(path)
        except (OSError, ValueError, UnicodeError, struct.error) as exc:
            item = {
                "filename": path.name,
                "name": path.stem,
                "bytes": path.stat().st_size,
                "error": str(exc),
                "adapter": False,
                "split": False,
            }
        result.append(item)
    return result


def hub_catalog(repository: object) -> dict[str, object]:
    if not isinstance(repository, str) or not re.fullmatch(
        r"[\w.-]+/[\w.-]+", repository
    ):
        raise ValueError("請輸入 Hugging Face 模型庫，格式為作者/模型庫")
    request = urllib.request.Request(
        f"https://huggingface.co/api/models/{repository}?blobs=true",
        headers={"User-Agent": "EdgeAgent/1.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    revision = data.get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("模型庫沒有可固定的版本")
    files = []
    for sibling in data.get("siblings", []):
        filename = sibling.get("rfilename", "")
        if not filename.lower().endswith(".gguf"):
            continue
        if re.search(r"-\d{5}-of-\d{5}\.gguf$", filename):
            continue
        digest = (sibling.get("lfs") or {}).get("sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        files.append(
            {
                "filename": filename,
                "projector": Path(filename).name.lower().startswith("mmproj"),
                "sha256": digest,
                "bytes": sibling.get("size", 0),
                "url": f"https://huggingface.co/{repository}/resolve/{revision}/{quote(filename, safe='/')}",
            }
        )
    return {"repository": repository, "revision": revision, "files": files}
