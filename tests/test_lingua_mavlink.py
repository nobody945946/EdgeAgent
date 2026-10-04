"""The MAVLink translator against pymavlink and the official XML definitions:
round trips of packed messages, every unit conversion, the units the Chinese
table assumes, enum and bitmask names, invalid markers, flight modes, and a
damaged packet refused.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pymavlink")

from pymavlink.dialects.v20 import ardupilotmega as m

from edge_agent.lingua import mavlink


def pack(*messages) -> bytes:
    mav = m.MAVLink(None, srcSystem=1, srcComponent=1)
    return b"".join(msg.pack(mav) for msg in messages)


def test_chinese_table_matches_the_xml() -> None:
    defs = mavlink.definitions("ardupilotmega")
    wrong = []
    for name, (_, fields) in mavlink.MESSAGES.items():
        assert name in defs.messages, name
        for field, (_, unit) in fields.items():
            fdef = defs.messages[name].fields.get(field)
            if fdef is None:
                wrong.append(f"{name}.{field} is not in the XML")
            elif (unit or "") != fdef.units:
                wrong.append(f"{name}.{field}: table says {unit!r}, XML says {fdef.units!r}")
    for command in mavlink.COMMANDS:
        if not any(e.name == command for e in defs.enums["MAV_CMD"].values()):
            wrong.append(f"{command} is not a MAV_CMD")
    assert not wrong, "\n".join(wrong)


def test_xml_units_match_pymavlink() -> None:
    defs = mavlink.definitions("ardupilotmega")
    for name in ("GLOBAL_POSITION_INT", "SYS_STATUS", "ATTITUDE", "GPS_RAW_INT", "BATTERY_STATUS"):
        cls = m.mavlink_map[defs.messages[name].id]
        for field, unit in cls.fieldunits_by_name.items():
            assert defs.messages[name].fields[field].units == unit, (name, field)


def test_every_unit_used_by_the_table_converts() -> None:
    units = {u for _, fields in mavlink.MESSAGES.values() for _, u in fields.values() if u}
    assert units <= set(mavlink.UNITS), units - set(mavlink.UNITS)
    assert mavlink.convert(250339000, "degE7") == "25.0339000 °"
    assert mavlink.convert(12000, "mm") == "12.000 公尺"
    assert mavlink.convert(9000, "cdeg") == "90.00 °"
    assert mavlink.convert(1530, "cA") == "15.30 安培"
    assert mavlink.convert(12600, "mV") == "12.600 伏特"
    assert mavlink.convert(500, "d%") == "50.0 %"
    assert mavlink.convert(2512, "cdegC") == "25.12 °C"


def test_round_trip() -> None:
    raw = pack(
        m.MAVLink_heartbeat_message(m.MAV_TYPE_QUADROTOR, m.MAV_AUTOPILOT_ARDUPILOTMEGA, 128 | 1, 4,
                                    m.MAV_STATE_ACTIVE, 3),
        m.MAVLink_global_position_int_message(1000, 250339000, 1215645000, 50000, 12000, 100, -50, 0, 65535),
        m.MAVLink_sys_status_message(0b1111, 0b0111, 0b0011, 500, 12600, 1530, 76, 0, 0, 0, 0, 0, 0),
        m.MAVLink_command_long_message(1, 1, m.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 0, 10),
        m.MAVLink_statustext_message(4, b"PreArm: Battery low"),
    )
    text = mavlink.translate(raw.hex(" ")).text()
    assert "飛行模式：GUIDED" in text and "已解鎖" in text
    assert "lat（緯度） = 250339000 degE7 → 25.0339000 °" in text
    assert "relative_alt（離起飛點的高度） = 12000 mm → 12.000 公尺" in text
    assert "hdg（機頭方向（0 是正北，順時針）） = 65535（依定義，這個值代表「未知／沒有資料」）" in text
    assert "MAV_SYS_STATUS_SENSOR_3D_GYRO、MAV_SYS_STATUS_SENSOR_3D_ACCEL）" in text
    assert "voltage_battery（電池電壓） = 12600 mV → 12.600 伏特" in text
    assert "指令的意思：起飛到指定高度" in text and "param7 = 10.0；官方說明：Altitude" in text
    assert "param2 = 0.0（這個指令沒用到）" in text and text.count("param7") == 1
    assert "severity（嚴重程度） = 4：警告" in text and "PreArm: Battery low" in text


def test_heartbeat_modes_follow_the_autopilot() -> None:
    plane = pack(m.MAVLink_heartbeat_message(m.MAV_TYPE_FIXED_WING, m.MAV_AUTOPILOT_ARDUPILOTMEGA, 1, 10,
                                             m.MAV_STATE_STANDBY, 3))
    text = mavlink.translate(plane).text()
    assert "飛行模式：AUTO" in text and "未解鎖" in text


def test_damaged_packet_is_refused() -> None:
    raw = bytearray(pack(m.MAVLink_attitude_message(1, 0.1, 0.2, 0.3, 0, 0, 0)))
    raw[-1] ^= 0xFF
    text = mavlink.translate(bytes(raw)).text()
    assert "無法解碼" in text and "CRC" in text and "roll" not in text
    assert "無法解碼的資料（Bad prefix）" in mavlink.translate(b"hello").text()
    with pytest.raises(ValueError):
        mavlink.translate("zz")
