"""Unit tests for app.utils.cli_chat_bridge pure conversion functions.

These cover the GUI<->CLI message round-trip without touching disk or
encryption — the storage helpers (resolve_project/write_back/etc.) are
exercised separately with a temp ZIYA_HOME once the CLI wiring lands.
"""
import time

from app.utils.cli_chat_bridge import (
    gui_messages_to_cli_history,
    cli_history_to_gui_messages,
    _cli_type_for_role,
    _gui_role_for_type,
    chat_display_label,
)


def test_role_mapping_bidirectional():
    assert _cli_type_for_role("assistant") == "ai"
    assert _cli_type_for_role("ai") == "ai"
    assert _cli_type_for_role("human") == "human"
    assert _cli_type_for_role("user") == "human"
    assert _cli_type_for_role("system") == "system"
    assert _cli_type_for_role("weird") == "human"

    assert _gui_role_for_type("ai") == "assistant"
    assert _gui_role_for_type("human") == "human"
    assert _gui_role_for_type("system") == "system"


def test_gui_to_cli_preserves_timestamp_and_images():
    gui = [
        {"id": "a", "role": "human", "content": "hi", "timestamp": 1000},
        {"id": "b", "role": "assistant", "content": "yo", "timestamp": 2000,
         "images": [{"data": "x"}]},
    ]
    hist = gui_messages_to_cli_history(gui)
    assert hist == [
        {"type": "human", "content": "hi", "_timestamp": 1000},
        {"type": "ai", "content": "yo", "_timestamp": 2000, "images": [{"data": "x"}]},
    ]


def test_gui_to_cli_omits_missing_optional_fields():
    gui = [{"id": "a", "role": "human", "content": "hi", "timestamp": 0}]
    hist = gui_messages_to_cli_history(gui)
    # timestamp 0 is falsy -> not carried; no images key
    assert hist == [{"type": "human", "content": "hi"}]


def test_cli_to_gui_new_messages_get_uuid_and_now():
    before = int(time.time() * 1000)
    hist = [
        {"type": "human", "content": "q"},
        {"type": "ai", "content": "a"},
    ]
    msgs = cli_history_to_gui_messages(hist, existing=[])
    after = int(time.time() * 1000)
    assert [m["role"] for m in msgs] == ["human", "assistant"]
    assert [m["content"] for m in msgs] == ["q", "a"]
    for m in msgs:
        assert m["id"]  # non-empty uuid
        assert before <= m["timestamp"] <= after
    # ids are unique
    assert msgs[0]["id"] != msgs[1]["id"]


def test_cli_to_gui_reuses_ids_for_aligned_prefix():
    existing = [
        {"id": "keep-1", "role": "human", "content": "q", "timestamp": 111},
        {"id": "keep-2", "role": "assistant", "content": "a", "timestamp": 222},
    ]
    # Same prefix, plus one new turn appended.
    hist = [
        {"type": "human", "content": "q"},
        {"type": "ai", "content": "a"},
        {"type": "human", "content": "followup"},
    ]
    msgs = cli_history_to_gui_messages(hist, existing=existing)
    assert msgs[0]["id"] == "keep-1"
    assert msgs[0]["timestamp"] == 111
    assert msgs[1]["id"] == "keep-2"
    assert msgs[1]["timestamp"] == 222
    # New turn gets a fresh id, not reused
    assert msgs[2]["id"] not in ("keep-1", "keep-2")


def test_cli_to_gui_role_mismatch_breaks_id_reuse():
    # If roles diverge at an index, the id must NOT be reused (it's a
    # different message, not an edit of the same one).
    existing = [{"id": "x", "role": "assistant", "content": "a", "timestamp": 5}]
    hist = [{"type": "human", "content": "a"}]
    msgs = cli_history_to_gui_messages(hist, existing=existing)
    assert msgs[0]["id"] != "x"
    assert msgs[0]["role"] == "human"


def test_cli_to_gui_explicit_timestamp_wins_over_prior():
    existing = [{"id": "x", "role": "human", "content": "q", "timestamp": 5}]
    hist = [{"type": "human", "content": "q", "_timestamp": 999}]
    msgs = cli_history_to_gui_messages(hist, existing=existing)
    assert msgs[0]["id"] == "x"           # aligned -> id reused
    assert msgs[0]["timestamp"] == 999    # explicit _timestamp wins


def test_round_trip_gui_cli_gui_is_stable():
    gui = [
        {"id": "a", "role": "human", "content": "hi", "timestamp": 1000},
        {"id": "b", "role": "assistant", "content": "yo", "timestamp": 2000},
    ]
    hist = gui_messages_to_cli_history(gui)
    back = cli_history_to_gui_messages(hist, existing=gui)
    # ids, roles, content, timestamps all preserved through the round trip
    assert [(m["id"], m["role"], m["content"], m["timestamp"]) for m in back] == [
        ("a", "human", "hi", 1000),
        ("b", "assistant", "yo", 2000),
    ]


def test_system_messages_survive_round_trip():
    gui = [{"id": "s", "role": "system", "content": "ctx", "timestamp": 1}]
    hist = gui_messages_to_cli_history(gui)
    assert hist[0]["type"] == "system"
    back = cli_history_to_gui_messages(hist, existing=gui)
    assert back[0]["role"] == "system"
    assert back[0]["id"] == "s"


def test_empty_inputs():
    assert gui_messages_to_cli_history([]) == []
    assert gui_messages_to_cli_history(None) == []
    assert cli_history_to_gui_messages([]) == []
    assert cli_history_to_gui_messages(None) == []


def test_chat_display_label():
    class S:
        def __init__(self, title, id):
            self.title = title
            self.id = id
    assert chat_display_label(S("My Chat", "abc123def456")) == "My Chat"
    assert chat_display_label(S("  ", "abc123def456")) == "untitled abc123de"
    assert chat_display_label({"title": "Dict Chat", "id": "z"}) == "Dict Chat"
