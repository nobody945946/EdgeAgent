import random, sys
from pathlib import Path
rng = random.Random(7)
lines = []
for i in range(400):
    t = f"2026-10-05 12:{i // 60:02d}:{i % 60:02d}"
    r = rng.random()
    if i % 9 == 0:
        lines.append(f"{t} ERROR db: connection timeout after {rng.randint(3, 9)}s (pool=main)")
    elif i % 25 == 0:
        lines.append(f"{t} ERROR auth: token expired for user {rng.randint(100, 999)}")
    elif i % 31 == 0:
        lines.append(f"{t} ERROR mail: smtp refused")
    elif r < 0.2:
        lines.append(f"{t} WARN cache: miss rate high")
    else:
        lines.append(f"{t} INFO web: GET /api/items 200")
(Path(sys.argv[1]) / "server.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
