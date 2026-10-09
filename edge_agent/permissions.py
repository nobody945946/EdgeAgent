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

import fnmatch
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
# What a command takes from the pipeline as a path is proven by what passes
# it along: the files Get-Item names (their links resolved by the proof), and
# the file each line of Get-Content came from (a property, so only commands
# that take paths by property, not the line itself as a path). Get-ChildItem
# lists what it finds, links included, so only a digest of each may follow.
PASSES_NAMED = {"Get-Item": "object", "Get-Content": "property"}
DIGESTS = {"Get-FileHash"}
# Reads of the machine itself, outside any workspace, for the chores an owner
# asks about ("how much memory", "how full is C:"): each from the module
# Windows ships it in, so a same-named function from anywhere else is not it.
SYSTEM_READS = {
    "Get-CimInstance": "CimCmdlets",
    "Get-ComputerInfo": "Microsoft.PowerShell.Management",
    "Get-PSDrive": "Microsoft.PowerShell.Management",
    "Get-Service": "Microsoft.PowerShell.Management",
    "Get-Volume": "Storage",
    "Get-Disk": "Storage",
    "Get-PhysicalDisk": "Storage",
    "Get-Partition": "Storage",
}
# The CIM classes a read may name: hardware, Windows itself, disks, power and
# services. Not processes or startup programs (what shows of them is their
# command lines, which carry passwords and keys), accounts, or environment
# variables (API keys).
CIM_CLASSES = {name.lower() for name in (
    "Win32_ComputerSystem", "Win32_OperatingSystem", "Win32_Processor", "Win32_PhysicalMemory",
    "Win32_LogicalDisk", "Win32_DiskDrive", "Win32_DiskPartition", "Win32_Volume",
    "Win32_VideoController", "Win32_Battery", "Win32_BIOS", "Win32_BaseBoard", "Win32_SoundDevice",
    "Win32_NetworkAdapter", "Win32_Service",
    "Win32_PerfFormattedData_PerfOS_Processor", "Win32_PerfFormattedData_PerfOS_Memory",
    "MSFT_PhysicalDisk", "MSFT_Disk", "MSFT_Volume", "MSFT_Partition",
)}
CIM_NAMESPACES = {"root/cimv2", "root\\cimv2", "root/microsoft/windows/storage", "root\\microsoft\\windows\\storage"}
# Command lines (a process's, a service's) and the views that show every
# property at once: a read never names them, so a key on some program's
# command line never reaches the model. A property pattern that could match
# one is refused too.
HIDDEN_MEMBERS = {"commandline", "binarypathname", "pathname", "startinfo", "environment",
                  "environmentvariables", "psobject", "psbase", "psadapted", "psextended",
                  "ciminstanceproperties"}
VIEW_PARAMS = {"Property", "ExpandProperty"}
# Common parameters that only say what to do with errors and warnings.
QUIET_PARAMS = {"ErrorAction", "WarningAction"}

# The script around the commands (a read-only task could not even count a
# log's errors): variables, operators, script blocks, if and foreach, as long
# as everything in them is proven too. Not: method calls but the listed pure
# ones, types but the listed value types, setting a property (a file's
# attributes and dates are properties), redirection but to $null or another
# stream, other statements (switch -File reads any file), other scopes'
# variables ($env:, $global:, a drive's ${c:\...}), or a variable the script
# did not set itself (a session's earlier commands could have put a key there).
PROVABLE_NODES = {
    "ScriptBlockAst", "NamedBlockAst", "PipelineAst", "CommandAst", "CommandParameterAst",
    "CommandExpressionAst", "StringConstantExpressionAst", "ExpandableStringExpressionAst",
    "ConstantExpressionAst", "ArrayLiteralAst", "ArrayExpressionAst", "HashtableAst", "ScriptBlockExpressionAst",
    "VariableExpressionAst", "BinaryExpressionAst", "UnaryExpressionAst", "ParenExpressionAst",
    "SubExpressionAst", "MemberExpressionAst", "InvokeMemberExpressionAst", "IndexExpressionAst",
    "TypeExpressionAst", "ConvertExpressionAst", "TypeConstraintAst", "AssignmentStatementAst",
    "StatementBlockAst", "IfStatementAst", "ForEachStatementAst", "FileRedirectionAst", "MergingRedirectionAst",
}
AUTOMATIC_READS = {"_", "psitem", "true", "false", "null", "matches", "psversiontable"}
# Value types: converting to them parses (a type with a constructor, such as
# a file writer, would run it).
PURE_TYPES = {"int", "int32", "long", "int64", "double", "decimal", "float", "single", "string", "char", "bool",
              "boolean", "byte", "datetime", "timespan", "version", "regex", "text.regularexpressions.regex",
              "pscustomobject", "management.automation.pscustomobject", "ordered", "hashtable",
              "collections.hashtable", "array", "object", "math", "convert", "stringsplitoptions"}
STATIC_METHODS = {
    "math": None,  # every one
    "string": {"join", "format", "isnullorempty", "isnullorwhitespace", "concat", "compare", "equals"},
    "regex": {"match", "matches", "ismatch", "replace", "split", "escape", "unescape"},
    "datetime": {"parse", "parseexact", "daysinmonth", "isleapyear"},
    "timespan": {"parse", "fromdays", "fromhours", "fromminutes", "fromseconds", "frommilliseconds"},
    "convert": {"toint32", "toint64", "todouble", "todecimal", "tostring", "toboolean", "tobase64string",
                "frombase64string"},
    "char": {"isdigit", "isletter", "isletterordigit", "iswhitespace", "isupper", "islower", "toupper", "tolower"},
    **{name: {"parse"} for name in ("int", "int32", "long", "int64", "double", "decimal")},
}
# Methods that only compute, whatever object they are called on (not Replace:
# a file's Replace swaps files; not ForEach: given a name, it calls that method).
PURE_METHODS = {"trim", "trimstart", "trimend", "tolower", "toupper", "tolowerinvariant", "toupperinvariant",
                "split", "substring", "contains", "containskey", "containsvalue", "startswith", "endswith",
                "indexof", "lastindexof", "padleft", "padright", "tostring", "equals", "compareto",
                "getenumerator", "normalize", "adddays", "addhours", "addminutes", "addseconds", "addmonths",
                "addyears"}


def _type(name: str) -> str:
    name = name.lower().removesuffix("[]")
    return name.removeprefix("system.")


def _pure_script(info: dict) -> bool:
    """Everything around the commands only computes (PROVABLE_NODES and the
    rules above); the commands themselves are proven one by one."""
    if not set(info["node_types"]) <= PROVABLE_NODES or info["background"]:
        return False
    if not all(redirect in ("discard", "merge") for redirect in info["redirects"]):
        return False  # into a file: not proven (Set-Content and Out-File are)
    if any(kind not in ("variable", "index") for kind in info["assignments"] + info["increments"]):
        return False
    if any(_type(name) not in PURE_TYPES for name in info["types"]) \
            or any(kind != "TypeExpressionAst" for kind in info["conversions"]):
        return False
    for member in info["members"]:
        name = (member["name"] or "").lower()
        if not name or name in HIDDEN_MEMBERS:
            return False
        if member["static"]:
            owner = _type(member["type"] or "")
            if member["invoke"] and (owner not in STATIC_METHODS
                                     or STATIC_METHODS[owner] is not None and name not in STATIC_METHODS[owner]):
                return False
        elif member["invoke"] and name not in PURE_METHODS:
            return False
    if any(str(text).lower() in HIDDEN_MEMBERS for text in info["strings"]):
        return False
    ready: dict[str, int] = {}
    for variable in sorted(info["variables"], key=lambda v: v["at"]):
        name = str(variable["name"]).lower()
        if not variable["plain"] or variable["splatted"]:
            return False
        if variable["assigned"] is not None:
            if variable["builtin"] and name != "null":
                return False
            if variable["op"] != "Equals" and name not in ready:
                return False  # += reads what was there first
            ready.setdefault(name, variable["assigned"])
        elif name not in AUTOMATIC_READS and not (name in ready and ready[name] <= variable["at"]):
            return False
    return True


def _hidden_view(item: dict) -> bool:
    """A property list that names, or could match, a hidden member, or that
    is computed when it runs."""
    for param in item["bound"]:
        if param["name"] in VIEW_PARAMS and (param.get("dynamic") or any(
                fnmatch.fnmatchcase(hidden, str(text).lower()) for text in param.get("strings") or []
                for hidden in HIDDEN_MEMBERS)):
            return True
    return False


def _system_read(item: dict, bound: dict) -> bool:
    """A read of the machine from SYSTEM_READS, from its own module; a CIM
    read names one of CIM_CLASSES (in its own namespace) and this computer."""
    if SYSTEM_READS.get(item["resolved"]) != item["module"]:
        return False
    if item["resolved"] != "Get-CimInstance":
        return True
    name, namespace = bound.get("ClassName"), bound.get("Namespace", "root/cimv2")
    return (isinstance(name, str) and name.lower() in CIM_CLASSES
            and isinstance(namespace, str) and namespace.lower() in CIM_NAMESPACES)


def _inside(value: object, root: Path, wildcards: bool = False) -> Path | None:
    """A literal filesystem path inside the workspace, or None. Only literal
    paths prove where a command lands; a wildcard could match a link leading
    out of the workspace."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    # Read the way Windows does, whatever runs the check: no UNC or device paths
    # (\\server, \\?\), a colon only in "X:\" (not "X:name", which means that
    # drive's own current folder, nor a stream or a provider), and no name ending
    # in a dot or a space (Windows drops it and writes another file).
    if value.startswith(("\\\\", "//")) \
            or (":" in value and not (len(value) > 2 and value[1] == ":" and value[0].isalpha()
                                      and value[2] in "\\/" and ":" not in value[2:])) \
            or any(part not in (".", "..") and part[-1:] in (".", " ") for part in value.replace("\\", "/").split("/")) \
            or (not wildcards and any(char in value for char in "*?[")):
        return None
    target = Path(value).expanduser()
    target = (target if target.is_absolute() else root / target).resolve()
    # The workspace itself is a place to read; as a write target it is a folder, which undo never covers.
    if not target.is_relative_to(root) or _repository_internals(target.relative_to(root)):
        return None
    return target


def _repository_internals(path: Path) -> bool:
    """Under .git: its config and hooks decide what later git commands run,
    so nothing there is changed (or proven) without the owner."""
    return any(part.lower() == ".git" for part in path.parts)


# git's reads (owner, 2026-10-08: reading uncommitted changes asked or was
# refused). Global options (-c, -C, --git-dir, --exec-path, --paginate) come
# before the subcommand and are refused with it; long options are refused when
# they abbreviate one that writes a file or runs a program.
GIT_READS = {"status", "diff", "log", "show", "blame", "ls-files"}
GIT_BRANCH_LIST = {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all", "--remotes"}
GIT_REFUSED = ("output", "ext-diff", "textconv", "no-index", "open-files-in-pager", "contents")


def _git_read(item: dict, root: Path) -> bool:
    """git reading the workspace's own repository: a listed subcommand first,
    then options and revisions that write nothing and run nothing. The
    repository is the workspace's (.git right there, so nothing above it is
    read), and its config can only have changed with the owner's approval
    (_repository_internals)."""
    if item["type"] != "Application" or Path(str(item["resolved"])).stem.lower() != "git" \
            or item["invocation"] != "Unknown" or not (root / ".git").exists():
        return False
    source = Path(str(item["source"] or ""))
    if not source.is_absolute() or source.resolve().is_relative_to(root):
        return False  # a git.exe in the workspace is the workspace's program, not git
    args = [str(arg) for arg in item.get("args") or []]
    while args[:1] == ["--no-pager"] or args[:1] == ["-C"] and len(args) > 1 and _inside(args[1], root) == root:
        args = args[1:] if args[0] == "--no-pager" else args[2:]  # -C the workspace itself: the same as no -C
    if not args:
        return False
    command, rest = args[0], args[1:]
    if command == "branch":
        return set(rest) <= GIT_BRANCH_LIST
    if command not in GIT_READS:
        return False
    for arg in rest:
        if arg.startswith("--") and len(arg) > 2:
            name = arg[2:].split("=", 1)[0]
            if any(refused.startswith(name) for refused in GIT_REFUSED):
                return False
        if _repository_internals(Path(arg.replace("\\", "/"))):
            return False
    return True


def _prove(command: str, directory: Path) -> tuple[list[Path], bool] | None:
    """What a command writes and whether it changes anything, when it is proven to touch nothing but the
    workspace: native cmdlets with literal arguments that read inside the
    workspace or write files it names there (returned, to copy first), git's
    reads of the workspace's own repository (_git_read), and reads of the
    machine itself (_system_read), in a script that only computes around them
    (_pure_script). None for anything else: other programs, dynamic code,
    method calls but pure ones, folder changes, deleting, changing the system,
    the network, a target that is not a literal path, a path passed along a
    pipeline, anything under .git, a command line."""
    try:
        info = powershell.analyze(command, cwd=str(directory), timeout=20)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if info["errors"] or info["other_blocks"] or not _pure_script(info):
        return None
    root = directory.resolve()
    writes: list[Path] = []
    changes = False
    by_offset = {item["at"]: item for item in info["all_commands"]}
    for item in info["all_commands"]:
        git = _git_read(item, root)
        entry = powershell.COMMANDS.get(item["resolved"])
        bound = {param["name"]: param["literal"] for param in item["bound"]}
        system = not git and entry is not None and entry.effect == "read" and _system_read(item, bound)
        kind = item["target_type"] or item["type"]
        if not git and (entry is None or item["binding_errors"] or item["invocation"] != "Unknown"
                        or item["resolved"] in LOCATION
                        or not (system and kind in ("Cmdlet", "Function")
                                or kind == "Cmdlet" and item["module"] in CORE_MODULES)):
            return None
        if not git and (set(bound) - set(entry.params) - QUIET_PARAMS or _hidden_view(item)):
            return None
        if not _piped_paths_proven(item, by_offset):
            return None
        places = {name: [_inside(value, root, wildcards=name == "LiteralPath")
                         for value in (bound[name] if isinstance(bound[name], list) else [bound[name]])]
                  for name in PATH_PARAMS & set(bound)}
        if any(None in paths for paths in places.values()):
            return None
        if git or entry.effect == "read":
            continue
        if item["resolved"] not in WRITES or any(len(paths) != 1 for paths in places.values()):
            return None  # a write names its one target
        landing = _landing(item["resolved"], {name: paths[0] for name, paths in places.items()}, bound)
        if landing is None or any(path == root or path.is_dir() for path in landing):
            return None  # a folder as a write target: nothing a file copy can put back
        writes.extend(landing)
        changes = True
    return writes, changes


def _piped_paths_proven(item: dict, by_offset: dict[int, dict]) -> bool:
    """Whatever the previous command passes along cannot land on a path
    parameter ("C:\\Users" | Get-ChildItem), or it is a path the proof has
    seen (PASSES_NAMED, DIGESTS). A command that names its own path leaves none
    open: the alternatives exclude each other."""
    if not item["position"] or {"Path", "LiteralPath", "FilePath"} & {param["name"] for param in item["bound"]}:
        return True
    if item["piped"] is None or item["piped_value"] is None:
        return False
    paths = PATH_PARAMS | {"PSPath"}
    if not set(item["piped"]) & paths:
        return True
    previous = by_offset.get(item["previous"]) or {}
    passes = PASSES_NAMED.get(previous.get("resolved"))
    if passes == "object" or passes == "property" and not set(item["piped_value"]) & paths:
        return True
    return previous.get("resolved") == "Get-ChildItem" and item["resolved"] in DIGESTS


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
        if str(bound.get("ItemType", "File")).lower() not in ("file", "directory"):
            return None  # links and junctions point at what no copy can put back
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


def reversible_edit(path: str, directory: Path) -> list[Path] | None:
    """The file to copy before an edit or write lands on `path` (as Pi
    resolves it); None when the owner decides: outside the workspace, a path
    Windows reads otherwise, or under .git."""
    target = _inside(path, directory.resolve(), wildcards=True)
    return None if target is None else [target]


def reversible_shell(command: str, directory: Path) -> list[Path] | None:
    """The files to copy before `command` runs, when it is proven to read and
    write only files it names inside the workspace (_prove); None when the
    owner decides. Programs (scripts, tests, git's writes) and the network
    always ask: what they do cannot be put back."""
    proof = _prove(command, directory)
    return None if proof is None else proof[0]
