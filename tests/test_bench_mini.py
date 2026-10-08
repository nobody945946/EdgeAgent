"""bench/mini_runner.py: observations exactly as the open trajectories
recorded them (bench/mini_templates.json)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The mini harness runs only in the bench's Linux sandbox (bash, /testbed).
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the mini harness runs in the Linux sandbox")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import mini_runner


def test_short_output_is_tojson_with_html_escapes(tmp_path: Path) -> None:
    text, submitted = mini_runner.observe("printf \"it's <a> & b\"", str(tmp_path))
    assert submitted is None
    assert text == '{\n  "returncode": 0,\n  "output": "it\\u0027s \\u003ca\\u003e \\u0026 b"\n}'


def test_long_output_keeps_head_and_tail(tmp_path: Path) -> None:
    text, _ = mini_runner.observe("python3 -c \"print('x' * 25000)\"", str(tmp_path))
    data = json.loads(text)
    assert list(data) == ["returncode", "output_head", "output_tail", "elided_chars", "warning"]
    assert len(data["output_head"]) == len(data["output_tail"]) == 5000
    assert data["elided_chars"] == 25001 - 10000 and data["warning"] == "Output too long."


def test_submission_and_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text, submitted = mini_runner.observe(f"echo {mini_runner.SUBMIT} && echo patch", str(tmp_path))
    assert text == "" and submitted == "patch"
    monkeypatch.setitem(mini_runner.TEMPLATES, "timeout_s", 1)
    text, submitted = mini_runner.observe("sleep 3", str(tmp_path))
    assert submitted is None
    assert text.startswith('{\n  "returncode": -1,\n  "output": "", "exception_info": "An error occurred')
    assert "timed out after 1 seconds" in text
