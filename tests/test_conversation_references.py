"""Conversation references (design/conversation-references.md, slice 1).

A URL ``mcp_fetch`` pulled at turn 4 and the model cited at turns 6 and 11 was
recoverable only by rereading the transcript; a file ``file_read`` opened was
remembered nowhere.  ``app/utils/conversation_references.py`` derives
picked-up references from the ``tool_display`` chunks ``stream_chunks``
forwards, records them on the chat record with per-turn uses, computes
citations from the reply text, and tells the model about them link-only in
the volatile tail.

Tests drive the real chat record (``chat_env``) and assert on it or on the
notice text; the seam tests check ``stream_chunks`` actually calls into the
module at the right points, since a correct module nobody calls fixes nothing.
"""
import os

import pytest

from tests.test_context_management_tools import chat_env, _read_chat  # noqa: F401  (fixture)
from app.utils import conversation_references as cr


def _display(tool, args, is_error=False, result="ok"):
    return {"type": "tool_display", "tool_name": tool, "args": args,
            "result": result, "is_error": is_error}


def _refs(env):
    return cr.get_references(_read_chat(env))


def _only(refs):
    assert len(refs) == 1, refs
    return next(iter(refs.values()))


# -- pickup derivation ------------------------------------------------------

class TestPickupDerivation:
    def test_each_tracked_tool_names_its_locator(self, chat_env):
        root = chat_env["project_root"]
        cases = [
            (_display("file_read", {"path": "src/main.py"}), "file", "src/main.py"),
            (_display("mcp_file_read", {"path": "src/main.py"}), "file", "src/main.py"),
            (_display("pdf_read_pages", {"path": "/docs/x.pdf", "start_page": 1}), "pdf", "/docs/x.pdf"),
            (_display("mcp_fetch", {"url": "https://example.com/a#frag"}), "url", "https://example.com/a"),
            (_display("chat_read", {"chat_id": "abc-123"}), "chat", "ziya://chat/abc-123"),
            (_display("context_add_file", {"path": "README.md"}), "file", "README.md"),
        ]
        for chunk, kind, locator in cases:
            got = cr.pickup_from_tool_display(chunk, root)
            assert got == {"kind": kind, "locator": locator, "tool": cr._tool_base_name(chunk["tool_name"])}, chunk

    def test_non_pickups(self, chat_env):
        root = chat_env["project_root"]
        assert cr.pickup_from_tool_display(_display("run_shell_command", {"command": "ls"}), root) is None
        assert cr.pickup_from_tool_display(_display("file_read", {"path": "x"}, is_error=True), root) is None
        # Direct wrappers carry no args.
        assert cr.pickup_from_tool_display({"type": "tool_display", "tool_name": "file_read", "result": "x"}, root) is None
        # Scratch and temp paths are not references.
        assert cr.pickup_from_tool_display(_display("file_read", {"path": ".ziya/notes.md"}), root) is None
        assert cr.pickup_from_tool_display(_display("file_read", {"path": "/tmp/out.txt"}), root) is None
        assert cr.pickup_from_tool_display({"type": "text", "content": "x"}, root) is None

    def test_locators_outside_the_project_stay_absolute(self, tmp_path, chat_env):
        other = tmp_path / "elsewhere" / "doc.md"
        other.parent.mkdir()
        other.write_text("x")
        got = cr.pickup_from_tool_display(_display("file_read", {"path": str(other)}), chat_env["project_root"])
        assert got["locator"] == os.path.realpath(str(other))


# -- recording on the chat record ------------------------------------------

class TestRecordTurn:
    def test_pickups_land_on_the_chat_record_with_a_read_use(self, chat_env):
        root = chat_env["project_root"]
        p = cr.pickup_from_tool_display(_display("file_read", {"path": "src/main.py"}), root)
        out = cr.record_turn(chat_env["chat_id"], root, [p], "I looked at it.", turn=3)
        assert out == {"picked_up": 1, "read": 1, "cited": 0}
        ref = _only(_refs(chat_env))
        assert ref["kind"] == "file" and ref["locator"] == "src/main.py"
        assert ref["provenance"] == "picked_up" and ref["added_turn"] == 3
        assert [(u["kind"], u["turn"]) for u in ref["uses"]] == [("read", 3)]
        assert ref["last_read_hash"]            # files get a baseline for "changed since"
        assert not cr.is_useful(ref)

    def test_citation_in_the_reply_is_recorded_and_makes_it_useful(self, chat_env):
        root = chat_env["project_root"]
        p = cr.pickup_from_tool_display(_display("mcp_fetch", {"url": "https://example.com/doc"}), root)
        cr.record_turn(chat_env["chat_id"], root, [p], "fetched it", turn=4)
        # No tool call this turn; the reply names the URL.
        out = cr.record_turn(chat_env["chat_id"], root, [], "See https://example.com/doc for details.", turn=6)
        assert out == {"picked_up": 0, "read": 0, "cited": 1}
        ref = _only(_refs(chat_env))
        assert ("cited", 6) in [(u["kind"], u["turn"]) for u in ref["uses"]]
        assert cr.is_useful(ref)

    def test_file_cited_by_basename_and_chat_by_id(self, chat_env):
        root = chat_env["project_root"]
        ps = [cr.pickup_from_tool_display(_display("file_read", {"path": "src/main.py"}), root),
              cr.pickup_from_tool_display(_display("chat_read", {"chat_id": "abc-123"}), root)]
        out = cr.record_turn(chat_env["chat_id"], root, ps,
                             "In main.py the loop… and conversation abc-123 covered it.", turn=2)
        assert out["cited"] == 2

    def test_rereading_in_a_second_turn_is_useful_and_uses_do_not_duplicate(self, chat_env):
        root = chat_env["project_root"]
        p = cr.pickup_from_tool_display(_display("file_read", {"path": "src/main.py"}), root)
        cr.record_turn(chat_env["chat_id"], root, [p, p], "", turn=1)   # two reads, one turn
        ref = _only(_refs(chat_env))
        assert len(ref["uses"]) == 1 and not cr.is_useful(ref)
        cr.record_turn(chat_env["chat_id"], root, [p], "", turn=2)
        ref = _only(_refs(chat_env))
        assert cr.is_useful(ref)

    def test_nothing_to_record_does_not_touch_the_record(self, chat_env):
        before = _read_chat(chat_env).get("_version")
        assert cr.record_turn(chat_env["chat_id"], chat_env["project_root"], [], "plain reply", 1) == \
            {"picked_up": 0, "read": 0, "cited": 0}
        assert _read_chat(chat_env).get("_version") == before
        assert cr.record_turn(None, chat_env["project_root"], [], "x", 1) is None
        assert cr.record_turn("no-such-chat", chat_env["project_root"], [], "x", 1) is None


class TestPin:
    def test_pin_then_pickup_keeps_pinned(self, chat_env):
        root = chat_env["project_root"]
        ref = cr.pin_reference(chat_env["chat_id"], root, "https://example.com/spec", turn=1, title="The spec")
        assert ref["provenance"] == "pinned" and ref["title"] == "The spec"
        p = cr.pickup_from_tool_display(_display("mcp_fetch", {"url": "https://example.com/spec"}), root)
        cr.record_turn(chat_env["chat_id"], root, [p], "", turn=2)
        stored = _only(_refs(chat_env))
        assert stored["provenance"] == "pinned"          # never demoted
        assert ("read", 2) in [(u["kind"], u["turn"]) for u in stored["uses"]]

    def test_pickup_then_pin_promotes_and_keeps_uses(self, chat_env):
        root = chat_env["project_root"]
        p = cr.pickup_from_tool_display(_display("file_read", {"path": "README.md"}), root)
        cr.record_turn(chat_env["chat_id"], root, [p], "", turn=1)
        ref = cr.pin_reference(chat_env["chat_id"], root, "README.md", turn=3)
        assert ref["provenance"] == "pinned" and ref["pinned_turn"] == 3
        assert len(ref["uses"]) == 1
        assert len(_refs(chat_env)) == 1

    def test_untrackable_locators_are_refused(self, chat_env):
        root = chat_env["project_root"]
        assert cr.pin_reference(chat_env["chat_id"], root, "ziya://folder/x", turn=1) is None
        assert cr.pin_reference(chat_env["chat_id"], root, ".ziya/scratch.md", turn=1) is None
        assert cr.pin_reference(chat_env["chat_id"], root, "", turn=1) is None
        assert _refs(chat_env) == {}

    def test_unpin_removes(self, chat_env):
        root = chat_env["project_root"]
        ref = cr.pin_reference(chat_env["chat_id"], root, "https://example.com/", turn=1)
        assert cr.unpin_reference(chat_env["chat_id"], root, ref["id"]) is True
        assert _refs(chat_env) == {}
        assert cr.unpin_reference(chat_env["chat_id"], root, ref["id"]) is False


# -- what the model sees ----------------------------------------------------

class TestNotice:
    def test_empty_when_nothing_recorded(self, chat_env):
        assert cr.format_references_notice(chat_env["chat_id"], chat_env["project_root"], 1) == ""
        assert cr.format_references_notice(None, chat_env["project_root"], 1) == ""

    def test_lists_pinned_first_then_picked_up_with_uses_and_disk_state(self, chat_env):
        root = chat_env["project_root"]
        cr.pin_reference(chat_env["chat_id"], root, "https://example.com/spec", turn=1, title="The spec")
        p = cr.pickup_from_tool_display(_display("file_read", {"path": "src/main.py"}), root)
        cr.record_turn(chat_env["chat_id"], root, [p], "", turn=2)
        n = cr.format_references_notice(chat_env["chat_id"], root, 3)
        lines = [l for l in n.splitlines() if l.startswith("- ")]
        assert lines[0].startswith("- [url, pinned] https://example.com/spec — The spec")
        assert lines[1].startswith("- [file, picked up] src/main.py (read turn 2")
        assert "CHANGED" not in n
        assert "content is NOT shown" in n
        # The file changes on disk: the model is told, without content.
        with open(os.path.join(root, "src", "main.py"), "w") as f:
            f.write("different\n")
        n2 = cr.format_references_notice(chat_env["chat_id"], root, 3)
        assert "src/main.py (read turn 2; CHANGED on disk since last read)" in n2
        assert "different" not in n2

    def test_unused_pickups_fade_but_pinned_and_useful_do_not(self, chat_env):
        root = chat_env["project_root"]
        unused = cr.pickup_from_tool_display(_display("mcp_fetch", {"url": "https://example.com/unused"}), root)
        used = cr.pickup_from_tool_display(_display("mcp_fetch", {"url": "https://example.com/used"}), root)
        cr.record_turn(chat_env["chat_id"], root, [unused, used], "", turn=1)
        cr.record_turn(chat_env["chat_id"], root, [], "see https://example.com/used", turn=2)
        cr.pin_reference(chat_env["chat_id"], root, "https://example.com/pinned", turn=1)
        early = cr.format_references_notice(chat_env["chat_id"], root, 1 + cr.FADE_TURNS - 1)
        assert "example.com/unused" in early
        late = cr.format_references_notice(chat_env["chat_id"], root, 1 + cr.FADE_TURNS)
        assert "example.com/unused" not in late
        assert "example.com/used" in late and "example.com/pinned" in late
        assert "1 picked-up reference(s) unused" in late
        assert cr.summarize(_read_chat(chat_env), 1 + cr.FADE_TURNS) == \
            {"pinned": 1, "picked_up": 2, "useful": 1, "faded": 1}


def test_turn_index_counts_prior_human_messages():
    assert cr.turn_index([]) == 1
    assert cr.turn_index([{"type": "human", "content": "a"}, {"type": "ai", "content": "b"},
                          {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]) == 3


# -- seams: stream_chunks must call this -----------------------------------

def _stream_chunks_src():
    src = open("app/server.py").read()
    return src[src.index("async def stream_chunks(body):"):]


def test_stream_chunks_collects_pickups_from_tool_display_and_records_after_the_reply():
    fn = _stream_chunks_src()
    first_display = fn.index("elif chunk.get('type') == 'tool_display':")
    collect = fn.index("pickup_from_tool_display(chunk", first_display)
    # Recorded once, after the reply is complete (after the validation-retry
    # stream, which also collects pickups) and BEFORE the done frame, so the
    # references_updated frame it produces reaches a client that stops
    # reading at done.  Not per tool call.
    # Imported under an alias in server.py: ``record_turn as record_reference_turn``.
    record = fn.index("record_reference_turn, conversation_id")
    retry_collect = fn.index("pickup_from_tool_display(retry_chunk")
    done = fn.index("yield f\"data: {json.dumps({'done': True})}\\n\\n\"")
    assert first_display < collect < retry_collect < record < done
    assert fn.count("record_reference_turn, conversation_id") == 1
    # The client learns the record changed from a frame, not by polling.
    assert "'references_updated'" in fn[record:done]
    # The validation-retry stream also carries tool_display chunks.
    retry = fn.index("elif retry_chunk.get('type') == 'tool_display':")
    assert "pickup_from_tool_display(retry_chunk" in fn[retry:retry + 600]


def test_references_notice_rides_the_volatile_turn_part():
    src = open("app/server.py").read()
    fn = src[src.index("def build_messages_for_streaming("):src.index("async def stream_chunks(body):")]
    assert "format_references_notice(" in fn
    # Joined into the same per-turn part as the tracked-files notice, which
    # is appended to the final user message, never the cached system prompt.
    i = fn.index("format_references_notice(")
    assert "_handoff_turn_part" in fn[i:i + 400]
