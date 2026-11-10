"""The task_cards skill must not teach the hand-built JSON store pattern.

Its "run blackboard" recipe was ``file_read .ziya/<card>-state.json ...
file_write the file back``; every card that filled .ziya/ with gigabytes of
derived JSON was an elaboration of it.  The recipe now has to point at an
append-only JSONL log read by ``analyze``, and the card validator's warning
must not fire on the skill's own example.  Fails until the skill text is
updated.
"""
import re

from app.data.built_in_skills import BUILT_IN_SKILLS
from app.models.task_card import Block
from app.utils.task_card_validation import validate_card_tree


def _skill(skill_id):
    return next(s for s in BUILT_IN_SKILLS if s["id"] == skill_id)


def _blackboard_section(prompt: str) -> str:
    start = prompt.index("## Carrying state between iterations")
    end = prompt.index("## Long-running commands", start)
    return prompt[start:end]


def test_blackboard_recipe_uses_jsonl_and_analyze_not_a_state_object():
    sec = _blackboard_section(_skill("task_cards")["prompt"])
    assert "-state.json" not in sec
    assert "file_write" not in sec.split("```")[1]  # the example instructions
    assert ".jsonl" in sec and "analyze(" in sec
    assert "store_output" in sec
    assert re.search(r"[Nn]ever (rewrite|copy)", sec)


def test_recipes_example_instructions_pass_the_validator_clean():
    sec = _blackboard_section(_skill("task_cards")["prompt"])
    example = sec.split("```")[1]
    # Strip the JSON-string escaping the doc uses so the text reads as an
    # author would paste it into a card.
    instr = example.split("instructions\":", 1)[1].strip().strip('"').replace('\\\\"', '"')
    res = validate_card_tree(Block(block_type="task", id="t", name="n", instructions=instr))
    assert not [w for w in res.warnings if "hand-built JSON store" in w.message], res.warnings


def test_statistical_skill_catalog_entry_leads_with_analysis():
    s = _skill("statistical_charts")
    assert "analy" in s["catalog_description"].lower()
    assert "analyze" in s["description"]
