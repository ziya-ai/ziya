"""
Living handoff draft prelude (design/conversation-handoff.md, build-order
step 3).

Prelude instruction states, asserted on the assembled prelude text (the
surface the model sees), against a real ChatStorage on tmp_path:

  - no draft, below threshold         → nothing
  - no draft, at/above threshold      → a SILENT instruction to write the
                                        draft this turn: no percentage, no
                                        "context" language, an explicit "do
                                        not mention this to the user"
  - draft present                     → draft + per-turn merge instruction,
                                        and no mention of context size
  - continuation child, no draft      → inherited prelude only, no drafting
                                        instruction (the user asked for this)

Replaced 2026-10-10: the original nudge fired from 70% and said "a
suggestion to raise with the user", so every long conversation turned into
"the context is getting full, let's wrap up".  The threshold is now late
(0.9) and the text is an instruction to the model, never a message for the
user.  The pressure figure itself reaches only the drawer.

Plus merge_draft, which handoff_write uses: section merge keeps unnamed
sections, replace supersedes, and a user edit survives a merge.
"""
import json
import time

import pytest

from app.storage.chats import ChatStorage
from app.utils import handoff_prelude as hp
from app.utils.handoff_prelude import (
    build_handoff_prelude, merge_draft, parse_sections, render_sections,
    record_context_pressure, get_context_pressure, estimate_messages_tokens,
    append_handoff_prelude,
)


@pytest.fixture
def storage(tmp_path):
    proj = tmp_path / "proj"
    (proj / "chats").mkdir(parents=True)
    return ChatStorage(proj)


@pytest.fixture(autouse=True)
def _clean_pressure():
    hp.clear_pressure_cache()
    yield
    hp.clear_pressure_cache()


def _write(storage, chat_id, **extra):
    now = int(time.time() * 1000)
    rec = {"id": chat_id, "title": f"chat {chat_id}", "messages": [],
           "createdAt": now, "lastActiveAt": now, "_version": now}
    rec.update(extra)
    storage._write_json(storage._chat_file(chat_id), rec)


def _draft(document="## Objective\nFinish the BFD root cause.", **over):
    d = {"document": document, "generatedAt": 1_700_000_000_000,
         "updatedAt": 1_700_000_000_000, "editedAt": None, "sourceMessageCount": 40}
    d.update(over)
    return d


# ── instruction states ─────────────────────────────────────────────

class TestStates:
    def test_plain_conversation_gets_nothing(self, storage):
        _write(storage, "a")
        assert build_handoff_prelude("a", storage=storage) == ""
        assert append_handoff_prelude("[skill]", "a", storage=storage) == "[skill]"

    @pytest.mark.parametrize("tokens", [0, 140_000, 150_000, 170_000, 179_999])
    def test_no_draft_below_threshold_means_nothing(self, storage, tokens, monkeypatch):
        """The old nudge fired from 70%; 75-89% must now be silent.  Pressure
        is still recorded (positive control) -- the drawer reads it."""
        monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
        _write(storage, "a")
        record_context_pressure("a", tokens=tokens, limit=200_000)
        assert get_context_pressure("a") == pytest.approx(tokens / 200_000)
        assert build_handoff_prelude("a", storage=storage) == ""

    def test_default_threshold_is_late(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
        assert hp.nudge_ratio() >= 0.9

    def test_at_threshold_the_model_is_told_to_write_the_draft_silently(self, storage, monkeypatch):
        monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
        _write(storage, "a")
        assert build_handoff_prelude("a", storage=storage, pressure=0.89) == ""
        out = build_handoff_prelude("a", storage=storage, pressure=0.90)
        assert "[Handoff draft — start one now]" in out
        assert "handoff_write" in out and "mode='replace'" in out
        # What made the old text harmful: it must not read as a message for
        # the user, must not name the context, and must not change behaviour.
        assert "do not mention the draft, context size, or wrapping up" in out
        assert "Continue the user's request exactly as you otherwise would" in out
        for bad in ("[Context pressure]", "suggestion to raise", "fresh conversation",
                    "%", "context is", "limit"):
            assert bad not in out, bad
        assert not hasattr(hp, "render_pressure_nudge")

    def test_threshold_is_configurable(self, storage, monkeypatch):
        monkeypatch.setenv("ZIYA_HANDOFF_NUDGE_RATIO", "0.5")
        _write(storage, "a")
        assert "start one now" in build_handoff_prelude("a", storage=storage, pressure=0.5)
        monkeypatch.setenv("ZIYA_HANDOFF_NUDGE_RATIO", "garbage")
        assert build_handoff_prelude("a", storage=storage, pressure=0.5) == ""

    def test_instruction_uses_cached_pressure_from_previous_turn(self, storage, monkeypatch):
        monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
        _write(storage, "a")
        record_context_pressure("a", tokens=190_000, limit=200_000)
        assert "start one now" in build_handoff_prelude("a", storage=storage)
        _write(storage, "b")
        assert build_handoff_prelude("b", storage=storage) == ""

    def test_draft_present_gives_merge_instruction_and_never_mentions_context(self, storage):
        _write(storage, "a", handoffDraft=_draft())
        record_context_pressure("a", tokens=198_000, limit=200_000)
        out = build_handoff_prelude("a", storage=storage)
        assert "[Handoff draft — living]" in out
        assert "mode='merge'" in out
        assert "Finish the BFD root cause." in out
        # A draft exists, so the start-one instruction is gone too.
        assert "start one now" not in out
        # The draft prelude must not read as "the conversation is ending".
        assert "do not mention it, context size, or wrapping up" in out
        assert "fresh conversation" not in out
        assert "%" not in out

    def test_empty_draft_document_is_not_a_draft(self, storage, monkeypatch):
        monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
        _write(storage, "a", handoffDraft=_draft(document="   "))
        record_context_pressure("a", tokens=198_000, limit=200_000)
        out = build_handoff_prelude("a", storage=storage)
        assert "[Handoff draft — living]" not in out
        assert "start one now" in out

    def test_user_edit_is_attributed_in_prelude(self, storage):
        _write(storage, "a", handoffDraft=_draft(editedAt=1_700_000_500_000))
        out = build_handoff_prelude("a", storage=storage)
        assert "edited by the user" in out

    def test_continuation_child_without_draft_gets_no_drafting_instruction(self, storage):
        _write(storage, "src", title="seg 1")
        _write(storage, "cont", branchedFrom="src", lineageKind="handoff",
               handoff={"document": "Objective: continue.", "generatedAt": 1, "sourceMessageCount": 3})
        out = build_handoff_prelude("cont", storage=storage)
        assert "[Conversation handoff]" in out
        assert "Objective: continue." in out
        assert "handoff_write" not in out
        assert "[Handoff draft" not in out
        assert "[Context pressure]" not in out

    def test_continuation_child_below_threshold_gets_only_the_inherited_prelude(self, storage, monkeypatch):
        monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
        _write(storage, "src", title="seg 1")
        _write(storage, "cont", branchedFrom="src", lineageKind="handoff",
               handoff={"document": "Objective: continue.", "generatedAt": 1, "sourceMessageCount": 3})
        record_context_pressure("cont", tokens=160_000, limit=200_000)
        out = build_handoff_prelude("cont", storage=storage)
        assert "[Conversation handoff]" in out
        assert "handoff_write" not in out

    def test_continuation_child_at_threshold_drafts_this_segment_only(self, storage):
        _write(storage, "src", title="seg 1")
        _write(storage, "cont", branchedFrom="src", lineageKind="handoff",
               handoff={"document": "Objective: continue.", "generatedAt": 1, "sourceMessageCount": 3})
        out = build_handoff_prelude("cont", storage=storage, pressure=0.95)
        assert out.index("[Conversation handoff]") < out.index("start one now")
        assert "inherited handoff above" in out

    def test_missing_record_is_silent(self, storage):
        record_context_pressure("ghost", 190_000, 200_000)
        assert build_handoff_prelude("ghost", storage=storage) == ""


# ── pressure measurement ──────────────────────────────────────────

class TestPressure:
    def test_estimate_handles_string_and_block_content(self, monkeypatch):
        # Goes through the calibrator (estimate_payload_tokens), so pin the
        # ratio: without this the user's ~/.ziya/token_calibration.json leaks
        # into the assertion.  String content and text blocks both count;
        # the image block does not.
        import app.utils.token_calibrator as tc
        from app.utils.token_calibrator import TokenCalibrator
        cal = TokenCalibrator(cache_file="/dev/null")
        cal.global_by_model["claude"] = 4.0
        monkeypatch.setattr(cal, "_get_current_model_family", lambda: "claude")
        monkeypatch.setattr(tc, "get_token_calibrator", lambda: cal)
        msgs = [{"role": "system", "content": "a" * 400},
                {"role": "user", "content": [{"type": "text", "text": "b" * 400},
                                             {"type": "image", "source": {}}]}]
        assert estimate_messages_tokens(msgs) == 200

    def test_record_ignores_unusable_limit(self):
        record_context_pressure("a", 100, None)
        record_context_pressure("a", 100, 0)
        assert get_context_pressure("a") is None
        assert get_context_pressure(None) is None


# ── merge_draft ──────────────────────────────────────────────────

class TestMergeDraft:
    def test_replace_from_nothing(self):
        d = merge_draft(None, sections={"objective": "Fix X", "state": "half done"},
                        mode="replace", now_ms=5, source_message_count=12)
        assert d["document"] == "## Objective\nFix X\n\n## State\nhalf done"
        assert d["generatedAt"] == 5 and d["updatedAt"] == 5
        assert d["editedAt"] is None and d["sourceMessageCount"] == 12

    def test_merge_touches_only_named_sections(self):
        existing = _draft(document=render_sections(
            {"objective": "Fix X", "decisions": "ruled out BPF", "gotchas": "gate ordering"}))
        d = merge_draft(existing, sections={"decisions": "ruled out BPF; suspect Tunnel Start"},
                        mode="merge", now_ms=9)
        parsed = parse_sections(d["document"])
        assert parsed["decisions"] == "ruled out BPF; suspect Tunnel Start"
        assert parsed["objective"] == "Fix X"
        assert parsed["gotchas"] == "gate ordering"
        assert d["generatedAt"] == existing["generatedAt"]
        assert d["updatedAt"] == 9

    def test_merge_preserves_user_edit_stamp(self):
        existing = _draft(editedAt=123)
        d = merge_draft(existing, sections={"state": "now green"}, mode="merge", now_ms=9)
        assert d["editedAt"] == 123

    def test_replace_clears_user_edit_stamp(self):
        existing = _draft(editedAt=123)
        d = merge_draft(existing, document="## Objective\nnew", mode="replace", now_ms=9)
        assert d["editedAt"] is None

    def test_merge_into_free_form_document_keeps_its_text_as_state(self):
        existing = _draft(document="We are chasing the BFD flap.")
        d = merge_draft(existing, sections={"decisions": "A"}, mode="merge")
        parsed = parse_sections(d["document"])
        assert parsed["state"] == "We are chasing the BFD flap."
        assert parsed["decisions"] == "A"

    def test_merge_with_no_existing_behaves_as_replace(self):
        d = merge_draft(None, sections={"state": "s"}, mode="merge", now_ms=3)
        assert d["document"] == "## State\ns"

    def test_rejects_empty_and_bad_mode(self):
        with pytest.raises(ValueError):
            merge_draft(None, sections={"objective": "  "}, mode="replace")
        with pytest.raises(ValueError):
            merge_draft(None, document="x", mode="upsert")

    def test_unknown_section_keys_are_ignored(self):
        d = merge_draft(None, sections={"objective": "o", "bogus": "z"}, mode="replace")
        assert "bogus" not in d["document"].lower()


# ── cache stability: what goes where ────────────────────────────────

class TestCacheStability:
    """Providers cache the system block.  Anything that changes turn-to-turn
    must therefore NOT be in the system part — it rides on the final user
    message instead.  These tests pin that contract."""

    def test_draft_never_enters_the_system_part(self, storage):
        _write(storage, "a", handoffDraft=_draft())
        sys_part, turn = hp.split_handoff_prelude("a", storage=storage)
        assert sys_part == ""
        assert "[Handoff draft — living]" in turn
        assert append_handoff_prelude("[skill]", "a", storage=storage) == "[skill]"

    def test_draft_prelude_is_identical_across_pressures(self, storage):
        _write(storage, "a", handoffDraft=_draft())
        record_context_pressure("a", tokens=142_000, limit=200_000)
        _, t1 = hp.split_handoff_prelude("a", storage=storage)
        record_context_pressure("a", tokens=196_000, limit=200_000)
        _, t2 = hp.split_handoff_prelude("a", storage=storage)
        assert t1 == t2

    def test_start_instruction_never_enters_the_system_part_and_is_stable(self, storage):
        _write(storage, "b")
        s1, t1 = hp.split_handoff_prelude("b", storage=storage, pressure=0.91)
        s2, t2 = hp.split_handoff_prelude("b", storage=storage, pressure=0.99)
        assert s1 == s2 == ""
        assert "start one now" in t1 and t1 == t2

    def test_system_part_is_stable_across_bead_changes_and_draft_merges(self, storage):
        """A continuation's system part depends only on the inherited doc."""
        _write(storage, "src", messages=[{"id": "m", "role": "human", "content": "x"}])
        _write(storage, "cont", branchedFrom="src", lineageKind="handoff",
               lineageRootId="src",
               handoff={"document": "Objective: X", "generatedAt": 1, "editedAt": None,
                        "sourceMessageCount": 1})
        s1, t1 = hp.split_handoff_prelude("cont", storage=storage)
        # Open a bead on the shared root tree.
        from app.models.bead import Bead, BeadTree
        from app.storage.beads import save_bead_tree
        save_bead_tree(BeadTree(beads=[Bead(id="b1", content="new thread", status="parked")]),
                       chat_storage=storage, conversation_id="cont")
        # Start a draft on the continuation.
        rec = storage._read_json(storage._chat_file("cont"))
        rec["handoffDraft"] = _draft()
        storage._write_json(storage._chat_file("cont"), rec)
        s2, t2 = hp.split_handoff_prelude("cont", storage=storage)
        assert s1 == s2, "system part must not move when beads or the draft change"
        assert "new thread" in t2 and "new thread" not in t1
        assert "[Handoff draft — living]" in t2

    def test_turn_prelude_lands_on_last_user_message_string_and_blocks(self):
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"}]
        assert hp.append_handoff_turn_prelude(msgs, "[Context pressure]\nnudge")
        assert msgs[0]["content"] == "SYS"          # system untouched
        assert msgs[1]["content"] == "q1"           # history untouched
        assert msgs[3]["content"].startswith("q2\n\n" + hp.TURN_PRELUDE_MARKER)
        assert msgs[3]["content"].endswith("nudge")

        blocks = [{"role": "user", "content": [{"type": "image", "source": {}},
                                               {"type": "text", "text": "q"}]}]
        assert hp.append_handoff_turn_prelude(blocks, "T")
        assert blocks[0]["content"][1]["text"].endswith("\n" + "T")
        assert blocks[0]["content"][0] == {"type": "image", "source": {}}

    def test_turn_prelude_noop_when_empty_or_no_user_message(self):
        msgs = [{"role": "system", "content": "SYS"}]
        assert not hp.append_handoff_turn_prelude(msgs, "X")
        assert not hp.append_handoff_turn_prelude([{"role": "user", "content": "q"}], "")
