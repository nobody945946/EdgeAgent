"""What the Agent may do, per client. The one owner of permission rules.

Each client (the browser UI, a Discord bot) has a profile: whether it may use
the Agent at all, what happens for file edits, shell commands and web access
("allow" runs it, "ask" waits for the owner's approval, "deny" refuses), and
which workspace folders it may open. The bridge enforces the rules; after web
content enters a turn, earlier "allow this turn" approvals lapse and
"allow" for edits and shell becomes "ask", because a page can carry
instructions.
"""

from __future__ import annotations

from pathlib import Path

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
            if profile[category] not in DECISIONS:
                raise ValueError(
                    f"{client}.{category} must be one of {', '.join(DECISIONS)}"
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
