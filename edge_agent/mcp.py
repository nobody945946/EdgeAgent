"""MCP servers the owner has added: which exist, for which clients, how far trusted.

The one owner of MCP configuration. Each server is a local program spoken to
over stdio (no remote URLs: nothing signs in or leaves the machine on its
own). Per server:

- "permission": "allow" lets its read-only tools (the server's readOnlyHint)
  run without asking; "ask" asks for every call. Calls that change things
  always ask, and destructive ones or any call on a "hardware" server
  (something that moves in the world, like a drone) ask again every time.
- "clients": which clients may use it ("ui" by default; Discord only when
  the owner adds it).
- "enabled": false keeps the entry without starting the program.
- "tools": the server's tools the model may find, by name or with * as a
  wildcard ([] = all). A server with ~170 tools would drown a small model;
  the others stay hidden and cannot be called.

The bridge (agent_bridge.mjs) enforces this; the model sees only a search
tool until it looks a server's tools up (deferred exposure), so a small model
is not handed every tool of every server at once.
"""

from __future__ import annotations

import re

from edge_agent.permissions import CLIENTS

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
PERMISSIONS = ("allow", "ask")
FIELDS = {"command", "args", "env", "description", "enabled", "permission", "hardware", "clients", "tools"}
TOOL_PATTERN = re.compile(r"^[A-Za-z0-9_.*-]{1,80}$")


def validate_mcp_servers(value: object) -> dict[str, dict[str, object]]:
    """A stored value checked and filled with defaults; errors name the field."""
    if not isinstance(value, dict):
        raise TypeError("mcp_servers must be an object")
    servers: dict[str, dict[str, object]] = {}
    namespaces: set[str] = set()
    for name, raw in value.items():
        if not isinstance(name, str) or not NAME.match(name):
            raise ValueError(f"MCP server name {name!r}: letters, digits, _ and - only")
        # Pi names tools mcp__<name with - as _>__<tool>: two names that meet
        # there would share tools.
        namespace = name.replace("-", "_").lower()
        if namespace in namespaces:
            raise ValueError(f"MCP server name {name!r} collides with another")
        namespaces.add(namespace)
        if not isinstance(raw, dict):
            raise TypeError(f"MCP server {name}: object required")
        extra = set(raw) - FIELDS
        if extra:
            raise ValueError(f"MCP server {name}: unknown fields {', '.join(sorted(extra))}")
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"MCP server {name}: command required (a local program)")
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise TypeError(f"MCP server {name}: args must be a list of strings")
        env = raw.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise TypeError(f"MCP server {name}: env must map names to strings")
        description = raw.get("description", "")
        if not isinstance(description, str) or len(description) > 300:
            raise ValueError(f"MCP server {name}: description is at most 300 characters")
        permission = raw.get("permission", "ask")
        if permission not in PERMISSIONS:
            raise ValueError(f"MCP server {name}: permission must be one of {', '.join(PERMISSIONS)}")
        clients = raw.get("clients", ["ui"])
        if not isinstance(clients, list) or not set(clients) <= set(CLIENTS):
            raise ValueError(f"MCP server {name}: clients must be among {', '.join(CLIENTS)}")
        tools = raw.get("tools", [])
        if not isinstance(tools, list) or not all(isinstance(t, str) and TOOL_PATTERN.match(t) for t in tools):
            raise ValueError(f"MCP server {name}: tools must be tool names (* as wildcard)")
        for flag in ("enabled", "hardware"):
            if not isinstance(raw.get(flag, flag == "enabled"), bool):
                raise TypeError(f"MCP server {name}: {flag} must be a boolean")
        servers[name] = {
            "command": command, "args": list(args), "env": dict(env),
            "description": description, "enabled": raw.get("enabled", True),
            "permission": permission, "hardware": raw.get("hardware", False),
            "clients": list(clients), "tools": list(tools),
        }
    return servers


def servers_for(client: str, servers: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """The enabled servers this client may use, as the bridge needs them."""
    return {
        name: {k: server[k] for k in ("command", "args", "env", "description", "permission", "hardware", "tools")}
        for name, server in servers.items()
        if server["enabled"] and client in server["clients"]
    }
