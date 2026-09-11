"""
The task_cards skill prompt must document EVERY block type the runtime accepts.

Motivating defect: the shipped prompt said "Six block shapes compose the
tree", documented eight, and the runtime accepted nine -- ``ask`` was absent
from the prose entirely while being present in ``Block.block_type``'s Literal.

That gap is not cosmetic.  The prompt IS the model's only description of the
grammar, so an undocumented block type is an unreachable feature: asked for a
human approval step, the model cannot author the block that implements one and
instead simulates a gate by handing a task a ``{{var.…}}`` flag and instructing
it to behave -- which leaves the guarded stage's write grant live regardless of
what the human answered.  A permission gate degraded into a polite request.

These tests assert the SEAM (the Literal on the model <-> the prose in the
skill) rather than either half, because both halves were individually correct
when the defect shipped.
"""
import re

import pytest

# Number words the grammar preamble may legitimately use, so the declared
# count is checked against reality rather than merely being present.
_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}


def _runtime_block_types():
    """The block types the executor actually accepts, from the model."""
    from typing import get_args
    from app.models.task_card import Block

    annotation = Block.model_fields["block_type"].annotation
    args = get_args(annotation)
    assert args, (
        "could not read the Literal on Block.block_type; this test derives "
        "the expected block types from the model on purpose -- if the field "
        "shape changed, update this helper rather than hardcoding a list"
    )
    return tuple(str(a) for a in args)


def _task_cards_prompt() -> str:
    from app.data.built_in_skills import BUILT_IN_SKILLS

    matches = [s for s in BUILT_IN_SKILLS if s["id"] == "task_cards"]
    assert len(matches) == 1, (
        f"expected exactly one task_cards skill, found {len(matches)}"
    )
    return matches[0]["prompt"]


def _task_cards_skill() -> dict:
    from app.data.built_in_skills import BUILT_IN_SKILLS

    return [s for s in BUILT_IN_SKILLS if s["id"] == "task_cards"][0]


class TestEveryBlockTypeIsDocumented:
    """Positive control first: the helper must find a real, non-trivial set."""

    def test_runtime_exposes_the_block_types_we_think_it_does(self):
        """Guards the other assertions from vacuously passing.

        If ``_runtime_block_types`` silently returned an empty tuple, every
        membership test below would pass while checking nothing.
        """
        types = _runtime_block_types()
        assert len(types) >= 8, f"suspiciously few block types: {types}"
        assert "task" in types and "ask" in types, (
            f"expected at least task and ask among block types, got {types}"
        )

    @pytest.mark.parametrize("block_type", _runtime_block_types())
    def test_block_type_has_a_documented_section(self, block_type):
        """Each accepted block type needs a ``**Name**`` section in the prose.

        Anchored on the bolded section marker rather than a bare word so an
        incidental mention ("a task card", "ask the user") cannot satisfy it --
        that leniency is what let ``ask`` appear absent while the word occurred
        six times in the file.
        """
        prompt = _task_cards_prompt()
        marker = f"**{block_type.capitalize()}**"
        assert marker in prompt, (
            f"block type {block_type!r} is accepted by the runtime "
            f"(Block.block_type) but has no {marker} section in the "
            f"task_cards skill prompt. An undocumented block type is one the "
            f"model cannot author."
        )

    @pytest.mark.parametrize("block_type", _runtime_block_types())
    def test_block_type_appears_in_a_json_example(self, block_type):
        """The section must show the literal wire value, not just describe it.

        A section that names a block in prose without ever spelling
        ``"block_type": "ask"`` leaves the model guessing the discriminator.
        """
        prompt = _task_cards_prompt()
        assert f'"block_type": "{block_type}"' in prompt, (
            f'no example spells \'"block_type": "{block_type}"\' in the '
            f"task_cards prompt; the model has to infer the wire value"
        )


class TestDeclaredShapeCountIsTrue:
    def test_preamble_count_matches_the_runtime(self):
        """"N block shapes compose the tree" must state the real N.

        The shipped prompt said "Six" while the runtime accepted nine. A wrong
        count is worse than none: it tells the model the list it just read is
        complete.
        """
        prompt = _task_cards_prompt()
        expected = len(_runtime_block_types())
        m = re.search(r"(\w+)\s+block shapes compose the tree", prompt)
        assert m, (
            "could not find the 'N block shapes compose the tree' preamble; "
            "if it was reworded, update this assertion to follow it"
        )
        declared = m.group(1).strip().lower()
        want = _NUMBER_WORDS.get(expected, str(expected))
        assert declared in (want, str(expected)), (
            f"the grammar preamble declares {declared!r} block shapes but the "
            f"runtime accepts {expected}. Update the preamble when a block "
            f"type is added."
        )

    def test_leaf_list_names_every_bodyless_block(self):
        """The 'which blocks are leaves' aside must not omit any leaf.

        task/state/call/ask all take no body. The shipped text named only
        task and state, implying call and ask could nest children.
        """
        prompt = _task_cards_prompt()
        m = re.search(r"except ([^)]*?), which are leaves", prompt, re.S)
        assert m, (
            "could not find the 'except ..., which are leaves' aside; if it "
            "was reworded, update this assertion to follow it"
        )
        named = m.group(1).lower()
        for leaf in ("task", "state", "call", "ask"):
            assert leaf in named, (
                f"{leaf!r} takes no body but is not named in the leaf aside "
                f"({m.group(1)!r}); omitting it implies it can nest children"
            )


class TestAskSemanticsThatChangeAuthoring:
    """Facts about Ask that alter how a card is built, not mere description.

    Each of these was learned from the executor source and would have changed
    the structure of a card authored without it, so each is worth pinning.
    """

    def test_rejection_is_documented_as_a_failure_not_a_branch(self):
        """``decision == "reject"`` returns a failed Artifact.

        This is THE load-bearing fact: because reject fails, ``on_failure:
        "stop"`` on the enclosing container is what makes a rejection prevent
        the guarded stage. Without knowing it, an author reaches for an
        instructed variable check instead.
        """
        prompt = _task_cards_prompt().lower()
        assert "rejection is a failure" in prompt or (
            "reject" in prompt and "failed artifact" in prompt
        ), (
            "the prompt does not state that an Ask rejection yields a FAILED "
            "artifact; without that, on_failure:'stop' as the gating "
            "mechanism is not derivable and the model will invent an "
            "instructed variable check instead"
        )

    def test_on_failure_stop_is_named_as_the_gating_mechanism(self):
        prompt = _task_cards_prompt()
        assert "on_failure" in prompt and re.search(
            r"gat\w+ a privileged|gating a privileged", prompt, re.I
        ), (
            "the prompt never connects Ask to on_failure:'stop' as the way to "
            "gate a privileged stage; that connection is the whole point"
        )

    def test_instructed_gating_is_explicitly_warned_against(self):
        """The wrong pattern must be named, not merely omitted.

        A model that has already reached for the instructed-flag pattern will
        keep reaching for it unless the prompt rules it out.
        """
        prompt = _task_cards_prompt()
        assert re.search(r"do not gate|Do NOT gate", prompt), (
            "the prompt does not warn against gating by instructing a task to "
            "check a {{var}} flag, which is the failure mode an author falls "
            "into when Ask's failure semantics are unclear"
        )

    def test_ask_variable_binding_is_documented(self):
        prompt = _task_cards_prompt()
        assert "ask_variable" in prompt and "{{var." in prompt, (
            "ask_variable binds the answer for {{var.NAME}} downstream; the "
            "prompt must say so or the field is unusable"
        )

    def test_ask_question_and_choices_are_documented(self):
        prompt = _task_cards_prompt()
        for field in ("ask_question", "ask_choices"):
            assert field in prompt, (
                f"{field} is a real Ask field but is undocumented"
            )

    def test_scheduled_runs_hold_at_an_ask_rather_than_failing(self):
        """Distinguishes Ask from an unsigned escalation under cron.

        A headless run FAILS on an unauthorized escalation but HOLDS at an
        Ask. Conflating the two makes a human-gated scheduled card look
        impossible, so the prompt has to separate them.
        """
        prompt = _task_cards_prompt().lower()
        assert "headless" in prompt and "hold" in prompt, (
            "the prompt does not explain that a scheduled (headless) run "
            "holds at an Ask instead of failing the way an unsigned "
            "escalation does; without it a human-gated cron card looks "
            "impossible to author"
        )


class TestSkillIsDiscoverableForApprovalRequests:
    """The skill has to LOAD for the phrasing users actually use.

    Correct prose in a skill that never activates is not a fix.
    """

    @pytest.mark.parametrize("phrase", [
        "approval", "checkpoint", "human in the loop", "gate",
    ])
    def test_approval_phrasings_are_keywords(self, phrase):
        keywords = [k.lower() for k in _task_cards_skill().get("keywords", [])]
        assert phrase in keywords, (
            f"{phrase!r} is not a task_cards keyword, so a request phrased "
            f"that way may not surface the skill that documents Ask"
        )

    def test_catalog_description_mentions_the_gate(self):
        """The one-line catalog entry is what the model routes on first."""
        desc = _task_cards_skill().get("catalog_description", "")
        assert "Ask" in desc, (
            f"catalog_description does not mention Ask: {desc!r}. It lists "
            "the block shapes, so omitting Ask hides the gate at the exact "
            "moment the model is deciding whether this skill is relevant."
        )
