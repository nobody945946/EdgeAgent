import sys
from pathlib import Path

root = Path(sys.argv[1]) / "舊資料"
root.mkdir(parents=True)
texts = {"會議紀錄.txt": "九月三日會議：決定下週上線。\n出席：王經理、陳小姐。\n",
         "通訊錄.txt": "林先生 0912-345-678\n黃太太 02-2345-6789\n",
         "備忘.txt": "記得繳水電費、續約租屋。\n",
         "readme.txt": "plain ascii notes\n"}
for name, text in texts.items():
    (root / name).write_bytes(text.encode("big5"))
(root / "新的筆記.txt").write_bytes("這個檔案本來就是 UTF-8：測試、轉檔。\n".encode("utf-8"))
