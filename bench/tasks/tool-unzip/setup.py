import sys, zipfile
from pathlib import Path
w = Path(sys.argv[1])
with zipfile.ZipFile(w / "素材.zip", "w") as f:
    f.writestr("logo.svg", "<svg></svg>")
    f.writestr("字型/說明.txt", "授權：可商用\n")
    f.writestr("背景.jpg", bytes(3000))
