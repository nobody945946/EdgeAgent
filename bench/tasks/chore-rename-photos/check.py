import hashlib
import os
import sys
from pathlib import Path

orig, final = Path(os.environ["BENCH_ORIG"]) / "相機", Path("相機")
want = {"IMG_0001.JPG": "2026-09-03_1422.jpg", "IMG_0002.JPG": "2026-09-03_1422_2.jpg",
        "IMG_0003.JPG": "2026-09-03_1805.jpg", "IMG_0004.JPG": "2026-09-04_0730.jpg",
        "IMG_0005.JPG": "2026-09-04_0731.jpg", "IMG_0006.JPG": "2026-10-01_2359.jpg",
        "IMG_0007.JPG": "2026-10-01_2359_2.jpg", "IMG_0008.JPG": "2026-10-01_2359_3.jpg"}
errors = []
for old, new in want.items():
    target = final / new
    if not target.is_file():
        errors.append(f"missing {new}")
    elif hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256((orig / old).read_bytes()).digest():
        errors.append(f"{new} is not {old}")
leftovers = [p.name for p in final.glob("IMG_*")]
errors += [f"not renamed: {n}" for n in leftovers]
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
