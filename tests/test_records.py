"""The user's records folder (records.py): prepared, paused and resumed."""

from __future__ import annotations

import pytest

from edge_agent import records


def test_records_prepare_and_status_follow_folder_availability(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(OSError):
        records.prepare_record_folder(str(missing))
    assert not missing.exists()
    assert records.recording_status(False, str(tmp_path)) == "關閉"
    assert not records.save_session(str(tmp_path), "fixture", [])
    records.prepare_record_folder(str(tmp_path))
    assert records.recording_status(True, str(tmp_path)) == "記錄中"
    assert records.save_session(str(tmp_path), "fixture", [])
    (tmp_path / records.MARKER).unlink()
    assert records.recording_status(True, str(tmp_path)).startswith("暫停")
    assert not records.save_session(str(tmp_path), "missing-marker", [])
    assert not (tmp_path / "edgeagent" / "missing-marker.jsonl").exists()


def test_records_accept_a_local_ssd_folder(tmp_path):
    assert records.prepare_record_folder(str(tmp_path)) == tmp_path.resolve()
    assert records.save_session(
        str(tmp_path), "fixture", [{"type": "fixture", "text": "LOCAL_SSD_FIXTURE"}]
    )
    assert "LOCAL_SSD_FIXTURE" in (tmp_path / "edgeagent" / "fixture.jsonl").read_text(
        encoding="utf-8"
    )
    (tmp_path / records.MARKER).unlink()
    assert not records.save_session(str(tmp_path), "paused", [{"text": "PAUSED_FIXTURE"}])
    assert records.recording_status(True, str(tmp_path)).startswith("暫停")
    records.prepare_record_folder(str(tmp_path))
    assert records.recording_status(True, str(tmp_path)) == "記錄中"
    assert records.save_session(str(tmp_path), "resumed", [{"text": "RESUMED_FIXTURE"}])
    assert sorted(path.name for path in (tmp_path / "edgeagent").iterdir()) == [
        "fixture.jsonl", "resumed.jsonl"
    ]


def test_records_reject_a_file(tmp_path):
    file = tmp_path / "fixture.txt"
    file.write_text("fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="紀錄資料夾"):
        records.prepare_record_folder(str(file))
    assert not (tmp_path / records.MARKER).exists()
