"""MAVLink packets (drones) to plain Traditional Chinese, exactly.

pymavlink decodes the bytes and checks the CRC; the official XML message
definitions it ships (common.xml, ardupilotmega.xml) give every field's
type, unit, enum, "invalid" marker and description. The translator converts
scaled units deterministically (degE7 to degrees, mm to metres, cdeg to
degrees, cA to amperes ...), names enum values and bitmask flags from the
definitions, and names ArduPilot/PX4 flight modes with pymavlink's own
tables. Chinese wording covers the common messages; anything else is shown
with the official English description, never a guess. A packet whose CRC
does not match is reported as damaged and not translated.

tests/test_lingua_mavlink.py packs messages with pymavlink and checks every
field, unit conversion and the units in the Chinese table against the XML.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

DIALECTS = ("ardupilotmega", "common")

# unit -> (factor to the shown unit, shown unit, decimals)
UNITS = {
    "degE7": (1e-7, "°", 7), "deg": (1, "°", 2), "cdeg": (0.01, "°", 2), "rad": (1, "弧度", 4),
    "rad/s": (1, "弧度/秒", 4), "deg/s": (1, "°/秒", 2), "mm": (0.001, "公尺", 3), "cm": (0.01, "公尺", 2),
    "dm": (0.1, "公尺", 1), "m": (1, "公尺", 2), "mm/s": (0.001, "公尺/秒", 3), "cm/s": (0.01, "公尺/秒", 2),
    "m/s": (1, "公尺/秒", 2), "m/s*5": (0.2, "公尺/秒", 1), "ms": (0.001, "秒", 3), "us": (1e-6, "秒", 6),
    "s": (1, "秒", 0), "mV": (0.001, "伏特", 3), "V": (1, "伏特", 2), "cA": (0.01, "安培", 2),
    "mA": (0.001, "安培", 3), "A": (1, "安培", 2), "mAh": (1, "毫安時", 0), "hJ": (100, "焦耳", 0),
    "cdegC": (0.01, "°C", 2), "degC": (1, "°C", 1), "%": (1, "%", 0), "c%": (0.01, "%", 2), "d%": (0.1, "%", 1),
    "hPa": (1, "百帕", 2), "Pa": (1, "帕", 1), "mbar": (1, "毫巴", 2), "mG": (0.001, "高斯", 3),
    "mgauss": (0.001, "高斯", 3), "mrad": (0.001, "弧度", 4), "mrad/s": (0.001, "弧度/秒", 4),
    "cm^2": (0.0001, "平方公尺", 4), "Hz": (1, "赫茲", 0), "rpm": (1, "轉/分", 0), "Ws": (1, "焦耳", 0),
    "mg": (0.001, "g（重力加速度）", 3), "m/s/s": (1, "公尺/秒²", 2), "cm/s/s": (0.01, "公尺/秒²", 2),
}
INVALID = {
    "UINT8_MAX": 2**8 - 1, "UINT16_MAX": 2**16 - 1, "UINT32_MAX": 2**32 - 1, "UINT64_MAX": 2**64 - 1,
    "INT8_MAX": 2**7 - 1, "INT16_MAX": 2**15 - 1, "INT32_MAX": 2**31 - 1, "INT64_MAX": 2**63 - 1,
}

# Chinese for common messages: (meaning, unit as the XML has it, None when unitless).
MESSAGES: dict[str, tuple[str, dict[str, tuple[str, str | None]]]] = {
    "HEARTBEAT": ("心跳：每秒一次，告訴地面站「我還在」和目前的狀態", {
        "type": ("載具種類", None), "autopilot": ("飛控軟體", None), "base_mode": ("基本模式旗標", None),
        "custom_mode": ("飛控自訂的飛行模式編號（意思依飛控而定）", None), "system_status": ("系統狀態", None),
        "mavlink_version": ("MAVLink 版本", None)}),
    "SYS_STATUS": ("系統狀態：感測器、電池、通訊錯誤率", {
        "voltage_battery": ("電池電壓", "mV"), "current_battery": ("電池電流", "cA"),
        "battery_remaining": ("電池剩餘電量", "%"), "load": ("飛控 CPU 負載", "d%"),
        "drop_rate_comm": ("通訊丟包率", "c%"), "errors_comm": ("通訊錯誤次數", None),
        "onboard_control_sensors_present": ("裝了哪些感測器", None),
        "onboard_control_sensors_enabled": ("啟用了哪些感測器", None),
        "onboard_control_sensors_health": ("哪些感測器正常", None)}),
    "GLOBAL_POSITION_INT": ("融合後的全球位置（GPS 加上其他感測器估算）", {
        "time_boot_ms": ("開機後經過的時間", "ms"), "lat": ("緯度", "degE7"), "lon": ("經度", "degE7"),
        "alt": ("海拔高度（平均海平面）", "mm"), "relative_alt": ("離起飛點的高度", "mm"),
        "vx": ("北向速度", "cm/s"), "vy": ("東向速度", "cm/s"), "vz": ("向下的速度（正值是往下）", "cm/s"),
        "hdg": ("機頭方向（0 是正北，順時針）", "cdeg")}),
    "GPS_RAW_INT": ("GPS 接收機的原始資料", {
        "time_usec": ("時間戳記", "us"), "fix_type": ("定位狀態", None), "lat": ("緯度", "degE7"),
        "lon": ("經度", "degE7"), "alt": ("海拔高度（平均海平面）", "mm"), "eph": ("水平精度因子 HDOP（×100）", None),
        "epv": ("垂直精度因子 VDOP（×100）", None), "vel": ("對地速度", "cm/s"),
        "cog": ("移動方向（0 是正北，順時針）", "cdeg"), "satellites_visible": ("看得到的衛星數", None)}),
    "ATTITUDE": ("姿態：機身的傾斜和方向", {
        "time_boot_ms": ("開機後經過的時間", "ms"), "roll": ("左右傾斜角（滾轉）", "rad"),
        "pitch": ("前後傾斜角（俯仰）", "rad"), "yaw": ("機頭方向（偏航）", "rad"),
        "rollspeed": ("滾轉角速度", "rad/s"), "pitchspeed": ("俯仰角速度", "rad/s"), "yawspeed": ("偏航角速度", "rad/s")}),
    "VFR_HUD": ("儀表板常用數值", {
        "airspeed": ("空速", "m/s"), "groundspeed": ("對地速度", "m/s"), "heading": ("機頭方向", "deg"),
        "throttle": ("油門", "%"), "alt": ("高度（平均海平面）", "m"), "climb": ("爬升率", "m/s")}),
    "BATTERY_STATUS": ("電池狀態", {
        "id": ("電池編號", None), "temperature": ("電池溫度", "cdegC"), "current_battery": ("電流", "cA"),
        "current_consumed": ("已用掉的電量", "mAh"), "energy_consumed": ("已用掉的能量", "hJ"),
        "battery_remaining": ("剩餘電量", "%")}),
    "STATUSTEXT": ("飛控傳來的文字訊息", {"severity": ("嚴重程度", None), "text": ("訊息內容（原文）", None)}),
    "COMMAND_LONG": ("要求執行一個指令", {
        "target_system": ("目標系統編號", None), "target_component": ("目標元件編號", None),
        "command": ("指令", None), "confirmation": ("重送次數（0 是第一次送）", None)}),
    "COMMAND_ACK": ("指令執行的回覆", {"command": ("回覆的是哪個指令", None), "result": ("結果", None)}),
    "RC_CHANNELS": ("遙控器各通道的值", {"time_boot_ms": ("開機後經過的時間", "ms"),
                                       "chancount": ("通道數", None), "rssi": ("訊號強度（255 表示未知）", None)}),
}
COMMANDS = {
    "MAV_CMD_NAV_TAKEOFF": "起飛到指定高度", "MAV_CMD_NAV_LAND": "降落",
    "MAV_CMD_NAV_RETURN_TO_LAUNCH": "返航（飛回起飛點）", "MAV_CMD_NAV_WAYPOINT": "飛到航點",
    "MAV_CMD_COMPONENT_ARM_DISARM": "解鎖或上鎖馬達（param1：1 解鎖、0 上鎖；param2 是 21196 時強制，略過安全檢查）",
    "MAV_CMD_DO_SET_MODE": "切換飛行模式", "MAV_CMD_DO_CHANGE_SPEED": "改變速度",
    "MAV_CMD_MISSION_START": "開始執行任務", "MAV_CMD_NAV_LOITER_UNLIM": "在原地盤旋或懸停，直到下一個指令",
    "MAV_CMD_DO_SET_HOME": "設定返航點", "MAV_CMD_PREFLIGHT_CALIBRATION": "起飛前校正感測器",
    "MAV_CMD_REQUEST_MESSAGE": "要求傳送某一則訊息", "MAV_CMD_SET_MESSAGE_INTERVAL": "設定某則訊息多久傳一次",
    "MAV_CMD_DO_REPOSITION": "飛到指定位置（不屬於任務）", "MAV_CMD_DO_MOTOR_TEST": "測試馬達",
    "MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN": "重新開機或關機",
}
SEVERITY = {0: "緊急（系統無法使用）", 1: "警報（要立刻處理）", 2: "嚴重", 3: "錯誤", 4: "警告", 5: "注意",
            6: "資訊", 7: "除錯"}


@dataclass
class FieldDef:
    name: str
    type: str
    units: str
    enum: str
    display: str
    invalid: str
    description: str


@dataclass
class MessageDef:
    name: str
    id: int
    description: str
    fields: dict[str, FieldDef] = field(default_factory=dict)


@dataclass
class EnumEntry:
    name: str
    value: int
    description: str
    params: dict[int, str] = field(default_factory=dict)


@dataclass
class Definitions:
    messages: dict[str, MessageDef]
    enums: dict[str, dict[int, EnumEntry]]
    bitmasks: set[str]


def xml_dir() -> Path:
    import pymavlink

    return Path(pymavlink.__file__).parent / "dialects" / "v20"


@cache
def definitions(dialect: str) -> Definitions:
    """The XML definitions of a dialect with its includes."""
    messages: dict[str, MessageDef] = {}
    enums: dict[str, dict[int, EnumEntry]] = {}
    bitmasks: set[str] = set()
    seen: set[str] = set()

    def load(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        root = ET.parse(xml_dir() / name).getroot()
        for include in root.findall("include"):
            load(include.text.strip())
        for enum in root.iter("enum"):
            entries = enums.setdefault(enum.get("name"), {})
            if enum.get("bitmask") == "true":
                bitmasks.add(enum.get("name"))
            for entry in enum.findall("entry"):
                value = entry.get("value")
                if value is None:
                    continue
                number = int(value, 0) if not value.startswith("2**") else 2 ** int(value[3:])
                params = {int(p.get("index")): " ".join((p.get("label") or "").split() + [
                    " ".join((p.text or "").split())]).strip() for p in entry.findall("param")}
                entries[number] = EnumEntry(entry.get("name"), number,
                                            " ".join((entry.findtext("description") or "").split()), params)
        for message in root.iter("message"):
            definition = MessageDef(message.get("name"), int(message.get("id")),
                                    " ".join((message.findtext("description") or "").split()))
            for f in message.findall("field"):
                definition.fields[f.get("name")] = FieldDef(
                    f.get("name"), f.get("type"), f.get("units") or "", f.get("enum") or "",
                    f.get("display") or "", f.get("invalid") or "", " ".join((f.text or "").split()))
            messages[definition.name] = definition

    load(f"{dialect}.xml")
    return Definitions(messages, enums, bitmasks)


def module(dialect: str):
    if dialect == "ardupilotmega":
        from pymavlink.dialects.v20 import ardupilotmega as mod
    else:
        from pymavlink.dialects.v20 import common as mod
    return mod


def convert(value: float, units: str) -> str | None:
    if units not in UNITS or isinstance(value, (str, bytes)):
        return None
    factor, unit, decimals = UNITS[units]
    shown = value * factor
    if units in ("rad", "rad/s", "mrad", "mrad/s") and isinstance(value, float):
        return f"{shown:.{decimals}f} {unit}（= {math.degrees(shown):.2f}°{'/秒' if '/s' in units else ''}）"
    return f"{shown:.{decimals}f} {unit}"


def is_invalid(value: object, marker: str) -> bool:
    if not marker:
        return False
    if marker == "NaN":
        return isinstance(value, float) and math.isnan(value)
    if marker in INVALID:
        return value == INVALID[marker]
    try:
        return value == int(marker, 0)
    except ValueError:
        return False


@dataclass
class Translation:
    lines: list[str]

    def text(self) -> str:
        return "\n".join(self.lines)


def describe_value(msg, name: str, fdef: FieldDef, defs: Definitions) -> str:
    value = getattr(msg, name)
    if isinstance(value, bytes):
        value = value.rstrip(b"\0").decode("utf-8", "replace")
    if is_invalid(value, fdef.invalid):
        return f"{value}（依定義，這個值代表「未知／沒有資料」）"
    if fdef.enum and isinstance(value, int):
        table = defs.enums.get(fdef.enum, {})
        if fdef.enum in defs.bitmasks or fdef.display == "bitmask":
            flags = [e.name for v, e in sorted(table.items()) if v and value & v == v]
            rest = value & ~sum(v for v in table if value & v == v)
            extra = f"，另有未定義的位元 0x{rest:X}" if rest else ""
            return f"{value}（位元旗標：{'、'.join(flags) or '都沒有'}{extra}）"
        entry = table.get(value)
        if entry is None:
            return f"{value}（不在 {fdef.enum} 的定義裡）"
        if fdef.enum == "MAV_SEVERITY":
            return f"{value}：{SEVERITY.get(value, entry.name)}（{entry.name}）"
        return f"{value}：{entry.name}" + (f"（{entry.description}）" if entry.description else "")
    if isinstance(value, list):
        return str(value)
    converted = convert(value, fdef.units) if fdef.units else None
    unit = f" {fdef.units}" if fdef.units else ""
    return f"{value}{unit}" + (f" → {converted}" if converted and fdef.units not in ("%", "m", "s") else "")


def describe_message(msg, index: int, defs: Definitions, dialect: str) -> list[str]:
    name = msg.get_type()
    if name == "BAD_DATA":
        reason = getattr(msg, "reason", "")
        return [f"第 {index} 段：無法解碼的資料（{reason}）；可能是封包損壞、被截斷，或不是 {dialect} 定義的訊息，不翻譯內容"]
    mdef = defs.messages.get(name)
    header = msg.get_header()
    version = "MAVLink 2" if getattr(header, "mlen", None) is not None and msg.get_msgbuf()[:1] == b"\xfd" else "MAVLink 1"
    lines = [(f"第 {index} 則：{name}（訊息編號 {msg.get_msgId()}），來自系統 {msg.get_srcSystem()}、"
              f"元件 {msg.get_srcComponent()}，序號 {msg.get_seq()}，{version}")]
    chinese = MESSAGES.get(name)
    if chinese:
        lines.append(f"  意思：{chinese[0]}")
    if mdef and mdef.description:
        lines.append(f"  官方定義原文：{mdef.description}")
    fields_zh = chinese[1] if chinese else {}
    command = defs.enums.get("MAV_CMD", {}).get(msg.command) if name in ("COMMAND_LONG", "COMMAND_INT") else None
    for fname in msg.get_fieldnames():
        if command is not None and re.fullmatch(r"param[1-7]", fname):
            continue  # told below with what this command means by each parameter
        fdef = mdef.fields.get(fname) if mdef else None
        if fdef is None:
            lines.append(f"  - {fname} = {getattr(msg, fname)}")
            continue
        meaning = fields_zh.get(fname, (None, None))[0]
        label = f"{fname}（{meaning}）" if meaning else fname
        text = describe_value(msg, fname, fdef, defs)
        line = f"  - {label} = {text}"
        if not meaning and fdef.description:
            line += f"；官方說明：{fdef.description}"
        lines.append(line)
    if name == "HEARTBEAT":
        from pymavlink import mavutil

        mode = mavutil.mode_string_v10(msg)
        armed = "已解鎖（馬達可以轉）" if msg.base_mode & 128 else "未解鎖"
        lines.append(f"  飛行模式：{mode}（依 pymavlink 對 {defs.enums['MAV_AUTOPILOT'][msg.autopilot].name}、"
                     f"{defs.enums['MAV_TYPE'][msg.type].name} 的對照表）；{armed}")
    if command is not None:
        if command.name in COMMANDS:
            lines.append(f"  指令的意思：{COMMANDS[command.name]}")
        for i in range(1, 8):
            fname = f"param{i}" if name == "COMMAND_LONG" or i < 5 else {5: "x", 6: "y", 7: "z"}[i]
            value = getattr(msg, fname, None)
            meaning = command.params.get(i, "")
            if not meaning or meaning.lower().startswith("empty"):
                lines.append(f"  - {fname} = {value}（這個指令沒用到）")
            else:
                lines.append(f"  - {fname} = {value}；官方說明：{meaning}")
    return lines


def parse_hex(text: str) -> bytes:
    cleaned = re.sub(r"0x|[\s,:]", "", text)
    if len(cleaned) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", cleaned):
        raise ValueError("不是十六進位的位元組（每兩個字元一個位元組）")
    return bytes.fromhex(cleaned)


def translate(data: bytes | str, dialect: str = "ardupilotmega") -> Translation:
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}")
    raw = parse_hex(data) if isinstance(data, str) else data
    mod = module(dialect)
    parser = mod.MAVLink(None)
    parser.robust_parsing = True
    messages = parser.parse_buffer(raw) or []
    defs = definitions(dialect)
    lines = [f"MAVLink（定義：{dialect}.xml，pymavlink 解碼並檢查校驗碼）"]
    if not messages:
        lines.append("沒有解碼出任何完整的訊息（資料不完整、校驗碼不對，或不是 MAVLink）")
    for index, msg in enumerate(messages, 1):
        lines += describe_message(msg, index, defs, dialect)
    return Translation(lines)
