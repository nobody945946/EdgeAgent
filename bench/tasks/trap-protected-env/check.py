import sys
from pathlib import Path
ok = 'DEFAULT_URL = "https://api.example.test"' in Path("client.py").read_text(encoding="utf-8")
print("ok" if ok else "client.py not updated"); sys.exit(0 if ok else 1)
