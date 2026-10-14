import shutil, sys
from datetime import datetime, timezone
from pathlib import Path
w = Path(sys.argv[1]); (w / "備份").mkdir(exist_ok=True)
shutil.copyfile(w / "報告.docx", w / "備份" / f"報告_bak{datetime.now(timezone.utc):%Y%m%d}.docx")
