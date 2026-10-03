import json, re, sys
from pathlib import Path
root = Path(sys.argv[1])
out = {}
for p in sorted((root / "logs").glob("*.log")):
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\S+) \S+ \[(\w+)\] (\w+):", line)
        if m and m.group(2) == "ERROR":
            day = out.setdefault(m.group(1), {})
            day[m.group(3)] = day.get(m.group(3), 0) + 1
(root / "錯誤統計.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
