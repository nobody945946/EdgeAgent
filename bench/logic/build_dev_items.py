"""Writes bench/logic/items-dev.jsonl: everyday read-only development questions on
the real SilentTalk code (eval only). Failure items break one line in a copy
and record the real pytest output."""
import json
import subprocess
import tempfile
from pathlib import Path

REPO, COMMIT = Path("/home/clawd/SilentTalk"), "c3a44e9"
INCLUDE = ["silenttalk", "scripts/dev", "tests"]
WS = "C:\\Users\\me\\SilentTalk"
repo = {"path": str(REPO), "commit": COMMIT, "include": INCLUDE}
items = []


def locate(id, prompt, file, function, also=()):
    items.append({"id": id, "kind": "dev", "task": "locate", "workspace": WS, "files": {}, "repo": repo,
                  "prompt": prompt, "answer": {"file": file, "function": function, "also": list(also)}})


locate("d-locate-owner-typed", "這個專案裡，判斷「只有主人在聊天室打字的話才能派工作給 Agent」的程式在哪個檔案、哪個函式？",
       "silenttalk/copilot/llm.py", "owner_messages", ["task_from_owner"])
locate("d-locate-approval-dm", "Agent 的批准請求會私訊給主人。送出這個私訊的是哪個檔案的哪個函式？",
       "silenttalk/discord_voice.py", "send_approval")
locate("d-locate-records-write", "對話紀錄寫進外接硬碟的程式在哪個檔案、哪個函式？什麼情況下它不會寫？",
       "silenttalk/records.py", "write", ["ready", ".agent-records"])
locate("d-locate-provenance", "訓練資料檔有版本戳記。拒絕把不同規則版本的資料檔混在一起用的，是哪個檔案的哪個函式？",
       "scripts/dev/provenance.py", "require_same", ["claim"])
locate("d-locate-approval-kind", "Agent 的批准請求會被分成幾種類型（例如受保護檔案、會刪除東西）。做這個分類的函式在哪？",
       "silenttalk/copilot/runtime.py", "_approval_kind")
locate("d-locate-voice-effect", "機魂（machine_spirit）的電子音效果定義在哪個檔案？",
       "silenttalk/voice_styles.py", "VOICE_EFFECTS")
locate("d-locate-records-page", "設定頁裡「紀錄」那一頁有哪些欄位，是在哪個檔案定義的？",
       "silenttalk/settings_schema.py", "records")
locate("d-locate-dm-split", "私訊的內容超過 Discord 的字數上限時，程式怎麼處理？寫在哪個函式？",
       "silenttalk/discord_voice.py", "send_private", ["MAX_TEXT_CHARS"])

MUTATIONS = [
    ("d-fail-provenance", "scripts/dev/provenance.py",
     '        elif found.get("version") != version:', '        elif found.get("version") == version:',
     "tests/test_provenance.py", "require_same 的版本比較寫反了（!= 變成 ==），版本相同的反而被拒絕、不同的被放過"),
    ("d-fail-records-ready", "silenttalk/records.py",
     "        if not self.ready():\n            self._note(\"paused\")\n            return False\n", "",
     "tests/test_records.py", "write 少了 ready() 的檢查，沒有標記檔或硬碟沒接上時也會寫"),
    ("d-fail-owner-typed", "silenttalk/copilot/llm.py",
     'if t.speaker_id == self._owner_id and not t.is_self and t.via == "text"',
     'if t.speaker_id == self._owner_id and not t.is_self',
     "tests/test_copilot_llm.py", "owner_messages 少了 via == \"text\" 的條件，主人用說的話也會被當成派工的指令"),
]


def git_files(commit):
    names = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "--name-only", commit, *INCLUDE],
                           capture_output=True, text=True, check=True).stdout.split()
    return names


for id, path, old, new, test, cause in MUTATIONS:
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(["git", "-C", str(REPO), "archive", COMMIT], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tmp], input=archive, check=True)
        target = Path(tmp) / path
        text = target.read_text(encoding="utf-8")
        assert text.count(old) == 1, (id, old)
        target.write_text(text.replace(old, new), encoding="utf-8")
        done = subprocess.run([str(REPO / ".venv/bin/python"), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test],
                              cwd=tmp, capture_output=True, text=True, timeout=600,
                              env={**__import__("os").environ, "PYTHONPATH": tmp})
        assert done.returncode != 0, (id, done.stdout[-500:])
        output = done.stdout[-3500:]
    items.append({"id": id, "kind": "dev", "task": "failure", "workspace": WS, "files": {},
                  "repo": {**repo, "mutate": {"file": path, "old": old, "new": new}},
                  "prompt": f"我跑 `pytest {test}` 失敗了，輸出如下。請找出是哪個檔案的哪一行造成的、為什麼，以及怎麼修。\n\n```\n{output}\n```",
                  "answer": {"file": path, "cause": cause, "fix": f"改回：{old.strip()}"}})

COMMITS = ["3b0795c", "e31b8bf", "cbefbea", "eae7032"]
for commit in COMMITS:
    diff = subprocess.run(["git", "-C", str(REPO), "show", "--format=", "--stat", "--patch", commit, "--", "silenttalk", "scripts", "tests"],
                          capture_output=True, text=True, check=True).stdout
    message = subprocess.run(["git", "-C", str(REPO), "show", "-s", "--format=%B", commit], capture_output=True, text=True, check=True).stdout
    message = "\n".join(l for l in message.splitlines() if not l.startswith("Co-Authored-By"))
    if len(diff) > 9000:
        diff = diff[:9000] + "\n…（後面省略）"
    items.append({"id": f"d-commit-{commit}", "kind": "dev", "task": "commit", "workspace": WS, "files": {}, "repo": repo,
                  "prompt": f"幫我替下面這份改動寫一個 commit 訊息（第一行簡短摘要，下面幾行說明為什麼這樣改、改了什麼），用英文寫。\n\n```diff\n{diff}\n```",
                  "answer": {"message": message.strip()}})

Path("/home/clawd/EdgeAgent/bench/logic/items-dev.jsonl").write_text(
    "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
print(len(items), [i["task"] for i in items].count("failure"), "failure items")
