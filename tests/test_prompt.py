import pytest

from edge_agent import prompt


def test_each_scope_says_who_asked_and_what_it_may_do():
    change, read, public = (prompt.system_prompt(s) for s in ("change", "read", "public"))
    for text in (change, read, public):
        assert text.startswith(prompt.card())
        assert prompt.CHARACTER in text
        # Spec v2: no catalog and no ready-made tools to point at.
        assert "tool_search" not in text and "run_checks" not in text
    assert "只能讀" in read
    # A public request is not the owner's: nothing of this computer's, whoever claims leave.
    assert "主人說可以" in public
    assert "expert coding assistant" not in change
    # Greetings and chat are answered in the Agent's own words (a "no need to look it up"
    # made the base E4B skip research on real questions); data comes from APIs and sources.
    for text in (change, read, public):
        assert "用自己的話回應" in text and "不用查" not in text
        assert "API" in text and "都試過才說查不到" in text


def test_the_working_method_is_the_mature_agents_one():
    """Spec v2 (2026-10-08): act in the same turn, defaults instead of
    questions, verify before reporting, deliver clearly, the owner's
    observations first; said positively, never quoting the wrong behaviour
    (a quoted "please wait" or a list of what needs approval made the base
    E4B, thinking off, stop on a plan or ask first)."""
    change = prompt.system_prompt("change")
    for rule in ("同一輪裡做完", "不用先問", "合理的預設", "看到結果對了才說完成", "完整路徑", "以他的觀察為準", "Start-Process"):
        assert rule in change
    assert "請稍候" not in change and "請確認" not in change


def test_the_card_is_what_the_character_wants_not_its_fears_or_catchphrases():
    card = prompt.card()
    assert "你想要" in card and "你在意" in card
    # The Agent used its fears to excuse not doing the work (owner's conversations, 2026-10-08).
    assert "你害怕" not in card
    assert "讚美萬機神" not in card


def test_unknown_scope_is_refused():
    with pytest.raises(ValueError):
        prompt.system_prompt("anyone")


def test_the_runtime_section_states_the_machine_permissions_and_apis_never_a_key():
    import shutil
    import subprocess

    from edge_agent.runtime import CORE_ROOT

    node = shutil.which("node")
    if node is None:
        pytest.skip("requires Node")
    subprocess.run([node, str(CORE_ROOT / "tests/runtime_facts_check.mjs")], check=True)


def test_the_loop_guard_stops_a_stuck_stream_and_not_long_output():
    """edge_agent/loop_guard.mjs, the Agent's only limit on output length."""
    import shutil
    import subprocess

    from edge_agent.runtime import CORE_ROOT

    node = shutil.which("node")
    if node is None:
        pytest.skip("requires Node")
    subprocess.run([node, str(CORE_ROOT / "tests/loop_guard_check.mjs")], check=True)
