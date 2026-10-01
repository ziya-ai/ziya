"""Unit tests for app/utils/chat_message_ops.py (id-merge + message ops).

See design/conversation-single-writer.md, "Turn authorship" and the
2026-09-25 shrink-path audit.  The endpoint/bulk-sync seams are covered in
tests/test_chat_message_endpoints.py.
"""
import pytest

from app.utils.chat_message_ops import (
    MessageOpError, TOMBSTONE_FIELD, delete_message, locate_message,
    merge_messages, patch_message, rewind,
)


def M(mid, role, content, **kw):
    return {"id": mid, "role": role, "content": content, "timestamp": 1, **kw}


def ids(ms):
    return [m["id"] for m in ms]


BASE = [M("h0", "human", "q0"), M("a0", "assistant", "r0"),
        M("h1", "human", "q1"), M("a1", "assistant", "r1")]


class TestMerge:
    def test_identical_is_unchanged(self):
        merged, differs = merge_messages(BASE, [dict(m) for m in BASE])
        assert ids(merged) == ids(BASE)
        assert differs is False

    def test_tail_append(self):
        merged, differs = merge_messages(BASE, BASE + [M("h2", "human", "q2")])
        assert ids(merged) == ["h0", "a0", "h1", "a1", "h2"]
        assert differs is False

    def test_lost_answer_race_keeps_server_answer(self):
        """Client never received a1, then asked h2.  Same message count as the
        server, so the old wholesale replace overwrote a1."""
        client = BASE[:3] + [M("h2", "human", "q2")]
        merged, differs = merge_messages(BASE, client)
        assert ids(merged) == ["h0", "a0", "h1", "a1", "h2"]
        assert differs is True

    def test_shorter_push_never_removes(self):
        merged, _ = merge_messages(BASE, [BASE[0], BASE[-1]])
        assert ids(merged) == ids(BASE)

    def test_core_fields_are_immutable_metadata_follows_client(self):
        client = [dict(m) for m in BASE]
        client[1] = {**client[1], "content": "", "muted": True}   # shell-style blanking
        merged, _ = merge_messages(BASE, client)
        assert merged[1]["content"] == "r0"
        assert merged[1]["muted"] is True

    def test_saved_turn_facts_are_immutable(self):
        """parts/turnStatus are the server's record of a turn; a stale or
        stripped browser copy must neither replace nor remove them."""
        parts = [{"type": "text", "text": "hi"},
                 {"type": "tool", "toolId": "t1", "toolName": "file_read", "result": "x"}]
        existing = [M("a0", "assistant", "hi", parts=parts, turnStatus="complete")]
        stale = [M("a0", "assistant", "hi", parts=[{"type": "text", "text": "old"}], turnStatus="streaming")]
        stripped = [M("a0", "assistant", "hi", muted=True)]
        m1, _ = merge_messages(existing, stale)
        m2, _ = merge_messages(existing, stripped)
        assert m1[0]["parts"] == parts and m1[0]["turnStatus"] == "complete"
        assert m2[0]["parts"] == parts and m2[0]["turnStatus"] == "complete"
        assert m2[0]["muted"] is True   # positive control: metadata still follows the client

    def test_client_can_remove_metadata(self):
        existing = [M("h0", "human", "q", _isFeedback=True, _feedbackStatus="pending")]
        merged, _ = merge_messages(existing, [M("h0", "human", "q")])
        assert "_isFeedback" not in merged[0]

    def test_server_authored_only_takes_mute(self):
        existing = [M("a0", "assistant", "srv", _serverAuthored=True, x=1)]
        merged, _ = merge_messages(existing, [M("a0", "assistant", "cli", muted=True, x=2)])
        assert merged[0]["content"] == "srv" and merged[0]["x"] == 1 and merged[0]["muted"] is True

    def test_push_minted_ids_match_by_content_and_adopt_server_id(self):
        client = [M(f"msg-{i}-r", m["role"], m["content"]) for i, m in enumerate(BASE)]
        merged, differs = merge_messages(BASE, client)
        assert ids(merged) == ids(BASE)
        assert differs is True        # so the caller bumps _version and the client adopts ids

    def test_real_id_repeat_text_is_new_not_swallowed(self):
        existing = [M("h0", "human", "continue"), M("a0", "assistant", "x")]
        client = existing + [M("uuid-2", "human", "continue")]
        merged, _ = merge_messages(existing, client)
        assert ids(merged) == ["h0", "a0", "uuid-2"]

    def test_tombstoned_id_is_not_resurrected(self):
        record = {TOMBSTONE_FIELD: {"ids": ["a1"], "fps": []}}
        merged, _ = merge_messages(BASE[:3], BASE, record)
        assert "a1" not in ids(merged)

    def test_tombstoned_fingerprint_applies_only_to_push_minted(self):
        record = {"messages": [dict(m) for m in BASE]}
        rewind(record, {"messageId": "h0"})           # tombstones a0..a1 by id+fp
        stale = [M("msg-9-x", "assistant", "r0")]       # legacy copy of a0
        fresh = [M("uuid-7", "assistant", "r0")]        # genuine new, same text
        m1, _ = merge_messages(record["messages"], record["messages"] + stale, record)
        m2, _ = merge_messages(record["messages"], record["messages"] + fresh, record)
        assert ids(m1) == ["h0"]
        assert ids(m2) == ["h0", "uuid-7"]


class TestOps:
    def test_rewind_truncates_and_edits(self):
        record = {"messages": [dict(m) for m in BASE], "_version": 5}
        out = rewind(record, {"messageId": "h1"}, text="q1 edited")
        assert ids(record["messages"]) == ["h0", "a0", "h1"]
        assert record["messages"][-1]["content"] == "q1 edited"
        assert record["messages"][-1]["_edited"] is True
        assert out["removed"] == 1 and record["_version"] > 5
        assert "a1" in record[TOMBSTONE_FIELD]["ids"]

    def test_rewind_refuses_non_human(self):
        with pytest.raises(MessageOpError) as e:
            rewind({"messages": [dict(m) for m in BASE]}, {"messageId": "a0"})
        assert e.value.status == 400

    def test_delete(self):
        record = {"messages": [dict(m) for m in BASE]}
        delete_message(record, {"messageId": "a0"})
        assert ids(record["messages"]) == ["h0", "h1", "a1"]

    def test_patch_in_place(self):
        record = {"messages": [dict(m) for m in BASE]}
        patch_message(record, {"messageId": "h0"}, content="new")
        assert ids(record["messages"]) == ids(BASE)
        assert record["messages"][0]["content"] == "new"

    def test_locator_fallback_for_legacy_ids(self):
        loc = {"index": 2, "role": "human", "contentHead": "q1", "contentLength": 2}
        assert locate_message(BASE, loc) == 2
        shifted = {**loc, "index": 0}
        assert locate_message(BASE, shifted) == 2      # unique match elsewhere

    def test_locator_refuses_ambiguous(self):
        msgs = [M("x", "human", "same"), M("y", "human", "same")]
        with pytest.raises(MessageOpError) as e:
            locate_message(msgs, {"index": 9, "role": "human", "contentHead": "same", "contentLength": 4})
        assert e.value.status == 409

    def test_locator_missing_id_without_fallback_is_404(self):
        with pytest.raises(MessageOpError) as e:
            locate_message(BASE, {"messageId": "nope"})
        assert e.value.status == 404
