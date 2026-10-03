import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1]) / "照片"
groups = {}
for p in sorted(root.iterdir()):
    groups.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(p)
(root / "重複").mkdir()
for files in groups.values():
    files.sort(key=lambda p: (len(p.name), p.name))
    for p in files[1:]:
        p.rename(root / "重複" / p.name)
