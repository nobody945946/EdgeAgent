"""python -m edge_agent.lingua, as the Agent's translate_machine tool runs it."""

from __future__ import annotations

import subprocess
import sys

import pytest

from edge_agent.lingua.engines import find_pwsh


def run(kind: str, text: str, *options: str) -> tuple[int, str]:
    done = subprocess.run([sys.executable, "-m", "edge_agent.lingua", kind, *options], input=text,
                          capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    return done.returncode, done.stdout


@pytest.mark.parametrize(("kind", "text", "options", "phrase"), [
    ("regex", "^\\d+$", ("--flavor", "js"), "全形數字不算"),
    ("regex", "^\\d+$", (), ".NET"),
    ("excel", "=VLOOKUP(A2,B:C,2)", (), "取回的是 C 欄"),
    ("sql", "DELETE FROM t", ("--dialect", "sqlite"), "會刪除資料：t 的每一列"),
    ("python", "x = round(y)", (), "round(2.5) 是 2"),
    ("machine_code", "31 c0", (), "把 eax 設成 0"),
    ("machine_code", "0120", ("--arch", "thumb"), "r0 = 1"),
])
def test_translates(kind, text, options, phrase) -> None:
    code, out = run(kind, text, *options)
    assert code == 0 and phrase in out, out


@pytest.mark.skipif(find_pwsh() is None, reason="no PowerShell here")
def test_powershell() -> None:
    code, out = run("powershell", "Remove-Item x -WhatIf")
    assert code == 0 and "只是模擬" in out


def test_mavlink() -> None:
    pytest.importorskip("pymavlink")
    from pymavlink.dialects.v20 import ardupilotmega as m

    raw = m.MAVLink_heartbeat_message(2, 3, 129, 4, 4, 3).pack(m.MAVLink(None, srcSystem=1, srcComponent=1))
    code, out = run("mavlink", raw.hex(" "))
    assert code == 0 and "飛行模式：GUIDED" in out


def test_refusal_says_why() -> None:
    code, out = run("sql", "SELECT (")
    assert code == 2 and out.startswith("無法翻譯：") and "\x1b" not in out
    code, out = run("regex", "(a")
    assert code == 2 and "無法翻譯" in out
