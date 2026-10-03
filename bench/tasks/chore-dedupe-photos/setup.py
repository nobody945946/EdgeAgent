import random, sys
from pathlib import Path
root = Path(sys.argv[1]) / "照片"
root.mkdir(parents=True)
rng = random.Random(3)
blobs = [bytes(rng.randrange(256) for _ in range(2000)) for _ in range(6)]
layout = {"海邊.jpg": 0, "海邊 (1).jpg": 0, "海邊 - 複製.jpg": 0, "生日.jpg": 1, "IMG_9.jpg": 1,
          "山.jpg": 2, "貓咪.jpg": 3, "狗.jpg": 4, "dog_copy.jpg": 4, "夜景.jpg": 5}
for name, i in layout.items():
    (root / name).write_bytes(blobs[i])
(root / "相似但不同.jpg").write_bytes(blobs[2][:-1] + b"X")
