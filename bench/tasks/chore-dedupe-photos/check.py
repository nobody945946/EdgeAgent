import sys
from pathlib import Path
keep = {"海邊.jpg", "生日.jpg", "山.jpg", "貓咪.jpg", "狗.jpg", "夜景.jpg", "相似但不同.jpg"}
moved = {"海邊 (1).jpg", "海邊 - 複製.jpg", "IMG_9.jpg", "dog_copy.jpg"}
root = Path("照片")
top = {p.name for p in root.iterdir() if p.is_file()}
dup = {p.name for p in (root / "重複").iterdir()} if (root / "重複").is_dir() else set()
errors = [f"should stay: {n}" for n in keep - top] + [f"should be moved: {n}" for n in moved - dup]
errors += [f"unexpected at top: {n}" for n in top - keep]
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
