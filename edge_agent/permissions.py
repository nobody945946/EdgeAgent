"""What the Agent may do, per client. The one owner of permission rules.

Each client (the browser UI, a Discord bot) has a profile: whether it may use
the Agent at all, what happens for file edits, shell commands and web access
("allow" runs it, "ask" waits for the owner's approval, "deny" refuses), and
which workspace folders it may open. Edits and shell also take "reversible",
the default (owner, 2026-10-08: approving every command is tiring; what can be
put back may run): the host copies what a change will touch (edge_agent.undo)
and lets it run, and asks the owner only for what cannot be put back
(reversible_shell).
A single task can be narrowed by its scope (scope_rules): "read" refuses
edits and makes shell "read", where the host runs the same provable reads and
refuses everything else without asking; "public" leaves only the web (no
files, no commands, nothing that asks the owner).
The bridge enforces the rules; after web
content enters a turn, earlier "allow this turn" approvals lapse and
"allow" for edits and shell becomes "ask", because a page can carry
instructions. Opening a URL that neither the user wrote nor a tool returned
always asks (once): the model built it, and it could carry data out.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from edge_agent.lingua import powershell

DECISIONS = ("allow", "ask", "deny")
REVERSIBLE = "reversible"  # edits and shell: run what can be put back, ask for the rest
CATEGORIES = ("edit", "shell", "web")
CLIENTS = ("ui", "discord")

DEFAULT_PERMISSIONS: dict[str, dict[str, object]] = {
    "ui": {
        "enabled": True,
        "edit": REVERSIBLE,
        "shell": REVERSIBLE,
        "web": "allow",
        "workspaces": [],
    },
    # Off until the owner turns it on.
    "discord": {
        "enabled": False,
        "edit": REVERSIBLE,
        "shell": REVERSIBLE,
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
        if profile.get("shell") == "routine":  # the earlier reads-only automatic mode, now part of reversible
            profile["shell"] = REVERSIBLE
        extra = set(profile) - set(DEFAULT_PERMISSIONS[client])
        if extra:
            raise ValueError(f"unknown permission fields: {', '.join(sorted(extra))}")
        if not isinstance(profile["enabled"], bool):
            raise TypeError("permissions enabled must be a boolean")
        for category in CATEGORIES:
            choices = DECISIONS if category == "web" else (*DECISIONS, REVERSIBLE)
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


# What one task may touch: "change" (the profile as set), "read" (look at
# this computer, change nothing), "public" (the web only: lookups anyone may
# ask for, with nothing of this computer's in reach).
SCOPES = ("change", "read", "public")


def scope_rules(rules: dict[str, str], scope: str) -> dict[str, str]:
    """Narrow a session's rules to one task's scope. It never widens: a
    refused category stays refused, web stays as set."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    if scope == "change":
        return dict(rules)
    shell = "deny" if scope == "public" or rules["shell"] == "deny" else "read"
    return {**rules, "edit": "deny", "shell": shell}


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


# External programs whose effects stay in the workspace or can be put back:
# scripts and tests of the workspace, the editor, and git's own history.
INTERPRETERS = {"python", "python3", "py", "node"}
GIT_KEEPS = {"status", "diff", "log", "show", "branch", "add", "commit", "init", "rev-parse", "ls-files"}


def _literal_path(value: object, root: Path) -> Path | None:
    """A literal file path inside the workspace, or None."""
    if not isinstance(value, str) or not value or any(char in value for char in "*?[") \
            or (":" in value and not (len(value) > 2 and value[1] == ":" and value[0].isalpha())):
        return None
    target = Path(value).expanduser()
    target = (target if target.is_absolute() else root / target).resolve()
    return target if target.is_relative_to(root) and target != root else None


def _landing(cmdlet: str, origin: Path, bound: dict, root: Path) -> Path | None:
    """Where a renamed, moved or copied file ends up, or None when it cannot be told."""
    if cmdlet == "Rename-Item":
        return _literal_path(str(origin.parent / str(bound.get("NewName", ""))), root) if bound.get("NewName") else None
    destination = _literal_path(bound.get("Destination"), root)
    if destination is None:
        return None
    return destination / origin.name if destination.is_dir() else destination


def _program_keeps(element: dict, root: Path) -> bool:
    name = Path(element["name"]).stem.lower()
    args = [str(a) for a in element["args"]]
    if name in INTERPRETERS:
        if args[:2] in (["-m", "pytest"], ["-m", "unittest"]):
            return True
        script = next((a for a in args if not a.startswith("-")), None)
        return script is not None and _literal_path(script, root) is not None
    if name in ("pytest", "code"):
        return True
    return name == "git" and bool(args) and args[0] in GIT_KEEPS


def reversible_shell(command: str, directory: Path, after_web: bool) -> list[Path] | None:
    """The files to copy before `command` runs when everything it does can be
    put back: reads, files it writes or overwrites by name inside the
    workspace, windows it opens, the workspace's own scripts and tests. None
    when the owner decides: deleting, system changes, sending data out,
    installing, other programs, a target the parser cannot name, and, once web
    content entered the turn, running programs or reaching the network."""
    try:
        info = powershell.analyze(command, cwd=str(directory), timeout=20)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if info["errors"] or info["other_blocks"] or any(s.get("background") for s in info["statements"]):
        return None
    root = directory.resolve()
    paths: list[Path] = []

    def named(values: list[object]) -> bool:
        found = [_literal_path(value, root) for value in values]
        paths.extend(p for p in found if p is not None)
        return bool(found) and None not in found

    for statement in info["statements"]:
        for element in statement.get("elements", []):
            if element["kind"] == "expression":
                if re.search(r">", element["text"]):  # a redirection the parser does not name
                    return None
                continue
            if element["kind"] != "command":
                return None
            redirects = [r for r in element["redirections"] if not r.get("merge")]
            if redirects and not named([r.get("to") for r in redirects]):
                return None
            bound = {param["name"]: param.get("literal") for param in element["bound"]}
            if (element["target_type"] or element["type"]) == "Cmdlet":
                entry = powershell.COMMANDS.get(element["resolved"])
                if entry is None or entry.effect in ("delete", "system"):
                    return None
                if entry.effect == "run":
                    if element["resolved"] != "Start-Process" or after_web:
                        return None
                elif entry.effect == "network":
                    sends = str(bound.get("Method") or "Get").lower() != "get" or {"Body", "InFile", "Form"} & set(bound)
                    if after_web or sends or ("OutFile" in bound and not named([bound["OutFile"]])):
                        return None
                elif element["resolved"] in ("Move-Item", "Copy-Item", "Rename-Item"):
                    # The file's old place (a copy leaves it) and where it lands.
                    origin = _literal_path(bound.get("Path") or bound.get("LiteralPath"), root)
                    landing = _landing(element["resolved"], origin, bound, root) if origin else None
                    if landing is None:
                        return None
                    paths.extend([landing] if element["resolved"] == "Copy-Item" else [origin, landing])
                elif entry.effect in ("write", "overwrite"):
                    names = entry.targets or ("Path", "LiteralPath", "FilePath")
                    if not named([bound[n] for n in names if n in bound]):
                        return None
            elif after_web or not _program_keeps(element, root):
                return None
    return paths
