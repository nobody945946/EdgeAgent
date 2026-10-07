"""The real Pi SDK keeping a conversation in RAM: history retrieval,
compaction, the output budget, media and the model's capabilities."""

from __future__ import annotations

import json
from datetime import UTC

import pytest

from edge_agent.attachments import decode_attachments
from edge_agent.chat import NativeChat
from tests.test_agent_chat import installed_pi, repeating_model  # noqa: F401 - fixture
from tests.test_attachments import media_fixture


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_retrieves_archived_fact_without_replaying_original_history(
    tmp_path, repeating_model  # noqa: F811 - imported fixture
):
    from datetime import datetime

    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    stamp = datetime.now(UTC).isoformat()
    chat.session("archive").entries = [
        {
            "type": "session",
            "version": 3,
            "id": "archive",
            "timestamp": stamp,
            "cwd": str(tmp_path),
        },
        {
            "type": "message",
            "id": "fact",
            "parentId": None,
            "timestamp": stamp,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "ticket-567 uses alpha.py. "
                        + "unrelated-padding " * 2000,
                    }
                ],
                "timestamp": 0,
            },
        },
        {
            "type": "message",
            "id": "kept",
            "parentId": "fact",
            "timestamp": stamp,
            "message": {"role": "user", "content": "recent item", "timestamp": 0},
        },
        {
            "type": "compaction",
            "id": "compact",
            "parentId": "kept",
            "timestamp": stamp,
            "summary": "Rolling summary",
            "firstKeptEntryId": "kept",
            "tokensBefore": 30000,
        },
    ]
    try:
        assert (
            list(chat.stream_reply("archive", "ticket-567", working_dir=tmp_path))[-1][
                0
            ]
            == "done"
        )
        request = state["requests"][-1]
        wire = json.dumps(request["messages"])
        assert "ticket-567 uses alpha.py" in wire
        assert "Historical reference excerpts" in wire
        assert len(wire) < 10000
        assert (
            len(
                [
                    entry
                    for entry in chat.session("archive").entries
                    if entry.get("id") == "fact"
                ]
            )
            == 1
        )
        assert not any(
            entry.get("message", {}).get("customType") == "edge-history"
            for entry in chat.session("archive").entries
        )
        assert "search_history" in [
            tool["function"]["name"] for tool in request["tools"]
        ]
        previous_pid = chat.bridge.process.pid
        chat.configure_context(131072, working_context_size=65536)
        assert (
            list(chat.stream_reply("archive", "ticket-567", working_dir=tmp_path))[-1][
                0
            ]
            == "done"
        )
        assert chat.bridge.process.pid != previous_pid
        assert "ticket-567 uses alpha.py" in json.dumps(
            state["requests"][-1]["messages"]
        )
        assert (
            list(chat.stream_reply("other", "ticket-567", working_dir=tmp_path))[-1][0]
            == "done"
        )
        assert "ticket-567 uses alpha.py" not in json.dumps(
            state["requests"][-1]["messages"]
        )
        assert not list(tmp_path.iterdir())
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_compacts_at_working_target_and_retains_ram_archive(
    tmp_path, repeating_model  # noqa: F811
):
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    # An owner who trades history for faster prompts: compaction at 32K of a
    # 128K window (by default the target is the whole window).
    chat.configure_context(131072, working_context_size=32768)
    try:
        assert (
            list(
                chat.stream_reply(
                    "compact", "archive-padding " * 4000, working_dir=tmp_path
                )
            )[-1][0]
            == "done"
        )
        result = list(
            chat.stream_reply("compact", "recent-padding " * 2400, working_dir=tmp_path)
        )
        assert any(name == "compacted" for name, _ in result)
        assert result[-1][0] == "done"
        assert any(
            entry.get("type") == "compaction"
            for entry in chat.session("compact").entries
        )
        assert any(
            "archive-padding" in json.dumps(entry.get("message", {}))
            for entry in chat.session("compact").entries
        )
        assert (
            list(chat.stream_reply("compact", "next-item", working_dir=tmp_path))[-1][0]
            == "done"
        )
        wire = json.dumps(state["requests"][-1]["messages"])
        assert "archive-padding" not in wire
        assert "recent-padding" in wire
        assert "fixture" in wire
        assert chat.context_size == 131072
        assert chat.max_output_tokens == 8192
        assert not list(tmp_path.iterdir())
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
@pytest.mark.parametrize("use_gateway", [False, True])
def test_real_sdk_continues_the_same_task_after_context_overflow_compaction(
    tmp_path, repeating_model, use_gateway  # noqa: F811
):
    from contextlib import ExitStack, nullcontext

    from tests.test_inference import routed_model

    port, state = repeating_model
    phases = []
    context_size = 65536 if use_gateway else 32768

    def respond(request, calls):
        if not request.get("tools"):
            phases.append("compact")
            return {"text": "Task: create first.txt and second.txt. first.txt is done; create second.txt next."}
        phase = sum(item != "compact" for item in phases)
        if phase == 1:
            phases.append("overflow")
            return {"status": 400, "body": {"error": {"message": f"This model's maximum context length is {context_size} tokens. Requested tokens exceed context length."}}}
        if phase in (0, 2):
            name = "first.txt" if phase == 0 else "second.txt"
            phases.append(name)
            return {"tool": "write", "input": {"path": name, "content": "synthetic completed step"}}
        phases.append("done")
        return {"text": "Both task steps completed."}

    state["answer"] = True
    stack = ExitStack()
    gateway = None
    if use_gateway:
        port, gateway = stack.enter_context(routed_model(port))
    chat = NativeChat(model_port=port)
    if gateway is not None:
        chat.api_key = gateway.access.agent_key
    chat.configure_context(context_size, max_output_tokens=8192, working_context_size=context_size)
    try:
        with gateway.access.agent_turn() if gateway else nullcontext():
            assert list(chat.stream_reply(
                "continuing-task", "synthetic-archive-context " * (1600 if use_gateway else 800),
                working_dir=tmp_path,
            ))[-1][0] == "done"
        state["response_for"] = respond
        with gateway.access.agent_turn() if gateway else nullcontext():
            events = list(chat.stream_reply(
                "continuing-task", "Create first.txt and second.txt. " + "synthetic-task-context " * (2000 if use_gateway else 1000),
                agent=True, working_dir=tmp_path,
                permissions={"edit": "allow", "shell": "deny", "web": "deny"},
            ))
        assert any(name == "compacted" for name, _ in events), (
            phases, [(name, data.get("message")) for name, data in events if name in ("error", "status")]
        )
        assert events[-1][0] == "done"
        assert (tmp_path / "first.txt").read_text() == "synthetic completed step"
        assert (tmp_path / "second.txt").read_text() == "synthetic completed step"
        assert phases.count("first.txt") == phases.count("second.txt") == 1
        assert phases.index("first.txt") < phases.index("compact") < phases.index("second.txt")
        assert chat.session("continuing-task").entries
    finally:
        chat.close()
        stack.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_history_tool_recovers_original_text_without_write_approval(
    tmp_path, repeating_model  # noqa: F811
):
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    try:
        assert (
            list(
                chat.stream_reply(
                    "lookup", "ticket-567 uses alpha.py", working_dir=tmp_path
                )
            )[-1][0]
            == "done"
        )
        state.update(
            answer=False,
            tool="search_history",
            input={"query": "ticket-567"},
            answer_after=3,
        )
        result = list(
            chat.stream_reply("lookup", "look up earlier item", working_dir=tmp_path)
        )
        assert result[-1][0] == "done"
        assert not any(name == "approval" for name, _ in result)
        assert any(
            name == "tool"
            and payload["tool"] == "search_history"
            and payload["phase"] == "finished"
            for name, payload in result
        )
        lookup = next(
            message
            for message in state["requests"][-1]["messages"]
            if message["role"] == "tool"
        )
        assert "ticket-567 uses alpha.py" in json.dumps(lookup)
        assert "Historical reference excerpts" in json.dumps(lookup)
        assert not list(tmp_path.iterdir())
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
@pytest.mark.parametrize("indices", [(0, 1), (1,)])
def test_real_sdk_media_reaches_provider_and_ram_history(
    tmp_path, repeating_model, indices  # noqa: F811
):
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    chat.configure_context(16384, "fixture", {"vision": True, "audio": True})
    files = media_fixture()
    _, media = decode_attachments(
        [files[index] for index in indices], chat.capabilities
    )
    try:
        assert (
            list(
                chat.stream_reply(
                    "fixture", "fixture", working_dir=tmp_path, media=media
                )
            )[-1][0]
            == "done"
        )
        assert (
            list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
            == "done"
        )
        for request in state["requests"]:
            assert request["cache_prompt"] is True
            assert request["reasoning_effort"] == "none"
            assert request["max_completion_tokens"] == 8192
            assert request["chat_template_kwargs"]["enable_thinking"] is False
            assert any(
                isinstance(message.get("content"), list)
                and {"text", "input_audio"}.issubset(
                    {item["type"] for item in message["content"]}
                )
                for message in request["messages"]
            )
            content = [
                item
                for message in request["messages"]
                if isinstance(message.get("content"), list)
                for item in message["content"]
            ]
            assert any(
                item.get("type") == "image_url"
                and item["image_url"]["url"].startswith("data:image/png;base64,")
                for item in content
            ) == (0 in indices)
            assert any(
                item.get("type") == "input_audio"
                and item["input_audio"]["data"] == media[-1]["data"]
                for item in content
            )
            assert not any(
                item.get("type") == "image_url"
                and "data:audio/" in item["image_url"]["url"]
                for item in content
            )
        chat.configure_context(16384, "vision-only", {"vision": True, "audio": False})
        events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
        assert events[-1][0] == "error"
        assert "不支援" in events[-1][1]["message"]
        assert len(state["requests"]) == 2
        assert not list(tmp_path.iterdir())
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_output_budget_change_rebuilds_process_without_losing_history(
    tmp_path, repeating_model  # noqa: F811
):
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    chat.configure_context(32768, "fixture", max_output_tokens=16384)
    try:
        assert (
            list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
            == "done"
        )
        previous_pid = chat.bridge.process.pid
        assert state["requests"][-1]["max_completion_tokens"] == 16384
        chat.configure_context(32768, "fixture", max_output_tokens=8192)
        assert (
            list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
            == "done"
        )
        assert chat.bridge.process.pid != previous_pid
        request = state["requests"][-1]
        assert request["max_completion_tokens"] == 8192
        assert len([m for m in request["messages"] if m["role"] == "user"]) == 2
        chat.configure_context(4096, "fixture", max_output_tokens=8192)
        assert (
            list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
            == "done"
        )
        # The upstream SDK always subtracts 4096 safety tokens. This reproduces
        # the old unusable window; the public service now rejects this context.
        assert state["requests"][-1]["max_completion_tokens"] == 1
        assert not list(tmp_path.iterdir())
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_refreshes_capabilities_for_same_base(tmp_path, repeating_model):  # noqa: F811
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    chat.configure_context(16384, "fixture", {"vision": False, "audio": False})
    try:
        assert (
            list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
            == "done"
        )
        previous_pid = chat.bridge.process.pid
        chat.configure_context(16384, "fixture", {"vision": True, "audio": True})
        _, media = decode_attachments([media_fixture()[1]], chat.capabilities)
        assert (
            list(
                chat.stream_reply(
                    "fixture", "fixture", working_dir=tmp_path, media=media
                )
            )[-1][0]
            == "done"
        )
        assert chat.bridge.process.pid != previous_pid
        assert any(
            item.get("type") == "input_audio"
            for message in state["requests"][-1]["messages"]
            if isinstance(message.get("content"), list)
            for item in message["content"]
        )
    finally:
        chat.close()
