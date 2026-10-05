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
z = Path("/work/報告.zip")
need(z.is_file(), "no 報告.zip")
if z.is_file():
    with zipfile.ZipFile(z) as f:
        names = {Path(n).name: f.read(n) for n in f.namelist() if not n.endswith("/")}
    for p in Path("/orig/報告").iterdir():
        need(names.get(p.name) == p.read_bytes(), "zip lacks " + p.name)
done()
