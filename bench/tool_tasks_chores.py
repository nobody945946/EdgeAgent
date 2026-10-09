"""train-tool tasks: a chore or question one of the catalog tools answers.

Organize, rename photos, convert text, find big or duplicate files, archive,
merge tables, logs, backups, git, code search, calculation, system status,
and a small fix verified by run_checks.
"""

from __future__ import annotations

import json
import random
import struct
from datetime import datetime, timedelta

import laptop
from tool_tasks_writer import Task

FOLDERS = ("下載", "Downloads", "桌面雜物", "收件夾", "暫放", "整理區")
PEOPLE = ("小美", "阿傑", "Kevin", "佳玲", "老陳", "Amy", "志明", "雅婷")


def exif_jpeg(when: datetime | None, size: int, rng: random.Random) -> bytes:
    """A minimal JPEG: SOI, an APP1 Exif segment with DateTimeOriginal when
    given (little- or big-endian TIFF), filler, EOI."""
    body = b""
    if when is not None:
        stamp = when.strftime("%Y:%m:%d %H:%M:%S").encode() + b"\0"
        little = rng.random() < 0.5
        e = "<" if little else ">"
        # TIFF header, IFD0 with one entry (ExifIFD pointer), Exif IFD with DateTimeOriginal.
        ifd0 = 8
        exif_ifd = ifd0 + 2 + 12 + 4
        value = exif_ifd + 2 + 12 + 4
        tiff = (b"II" if little else b"MM") + struct.pack(e + "HI", 42, ifd0)
        tiff += struct.pack(e + "H", 1) + struct.pack(e + "HHII", 0x8769, 4, 1, exif_ifd) + struct.pack(e + "I", 0)
        tiff += struct.pack(e + "H", 1) + struct.pack(e + "HHII", 0x9003, 2, len(stamp), value) + struct.pack(e + "I", 0)
        tiff += stamp
        payload = b"Exif\0\0" + tiff
        body = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    filler = bytes(rng.getrandbits(8) for _ in range(64)) * max(1, size // 64)
    return b"\xff\xd8" + body + b"\xff\xdb" + struct.pack(">H", 2 + len(filler[:60000])) + filler[:60000] + b"\xff\xd9"


TYPE_POOLS = {
    "圖片": ("jpg", "png", "gif", "webp"), "影片": ("mp4", "mov", "mkv"), "音樂": ("mp3", "m4a", "flac"),
    "文件": ("docx", "txt", "odt"), "試算表": ("xlsx", "csv"), "PDF": ("pdf",),
    "壓縮檔": ("zip", "7z", "rar"), "安裝程式": ("exe", "msi"),
}
STEMS = ("報價單", "旅遊", "IMG_2231", "會議錄音", "成績", "說明書", "合約", "Setup", "期末報告", "家計簿",
         "screenshot", "演唱會", "履歷", "收據", "podcast", "壁紙", "課表", "驅動程式", "季報", "相簿")


# The folder names people ask for, and which of the pools each one takes.
NAMED_SETS = (
    {"圖片": ("圖片",), "影音": ("影片", "音樂"), "文件": ("文件", "試算表", "PDF"), "壓縮檔": ("壓縮檔",), "其他": ("安裝程式",)},
    {"照片": ("圖片",), "影片": ("影片",), "音樂": ("音樂",), "文書": ("文件", "試算表", "PDF"), "其它": ("壓縮檔", "安裝程式")},
    {"圖片": ("圖片",), "文件": ("文件", "試算表", "PDF"), "安裝檔": ("安裝程式",), "其他": ("影片", "音樂", "壓縮檔")},
)


def gen_organize(rng: random.Random) -> Task:
    folder = rng.choice(FOLDERS)
    named = rng.choice(NAMED_SETS) if rng.random() < 0.5 else None
    if named:
        t = Task("tool", "change", f"使用者的「{folder}」資料夾很亂，他要把檔案依種類分到這幾個子資料夾：{'、'.join(named)}"
                                   "（資料夾名稱照他說的），不能弄丟任何檔案。", [folder, *named])
        home = {pool: name for name, pools in named.items() for pool in pools}
    else:
        t = Task("tool", "change", f"使用者的「{folder}」資料夾很亂，檔案都混在一起（圖片、影片、文件、安裝檔、壓縮檔…）。"
                                   "他想讓這些檔案依種類分到不同的子資料夾，不能弄丟任何檔案。", [folder])
        home = None
    names = set()
    for _ in range(rng.randint(9, 16)):
        kind = rng.choice(list(TYPE_POOLS))
        name = f"{rng.choice(STEMS)}{rng.randint(1, 99)}.{rng.choice(TYPE_POOLS[kind])}"
        if name in names:
            continue
        names.add(name)
        t.files[f"{folder}/{name}"] = name.encode() + bytes(rng.randint(10, 400))
    cats = {ext: k for k, exts in TYPE_POOLS.items() for ext in exts}
    t.check = f'''
    import collections
    cats = {json.dumps(cats, ensure_ascii=False)}
    home = {home!r}
    where = collections.defaultdict(set)
    for p in (O / {folder!r}).iterdir():
        found = [q for q in W.rglob(p.name) if q.read_bytes() == p.read_bytes()]
        need(len(found) == 1, "lost or duplicated " + p.name)
        if found:
            need(found[0].parent != W / {folder!r}, "not sorted: " + p.name)
            kind = cats[p.suffix[1:].lower()]
            if home:
                need(found[0].parent == W / {folder!r} / home[kind], "not in the folder asked for: " + p.name)
            where[kind].add(found[0].parent)
    need(all(len(v) == 1 for v in where.values()), "one kind spread over folders")
    if not home:
        homes = [next(iter(v)) for v in where.values()]
        need(len(set(homes)) == len(homes), "different kinds share a folder")
    '''
    return t


def gen_rename_photos(rng: random.Random) -> Task:
    folder = rng.choice(("照片", "手機照片", "相機", "Photos", "旅行照片"))
    pattern = rng.choice((("2024-05-01_143000", r"\d{4}-\d{2}-\d{2}_\d{6}"),
                          ("20240501_143000", r"\d{8}_\d{6}")))
    t = Task("tool", "change", f"「{folder}」資料夾裡的照片檔名是 IMG_xxxx、DSC_xxxx 這種亂碼，使用者想把每張照片改成拍攝的日期時間，"
                               f"格式像「{pattern[0]}」，副檔名不變，照片內容不能動。", [folder, pattern[0]])
    start = datetime(2023, 1, 1) + timedelta(days=rng.randint(0, 600))
    expected = {}
    for i in range(rng.randint(4, 8)):
        when = start + timedelta(hours=rng.randint(0, 2000), seconds=rng.randint(0, 3599))
        name = f"{rng.choice(('IMG_', 'DSC_', 'PXL_'))}{rng.randint(1000, 9999)}{i}.jpg"
        t.files[f"{folder}/{name}"] = exif_jpeg(when, 2000, rng)
        expected[name] = when.strftime("%Y%m%d%H%M%S")
    t.check = f'''
    expected = {expected!r}
    for name, stamp in expected.items():
        data = (O / {folder!r} / name).read_bytes()
        found = [q for q in W.rglob("*.jpg") if q.read_bytes() == data]
        need(len(found) == 1, "lost " + name)
        if found:
            digits = re.sub(r"\\D", "", found[0].stem)
            need(re.fullmatch({pattern[1]!r}, found[0].stem.split(" ")[0]) and digits.startswith(stamp), "wrong name " + found[0].name)
    '''
    return t


def gen_convert(rng: random.Random) -> Task:
    folder = rng.choice(("舊文件", "以前的筆記", "old_txt", "備忘錄", "客戶資料"))
    t = Task("tool", "change", f"「{folder}」資料夾裡的文字檔有些是以前用 Big5 存的，打開變亂碼；使用者要全部轉成 UTF-8、直接覆蓋原檔、"
                               "內容一個字都不能變，本來就是 UTF-8 的不要再轉。", [folder])
    texts = ["今天開會決定延後出貨，請通知倉庫。", "王小姐電話 0933-112-889，週三回電。", "採購清單：衛生紙、洗衣精、咖啡豆。",
             "颱風假期間的值班表如下：週一阿傑、週二佳玲。", "專案代號：鳳梨酥，預算新台幣三十萬元。", "記得幫貓咪預約疫苗。"]
    want = {}
    for i, text in enumerate(rng.sample(texts, rng.randint(3, 5))):
        name = f"{rng.choice(('紀錄', '筆記', 'memo', '清單'))}{i + 1}.txt"
        encoding = "utf-8" if i == 0 else "big5"
        t.files[f"{folder}/{name}"] = (text + "\n").encode(encoding)
        want[name] = text
    t.check = f'''
    for name, text in {want!r}.items():
        try:
            got = (W / {folder!r} / name).read_bytes().decode("utf-8-sig").strip()
        except (OSError, UnicodeError):
            got = None
        need(got == text, "not converted exactly: " + name)
    '''
    t.extra["may_change"] = [f"{folder}/*"]
    return t


def gen_large(rng: random.Random) -> Task:
    folder = rng.choice(FOLDERS)
    n = rng.choice((3, 5))
    t = Task("tool", "read", f"使用者的硬碟快滿了，想知道「{folder}」資料夾（含子資料夾）裡最大的 {n} 個檔案是哪些、各多大，只要列出來，不要動檔案。",
             [folder, str(n)])
    sizes = {}
    for i in range(rng.randint(8, 12)):
        sub = rng.choice(("", "舊的/", "2023/"))
        name = f"{sub}{rng.choice(STEMS)}{i}.{rng.choice(('mp4', 'zip', 'iso', 'pdf', 'jpg', 'exe'))}"
        size = rng.randint(1, 60) * 97_000 + i
        t.files[f"{folder}/{name}"] = bytes(size)
        sizes[name.split("/")[-1]] = size
    top = sorted(sizes, key=sizes.get, reverse=True)[:n]
    t.check = f'''
    top = {top!r}
    spots = [ANSWER.find(name) for name in top]
    need(all(s >= 0 for s in spots), "missing one of " + ", ".join(top))
    need(spots == sorted(spots), "not largest first")
    '''
    t.extra["may_change"] = []
    return t


def gen_duplicates(rng: random.Random) -> Task:
    folder = rng.choice(("圖庫", "相片備份", "素材", "Pictures"))
    t = Task("tool", "read", f"使用者覺得「{folder}」資料夾裡有很多重複的檔案，想知道哪些檔案內容完全一樣，先列出來，不要刪。", [folder])
    pairs = []
    for i in range(rng.randint(2, 3)):
        data = bytes(rng.getrandbits(8) for _ in range(rng.randint(500, 3000)))
        a, b = f"{rng.choice(STEMS)}_{i}.jpg", f"複製 - {rng.choice(STEMS)}_{i}.jpg"
        t.files[f"{folder}/{a}"] = data
        t.files[f"{folder}/{rng.choice(('', '舊/'))}{b}"] = data
        pairs.append((a, b))
    for i in range(3):
        t.files[f"{folder}/單獨{i}.jpg"] = bytes(rng.getrandbits(8) for _ in range(900))
    t.check = f'''
    for a, b in {pairs!r}:
        need(a in ANSWER and b in ANSWER, "missing pair " + a)
    '''
    t.extra["may_change"] = []
    return t


def gen_archive(rng: random.Random) -> Task:
    folder = rng.choice(FOLDERS)
    ext, what = rng.choice((("exe", "安裝檔"), ("tmp", "暫存檔"), ("log", "舊的 log 檔"), ("bak", "備份殘檔")))
    t = Task("tool", "change", f"使用者要把「{folder}」資料夾裡所有的 .{ext}（{what}）清掉，其他檔案留著。", [folder, f".{ext}"])
    for i in range(rng.randint(3, 5)):
        t.files[f"{folder}/{rng.choice(STEMS)}{i}.{ext}"] = f"{ext}{i}".encode() + bytes(300)
    for i in range(3):
        t.files[f"{folder}/{rng.choice(STEMS)}{i}.{rng.choice(('pdf', 'docx', 'jpg'))}"] = bytes(200 + i)
    t.check = f'''
    for p in (O / {folder!r}).glob("*.{ext}"):
        need(not (W / {folder!r} / p.name).exists(), "still there: " + p.name)
        need([q for q in W.rglob(p.name) if q.read_bytes() == p.read_bytes()], "deleted instead of archived: " + p.name)
    for p in (O / {folder!r}).iterdir():
        if p.suffix != ".{ext}":
            need((W / {folder!r} / p.name).exists(), "moved a kept file: " + p.name)
    '''
    return t


def gen_merge(rng: random.Random) -> Task:
    folder = rng.choice(("報表", "銷售", "帳務", "出勤"))
    out = rng.choice(("合併.csv", "全年.csv", "總表.csv"))
    months = rng.sample(range(1, 13), rng.randint(3, 5))
    header = rng.choice((["日期", "品項", "金額"], ["日期", "姓名", "時數"]))
    t = Task("tool", "change", f"「{folder}」資料夾裡有每個月一個 CSV（欄位一樣，有幾個是 Big5 存的），使用者要合併成一個「{out}」放在同一個資料夾，"
                               "標題列只要一次，每一列資料都要在。", [folder, out])
    total = 0
    for m in months:
        rows = [",".join(header)]
        for d in range(rng.randint(2, 6)):
            rows.append(f"2025-{m:02d}-{d + 1:02d},{rng.choice(PEOPLE)},{rng.randint(1, 900)}")
            total += 1
        t.files[f"{folder}/2025-{m:02d}.csv"] = ("\n".join(rows) + "\n").encode(rng.choice(("utf-8", "big5")))
    t.check = f'''
    import csv, io
    path = W / {folder!r} / {out!r}
    need(path.is_file(), "no output")
    if path.is_file():
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            text = raw.decode("cp950")
        rows = list(csv.reader(io.StringIO(text)))
        need(rows and rows[0] == {header!r}, "header")
        need(len([r for r in rows[1:] if r]) == {total}, "row count {total}")
        need(sum(r == {header!r} for r in rows) == 1, "header repeated")
    '''
    return t


def gen_log(rng: random.Random) -> Task:
    name = rng.choice(("app.log", "service.log", "伺服器.log", "error.log"))
    errs = rng.sample(("disk quota exceeded", "payment gateway timeout", "invalid session token", "printer offline",
                       "upstream returned 502", "certificate expired"), 3)
    counts = sorted(rng.sample(range(4, 40), 3), reverse=True)
    lines = []
    for e, c in zip(errs, counts, strict=True):
        lines += [f"ERROR {e} (id={rng.randint(1000, 9999)})"] * c
    lines += ["INFO request ok"] * rng.randint(50, 120) + ["WARN slow response"] * rng.randint(5, 20)
    rng.shuffle(lines)
    stamped = [f"2025-11-{rng.randint(1, 28):02d} 10:{i % 60:02d}:00 {line}" for i, line in enumerate(lines)]
    t = Task("tool", "read", f"使用者想知道「{name}」裡最常出現的錯誤是哪一種、出現幾次。", [name])
    t.text(name, "\n".join(stamped) + "\n")
    t.check = f'''
    need({errs[0]!r} in ANSWER, "top error")
    need(re.search(r"(?<!\\d){counts[0]}(?!\\d)", ANSWER), "count {counts[0]}")
    '''
    t.extra["may_change"] = []
    return t


def gen_backup(rng: random.Random) -> Task:
    folder = rng.choice(("論文", "公司帳本", "家庭照片", "專案資料", "畢業製作"))
    t = Task("tool", "change", f"使用者要在動手整理之前，先把「{folder}」整個資料夾備份一份（放在同一個工作資料夾裡），原本的不能動。", [folder])
    for i in range(rng.randint(3, 7)):
        t.files[f"{folder}/{rng.choice(('', '附件/', '草稿/'))}{rng.choice(STEMS)}{i}.{rng.choice(('docx', 'xlsx', 'jpg', 'txt'))}"] = bytes(rng.getrandbits(8) for _ in range(400))
    t.check = f'''
    import zipfile
    originals = {{p.relative_to(O / {folder!r}).as_posix(): p.read_bytes() for p in (O / {folder!r}).rglob("*") if p.is_file()}}
    for rel, data in originals.items():
        need((W / {folder!r} / rel).read_bytes() == data, "original changed: " + rel)
    copies = 0
    for p in W.rglob("*"):
        if p.is_file() and {folder!r} not in p.relative_to(W).parts[:1] and p.read_bytes() in originals.values():
            copies += 1
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as z:
                copies += sum(z.read(n) in originals.values() for n in z.namelist() if not n.endswith("/"))
    need(copies >= len(originals), "no complete backup")
    '''
    return t


def gen_git(rng: random.Random) -> Task:
    func = rng.choice(("format_price", "parse_date", "load_config", "send_mail"))
    t = Task("tool", "read", f"使用者在一個 git 專案裡改了 {func} 這個函式但還沒 commit，他想要你看還沒 commit 的修改、寫一段 commit 訊息給他，不要真的 commit。",
             ["commit"])
    old = f"def {func}(value):\n    return str(value)\n"
    new = f"def {func}(value, strict=False):\n    if strict and value is None:\n        raise ValueError('{func}: value is required')\n    return str(value)\n"
    t.text("lib.py", old)
    t.text("README.md", "# tools\n")
    t.dirty["lib.py"] = new.encode()
    t.extra.update({"git": True, "may_change": []})
    t.check = f'''
    import subprocess
    log = subprocess.run(["git", "-C", "/work", "log", "--oneline"], capture_output=True, text=True).stdout
    need(len(log.splitlines()) == 1, "committed")
    need({func!r} in ANSWER or "lib.py" in ANSWER, "names the change")
    need(re.search(r"(strict|ValueError|None|必填|空值|嚴格)", ANSWER), "describes the change")
    '''
    return t


def gen_fix(rng: random.Random) -> Task:
    cases = (
        ("tax.py", "def add_tax(price, rate=0.05):\n    return round(price * rate)\n",
         "def add_tax(price, rate=0.05):\n    return round(price * (1 + rate))\n",
         "from tax import add_tax\n\ndef test_tax():\n    assert add_tax(100) == 105\n    assert add_tax(200, 0.1) == 220\n",
         "add_tax 算出來的含稅價只有稅金，沒有加上原價"),
        ("names.py", "def initials(name):\n    return ''.join(p[0] for p in name.split(' '))\n",
         "def initials(name):\n    return ''.join(p[0].upper() for p in name.split())\n",
         "from names import initials\n\ndef test_initials():\n    assert initials('ada  lovelace') == 'AL'\n    assert initials('alan turing') == 'AT'\n",
         "initials 遇到兩個空白會壞掉，而且縮寫應該是大寫"),
        ("stats.py", "def median(xs):\n    xs = sorted(xs)\n    return xs[len(xs) // 2]\n",
         "def median(xs):\n    xs = sorted(xs)\n    n = len(xs)\n    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2\n",
         "from stats import median\n\ndef test_median():\n    assert median([3, 1, 2]) == 2\n    assert median([4, 1, 3, 2]) == 2.5\n",
         "median 在偶數個數字時算錯，應該取中間兩個的平均"),
        ("slug.py", "def slug(title):\n    return title.lower().replace(' ', '-')\n",
         "import re\n\ndef slug(title):\n    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')\n",
         "from slug import slug\n\ndef test_slug():\n    assert slug('Hello World!') == 'hello-world'\n    assert slug('  A  B ') == 'a-b'\n",
         "slug 沒有去掉標點和多餘空白"),
        ("clock.py", "def to_minutes(text):\n    h, m = text.split(':')\n    return int(h) * 60 + int(m)\n",
         "def to_minutes(text):\n    h, m = text.strip().split(':')\n    h = int(h) % 12 + (12 if text.strip().lower().endswith('pm') else 0) if text.strip().lower()[-2:] in ('am', 'pm') else int(h)\n    return h * 60 + int(m[:2])\n",
         "from clock import to_minutes\n\ndef test_minutes():\n    assert to_minutes('09:30') == 570\n    assert to_minutes('2:15 pm') == 855\n    assert to_minutes('12:05 am') == 5\n",
         "to_minutes 不認得 am/pm 的寫法"),
    )
    name, broken, fixed, tests, symptom = rng.choice(cases)
    t = Task("tool", "change", f"小專案裡 {name} 有問題：{symptom}。tests 資料夾裡有測試可以跑。使用者要修好並確認測試會過。",
             [name.split(".")[0]])
    t.text(name, broken)
    t.text("tests/test_" + name, tests)
    t.extra.update({"git": True, "may_change": [name, "tests/*"]})
    t.check = f'''
    import subprocess, shutil
    shutil.copytree(W, "/tmp/fix", dirs_exist_ok=True)
    Path("/tmp/fix/tests/test_hidden.py").write_text({tests!r})
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_hidden.py"], cwd="/tmp/fix", capture_output=True, text=True)
    need(r.returncode == 0, "tests fail: " + r.stdout[-300:])
    need(not A.get("unverified"), "changed code without a passing check")
    '''
    return t


def gen_find_code(rng: random.Random) -> Task:
    feature, func, words = rng.choice((("發票號碼的檢查", "check_invoice_no", "invoice"), ("會員點數的計算", "reward_points", "points"),
                                        ("密碼強度的判斷", "password_strength", "password"), ("寄送提醒信", "send_reminder", "reminder")))
    t = Task("tool", "read", f"使用者想知道專案裡「{feature}」是在哪個檔案的第幾行寫的。", [feature])
    other = ["def helper():\n    return 1\n", "class Store:\n    def open(self):\n        return True\n"]
    pad = "\n" * rng.randint(2, 12)
    target = f"app/{rng.choice(('core', 'services', 'utils'))}/{words}.py"
    body = f'"""{words} helpers."""\n{pad}import re\n\n\ndef {func}(value):\n    return bool(re.match(r".+", str(value)))\n'
    t.text(target, body)
    t.text("app/__init__.py", "")
    for i, o in enumerate(other):
        t.text(f"app/mod{i}.py", o)
    line = body.splitlines().index(f"def {func}(value):") + 1
    t.check = f'''
    need({target.split('/')[-1]!r} in ANSWER, "file")
    need(re.search(r"(?<!\\d){line}(?!\\d)", ANSWER), "line {line}")
    '''
    t.extra["may_change"] = []
    return t


def gen_calc(rng: random.Random) -> Task:
    a, b, c = rng.randint(1200, 98000), rng.randint(300, 9000), rng.choice((0.9, 0.85, 0.8, 0.75, 0.95))
    people = rng.randint(3, 9)
    value = round((a + b) * c / people, 2)
    t = Task("tool", "read", f"使用者要算錢：兩筆費用 {a:,} 元和 {b:,} 元加起來，打 {int(c * 100)} 折，再由 {people} 個人平分，每人要付多少（算到小數點兩位）。",
             [f"{a:,}".replace(",", ""), str(people)])
    whole = f"{value:,.2f}"
    t.check = f'''
    need({whole!r} in ANSWER or {whole.replace(",", "")!r} in ANSWER, "value {whole}")
    '''
    t.extra["may_change"] = []
    return t


def gen_sysinfo(rng: random.Random) -> Task:
    t = Task("tool", "read", rng.choice(("使用者想知道他這台電腦的記憶體總共多大。", "使用者覺得電腦很慢，想知道記憶體總共多大。")), ["記憶體"])
    # The stand-in laptop's memory (what its Get-CimInstance and the runtime
    # section say), not the container's: the check runs on the dev box.
    t.check = f'''
    total_kb = {laptop.TOTAL_KB}
    gib = total_kb / 1024 / 1024
    found = [float(x.replace(",", "")) for x in re.findall(r"([\\d,]+(?:\\.\\d+)?)\\s*(?:GB|GiB|G)", ANSWER)]
    found += [float(x.replace(",", "")) / 1024 for x in re.findall(r"([\\d,]+(?:\\.\\d+)?)\\s*(?:MB|MiB)", ANSWER)]
    need(any(abs(x - gib) / gib < 0.1 for x in found), "total about %.1f GiB" % gib)
    '''
    t.extra["may_change"] = []
    return t
