"""python -m edge_agent.lingua KIND [options] < text

The Agent's translate_machine tool runs this: one translation, text in on
stdin, the exact Chinese out on stdout (exit 0), or why it cannot be
translated (exit 2). Nothing is executed; PowerShell is only asked to parse.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

KINDS = ("regex", "powershell", "excel", "sql", "python", "mavlink", "machine_code")


def regex_samples(pattern: str, flavor: str, flags: str, samples: list[str]) -> str:
    """The real engine's answer for each sample: does it match, and what."""
    from edge_agent.lingua.engines import RUNNERS

    engine = {"python": "Python", "dotnet": ".NET（PowerShell）", "js": "JavaScript（Node）"}[flavor]
    result = RUNNERS[flavor]([{"pattern": pattern, "flags": flags, "inputs": samples}])
    if result is None:
        return f"（這台電腦沒有 {engine} 引擎，範例沒有實際跑）"
    lines = [f"用真的 {engine} 引擎跑範例（找第一個符合的地方，不要求整段都符合，除非樣式有 ^ 和 $）："]
    for sample, (match, groups) in zip(samples, result[0].get("found", [])):
        if match is None:
            lines.append(f"- 「{sample}」：不符合")
        else:
            shown = "、".join(f"第 {i} 組「{g}」" if g is not None else f"第 {i} 組沒有比到" for i, g in enumerate(groups, 1))
            lines.append(f"- 「{sample}」：符合，比到「{match}」" + (f"；{shown}" if shown else ""))
    return "\n".join(lines)


def translate(kind: str, text: str, flavor: str, flags: str, dialect: str, arch: str, address: int,
              samples: list[str] | None = None) -> str:
    if kind == "regex":
        from edge_agent.lingua import regex

        pattern = text.rstrip("\r\n")
        out = regex.translate(pattern, flavor, flags).text()
        return out + ("\n" + regex_samples(pattern, flavor, flags, samples) if samples else "")
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
    parser.add_argument("--samples", default="", help="regex: a JSON list of strings to run through the real engine")
    args = parser.parse_args(argv)
    try:
        samples = json.loads(args.samples) if args.samples else None
        if samples is not None and not (isinstance(samples, list) and all(isinstance(x, str) for x in samples)):
            raise ValueError("--samples 要是字串的 JSON 清單")
        out = translate(args.kind, sys.stdin.read(), args.flavor, args.flags, args.dialect, args.arch,
                        int(args.address, 0), samples)
    # Each translator refuses with a ValueError subclass; no PowerShell is a RuntimeError.
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"無法翻譯：{exc}")
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
