"""edge_agent.toolbox: the interface every tool shares (registry, CLI, parameters, output, search)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_agent.toolbox import REGISTRY, ToolError, check_params, clip, resolve_inside
from tests.toolbox_helpers import cli, write

# ---------------------------------------------------------------- interface


def test_describe_is_json_with_described_parameters() -> None:
    code, out = cli("describe")
    assert code == 0
    tools = json.loads(out)
    assert {t["name"] for t in tools} == set(REGISTRY) and len(tools) == 20
    for t in tools:
        assert set(t) == {"name", "summary", "keywords", "parameters", "kind", "destructive", "windows_only"}
        assert t["kind"] in ("read", "change", "run") and t["summary"] and t["keywords"]
        assert t["parameters"]["type"] == "object"
        for name, spec in t["parameters"]["properties"].items():
            assert spec.get("description"), (t["name"], name)
        assert set(t["parameters"].get("required", [])) <= set(t["parameters"]["properties"])
        if t["kind"] == "read":
            assert not t["destructive"] and REGISTRY[t["name"]].preview is None
        if t["kind"] == "change":
            assert REGISTRY[t["name"]].preview is not None
        assert any("\u4e00" <= ch <= "\u9fff" for ch in t["keywords"]), t["name"]  # Chinese search words too


def test_cli_exit_codes(tmp_path: Path) -> None:
    code, out = cli("run", "calculate", stdin='{"expression": "1/4 + 1/4"}')
    assert code == 0 and "結果：0.5（精確值）" in out
    write(tmp_path / "a.txt")
    code, out = cli("run", "find_large_files", stdin='{"folder": "."}', cwd=tmp_path)
    assert code == 0 and "a.txt" in out
    code, out = cli("run", "find_large_files", stdin='{"folder": ".."}', cwd=tmp_path)
    assert code == 2 and "工作區外面" in out
    code, out = cli("run", "no_such_tool", stdin="{}")
    assert code == 2 and "沒有叫做" in out
    code, out = cli("run", "calculate", stdin="{not json")
    assert code == 2 and "JSON" in out
    code, out = cli("preview", "calculate", stdin='{"expression": "1"}')
    assert code == 2 and "不需要預覽" in out
    code, out = cli("preview", "organize_folder", stdin='\ufeff{"folder": "."}', cwd=tmp_path)
    assert code == 0 and "預計" in out


def test_resolve_inside_refuses_outside_and_links_out(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    write(root / "in.txt")
    write(tmp_path / "secret.txt")
    assert resolve_inside(root, "in.txt") == (root / "in.txt").resolve()
    assert resolve_inside(root, str(root / "in.txt")) == (root / "in.txt").resolve()
    for bad in ("../secret.txt", str(tmp_path / "secret.txt"), "sub/../../secret.txt"):
        with pytest.raises(ToolError, match="工作區外面"):
            resolve_inside(root, bad, must_exist=False)
    try:
        (root / "link.txt").symlink_to(tmp_path / "secret.txt")
        (root / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    for bad in ("link.txt", "linkdir/secret.txt"):
        with pytest.raises(ToolError, match="工作區外面"):
            resolve_inside(root, bad)
    with pytest.raises(ToolError, match="找不到"):
        resolve_inside(root, "missing.txt")


def test_parameters_are_checked_and_coerced() -> None:
    schema = REGISTRY["find_large_files"].parameters
    assert check_params(schema, {"folder": "x", "top": "5", "min_size_mb": "1.5"}) == \
        {"folder": "x", "top": 5, "min_size_mb": 1.5}
    with pytest.raises(ToolError, match="不認得的參數：recursive"):
        check_params(schema, {"folder": "x", "recursive": True})
    with pytest.raises(ToolError, match="缺少參數 folder"):
        check_params(schema, {})
    with pytest.raises(ToolError, match="之間"):
        check_params(schema, {"folder": "x", "top": 0})
    organize = REGISTRY["organize_folder"].parameters
    assert check_params(organize, {"folder": "x", "by": "Month", "include_subfolders": "true"})["by"] == "month"
    with pytest.raises(ToolError, match="只能是"):
        check_params(organize, {"folder": "x", "by": "size"})
    assert check_params(REGISTRY["zip_files"].parameters, {"paths": "a.txt", "output": "o"})["paths"] == ["a.txt"]


def test_output_is_capped() -> None:
    long = "\n".join(f"第 {i} 行" for i in range(5000))
    out = clip(long)
    assert len(out) < 6100 and "後面省略了" in out


def test_catalog_search_reads_chinese_and_english():
    """edge_agent/catalog.mjs: Chinese requests are indexed as character pairs."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    from edge_agent.toolbox import specs

    script = (
        "import { rank, searchText } from './edge_agent/catalog.mjs';"
        "const tools = JSON.parse(process.argv[1]).map(t => ({ name: t.name, text: searchText({ ...t, description: t.summary }) }));"
        "console.log(JSON.stringify(['把下載資料夾整理一下', '照片 改名 日期', 'convert Big5 text to UTF-8', '跑測試']"
        ".map(q => rank(q, tools, 3).map(m => m.name))));"
    )
    out = subprocess.run([node, "--input-type=module", "-e", script, json.dumps(specs())],
                         capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], check=True)
    organize, photos, encoding, checks = json.loads(out.stdout)
    assert organize[0] == "organize_folder"
    assert photos[0] == "rename_photos_by_date"
    assert encoding[0] == "convert_text_encoding"
    assert "run_checks" in checks


def test_a_servers_tools_are_listed_together():
    """Chinese words that only name a server tie its English-described tools;
    once one is found, the rest of that small server come along (catalog.mjs)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        "import { rank, searchText, withServers } from './edge_agent/catalog.mjs';"
        "const ns = { name: 'mcp__drone', description: '無人機：狀態、位置、起飛、降落' };"
        "const tools = ['arm', 'land', 'return_to_launch', 'get_position', 'takeoff', 'get_status']"
        ".map(n => ({ name: 'mcp__drone__' + n, description: n === 'get_status' ? 'Vehicle status: armed, flight mode, "
        "battery voltage and percentage, GPS fix and satellites' : n, namespace: ns }));"
        "const docs = tools.map(t => ({ name: t.name, text: searchText(t) }));"
        "const found = rank('無人機電池狀態', docs, 5);"
        "console.log(JSON.stringify([found.map(m => m.name), withServers(found, tools.map(t => ({ name: t.name, server: t.namespace.name })))]));"
    )
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[1], check=True)
    ranked, listed = json.loads(out.stdout)
    assert "mcp__drone__get_status" not in ranked  # the tie the cut would lose
    assert "mcp__drone__get_status" in listed and len(listed) == 6
