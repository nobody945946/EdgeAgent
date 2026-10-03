import shutil
import sys
from datetime import datetime
from pathlib import Path

# The reference backup made directly; the grader reruns 備份.ps1 with pwsh.
root = Path(sys.argv[1])
target = root / "備份" / datetime.now().strftime("%Y%m%d")
shutil.copytree(root / "文件", target,
                ignore=lambda d, names: [n for n in names if n.endswith(".tmp") or n.startswith("~$")])
