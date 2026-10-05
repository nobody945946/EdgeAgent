import sys
from pathlib import Path
w = Path(sys.argv[1]) / "下載"
w.mkdir()
for name in ("chrome_setup.exe", "LINE安裝.exe", "驅動程式_v5.exe"):
    (w / name).write_bytes(b"MZ" + name.encode() + bytes(2000))
(w / "說明書.pdf").write_bytes(b"%PDF-1.4" + bytes(500))
(w / "風景.jpg").write_bytes(b"\xff\xd8" + bytes(800))
