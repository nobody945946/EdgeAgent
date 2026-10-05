"""What this computer is and what runs on it. Read-only, every query.

Windows parts use the registry (winreg) and ctypes where they can, and
PowerShell's Get-CimInstance / Get-Process / Get-Net* where they cannot;
nothing here changes a setting. A part that cannot be read is named as such
in the result instead of failing the whole report.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from edge_agent.toolbox import IS_WINDOWS, NO_WINDOW, Tool, register
from edge_agent.toolbox.fs import cap, human_size


def _decode(data: bytes) -> str:
    """Native Windows tools print in the console code page, not UTF-8."""
    for codec in ("utf-8", "oem", "mbcs") if IS_WINDOWS else ("utf-8",):
        try:
            return data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


def _command(args: list[str], timeout: float = 20) -> str:
    done = subprocess.run(args, capture_output=True, timeout=timeout, check=False, creationflags=NO_WINDOW)
    if done.returncode != 0:
        error = _decode(done.stderr or done.stdout).strip().splitlines()
        raise RuntimeError(error[0] if error else f"結束代碼 {done.returncode}")
    return _decode(done.stdout)


def powershell(script: str, timeout: float = 30) -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise RuntimeError("找不到 PowerShell")
    prefix = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
              "$ProgressPreference = 'SilentlyContinue'; ")
    done = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", prefix + script],
                          capture_output=True, timeout=timeout, check=False, creationflags=NO_WINDOW)
    out = done.stdout.decode("utf-8-sig", errors="replace")
    # A non-terminating error (one adapter, one process) still leaves the rest of the answer.
    if done.returncode != 0 and not out.strip():
        error = _decode(done.stderr).strip().splitlines()
        raise RuntimeError(error[0] if error else f"PowerShell 結束代碼 {done.returncode}")
    return out


def ps_json(script: str, timeout: float = 30) -> list[dict]:
    """A PowerShell pipeline's objects as dicts (one object comes back unwrapped, so rewrap)."""
    out = powershell(f"@({script}) | ConvertTo-Json -Depth 4 -Compress", timeout).strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def _part(lines: list[str], missing: list[str], title: str, fn) -> None:
    try:
        lines += fn()
    except Exception as exc:  # one unreadable part must not hide the rest
        missing.append(f"- {title}：{exc}")


# ---------------------------------------------------------------- system_status


def _os_line() -> list[str]:
    if IS_WINDOWS:
        build = int(platform.version().split(".")[-1] or 0)
        name = "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
                name += f"（{winreg.QueryValueEx(k, 'DisplayVersion')[0]}，組建 {build}）"
        except OSError:
            name += f"（組建 {build}）"
        return [f"作業系統：{name}，{platform.machine()}"]
    pretty = platform.system()
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
        pretty = re.search(r'^PRETTY_NAME="?([^"\n]*)', text, re.M).group(1)
    except (OSError, AttributeError):
        pass
    return [f"作業系統：{pretty}（核心 {platform.release()}），{platform.machine()}"]


def _cpu_line() -> list[str]:
    logical = os.cpu_count() or 0
    cores = ""
    if IS_WINDOWS:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            name = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        try:
            cores = str(int(powershell("(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum")))
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
            cores = ""
    else:
        info = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        names = re.findall(r"^model name\s*:\s*(.+)$", info, re.M)
        name = names[0].strip() if names else platform.processor() or "不明"
        pairs = set(re.findall(r"^physical id\s*:\s*(\d+)$.*?^core id\s*:\s*(\d+)$", info, re.M | re.S))
        cores = str(len(pairs)) if pairs else ""
    return [f"CPU：{name}（" + (f"{cores} 核心、" if cores else "") + f"{logical} 執行緒）"]


def _memory_line() -> list[str]:
    if IS_WINDOWS:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                        ("page_avail", ctypes.c_ulonglong), ("virt_total", ctypes.c_ulonglong),
                        ("virt_avail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]

        status = Status(length=ctypes.sizeof(Status))
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx 失敗")
        total, avail = status.total, status.avail
    else:
        info = dict(re.findall(r"^(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M))
        total, avail = int(info["MemTotal"]) * 1024, int(info["MemAvailable"]) * 1024
    return [f"記憶體：總共 {human_size(total)}，可用 {human_size(avail)}（已用 {100 - avail * 100 // total}%）"]


REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk",
           "nfs", "nfs4", "cifs", "9p", "drvfs"}


def _disk_lines() -> list[str]:
    if IS_WINDOWS:
        mounts = os.listdrives()
    else:
        mounts, devices = [], set()
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[2] in REAL_FS:
                mount = parts[1].replace("\\040", " ")
                try:
                    device = os.stat(mount).st_dev
                except OSError:
                    continue
                if device not in devices:
                    devices.add(device)
                    mounts.append(mount)
    lines = ["磁碟："]
    for mount in mounts:
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue  # an empty card reader or a disconnected network drive
        used = (usage.total - usage.free) * 100 // usage.total if usage.total else 0
        lines.append(f"  {mount}  可用 {human_size(usage.free)}／共 {human_size(usage.total)}（已用 {used}%）")
    return lines if len(lines) > 1 else ["磁碟：沒有讀到任何磁碟"]


def _gpu_lines() -> list[str]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ["顯示卡記憶體：沒有 nvidia-smi（沒有 NVIDIA 顯示卡或沒裝驅動），沒有查"]
    out = _command([exe, "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"])
    lines = ["顯示卡記憶體（nvidia-smi）："]
    for row in out.strip().splitlines():
        name, used, total = (c.strip() for c in row.split(","))
        lines.append(f"  {name}：已用 {used}／共 {total}")
    return lines


def process_memory() -> dict[str, tuple[int, int]]:
    """name -> (how many processes, bytes in memory), all processes of this machine."""
    groups: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    if IS_WINDOWS:
        for p in ps_json("Get-Process | ForEach-Object { [pscustomobject]@{ n = $_.ProcessName; m = $_.WorkingSet64 } }"):
            groups[str(p["n"])][0] += 1
            groups[str(p["n"])][1] += int(p["m"] or 0)
    else:
        for status in Path("/proc").glob("[0-9]*/status"):
            try:
                text = status.read_text(errors="replace")
            except OSError:
                continue  # the process ended while we looked
            name = re.search(r"^Name:\s*(.*)$", text, re.M)
            rss = re.search(r"^VmRSS:\s*(\d+) kB", text, re.M)
            if name and rss:
                groups[name.group(1)][0] += 1
                groups[name.group(1)][1] += int(rss.group(1)) * 1024
    return {name: (count, size) for name, (count, size) in groups.items()}


def _process_lines() -> list[str]:
    top = sorted(process_memory().items(), key=lambda kv: -kv[1][1])[:12]
    lines = ["用最多記憶體的程式（同名的合併計算）："]
    for name, (count, size) in top:
        lines.append(f"  {name}" + (f" ×{count}" if count > 1 else "") + f"  {size / 1024 / 1024:,.0f} MB")
    return lines


def status_run(params: dict, root: Path) -> str:
    lines: list[str] = []
    missing: list[str] = []
    for title, fn in (("作業系統", _os_line), ("CPU", _cpu_line), ("記憶體", _memory_line), ("磁碟", _disk_lines),
                      ("顯示卡", _gpu_lines), ("程式", _process_lines)):
        _part(lines, missing, title, fn)
    lines.append(f"查詢時間：{datetime.now():%Y-%m-%d %H:%M:%S}")
    if missing:
        lines += ["無法取得：", *missing]
    return "\n".join(lines)


register(Tool(
    name="system_status",
    summary="Report OS, CPU, RAM, free disk space, GPU memory and the programs using the most memory.",
    keywords="電腦狀態 系統資訊 記憶體 RAM CPU 硬碟 空間 顯示卡 GPU 為什麼很慢 卡頓 哪個程式吃記憶體 規格 "
             "system status info memory disk space slow task manager 工作管理員",
    parameters={"type": "object", "properties": {}},
    kind="read", destructive=False, windows_only=False,
    run=status_run, preview=None,
))


# ---------------------------------------------------------------- installed_apps

UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


def _value(reg, key, name: str) -> object:
    try:
        return reg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _subkeys(reg, key):
    i = 0
    while True:
        try:
            yield reg.EnumKey(key, i)
        except OSError:
            return
        i += 1


def uninstall_entries(reg) -> list[dict]:
    """Programs from the Uninstall keys: 64-bit and 32-bit machine-wide, and this user's."""
    views = ((reg.HKEY_LOCAL_MACHINE, reg.KEY_WOW64_64KEY), (reg.HKEY_LOCAL_MACHINE, reg.KEY_WOW64_32KEY),
             (reg.HKEY_CURRENT_USER, 0))
    found: dict[tuple[str, str], dict] = {}
    for hive, view in views:
        try:
            top = reg.OpenKey(hive, UNINSTALL, 0, reg.KEY_READ | view)
        except OSError:
            continue
        with top:
            for sub in _subkeys(reg, top):
                try:
                    key = reg.OpenKey(top, sub)
                except OSError:
                    continue
                with key:
                    name = _value(reg, key, "DisplayName")
                    # Hidden parts of other programs and their updates, as Settings > Apps hides them.
                    if not name or _value(reg, key, "SystemComponent") == 1 or _value(reg, key, "ParentKeyName"):
                        continue
                    version = str(_value(reg, key, "DisplayVersion") or "")
                    raw_date = str(_value(reg, key, "InstallDate") or "")
                    entry = {"name": str(name).strip(), "version": version,
                             "publisher": str(_value(reg, key, "Publisher") or ""),
                             "date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                             if re.fullmatch(r"\d{8}", raw_date) else ""}
                    found.setdefault((entry["name"].casefold(), version), entry)
    return sorted(found.values(), key=lambda e: (e["name"].casefold(), e["version"]))


def installed_text(entries: list[dict], name_filter: str | None) -> str:
    shown_entries = [e for e in entries if not name_filter or name_filter.casefold() in
                     f"{e['name']} {e['publisher']}".casefold()]
    if not shown_entries:
        return f"沒有名稱或發行者包含「{name_filter}」的已安裝程式（共檢查 {len(entries)} 個）。"
    head = (f"名稱或發行者包含「{name_filter}」的已安裝程式：{len(shown_entries)} 個" if name_filter
            else f"已安裝的程式：{len(shown_entries)} 個（和「設定 > 應用程式」的清單一樣不含系統元件和更新）")
    lines = [" ｜ ".join(x for x in (e["name"], e["version"] and f"版本 {e['version']}", e["publisher"],
                                     e["date"] and f"安裝於 {e['date']}") if x) for e in shown_entries]
    tail = [] if len(lines) <= 60 or name_filter else ["要找特定程式，請用 name_filter。"]
    return "\n".join([head, *cap(lines, 60), *tail])


def installed_run(params: dict, root: Path) -> str:
    import winreg

    return installed_text(uninstall_entries(winreg), params["name_filter"])


register(Tool(
    name="installed_apps",
    summary="List installed programs (name, version, publisher, install date) from the Windows registry.",
    keywords="已安裝 安裝了什麼 軟體 程式 應用程式 版本 有沒有裝 installed apps programs software version "
             "list uninstall 解除安裝",
    parameters={
        "type": "object",
        "properties": {
            "name_filter": {"type": "string",
                            "description": "只列出名稱或發行者包含這個字的程式（不分大小寫），例如「python」"},
        },
    },
    kind="read", destructive=False, windows_only=True,
    run=installed_run, preview=None,
))


# ---------------------------------------------------------------- startup_apps

RUN_KEYS = r"SOFTWARE\Microsoft\Windows\CurrentVersion"
APPROVED = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"


def _values(reg, hive, path: str, view: int = 0) -> dict[str, object]:
    try:
        key = reg.OpenKey(hive, path, 0, reg.KEY_READ | view)
    except OSError:
        return {}
    out = {}
    with key:
        i = 0
        while True:
            try:
                name, data, _ = reg.EnumValue(key, i)
            except OSError:
                return out
            out[name] = data
            i += 1


def _disabled(approved: dict[str, object], name: str) -> bool:
    """Task Manager's off switch: StartupApproved data starting with 3 (the Run entry stays)."""
    data = approved.get(name)
    return isinstance(data, bytes | bytearray) and len(data) > 0 and data[0] == 3


def startup_entries(reg, folders: list[tuple[str, Path]], targets) -> list[tuple[str, str, str, bool]]:
    """(where, name, command or target, disabled) for every registry Run entry and Startup folder item."""
    hkcu, hklm = reg.HKEY_CURRENT_USER, reg.HKEY_LOCAL_MACHINE
    places = (
        ("目前使用者 Run", hkcu, "Run", 0, "Run"),
        ("目前使用者 RunOnce", hkcu, "RunOnce", 0, None),
        ("所有使用者 Run", hklm, "Run", reg.KEY_WOW64_64KEY, "Run"),
        ("所有使用者 Run（32 位元）", hklm, "Run", reg.KEY_WOW64_32KEY, "Run32"),
        ("所有使用者 RunOnce", hklm, "RunOnce", reg.KEY_WOW64_64KEY, None),
    )
    entries = []
    seen = set()
    for where, hive, sub, view, approved_key in places:
        approved = _values(reg, hive, f"{APPROVED}\\{approved_key}") if approved_key else {}
        for name, command in _values(reg, hive, f"{RUN_KEYS}\\{sub}", view).items():
            if (hive, sub, name, str(command)) not in seen:
                seen.add((hive, sub, name, str(command)))
                entries.append((where, name, str(command), _disabled(approved, name)))
    for where, folder in folders:
        hive = hkcu if "目前" in where else hklm
        approved = _values(reg, hive, f"{APPROVED}\\StartupFolder")
        items = sorted(p for p in folder.iterdir() if p.name.casefold() != "desktop.ini") if folder.is_dir() else []
        links = targets([p for p in items if p.suffix.casefold() == ".lnk"]) if items else {}
        for item in items:
            entries.append((where, item.name, links.get(str(item), str(item)), _disabled(approved, item.name)))
    return entries


def _lnk_targets(paths: list[Path]) -> dict[str, str]:
    """Shortcut targets via the Windows Script Host; a failure leaves the path itself."""
    if not paths:
        return {}
    quoted = ",".join("'" + str(p).replace("'", "''") + "'" for p in paths)
    script = ("$s = New-Object -ComObject WScript.Shell; "
              f"@({quoted}) | ForEach-Object {{ $l = $s.CreateShortcut($_); "
              "[pscustomobject]@{ p = $_; t = ($l.TargetPath + ' ' + $l.Arguments).Trim() } }")
    try:
        return {str(r["p"]): str(r["t"]) for r in ps_json(script)}
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        return {}


def startup_text(entries: list[tuple[str, str, str, bool]]) -> str:
    if not entries:
        return "沒有找到開機自動啟動的程式（登錄檔 Run／RunOnce 和「啟動」資料夾都是空的）。"
    on = sum(1 for e in entries if not e[3])
    lines = [f"開機自動啟動的項目 {len(entries)} 個（啟用 {on}、在工作管理員裡停用 {len(entries) - on}）："]
    lines += cap([f"- {name}" + ("（已停用）" if off else "") + f"｜{where}｜{command[:160]}"
                  for where, name, command, off in entries], 60)
    lines.append("只列出登錄檔 Run／RunOnce 和「啟動」資料夾；工作排程器的工作和服務不在這裡。這個工具不會改任何設定。")
    return "\n".join(lines)


def startup_run(params: dict, root: Path) -> str:
    import winreg

    folders = []
    if os.environ.get("APPDATA"):
        folders.append(("目前使用者「啟動」資料夾",
                        Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"))
    if os.environ.get("PROGRAMDATA"):
        folders.append(("所有使用者「啟動」資料夾",
                        Path(os.environ["PROGRAMDATA"]) / r"Microsoft\Windows\Start Menu\Programs\StartUp"))
    return startup_text(startup_entries(winreg, folders, _lnk_targets))


register(Tool(
    name="startup_apps",
    summary="List programs that start automatically at sign-in (registry Run keys and Startup folders).",
    keywords="開機 自動啟動 啟動項目 開機很慢 常駐 startup apps autorun run at login boot slow 開機程式",
    parameters={"type": "object", "properties": {}},
    kind="read", destructive=False, windows_only=True,
    run=startup_run, preview=None,
))


# ---------------------------------------------------------------- windows_info


def _yes(value: object) -> str:
    return {True: "是", False: "否"}.get(value, "不明") if isinstance(value, bool) or value is None else str(value)


def _power() -> list[str]:
    lines = ["目前的電源計畫：" + _command(["powercfg", "/getactivescheme"]).strip()]
    for battery in ps_json("Get-CimInstance Win32_Battery | Select-Object EstimatedChargeRemaining, BatteryStatus"):
        plugged = {1: "用電池", 2: "接著電源"}.get(battery.get("BatteryStatus"), f"狀態 {battery.get('BatteryStatus')}")
        lines.append(f"電池：剩 {battery.get('EstimatedChargeRemaining')}%（{plugged}）")
    return lines


def network_text(adapters: list[dict], configs: list[dict]) -> list[str]:
    by_alias = {c.get("alias"): c for c in configs}
    lines = []
    for a in sorted(adapters, key=lambda a: (a.get("Status") != "Up", str(a.get("Name")))):
        lines.append(f"- {a.get('Name')}（{a.get('InterfaceDescription')}）：{a.get('Status')}"
                     + (f"，{a.get('LinkSpeed')}" if a.get("Status") == "Up" else "") + f"，MAC {a.get('MacAddress')}")
        config = by_alias.get(a.get("Name"))
        if config and a.get("Status") == "Up":
            for title, k in (("IPv4", "ipv4"), ("IPv6", "ipv6"), ("預設閘道", "gw"), ("DNS", "dns")):
                values = [str(v) for v in (config.get(k) or []) if v]
                if values:
                    lines.append(f"    {title}：{'、'.join(values)}")
    return lines or ["沒有找到網路介面卡"]


def _network() -> list[str]:
    adapters = ps_json("Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress")
    configs = ps_json("Get-NetIPConfiguration | ForEach-Object { [pscustomobject]@{ alias = $_.InterfaceAlias; "
                      "ipv4 = @($_.IPv4Address | ForEach-Object IPAddress); ipv6 = @($_.IPv6Address | "
                      "ForEach-Object IPAddress); gw = @($_.IPv4DefaultGateway | ForEach-Object NextHop); "
                      "dns = @($_.DNSServer | Where-Object AddressFamily -eq 2 | ForEach-Object ServerAddresses) } }")
    return ["網路介面卡：", *network_text(adapters, configs)]


def _time() -> list[str]:
    info = ps_json("$tz = Get-TimeZone; $os = Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{ "
                   "id = $tz.Id; name = $tz.DisplayName; dst = $tz.SupportsDaylightSavingTime; "
                   "now = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'); "
                   "utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'); "
                   "boot = $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm') }")[0]
    return [f"時區：{info['name']}（{info['id']}；有日光節約時間：{_yes(info['dst'])}）",
            f"現在：{info['now']}（UTC {info['utc']}）", f"上次開機：{info['boot']}"]


def _display() -> list[str]:
    lines = ["顯示卡和解析度（每張顯示卡只回報目前的主要畫面）："]
    for v in ps_json("Get-CimInstance Win32_VideoController | Select-Object Name, CurrentHorizontalResolution, "
                     "CurrentVerticalResolution, CurrentRefreshRate, DriverVersion"):
        size = (f"{v['CurrentHorizontalResolution']}×{v['CurrentVerticalResolution']}，{v['CurrentRefreshRate']} Hz"
                if v.get("CurrentHorizontalResolution") else "沒有接螢幕或沒有回報")
        lines.append(f"- {v.get('Name')}：{size}，驅動程式版本 {v.get('DriverVersion')}")
    return lines


DEFENDER_FIELDS = (
    ("AMServiceEnabled", "防毒服務在執行"), ("AntivirusEnabled", "防毒已開啟"),
    ("RealTimeProtectionEnabled", "即時保護已開啟"), ("IsTamperProtected", "防竄改保護已開啟"),
    ("sig", "病毒碼更新時間"), ("AntivirusSignatureVersion", "病毒碼版本"),
    ("QuickScanAge", "距離上次快速掃描（天）"), ("FullScanAge", "距離上次完整掃描（天）"),
)


def _defender() -> list[str]:
    status = ps_json("Get-MpComputerStatus | Select-Object AMServiceEnabled, AntivirusEnabled, "
                     "RealTimeProtectionEnabled, IsTamperProtected, AntivirusSignatureVersion, QuickScanAge, "
                     "FullScanAge, @{ n = 'sig'; e = { $_.AntivirusSignatureLastUpdated.ToString('yyyy-MM-dd HH:mm') } }")
    if not status:
        return ["Microsoft Defender 沒有回報狀態（可能裝了其他防毒軟體）"]
    return ["Microsoft Defender："] + [f"- {title}：{_yes(status[0].get(k))}" for k, title in DEFENDER_FIELDS]


def path_text(effective: str, machine: str, user: str) -> list[str]:
    def entries(value: str) -> list[str]:
        return [os.path.expandvars(p.strip()) for p in value.split(";") if p.strip()]

    machine_set = {os.path.normcase(p.rstrip("\\/")) for p in entries(machine)}
    user_set = {os.path.normcase(p.rstrip("\\/")) for p in entries(user)}
    lines, seen = [], set()
    for p in entries(effective):
        k = os.path.normcase(p.rstrip("\\/"))
        source = "系統" if k in machine_set else "使用者" if k in user_set else "只在目前的程式環境"
        mark = "存在" if os.path.isdir(p) else "不存在！"
        lines.append(f"- {p}｜{source}｜{mark}" + ("｜重複" if k in seen else ""))
        seen.add(k)
    missing = sum("不存在" in line for line in lines)
    return [f"PATH 有 {len(lines)} 個資料夾（依搜尋順序），{missing} 個不存在：", *cap(lines, 60)]


def _path() -> list[str]:
    import winreg

    def read(hive, key: str) -> str:
        try:
            with winreg.OpenKey(hive, key) as k:
                return str(winreg.QueryValueEx(k, "Path")[0])
        except OSError:
            return ""

    machine = read(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    return path_text(os.environ.get("PATH", ""), machine, read(winreg.HKEY_CURRENT_USER, "Environment"))


TOPICS = {"power": _power, "network": _network, "time": _time, "display": _display, "defender": _defender,
          "path": _path}


def windows_info_run(params: dict, root: Path) -> str:
    try:
        lines = TOPICS[params["topic"]]()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        return f"查不到 {params['topic']} 的資訊：{exc}"
    return "\n".join([*lines, "（只是查詢，沒有改任何設定）"])


register(Tool(
    name="windows_info",
    summary="Read-only Windows settings: power plan, network adapters/IP, time zone, display, Defender, PATH.",
    keywords="Windows 設定 電源 電池 網路 IP 網卡 DNS 時區 時間 解析度 螢幕 防毒 Defender PATH 環境變數 "
             "power plan network ip address time zone display resolution antivirus environment",
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "enum": list(TOPICS),
                      "description": "要查什麼：power 電源計畫和電池、network 網路和 IP、time 時區和時間、"
                                     "display 解析度、defender 防毒狀態、path PATH 環境變數"},
        },
        "required": ["topic"],
    },
    kind="read", destructive=False, windows_only=True,
    run=windows_info_run, preview=None,
))
