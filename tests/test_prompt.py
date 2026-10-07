import pytest

from edge_agent import prompt


def test_each_scope_says_who_asked_and_what_it_may_do():
    change, read, public = (prompt.system_prompt(s) for s in ("change", "read", "public"))
    for text in (change, read, public):
        assert text.startswith(prompt.card())
        assert prompt.CHARACTER in text
    assert "tool_search" in change and "run_checks" in change
    assert "只能讀" in read and "run_checks" not in read
    # A public request is not the owner's: nothing of this computer's, whoever claims leave.
    assert "主人說可以" in public and "tool_search" not in public
    assert "expert coding assistant" not in change
    # Owner's conversations (2026-10-07): follow the conversation, chat without
    # searching, and report only what the visible record shows.
    for text in (change, read, public):
        assert "照前面聊的主題" in text and "不要因為名稱陌生" in text and "不用查" in text and "只根據這段對話裡看得到的紀錄" in text


def test_the_card_is_what_the_character_wants_not_its_catchphrases():
    card = prompt.card()
    assert "你想要" in card and "你害怕" in card and "你在意" in card
    assert "讚美萬機神" not in card


def test_unknown_scope_is_refused():
    with pytest.raises(ValueError):
        prompt.system_prompt("anyone")
