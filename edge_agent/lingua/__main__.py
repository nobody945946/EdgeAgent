"""python -m edge_agent.lingua KIND [options] < text

The Agent's translate_machine tool runs this: one translation, text in on
stdin, the exact Chinese out on stdout (exit 0), or why it cannot be
translated (exit 2). Nothing is executed; PowerShell is only asked to parse.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

KINDS = ("regex", "powershell", "excel", "sql", "python", "mavlink", "machine_code")


def translate(kind: str, text: str, flavor: str, flags: str, dialect: str, arch: str, address: int) -> str:
    if kind == "regex":
        from edge_agent.lingua import regex

        return regex.translate(text.rstrip("\r\n"), flavor, flags).text()
    if kind == "powershell":
        from edge_agent.lingua import powershell

        return powershell.translate(text).text()
    if kind == "excel":
        from edge_agent.lingua import excel

        return excel.translate(text.strip()).text()
    if kind == "sql":
        from edge_agent.lingua import sql

        return sql.translate(text, dialect if dialect in sql.DIALECTS else "").text()
    if kind == "python":
        from edge_agent.lingua import python

        return python.translate(text).text()
    if kind == "mavlink":
        from edge_agent.lingua import mavlink

        return mavlink.translate(text, dialect if dialect in mavlink.DIALECTS else "ardupilotmega").text()
    from edge_agent.lingua import asm

    return asm.translate(text, arch, address).text()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m edge_agent.lingua")
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("--flavor", default="dotnet", choices=("python", "dotnet", "js"),
                        help="regex rules; .NET is what PowerShell uses")
    parser.add_argument("--flags", default="")
    parser.add_argument("--dialect", default="", help="sql: sqlite/postgres/tsql; mavlink: ardupilotmega/common")
    parser.add_argument("--arch", default="x86-64", choices=("x86-64", "x86-32", "thumb", "arm", "arm64"))
    parser.add_argument("--address", default="0", help="machine code start address (0x... or decimal)")
    args = parser.parse_args(argv)
    try:
        out = translate(args.kind, sys.stdin.read(), args.flavor, args.flags, args.dialect, args.arch,
                        int(args.address, 0))
    # Each translator refuses with a ValueError subclass; no PowerShell is a RuntimeError.
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"無法翻譯：{exc}")
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
