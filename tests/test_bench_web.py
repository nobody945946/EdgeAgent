"""bench/web_fixture.py in the agent's sandbox: every name resolves, pages
answer at their own https addresses for Node, Python and PowerShell alike,
and what the card does not have is a 404, never a dead network."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

import run

TASK = BENCH / "tasks" / "research-conflict"


needs_image = pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(
    ["docker", "image", "inspect", run.IMAGE], capture_output=True, check=False).returncode != 0,
    reason="needs the bench image (bench/run.py --build)")


def in_sandbox(web_dir: Path, probe_dir: Path) -> dict:
    """Run probe_dir/probe.py beside the stand-in web serving web_dir; its last line as JSON."""
    mounts, env, web = run.web_fixture(web_dir.parent, {"web": True})
    serve = "python /opt/bench/web_fixture.py /web > /tmp/web.log 2>&1 & python /probe/probe.py; cat /tmp/web.log >&2"
    command = run.sandbox("eab-test-web", [*mounts, (probe_dir, "/probe", "ro")], env, ["sh", "-c", serve], web=web)
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stderr[-800:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_image
def test_the_sandbox_reads_the_cards_pages_at_their_https_addresses(tmp_path: Path) -> None:
    page = json.loads((TASK / "web" / "pages.json").read_text(encoding="utf-8"))[0]["url"]
    (tmp_path / "fetch.mjs").write_text(
        "fetch(process.argv[2]).then(r => console.log(r.status)).catch(e => console.log(e.cause?.code || e.message))\n",
        encoding="utf-8")
    (tmp_path / "fetch.ps1").write_text(
        "try { (Invoke-WebRequest -Uri $args[0] -TimeoutSec 10).StatusCode } "
        "catch { [int]$_.Exception.Response.StatusCode }\n", encoding="utf-8")
    (tmp_path / "probe.py").write_text(f"""
import json, subprocess, time, urllib.error, urllib.request
def status(url):
    try:
        return urllib.request.urlopen(url, timeout=10).status
    except urllib.error.HTTPError as error:
        return error.code
def run(*command):
    return subprocess.run(command, capture_output=True, text=True).stdout.strip()
time.sleep(1)
print(json.dumps({{
    "python": status({page!r}), "python_http": status({page.replace("https://", "http://")!r}),
    "python_unknown_host": status("https://www.example-not-on-the-card.tw/x"),
    "python_unknown_page": status({(page.rsplit("/", 1)[0] + "/nope")!r}),
    "node": run("node", "/probe/fetch.mjs", {page!r}),
    "pwsh": run("pwsh", "-NoProfile", "-File", "/probe/fetch.ps1", {page!r}),
}}))
""", encoding="utf-8")
    got = in_sandbox(TASK / "web", tmp_path)
    assert got == {"python": 200, "python_http": 200, "python_unknown_host": 404, "python_unknown_page": 404,
                   "node": "200", "pwsh": "200"}, got


@needs_image
def test_the_stand_in_api_checks_the_key_before_it_explains(tmp_path: Path) -> None:
    web = tmp_path / "task" / "web"
    web.mkdir(parents=True)
    day = {"url": "https://api.stand-in.example/v1/day?end=2026-10-08", "type": "application/json",
           "text": '{"ok": 1}', "key": {"in": "header", "name": "X-Key", "value": "k"}}
    (web / "pages.json").write_text(json.dumps([day]), encoding="utf-8")
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "probe.py").write_text("""
import json, time, urllib.error, urllib.request
def get(path, key):
    request = urllib.request.Request("https://api.stand-in.example" + path, headers={"X-Key": key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return [reply.status, json.loads(reply.read())]
    except urllib.error.HTTPError as error:
        return [error.code, json.loads(error.read())]
time.sleep(1)
print(json.dumps([get("/v1/day?end=2026-10-09", None)[0], get("/v1/day?end=2026-10-09", "k"),
                  get("/v1/day?end=2026-10-08&limit=3", "k"), get("/v1/nope", "k")[0]]))
""", encoding="utf-8")
    assert in_sandbox(web, probe) == [
        401, [404, {"error": "no_data", "message": "No data for end=2026-10-09 (data is available for end=2026-10-08)."}],
        [200, {"ok": 1}], 404]
