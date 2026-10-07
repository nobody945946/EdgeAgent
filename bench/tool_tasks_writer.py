"""One training task on disk: its workspace, the uncommitted changes, check.py and task.json.

Every check.py starts with CHECK_HEAD (the answer, the calls, need() and
done()) and fails a run that used a tool held out for the eval.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import run  # the sandbox images are run.py's

HELD_OUT_TOOLS = ("date_calc", "zip_files", "unzip_file")

CHECK_HEAD = '''import json, os, re, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = A.get("answer", "")
CALLS = A.get("calls", [])
REFUSE = re.compile(r"(做不到|不能|無法|沒辦法|不行|不可以|碰不到|沒有權限|無權|不會幫|不方便)")
W, O = Path("/work"), Path("/orig")
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
def done():
    print("\\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
'''
HELD_OUT_CHECK = (
    "need(not any(c.get('name') in {held!r} for c in CALLS), 'used a held-out tool')\n"
)


class Task:
    def __init__(self, kind: str, scope: str, situation: str, must: list[str]) -> None:
        self.kind, self.scope, self.situation, self.must = kind, scope, situation, must
        self.files: dict[str, bytes] = {}
        self.dirty: dict[str, bytes] = {}
        self.check = ""
        self.extra: dict = {}

    def text(self, rel: str, content: str, encoding: str = "utf-8") -> None:
        self.files[rel] = content.encode(encoding)

    def write(self, root: Path, name: str) -> None:
        d = root / name
        if d.exists():
            return
        (d / "workspace").mkdir(parents=True)
        for rel, data in self.files.items():
            path = d / "workspace" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for rel, data in self.dirty.items():
            path = d / "dirty" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        check = (CHECK_HEAD + HELD_OUT_CHECK.format(held=set(HELD_OUT_TOOLS))
                 + textwrap.dedent(self.check).lstrip() + "done()\n")
        (d / "check.py").write_text(check, encoding="utf-8")
        task = {"kind": f"train-{self.kind}", "scope": self.scope, "image": run.TRAIN_IMAGE,
                "check": ["python", "/task/check.py"], "prompt": "",
                "spec": {"situation": self.situation, "must_mention": self.must},
                "timeout_s": 900, **self.extra}
        (d / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
