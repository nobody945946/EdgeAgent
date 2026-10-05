import sys
from pathlib import Path
w = Path(sys.argv[1])
(w / "timer.py").write_text("""def parse(text):\n    minutes, seconds = text.split(":")\n    return int(minutes) * 60 + int(seconds)\n""")
(w / "README.md").write_text("# timer\n")
