"""edge_agent.toolbox.system: system status and the Windows-only lookups (registry faked)."""

from __future__ import annotations

from pathlib import Path
from typing import Self

import pytest

from edge_agent.toolbox import IS_WINDOWS, REGISTRY, WINDOWS_ONLY, system
from tests.toolbox_helpers import run, write


def test_system_status_reports_each_part() -> None:
    out = run("system_status", Path.cwd())
    for part in ("作業系統：", "CPU：", "記憶體：", "磁碟：", "顯示卡記憶體", "用最多記憶體的程式"):
        assert part in out or part.rstrip("：") in out, part


@pytest.mark.skipif(IS_WINDOWS, reason="checks the message other systems get")
@pytest.mark.parametrize(("name", "params"), [("installed_apps", {}), ("startup_apps", {}),
                                              ("windows_info", {"topic": "power"})])
def test_windows_only_tools_say_so(tmp_path: Path, name: str, params: dict) -> None:
    assert REGISTRY[name].windows_only and run(name, tmp_path, **params) == WINDOWS_ONLY


class FakeKey:
    def __init__(self, hive: str, view: int, path: str) -> None:
        self.hive, self.view, self.path = hive, view, path

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeReg:
    """Enough of winreg: keys are (hive, view, path) -> values."""

    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"
    KEY_READ, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 0x20019, 0x100, 0x200

    def __init__(self, keys: dict[tuple[str, int, str], dict]) -> None:
        self.keys = keys

    def OpenKey(self, parent, sub: str, reserved: int = 0, access: int = 0x20019) -> FakeKey:
        if isinstance(parent, FakeKey):
            hive, view, path = parent.hive, parent.view, f"{parent.path}\\{sub}"
        else:
            hive, view, path = parent, access & 0x300, sub
        if not any(k[:2] == (hive, view) and (k[2] == path or k[2].startswith(path + "\\")) for k in self.keys):
            raise FileNotFoundError(path)
        return FakeKey(hive, view, path)

    def EnumKey(self, key: FakeKey, i: int) -> str:
        children = sorted({k[2][len(key.path) + 1:].split("\\")[0] for k in self.keys
                           if k[:2] == (key.hive, key.view) and k[2].startswith(key.path + "\\")})
        if i >= len(children):
            raise OSError("no more keys")
        return children[i]

    def QueryValueEx(self, key: FakeKey, name: str):
        values = self.keys.get((key.hive, key.view, key.path), {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    def EnumValue(self, key: FakeKey, i: int):
        items = list(self.keys.get((key.hive, key.view, key.path), {}).items())
        if i >= len(items):
            raise OSError("no more values")
        return (*items[i], 1)


def test_installed_apps_from_registry() -> None:
    u = system.UNINSTALL
    reg = FakeReg({
        ("HKLM", 0x100, f"{u}\\Py"): {"DisplayName": "Python 3.12.4 (64-bit)", "DisplayVersion": "3.12.4",
                                      "Publisher": "Python Software Foundation", "InstallDate": "20240115"},
        ("HKLM", 0x200, f"{u}\\Py"): {"DisplayName": "Python 3.12.4 (64-bit)", "DisplayVersion": "3.12.4"},
        ("HKLM", 0x200, f"{u}\\Zip"): {"DisplayName": "7-Zip 23.01", "DisplayVersion": "23.01"},
        ("HKLM", 0x100, f"{u}\\Hidden"): {"DisplayName": "Runtime part", "SystemComponent": 1},
        ("HKLM", 0x100, f"{u}\\KB1"): {"DisplayName": "Update for X", "ParentKeyName": "X"},
        ("HKLM", 0x100, f"{u}\\NoName"): {"DisplayVersion": "1"},
        ("HKCU", 0, f"{u}\\Code"): {"DisplayName": "Microsoft Visual Studio Code (User)", "DisplayVersion": "1.90"},
    })
    entries = system.uninstall_entries(reg)
    assert [e["name"] for e in entries] == ["7-Zip 23.01", "Microsoft Visual Studio Code (User)",
                                            "Python 3.12.4 (64-bit)"]
    out = system.installed_text(entries, "PYTHON")
    assert "1 個" in out and "版本 3.12.4" in out and "安裝於 2024-01-15" in out and "7-Zip" not in out
    assert "沒有名稱" in system.installed_text(entries, "photoshop")


def test_startup_apps_from_registry_and_folder(tmp_path: Path) -> None:
    run_key = f"{system.RUN_KEYS}\\Run"
    reg = FakeReg({
        ("HKCU", 0, run_key): {"OneDrive": '"C:\\OneDrive.exe" /background', "Discord": "C:\\Discord.exe"},
        ("HKCU", 0, f"{system.APPROVED}\\Run"): {"Discord": b"\x03\0\0\0"},
        ("HKLM", 0x100, run_key): {"SecurityHealth": "C:\\Windows\\SecurityHealthSystray.exe"},
    })
    folder = tmp_path / "Startup"
    write(folder / "tool.lnk")
    write(folder / "desktop.ini")
    entries = system.startup_entries(reg, [("目前使用者「啟動」資料夾", folder)],
                                     lambda paths: {str(p): "C:\\tool.exe --tray" for p in paths})
    out = system.startup_text(entries)
    assert "項目 4 個（啟用 3、在工作管理員裡停用 1）" in out
    assert "Discord（已停用）" in out and "C:\\tool.exe --tray" in out and "desktop.ini" not in out
    assert "沒有找到" in system.startup_text([])


def test_windows_info_formatting(tmp_path: Path) -> None:
    real = tmp_path / "bin"
    real.mkdir()
    out = "\n".join(system.path_text(f"{real};{tmp_path / 'gone'};{real}", str(real), str(tmp_path / "gone")))
    assert "3 個資料夾" in out and "1 個不存在" in out and "｜系統｜存在" in out and "重複" in out
    lines = system.network_text(
        [{"Name": "Wi-Fi", "InterfaceDescription": "Intel AX201", "Status": "Up", "LinkSpeed": "866.7 Mbps",
          "MacAddress": "AA-BB"}, {"Name": "乙太網路", "InterfaceDescription": "Realtek", "Status": "Disconnected",
                                   "MacAddress": "CC-DD"}],
        [{"alias": "Wi-Fi", "ipv4": ["192.168.1.20"], "ipv6": [], "gw": ["192.168.1.1"], "dns": ["8.8.8.8"]}])
    assert lines[0].startswith("- Wi-Fi") and "IPv4：192.168.1.20" in "\n".join(lines) and "Disconnected" in lines[-1]
