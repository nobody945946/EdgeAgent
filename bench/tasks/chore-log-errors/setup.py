import random, sys
from pathlib import Path
root = Path(sys.argv[1]) / "logs"
root.mkdir(parents=True)
rng = random.Random(5)
mods = ["auth", "db", "api", "mail"]
for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
    lines = []
    for i in range(400):
        level = rng.choices(["INFO", "WARN", "ERROR"], [80, 15, 5])[0]
        lines.append(f"{day} {i // 20:02d}:{i % 60:02d}:00 [{level}] {rng.choice(mods)}: event {i}")
    lines.insert(50, f"{day} 02:00:00 [INFO] api: message mentions [ERROR] but is info")
    (root / f"app-{day}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
