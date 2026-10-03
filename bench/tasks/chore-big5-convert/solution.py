import sys
from pathlib import Path

for p in (Path(sys.argv[1]) / "舊資料").glob("*.txt"):
    raw = p.read_bytes()
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        p.write_bytes(raw.decode("big5").encode("utf-8"))
