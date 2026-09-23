"""
Living handoff draft + context-pressure nudge (design/conversation-handoff.md,
build-order step 3).

Prelude instruction states, asserted on the assembled prelude text (the
surface the model sees), against a real ChatStorage on tmp_path:

  - no draft, low pressure            → nothing
  - no draft, pressure ≥ threshold    → nudge, exactly at the threshold
  - draft present                     → draft + per-turn merge instruction,
                                        and NO nudge even under pressure
  - continuation child, no draft      → inherited prelude only, no drafting
                                        instruction (the user asked for this)
  - continuation child under pressure → inherited + nudge

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

    def test_nudge_appears_exactly_at_threshold(self, storage, monkeypatch):
        monkeypatch.delenv("ZIYA_HANDOFF_NUDGE_RATIO", raising=False)
        _write(storage, "a")
        assert build_handoff_prelude("a", storage=storage, pressure=0.69) == ""
        out = build_handoff_prelude("a", storage=storage, pressure=0.70)
        assert "[Context pressure]" in out
        # No percentage in the text: it must be byte-stable across turns
        # (see TestCacheStability).  The figure is available via handoff_read.
        assert "%" not in out
        assert "handoff_write" in out
        assert "Do not write a handoff to a file" in out
        # The nudge must not carry an inherited-doc hint on a plain source.
        assert "inherited handoff above" not in out

    def test_threshold_is_configurable(self, storage, monkeypatch):
        monkeypatch.setenv("ZIYA_HANDOFF_NUDGE_RATIO", "0.5")
        _write(storage, "a")
        assert "[Context pressure]" in build_handoff_prelude("a", storage=storage, pressure=0.5)
        monkeypatch.setenv("ZIYA_HANDOFF_NUDGE_RATIO", "garbage")
        assert build_handoff_prelude("a", storage=storage, pressure=0.5) == ""

    def test_nudge_uses_cached_pressure_from_previous_turn(self, storage):
        _write(storage, "a")
        record_context_pressure("a", tokens=150_000, limit=200_000)
        assert get_context_pressure("a") == pytest.approx(0.75)
        assert "[Context pressure]" in build_handoff_prelude("a", storage=storage)
        # A different conversation is unaffected.
        _write(storage, "b")
        assert build_handoff_prelude("b", storage=storage) == ""

    def test_draft_present_gives_merge_instruction_and_suppresses_nudge(self, storage):
        _write(storage, "a", handoffDraft=_draft())
        out = build_handoff_prelude("a", storage=storage, pressure=0.95)
        assert "[Handoff draft — living]" in out
        assert "mode='merge'" in out
        assert "Finish the BFD root cause." in out
        assert "[Context pressure]" not in out

    def test_empty_draft_document_is_not_a_draft(self, storage):
        _write(storage, "a", handoffDraft=_draft(document="   "))
        out = build_handoff_prelude("a", storage=storage, pressure=0.9)
        assert "[Handoff draft" not in out
        assert "[Context pressure]" in out

    def test_user_edit_is_attributed_in_prelude(self, storage):
        _write(storage, "a", handoffDraft=_draft(editedAt=1_700_000_500_000))
        out = build_handoff_prelude("a", storage=storage)
        assert "edited by the user" in out

    def test_continuation_child_without_draft_gets_no_drafting_instruction(self, storage):
        _write(storage, "src", title="seg 1")
        _write(storage, "cont", branchedFrom="src", lineageKind="handoff",
               handoff={"document": "Objective: continue.", "generatedAt": 1, "sourceMessageCount": 3})
        out = build_handoff_prelude("cont", storage=storage, pressure=0.2)
        assert "[Conversation handoff]" in out
        assert "Objective: continue." in out
        assert "handoff_write" not in out
        assert "[Handoff draft" not in out
        assert "[Context pressure]" not in out

    def test_continuation_child_under_pressure_gets_inherited_plus_nudge(self, storage):
        _write(storage, "src", title="seg 1")
        _write(storage, "cont", branchedFrom="src", lineageKind="handoff",
               handoff={"document": "Objective: continue.", "generatedAt": 1, "sourceMessageCount": 3})
        out = build_handoff_prelude("cont", storage=storage, pressure=0.8)
        assert out.index("[Conversation handoff]") < out.index("[Context pressure]")
        assert "inherited handoff above" in out

    def test_missing_record_is_silent(self, storage):
        record_context_pressure("ghost", 190_000, 200_000)
        assert build_handoff_prelude("ghost", storage=storage) == ""


# ── pressure measurement ──────────────────────────────────────────

class TestPressure:
    def test_estimate_handles_string_and_block_content(self):
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

    def test_draft_and_nudge_never_enter_the_system_part(self, storage):
        _write(storage, "a", handoffDraft=_draft())
        sys_part, turn = hp.split_handoff_prelude("a", storage=storage)
        assert sys_part == ""
        assert "[Handoff draft — living]" in turn
        assert append_handoff_prelude("[skill]", "a", storage=storage) == "[skill]"

        _write(storage, "b")
        sys_part, turn = hp.split_handoff_prelude("b", storage=storage, pressure=0.9)
        assert sys_part == ""
        assert "[Context pressure]" in turn

    def test_nudge_text_is_identical_across_pressures(self, storage):
        _write(storage, "a")
        _, t1 = hp.split_handoff_prelude("a", storage=storage, pressure=0.71)
        _, t2 = hp.split_handoff_prelude("a", storage=storage, pressure=0.93)
        assert t1 == t2

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
