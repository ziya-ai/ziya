"""
The system block is the prompt-cache prefix; anything that varies turn to
turn must live AFTER the cache boundary.

Observed (usage ledger, conv 91acef70): two trivial turns 15 s apart, the
second read 0 tokens from cache because the ~200K system block changed by
137 tokens between turns.  Three known turn-varying pieces lived in it:

  * the "## Message Timing" paragraph, gated on ``chat_history`` (so it
    appeared only from turn 2 on);
  * the bead-check nudge, which appears once ``turn_count >= 3`` and
    disappears when a bead is parked;
  * the memory overview's live "N total memories" / "N on probation" counts.

These tests drive the real ``PrecisionPromptSystem.build_messages`` with
the codebase/template dependencies mocked (same harness as
tests/test_message_timestamps.py) and assert on the OUTERMOST surface: the
system message must not change across turns, and the volatile text must
still be present on the final user message.
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402


class _FakeMsg:
    def __init__(self, type_, content):
        self.type = type_
        self.content = content


class _FakePrompt:
    def format_messages(self, codebase="", tools="", question=""):
        msgs = [_FakeMsg("system", "BASE_SYSTEM_PROMPT")]
        if question:
            msgs.append(_FakeMsg("human", question))
        return msgs


def _build(chat_history, question="current question", **extra_patches):
    from app.utils.precision_prompt_system import PrecisionPromptSystem
    sys_obj = PrecisionPromptSystem()
    patches = [
        mock.patch("app.agents.agent.extract_codebase", return_value=""),
        mock.patch("app.agents.prompts_manager.get_extended_prompt",
                   return_value=_FakePrompt()),
    ]
    for target, value in extra_patches.items():
        patches.append(mock.patch(target, **value))
    with mock.patch.multiple("os", environ=os.environ):
        for p in patches:
            p.start()
        try:
            return sys_obj.build_messages(
                request_path="/api/chat",
                model_info={"model_name": "sonnet4.0", "model_family": "claude",
                            "endpoint": "bedrock", "model_id": "x"},
                files=[],
                question=question,
                chat_history=chat_history,
                conversation_id="test-conv-volatile",
            )
        finally:
            for p in reversed(patches):
                p.stop()


def _history(n_turns):
    hist = []
    for i in range(n_turns):
        hist.append({"type": "human", "content": f"q{i}"})
        hist.append({"type": "ai", "content": f"a{i}"})
    return hist


def _last_user_text(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                return "".join(b.get("text", "") for b in c if isinstance(b, dict))
            return c
    raise AssertionError("no user message")


class TestSystemBlockIsTurnStable:

    def test_turn1_and_turn2_system_prompts_identical(self):
        """The Message Timing paragraph used to appear only from turn 2."""
        t1 = _build([])
        t2 = _build(_history(1))
        assert t1[0]["role"] == "system" and t2[0]["role"] == "system"
        assert t1[0]["content"] == t2[0]["content"]
        assert "## Message Timing" in t1[0]["content"]

    def test_bead_nudge_threshold_does_not_change_system_block(self):
        """turn_count 2 -> 3 flips the bead-check nudge on; the system
        block must not see it."""
        nudge = "\n\n### Bead check: no threads tracked yet. park a bead now."

        def fake_status(turn_count=0):
            return nudge if turn_count >= 3 else ""

        with mock.patch("app.utils.bead_prompt.get_bead_status_summary",
                        side_effect=fake_status), \
             mock.patch("app.utils.bead_prompt.get_bead_directive",
                        return_value="\n\n## Beads directive (static)"):
            before = _build(_history(2))
            after = _build(_history(3))
        assert before[0]["content"] == after[0]["content"]
        assert "Beads directive (static)" in after[0]["content"]
        # Positive: the nudge still reaches the model, on the user turn.
        assert "Bead check" not in after[0]["content"]
        assert "Bead check" not in _last_user_text(before)
        assert _last_user_text(after).endswith(nudge)

    def test_memory_counts_change_does_not_change_system_block(self):
        """Live counts go to the tail; guidance/handles stay in the prefix."""
        stable = "\n\n## Persistent Memory\nguidance\n- **Domain** — `d1`"

        def sections_v1():
            return stable, "\n*10 total memories across 1 domains.*"

        def sections_v2():
            return stable, ("\n*11 total memories across 1 domains.*"
                            "\n*1 memory proposal(s) on probation — no review needed.*")

        with mock.patch("app.memory.prompt.get_memory_prompt_sections",
                        side_effect=sections_v1, create=True):
            a = _build(_history(1))
        with mock.patch("app.memory.prompt.get_memory_prompt_sections",
                        side_effect=sections_v2, create=True):
            b = _build(_history(1))
        assert a[0]["content"] == b[0]["content"]
        assert "## Persistent Memory" in a[0]["content"]
        assert "total memories" not in a[0]["content"]
        assert "10 total memories" in _last_user_text(a)
        assert "11 total memories" in _last_user_text(b)
        assert "on probation" in _last_user_text(b)

    def test_tail_appended_after_question_and_timestamp_tags(self):
        """Appending must not disturb the relocated timestamp tags that
        test_message_timestamps asserts are at the START of the question."""
        nudge = "\n\n### Bead check: x"
        block = ('\n\n## Session Context\n'
                 '<CurrentDateTime value="2026-01-02 03:04:05" />')
        with mock.patch("app.utils.bead_prompt.get_bead_status_summary",
                        return_value=nudge), \
             mock.patch("app.utils.session_context_prompt.build_session_context_section",
                        return_value=block), \
             mock.patch("app.shadow.context.attached_sessions_tag",
                        return_value=""):
            msgs = _build(_history(3), question="the question")
        text = _last_user_text(msgs)
        assert text.startswith('<CurrentDateTime value="2026-01-02 03:04:05" />\nthe question')
        assert text.endswith(nudge)

    def test_no_volatile_pieces_leaves_question_untouched(self):
        """Negative: with nothing volatile, the question is exactly the
        question (plus relocated tags) -- no stray separator appended."""
        with mock.patch("app.utils.bead_prompt.get_bead_status_summary",
                        return_value=""), \
             mock.patch("app.memory.prompt.get_memory_prompt_sections",
                        return_value=("", ""), create=True), \
             mock.patch("app.utils.session_context_prompt.build_session_context_section",
                        return_value="\n\n## Session Context\n"), \
             mock.patch("app.shadow.context.attached_sessions_tag",
                        return_value=""):
            msgs = _build(_history(1), question="just this")
        assert _last_user_text(msgs) == "just this"


class TestFallbackWithoutSplitApi:

    def test_old_single_section_api_still_lands_in_system_block(self):
        """Until app/memory/prompt.py grows get_memory_prompt_sections, the
        combined section is treated as stable (pre-existing behaviour)."""
        import app.memory.prompt as mp
        had = hasattr(mp, "get_memory_prompt_sections")
        saved = getattr(mp, "get_memory_prompt_sections", None)
        try:
            if had:
                delattr(mp, "get_memory_prompt_sections")
            with mock.patch("app.memory.get_memory_prompt_section",
                            return_value="\n\n## Persistent Memory\nALL"):
                msgs = _build(_history(1))
            assert "## Persistent Memory\nALL" in msgs[0]["content"]
        finally:
            if had:
                setattr(mp, "get_memory_prompt_sections", saved)
