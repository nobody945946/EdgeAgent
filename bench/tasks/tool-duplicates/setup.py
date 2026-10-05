import sys
from pathlib import Path
w = Path(sys.argv[1]) / "照片"
(w / "手機").mkdir(parents=True)
a, b, c = bytes(range(256)) * 300, bytes(range(255, -1, -1)) * 300, b"\xff\xd8" + bytes(70000)
(w / "海邊.jpg").write_bytes(a)
(w / "手機" / "IMG_0412.jpg").write_bytes(a)
(w / "生日.jpg").write_bytes(b)
(w / "生日 (1).jpg").write_bytes(b)
(w / "貓.jpg").write_bytes(c)
(w / "貓_修圖.jpg").write_bytes(c[:-1] + b"\x01")
