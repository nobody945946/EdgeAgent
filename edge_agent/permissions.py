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


CORE_MODULES = {
    "Microsoft.PowerShell.Core",
    "Microsoft.PowerShell.Management",
    "Microsoft.PowerShell.Utility",
    "Microsoft.PowerShell.Security",
}
# Changing the current folder would make every later relative path mean
# somewhere the proof did not look.
LOCATION = {"Set-Location", "Push-Location", "Pop-Location"}
PATH_PARAMS = {"Path", "LiteralPath", "FilePath", "Destination", "DestinationPath"}
# Writes whose targets are the named files (copied before they run); any other
# cmdlet that changes something waits for the owner.
WRITES = {"Set-Content", "Add-Content", "Out-File", "Export-Csv", "New-Item", "Copy-Item", "Move-Item",
          "Rename-Item"}
READ_NEEDS_PATH = {"Get-Content", "Select-String", "Get-FileHash", "Import-Csv"}


def _inside(value: object, root: Path, wildcards: bool = False) -> Path | None:
    """A literal filesystem path inside the workspace, or None. Only literal
    paths prove where a command lands; a wildcard could match a link leading
    out of the workspace."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    if (":" in value and not (len(value) > 2 and value[1] == ":" and value[0].isalpha())) \
            or (not wildcards and any(char in value for char in "*?[")):
        return None
    target = Path(value).expanduser()
    target = (target if target.is_absolute() else root / target).resolve()
    # The workspace itself is a place to read; as a write target it is a folder, which undo never covers.
    return target if target.is_relative_to(root) else None


def _prove(command: str, directory: Path) -> tuple[list[Path], bool] | None:
    """What a command writes and whether it changes anything, when it is proven to touch nothing but the
    workspace: native cmdlets in plain pipelines with literal arguments that
    read inside the workspace or write files it names there (returned, to copy
    first). None for anything else: external programs, dynamic code, script
    blocks, sub-expressions, method calls, folder changes, deleting, the
    system, the network, a target that is not a literal path."""
    try:
        info = powershell.analyze(command, cwd=str(directory), timeout=20)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if info["errors"] or info["other_blocks"] or not info["simple_literals"]:
        return None
    statements = info["statements"]
    if not statements or any(s.get("kind") != "pipeline" or s.get("background") for s in statements):
        return None
    elements = [e for s in statements for e in s.get("elements", [])]
    commands = info["all_commands"]
    if not commands or any(e.get("kind") != "command" for e in elements) or len(commands) != len(elements):
        return None
    root = directory.resolve()
    writes: list[Path] = []
    changes = False
    for item in commands:
        entry = powershell.COMMANDS.get(item["resolved"])
        if ((item["target_type"] or item["type"]) != "Cmdlet" or entry is None or item["binding_errors"]
                or item["invocation"] != "Unknown" or item["module"] not in CORE_MODULES
                or item["resolved"] in LOCATION):
            return None
        bound = {param["name"]: param["literal"] for param in item["bound"]}
        if set(bound) - set(entry.params):
            return None
        if item["resolved"] in READ_NEEDS_PATH and not {"Path", "LiteralPath"} & set(bound):
            return None  # FileInfo pipeline inputs can follow links outside the workspace
        places = {name: _inside(bound[name], root, wildcards=name == "LiteralPath")
                  for name in PATH_PARAMS & set(bound)}
        if None in places.values():
            return None
        for redirect in item["redirections"]:
            if redirect.get("merge"):
                continue
            target = _inside(redirect.get("to"), root)
            if target is None:
                return None
            writes.append(target)
            changes = True
        if entry.effect == "read":
            continue
        if item["resolved"] not in WRITES:
            return None
        landing = _landing(item["resolved"], places, bound)
        if landing is None or any(path == root or path.is_dir() for path in landing):
            return None  # a folder as a write target: nothing a file copy can put back
        writes.extend(landing)
        changes = True
    return writes, changes


def _landing(cmdlet: str, places: dict[str, Path], bound: dict) -> list[Path] | None:
    """The files a write changes (its targets, and a moved file's old place),
    or None when they cannot be told from the command."""
    source = places.get("Path") or places.get("LiteralPath")
    if cmdlet in ("Copy-Item", "Move-Item", "Rename-Item"):
        if source is None or not source.is_file() or {"Recurse", "Container", "Filter", "Include", "Exclude"} & set(bound):
            return None
        if cmdlet == "Rename-Item":
            name = bound.get("NewName")
            if not isinstance(name, str) or not name or any(c in name for c in "\\/:*?["):
                return None
            moved = source.parent / name
        else:
            destination = places.get("Destination")
            if destination is None:
                return None
            moved = destination / source.name if destination.is_dir() else destination
        return [moved] if cmdlet == "Copy-Item" else [source, moved]
    if cmdlet == "New-Item":
        target = source
        if target is not None and isinstance(bound.get("Name"), str):
            target = target / bound["Name"]
        if target is None:
            return None
        return [] if str(bound.get("ItemType", "File")).lower() == "directory" else [target]
    targets = [places[name] for name in ("Path", "LiteralPath", "FilePath") if name in places]
    return targets or None


def routine_shell(command: str, directory: Path) -> bool:
    """Prove a literal native read-only pipeline inside the current workspace."""
    proof = _prove(command, directory)
    return proof is not None and not proof[1]


def reversible_shell(command: str, directory: Path) -> list[Path] | None:
    """The files to copy before `command` runs, when it is proven to read and
    write only files it names inside the workspace (_prove); None when the
    owner decides. Programs (scripts, tests, git) and the network always ask:
    what they do cannot be put back."""
    proof = _prove(command, directory)
    return None if proof is None else proof[0]
