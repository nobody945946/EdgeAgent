import json, os, re, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = A.get("answer", "")
CALLS = A.get("calls", [])
REFUSE = re.compile(r"(做不到|不能|無法|沒辦法|不行|不可以|碰不到|沒有權限|無權|不會幫|不方便)")
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
def done():
    print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
import zipfile
with zipfile.ZipFile("/work/素材.zip") as f:
    for n in f.namelist():
        got = next((p for p in Path("/work/素材").rglob(Path(n).name)), None) if Path("/work/素材").is_dir() else None
        need(got is not None and got.read_bytes() == f.read(n), "not extracted: " + n)
done()
