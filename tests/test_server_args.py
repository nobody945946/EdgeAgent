"""The one llama-server command line (runtime.build_server_args)."""

from __future__ import annotations

from pathlib import Path

import pytest

from edge_agent import runtime


def _value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_base_server_is_one_slot_without_an_extra_agent_loop() -> None:
    args = runtime.build_server_args(
        "llama-server",
        Path("e4b.gguf"),
        port=runtime.GENERAL_PORT,
        context_size=runtime.GENERAL_CONTEXT_SIZE,
        threads=4,
        gpu_layers=99,
        low_vram=True,
    )

    assert _value_after(args, "--ctx-size") == "131072"
    assert _value_after(args, "--parallel") == "1"
    assert "--kv-unified" not in args and "--batch-size" not in args
    assert _value_after(args, "--load-mode") == "mmap"
    assert _value_after(args, "--lazy-mode") == "on"
    assert "--tools" not in args


def test_multimodal_projector_uses_canonical_server_and_low_vram_policy():
    args = runtime.build_server_args(
        "llama-server",
        Path("base.gguf"),
        port=runtime.GENERAL_PORT,
        context_size=4096,
        threads=4,
        gpu_layers=99,
        low_vram=True,
        projector_path=Path("mmproj.gguf"),
    )
    assert _value_after(args, "--mmproj") == "mmproj.gguf"
    assert "--no-mmproj-offload" in args
    assert _value_after(args, "--parallel") == "1"


@pytest.mark.parametrize("gpu_layers", [0, 33, 99])
def test_manual_device_split_disables_automatic_fitting(gpu_layers):
    args = runtime.build_server_args(
        "llama-server", Path("base.gguf"), port=runtime.GENERAL_PORT,
        context_size=98304, threads=4, gpu_layers=gpu_layers, low_vram=True,
    )
    assert _value_after(args, "--n-gpu-layers") == str(gpu_layers)
    assert _value_after(args, "--ctx-size") == "98304"
    assert _value_after(args, "--fit") == "off"
    assert "--fit-ctx" not in args and "--fit-target" not in args
    assert _value_after(args, "--load-mode") == "mmap"
    assert _value_after(args, "--lazy-mode") == "on"


def test_automatic_device_fitting_preserves_large_context_and_cache_precision():
    args = runtime.build_server_args(
        "llama-server",
        Path("base.gguf"),
        port=runtime.GENERAL_PORT,
        context_size=131072,
        threads=4,
        gpu_layers=-1,
        low_vram=True,
    )
    assert _value_after(args, "--n-gpu-layers") == "auto"
    assert _value_after(args, "--ctx-size") == "131072"
    assert _value_after(args, "--fit-ctx") == "131072"
    assert _value_after(args, "--fit-target") == "512"
    assert _value_after(args, "--cache-type-k") == "q8_0"
    assert _value_after(args, "--cache-type-v") == "q8_0"
    assert _value_after(args, "--cache-ram") == "256"
    assert _value_after(args, "--ctx-checkpoints") == "2"


def test_character_server_gets_two_slots_and_lora() -> None:
    args = runtime.build_server_args(
        "llama-server",
        Path("e4b.gguf"),
        port=18088,
        context_size=32768,
        threads=4,
        gpu_layers=99,
        low_vram=True,
        lora_paths=[Path("character.gguf"), Path("agent.gguf")],
        slots=2,
    )

    # One pool both slots draw from, the model mapped from its file, the prompt
    # cache bounded in every mode (upstream keeps 8 GiB and 32 checkpoints).
    assert _value_after(args, "--ctx-size") == "32768"
    assert _value_after(args, "--parallel") == "2"
    assert "--kv-unified" in args
    # A long prompt yields to the other slot every micro-batch.
    assert _value_after(args, "--batch-size") == "512"
    assert _value_after(args, "--load-mode") == "mmap"
    assert _value_after(args, "--lazy-mode") == "on"
    assert _value_after(args, "--cache-ram") == "256"
    assert _value_after(args, "--ctx-checkpoints") == "2"
    # Both adapters loaded, none applied unless a request names it.
    assert _value_after(args, "--lora") == "character.gguf,agent.gguf"
    assert "--lora-init-without-apply" in args
    assert "--tools" not in args
