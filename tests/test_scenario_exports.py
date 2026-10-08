"""bench/scenario_exports.py: the owner's exported conversations as cards, without a model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import scenario_cards  # noqa: E402
import scenario_exports  # noqa: E402

EXPORT = """﻿EdgeAgent 對話紀錄
匯出時間：2026/10/8 上午9:25:50

【狀態】
已開啟 Workspace

【使用者】
你測試以後告訴我，開啟 vscode 的指令怎麼寫

【工具】
失敗 · run_checks
{
  "command": "code --new-window"
}
The operation was aborted

【批准】
已批准 · run_checks

【Agent】
指令是 code --new-window。
"""


def test_an_export_reads_as_who_said_what() -> None:
    text = scenario_exports.transcript(EXPORT)
    assert text.splitlines()[0] == "使用者：你測試以後告訴我，開啟 vscode 的指令怎麼寫"
    assert "（工具 失敗 · run_checks：" in text and "助理：指令是 code --new-window。" in text
    assert "已開啟 Workspace" not in text


def test_conversations_become_a_card_and_variants_or_a_held_out_card(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scenario_cards, "OUT", tmp_path)
    monkeypatch.setattr(scenario_exports.run, "Server", lambda *a, **k: SimpleNamespace(
        port=0, parallel=2, close=lambda: None))
    monkeypatch.setattr(scenario_exports.run, "close_on_term", lambda *a: None)

    def ask(port: int, prompt: str, max_tokens: int = 6000) -> str:
        return json.dumps({"family": "I", "persona": "急性子的工程師", "opening": "直接丟需求",
                           "goal": "拿到測過的指令", "plan": ["要求測過再給", "指出它其實成功了"],
                           "must": ["真的執行並回報結果"], "must_not": ["和使用者的觀察爭辯"], "max_turns": 4})

    monkeypatch.setattr(scenario_cards, "ask", ask)
    exports = []
    for n in range(8):  # enough that both splits occur
        path = tmp_path / f"export-{n}.txt"
        path.write_text(EXPORT + f"\n【使用者】\n第 {n} 場\n", encoding="utf-8")
        exports.append(path)
    monkeypatch.setattr(sys, "argv", ["scenario_exports", "--config", "teacher-38-iq3", "--variants", "2",
                                      *map(str, exports)])
    scenario_exports.main()
    train = [json.loads(line) for line in (tmp_path / "train-cards.jsonl").read_text().splitlines()]
    evaluation = [json.loads(line) for line in (tmp_path / "eval-cards.jsonl").read_text().splitlines()]
    held = [p for p in exports if scenario_exports.held_out(p.read_bytes())]
    assert len(evaluation) == len(held) > 0 and len(train) == 3 * (len(exports) - len(held))
    assert all(c["source"]["variant_of"] is None for c in evaluation)
    assert all(c["id"].split("-")[1] == "I" and c["source"]["from"] == "owner" for c in train + evaluation)
    assert not {c["source"]["export"] for c in train} & {c["source"]["export"] for c in evaluation}
    scenario_exports.main()  # resumes: nothing twice
    assert len((tmp_path / "train-cards.jsonl").read_text().splitlines()) == len(train)
