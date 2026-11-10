"""The shipped comparator prompt must be the same-claim prompt.

Pins the rules that the 2026-11-09 A/B replication showed matter (see
.ziya/memory-update-audit-plan.md): the old prompt merged by topic and
destroyed a distinct fact in 31 of 46 UPDATEs; the same-claim prompt made 0
destructive UPDATEs in 750 judged decisions.  A future edit that restores
"more complete version -> UPDATE" or "same topic -> UPDATE" fails here.

Also asserts the shipped prompt and the A/B arm file have not drifted apart,
so a re-run of the sim's arm B still measures what ships.
"""
import re
from pathlib import Path

from app.memory.comparator import COMPARE_PROMPT

_ARM_B = Path(__file__).resolve().parent.parent / "scripts" / "comparator_prompts" / "arm_b_same_claim.txt"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\\\n", "")).strip()


def test_prompt_requires_same_claim_not_same_topic():
    p = COMPARE_PROMPT
    assert "SAME CLAIM" in p
    assert "Same topic, different claim -> ADD" in p
    assert "different layer label" in p and "never the" in p
    assert "When unsure whether two claims are the same, choose ADD" in p


def test_prompt_drops_the_topic_merge_rules():
    p = COMPARE_PROMPT
    assert "more complete version of an existing memory, choose UPDATE" not in p
    assert "addresses the same topic, prefer UPDATE" not in p
    # A broader NEW that drops specifics is explicitly NOT an update.
    assert "drops specifics" in p and "is NOT an" in p


def test_shipped_prompt_matches_arm_b_file():
    assert _norm(COMPARE_PROMPT) == _norm(_ARM_B.read_text())
