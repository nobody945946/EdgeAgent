from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Iterable, Iterator
from pathlib import Path
from uuid import uuid4

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from edge_agent.api import (
    ensure_service,
    request_base,
    service_status,
    shutdown_service,
)
from edge_agent.runtime import CONTROL_PORT, CORE_ROOT, EdgeAgentError

AUTOSTART_NAME = "EdgeAgent.cmd"
LEGACY_AUTOSTART_NAME = "EdgeAgent.lnk"
DISABLED_AUTOSTART_DIR = CORE_ROOT / "disabled-autostart"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is unavailable")
    return (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )


def install_command() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    links = Path(local_appdata) / "Microsoft" / "WinGet" / "Links"
    links.mkdir(parents=True, exist_ok=True)
    shim = links / "edge-agent.cmd"
    python = CORE_ROOT / ".venv" / "Scripts" / "python.exe"
    cli = CORE_ROOT / "edge_agent" / "cli.py"
    if not python.is_file() or not cli.is_file():
        raise RuntimeError(f"EdgeAgent is not installed at {CORE_ROOT}")

    # ASCII launch files work even when cmd.exe has no console/code-page handle.
    # Paths decode to Unicode inside PowerShell; user arguments stay separate.
    def encoded_path(path: Path) -> str:
        return base64.b64encode(str(path).encode("utf-16-le")).decode("ascii")

    launcher = links / "edge-agent-launch.ps1"
    launcher.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$python = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded_path(python)}'))\n"
        f"$cli = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded_path(cli)}'))\n"
        "& $python $cli @args\nexit $LASTEXITCODE\n",
        encoding="ascii",
    )
    shim.write_text(
        '@echo off\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0edge-agent-launch.ps1" %*\n',
        encoding="ascii",
    )
    # Retire the former product's launch artifacts after the replacement exists.
    for previous in (links / "e4b.cmd", links / "e4b-launch.ps1"):
        previous.unlink(missing_ok=True)
    startup = _startup_dir()
    for previous in (startup / "E4B Edge AI.cmd", startup / "E4B Edge AI.lnk"):
        _preserve_disabled(previous)
    if os.name == "nt":
        _register_command_path(links)
    return shim


def _register_command_path(links: Path) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = str(current).split(";")
        if str(links).casefold() not in [
            entry.rstrip("\\").casefold() for entry in entries
        ]:
            winreg.SetValueEx(
                key, "Path", 0, kind, str(current).rstrip(";") + ";" + str(links)
            )
    # Notify Explorer so future terminals inherit the user command path.
    import ctypes

    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 2, 1000, ctypes.byref(result)
    )


def autostart_enabled() -> bool:
    startup = _startup_dir()
    return (startup / AUTOSTART_NAME).exists() or (
        startup / LEGACY_AUTOSTART_NAME
    ).exists()


def _preserve_disabled(path: Path) -> None:
    if not path.exists():
        return
    DISABLED_AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    destination = DISABLED_AUTOSTART_DIR / path.name
    suffix = 1
    while destination.exists():
        destination = DISABLED_AUTOSTART_DIR / f"{path.stem}.{suffix}{path.suffix}"
        suffix += 1
    path.rename(destination)


def set_autostart(enabled: bool) -> None:
    startup = _startup_dir()
    startup.mkdir(parents=True, exist_ok=True)
    command = startup / AUTOSTART_NAME
    legacy = startup / LEGACY_AUTOSTART_NAME
    if enabled:
        pythonw = CORE_ROOT / ".venv" / "Scripts" / "pythonw.exe"
        cli = CORE_ROOT / "edge_agent" / "cli.py"
        command.write_text(
            f'@echo off\nstart "" "{pythonw}" "{cli}" start\n',
            encoding="utf-8",
        )
        _preserve_disabled(legacy)
    else:
        _preserve_disabled(command)
        _preserve_disabled(legacy)


def print_status() -> None:
    status = service_status()
    if status is None:
        print("EdgeAgent stopped (GPU and model memory released)")
        return
    mode = status.get("mode")
    if mode == "base":
        print(f"EdgeAgent running: {status.get('url')}")
    elif mode == "character":
        print("EdgeAgent is leased to SilentTalk (character LoRA)")
    else:
        print(f"EdgeAgent status: {mode}")


def _iter_sse(lines: Iterable[bytes]) -> Iterator[tuple[str, dict[str, object]]]:
    event = "message"
    data: list[str] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                payload = json.loads("\n".join(data))
                if isinstance(payload, dict):
                    yield event, payload
            event = "message"
            data = []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        payload = json.loads("\n".join(data))
        if isinstance(payload, dict):
            yield event, payload


def _chat_turn(session_id: str, text: str, thinking_effort: str) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{CONTROL_PORT}/chat",
        data=json.dumps(
            {
                "session_id": session_id,
                "text": text,
                "thinking_effort": thinking_effort,
                "agent": False,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    completed = False
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            for event, payload in _iter_sse(response):
                if event == "delta":
                    sys.stdout.write(str(payload.get("text", "")))
                    sys.stdout.flush()
                elif event == "compacted":
                    print(f"\n[{payload.get('message', '對話記憶已整理')}]", end="")
                elif event == "done":
                    completed = True
                elif event == "error":
                    print(
                        f"\nEdgeAgent error: {payload.get('message', 'unknown error')}"
                    )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"\nEdgeAgent error ({exc.code}): {detail}")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"\nEdgeAgent error: {exc}")
    if completed:
        print()
    elif not sys.stdout.isatty():
        return
    else:
        print("\n[本回合未完成]")


def run_chat(thinking_effort: str) -> int:
    request_base()
    session_id = uuid4().hex
    print("EdgeAgent terminal chat  |  /reset 重設對話  |  /exit 離開")
    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in {"/exit", "/quit"}:
            return 0
        if text.lower() == "/reset":
            session_id = uuid4().hex
            print("[已開始新對話]")
            continue
        print("EdgeAgent> ", end="", flush=True)
        try:
            _chat_turn(session_id, text, thinking_effort)
        except KeyboardInterrupt:
            print("\n[本回合已中斷]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge-agent", description="Local EdgeAgent")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("start", help="start EdgeAgent without opening a browser")
    subparsers.add_parser("stop", help="stop EdgeAgent and release GPU memory")
    subparsers.add_parser("status", help="show the current EdgeAgent mode")
    subparsers.add_parser(
        "install", help="install or refresh the global edge-agent command"
    )
    chat = subparsers.add_parser("chat", help="chat with EdgeAgent in this terminal")
    chat.add_argument(
        "--thinking-effort",
        choices=("off", "low", "medium", "high"),
        default="medium",
    )
    auto = subparsers.add_parser("autostart", help="control Windows login startup")
    auto.add_argument("state", choices=("on", "off", "status"))
    args = parser.parse_args(argv)

    try:
        if args.command == "chat":
            return run_chat(args.thinking_effort)
        if args.command is None:
            status = ensure_service(require_ready=False)
            webbrowser.open(str(status["url"]))
            return 0
        if args.command == "start":
            request_base()
            print_status()
            return 0
        if args.command == "stop":
            shutdown_service()
            print_status()
            return 0
        if args.command == "status":
            print_status()
            return 0
        if args.command == "install":
            print(install_command())
            return 0
        if args.command == "autostart":
            if args.state == "on":
                set_autostart(True)
            elif args.state == "off":
                set_autostart(False)
            print("EdgeAgent autostart: " + ("on" if autostart_enabled() else "off"))
            return 0
    except (EdgeAgentError, OSError, RuntimeError) as exc:
        print(f"EdgeAgent error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
