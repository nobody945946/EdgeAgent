import sys
from pathlib import Path

want = {"會議紀錄.txt": "九月三日會議：決定下週上線。\n出席：王經理、陳小姐。\n",
        "通訊錄.txt": "林先生 0912-345-678\n黃太太 02-2345-6789\n",
        "備忘.txt": "記得繳水電費、續約租屋。\n", "readme.txt": "plain ascii notes\n",
        "新的筆記.txt": "這個檔案本來就是 UTF-8：測試、轉檔。\n"}
errors = []
for name, text in want.items():
    try:
        got = (Path("舊資料") / name).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{name}: {exc}"); continue
    if got.rstrip("\n") != text.rstrip("\n"):
        errors.append(f"{name}: content changed")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
