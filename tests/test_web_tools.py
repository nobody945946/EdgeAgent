import shutil
import subprocess

import pytest

from edge_agent.runtime import CORE_ROOT, NODE_VERSION


def test_web_tools_contract():
    """Private addresses refused, pages reduced to text, search results parsed."""
    pinned = CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
    node = str(pinned) if pinned.is_file() else shutil.which("node")
    if node is None:
        pytest.skip("requires Node")
    subprocess.run([node, str(CORE_ROOT / "tests/web_tools_check.mjs")], check=True)


def test_shell_guard_contract():
    """Deletes and user-owned configuration need an explicit approval."""
    pinned = CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
    node = str(pinned) if pinned.is_file() else shutil.which("node")
    if node is None:
        pytest.skip("requires Node")
    subprocess.run([node, str(CORE_ROOT / "tests/shell_guard_check.mjs")], check=True)
