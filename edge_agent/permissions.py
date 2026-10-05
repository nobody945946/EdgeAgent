"""What the Agent may do, per client. The one owner of permission rules.

Each client (the browser UI, a Discord bot) has a profile: whether it may use
the Agent at all, what happens for file edits, shell commands and web access
("allow" runs it, "ask" waits for the owner's approval, "deny" refuses), and
which workspace folders it may open. Shell also supports "routine": the host
approves native literal reads, and asks for everything else through the bridge.
A single task can be narrowed to read-only (read_only_rules): edits are
refused and shell becomes "read", where the host runs the same provable reads
and refuses everything else without asking.
The bridge enforces the rules; after web
content enters a turn, earlier "allow this turn" approvals lapse and
"allow" for edits and shell becomes "ask", because a page can carry
instructions. Opening a URL that neither the user wrote nor a tool returned
always asks (once): the model built it, and it could carry data out.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from edge_agent.lingua import powershell

DECISIONS = ("allow", "ask", "deny")
CATEGORIES = ("edit", "shell", "web")
CLIENTS = ("ui", "discord")

DEFAULT_PERMISSIONS: dict[str, dict[str, object]] = {
    "ui": {
        "enabled": True,
        "edit": "ask",
        "shell": "ask",
        "web": "allow",
        "workspaces": [],
    },
    # Off until the owner turns it on; then every change waits for approval.
    "discord": {
        "enabled": False,
        "edit": "ask",
        "shell": "ask",
        "web": "allow",
        "workspaces": [],
    },
}


def validate_permissions(value: object) -> dict[str, dict[str, object]]:
    """Merge a stored value over the defaults; reject unknown or invalid keys."""
    if not isinstance(value, dict):
        raise TypeError("permissions must be an object")
    unknown = set(value) - set(CLIENTS)
    if unknown:
        raise ValueError(f"unknown permission clients: {', '.join(sorted(unknown))}")
    result: dict[str, dict[str, object]] = {}
    for client in CLIENTS:
        profile = {**DEFAULT_PERMISSIONS[client], **(value.get(client) or {})}
        extra = set(profile) - set(DEFAULT_PERMISSIONS[client])
        if extra:
            raise ValueError(f"unknown permission fields: {', '.join(sorted(extra))}")
        if not isinstance(profile["enabled"], bool):
            raise TypeError("permissions enabled must be a boolean")
        for category in CATEGORIES:
            choices = (*DECISIONS, "routine") if category == "shell" else DECISIONS
            if profile[category] not in choices:
                raise ValueError(
                    f"{client}.{category} must be one of {', '.join(choices)}"
                )
        roots = profile["workspaces"]
        if not isinstance(roots, list) or not all(
            isinstance(r, str) and r.strip() for r in roots
        ):
            raise TypeError("workspaces must be a list of folder paths")
        if len(roots) > 32:
            raise ValueError("at most 32 workspace folders")
        result[client] = {**profile, "workspaces": [r.strip() for r in roots]}
    return result


def profile_for(
    permissions: dict[str, dict[str, object]], client: object
) -> dict[str, object]:
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {', '.join(CLIENTS)}")
    profile = permissions[str(client)]
    if not profile["enabled"]:
        raise PermissionError(
            f"Agent access for {client} is turned off in the permission settings"
        )
    return profile


def workspace_allowed(profile: dict[str, object], directory: Path) -> bool:
    """An empty list allows any folder; otherwise the folder must be inside one."""
    roots = profile["workspaces"]
    if not roots:
        return True
    target = directory.expanduser().resolve()
    return any(
        target.is_relative_to(Path(root).expanduser().resolve()) for root in roots
    )


def bridge_rules(profile: dict[str, object]) -> dict[str, str]:
    """The decisions the bridge enforces for one session."""
    return {category: str(profile[category]) for category in CATEGORIES}


def read_only_rules(rules: dict[str, str]) -> dict[str, str]:
    """Narrow a session's rules for a task the owner asked only to read: no
    edits, and only provable reads in the shell (never asking). It cannot
    widen anything: a refused shell stays refused, web stays as set."""
    return {**rules, "edit": "deny", "shell": "deny" if rules["shell"] == "deny" else "read"}


def routine_shell(command: str, directory: Path) -> bool:
    """Prove a literal native read-only pipeline inside the current workspace.

    Dynamic code, external programs, provider paths and unknown syntax require
    an owner decision. Human translation is never used as a security verdict.
    """
    try:
        info = powershell.analyze(command, cwd=str(directory), timeout=20)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        return False
    if (
        info["errors"]
        or info["other_blocks"]
        or not info["simple_literals"]
        or any(item.get("background") for item in info["statements"])
    ):
        return False
    commands = info["all_commands"]
    if not commands:
        return False
    root = directory.resolve()
    for item in commands:
        entry = powershell.COMMANDS.get(item["resolved"])
        kind = item["target_type"] or item["type"]
        if (
            kind != "Cmdlet"
            or entry is None
            or entry.effect != "read"
            or item["binding_errors"]
            or item["redirections"]
            or item["invocation"] != "Unknown"
            or item["module"]
            not in {
                "Microsoft.PowerShell.Core",
                "Microsoft.PowerShell.Management",
                "Microsoft.PowerShell.Utility",
                "Microsoft.PowerShell.Security",
            }
        ):
            return False
        if item["resolved"] in {
            "Get-Content",
            "Select-String",
            "Get-FileHash",
            "Import-Csv",
        } and not any(
            param["name"] in {"Path", "LiteralPath"} for param in item["bound"]
        ):
            # FileInfo pipeline inputs can follow links outside the workspace.
            return False
        for param in item["bound"]:
            if param["name"] not in entry.params:
                return False
            if param["name"] in {"Path", "LiteralPath", "FilePath"}:
                value = param["literal"]
                if not isinstance(value, str) or not value:
                    return False
                # Only literal filesystem paths can prove the resolved target.
                # A wildcard could match a link leading outside this workspace.
                if ":" in value and not (
                    len(value) > 2 and value[1] == ":" and value[0].isalpha()
                ):
                    return False
                target = Path(value).expanduser()
                if not target.is_absolute():
                    target = root / target
                if (
                    any(char in value for char in "*?[")
                    and param["name"] != "LiteralPath"
                ):
                    return False
                if not target.resolve().is_relative_to(root):
                    return False
    return True
