import shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
for p in [root / "src/__pycache__", root / ".pytest_cache", root / "build"]:
    shutil.rmtree(p)
(root / "debug.tmp").unlink()
