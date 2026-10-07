"""What the router loads for a mode: the model, the adapters and projector,
and how one context pool is shared by the Agent and a leased character."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from edge_agent.config import bounded_int, required_string
from edge_agent.context_pool import ContextAllocation, allocate_context
from edge_agent.deploy import ensure_projector
from edge_agent.models import adapter_file, model_file, model_metadata, projector_file
from edge_agent.runtime import CONTROL_PORT, build_server_args, find_llama_server


@dataclass(frozen=True, slots=True)
class LoadPlan:
    model_path: Path
    agent_lora: Path | None
    character_lora: Path | None
    # Every adapter the server loads, in the order requests name them by.
    loras: list[Path]
    context_size: int
    allocation: ContextAllocation
    args: list[str]


def plan_load(
    mode: str,
    config: dict[str, object],
    character: dict[str, object] | None,
    model_dir: Path,
    port: int,
) -> LoadPlan:
    """`config` is the Agent's own selection; a character lease (`character`)
    supplies the model and its adapter for both clients."""
    if mode == "base":
        model_path = model_file(model_dir, str(config["model"]))
        metadata = model_metadata(model_path)
        if metadata["adapter"] or metadata["split"] or metadata["projector"]:
            raise ValueError("selected file is not a standalone model")
        agent_lora = adapter_file(model_dir, metadata, str(config["lora"]))
        character_lora = None
        projector_path = (
            projector_file(model_dir, str(config["mmproj"]))
            if config["mmproj"]
            else ensure_projector(model_path, str(metadata["architecture"]))
        )
        character_context = 0
    elif mode == "character":
        if character is None:
            raise ValueError("character configuration required")
        model_path = model_file(model_dir, required_string(character, "model"))
        lora_name = required_string(character, "lora")
        metadata = model_metadata(model_path)
        # The character's adapter is the lease's; the Agent keeps its own
        # (chosen for its model), when that model is the leased one.
        character_lora = adapter_file(model_dir, metadata, lora_name)
        agent_lora = (adapter_file(model_dir, metadata, str(config["lora"]))
                      if config["model"] == model_path.name else None)
        # How much of the shared window the character's turns need.
        character_context = bounded_int(character, "context_size", 1_024, 262_144)
        character_port = bounded_int(character, "port", 1_024, 65_535)
        if character_port in (CONTROL_PORT, port):
            raise ValueError("character port conflicts with EdgeAgent")
        # The Agent shares the lease and reads the images people post in a
        # chat (the owner, 2026-10-05: OCR in chat), so the leased base
        # brings its own pinned projector (E4B: about 0.55 GB of RAM with
        # low VRAM, which keeps it off the GPU).
        projector_path = ensure_projector(model_path, str(metadata["architecture"]))
    else:
        raise ValueError(f"unknown EdgeAgent mode: {mode}")

    # EdgeAgent owns the server: one pool of context (its setting, up to
    # the model's own limit) that every slot draws from, placed on the
    # GPU and CPU by --fit. A lease supplies the model and adapter for
    # both clients. One slot is lent to the idle character; an Agent task
    # owns the full window. Standalone preferences remain in config.
    context_size = int(config["context_size"])
    limit = metadata["context_length"]
    if isinstance(limit, int) and limit > 0:
        context_size = min(context_size, limit)
        character_context = min(character_context, limit)
    # The character's adapter first (index 0, as the lease's client
    # names it), then the Agent's; one file serving both is loaded once.
    loras = list(dict.fromkeys(path for path in (character_lora, agent_lora) if path))
    allocation = allocate_context(context_size, character_context)
    # Keep one public inference endpoint for the Agent and leased clients.
    args = build_server_args(
        find_llama_server(),
        model_path,
        port=port,
        context_size=context_size,
        threads=max(2, min(6, (os.cpu_count() or 4) // 2)),
        gpu_layers=int(config["gpu_layers"]),
        low_vram=bool(config["low_vram"]),
        lora_paths=loras,
        projector_path=projector_path,
        slots=allocation.slots,
    )
    return LoadPlan(model_path, agent_lora, character_lora, loras, context_size,
                    allocation, args)
