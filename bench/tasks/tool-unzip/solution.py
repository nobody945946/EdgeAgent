import sys, zipfile
from pathlib import Path
w = Path(sys.argv[1])
zipfile.ZipFile(w / "素材.zip").extractall(w / "素材")
