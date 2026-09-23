"""
Step 1 of design/conversation-handoff.md: lineage discriminator + handoff
fields on the chat record, surfaced on the SUMMARY listing.

The sidebar renders chain rows and source dimming from ChatSummary alone,
so every summary builder (ChatStorage.list_summaries, the global listing,
and _chat_to_summary) must carry lineageKind / handedOffTo / hasHandoff.
A field declared on Chat but not built into the summary is invisible on
the one path the sidebar actually reads — that is the bug this guards.

These FAIL against the unpatched tree (ChatSummary has no such fields).
"""
import time

import pytest

from app.models.chat import Chat, ChatSummary, HandoffDoc
from app.storage.chats import ChatStorage


@pytest.fixture
def storage(tmp_path):
    proj = tmp_path / "proj"
    (proj / "chats").mkdir(parents=True)
    return ChatStorage(proj)


def _write(storage, chat_id, **extra):
    now = int(time.time() * 1000)
    rec = {
        "id": chat_id,
        "title": f"chat {chat_id}",
        "messages": [],
        "createdAt": now,
        "lastActiveAt": now,
        "_version": now,
    }
    rec.update(extra)
    storage._write_json(storage._chat_file(chat_id), rec)


def _summary(storage, chat_id) -> ChatSummary:
    found = [s for s in storage.list_summaries() if s.id == chat_id]
    assert len(found) == 1, f"summary for {chat_id} missing from listing"
    return found[0]


def test_trunk_summary_has_unset_lineage_fields(storage):
    _write(storage, "trunk")
    s = _summary(storage, "trunk")
    assert s.lineageKind is None
    assert s.handedOffTo is None
    assert s.hasHandoff is False


def test_handoff_child_summary_carries_kind_and_hasHandoff(storage):
    _write(storage, "src")
    _write(
        storage, "child",
        branchedFrom="src",
        lineageKind="handoff",
        lineageRootId="src",
        handoff={"document": "# Objective\nfinish it", "generatedAt": 1,
                 "sourceMessageCount": 62},
    )
    s = _summary(storage, "child")
    assert s.branchedFrom == "src"
    assert s.lineageKind == "handoff"
    # Boolean, not the document — listings must stay small.
    assert s.hasHandoff is True
    assert not hasattr(s, "document")


def test_source_summary_carries_handedOffTo(storage):
    _write(storage, "src", handedOffTo="child")
    s = _summary(storage, "src")
    assert s.handedOffTo == "child"
    assert s.hasHandoff is False   # the SOURCE has no document of its own


def test_malformed_handoff_value_does_not_break_listing(storage):
    # A hand-edited record with a non-dict handoff must not fail the whole
    # listing; it just reads as "no handoff".
    _write(storage, "bad", handoff="oops")
    _write(storage, "ok")
    ids = {s.id for s in storage.list_summaries()}
    assert ids == {"bad", "ok"}
    assert _summary(storage, "bad").hasHandoff is False


def test_chat_model_round_trips_handoff_doc():
    now = int(time.time() * 1000)
    chat = Chat(
        id="c", title="t", createdAt=now, lastActiveAt=now,
        branchedFrom="src", lineageKind="handoff", lineageRootId="src",
        handoff=HandoffDoc(document="doc", generatedAt=now, sourceMessageCount=3),
    )
    back = Chat.model_validate(chat.model_dump())
    assert back.lineageKind == "handoff"
    assert back.handoff is not None
    assert back.handoff.document == "doc"
    assert back.handoff.editedAt is None


def test_chat_to_summary_carries_lineage_fields():
    from app.api.chats import _chat_to_summary
    now = int(time.time() * 1000)
    chat = Chat(
        id="c", title="t", createdAt=now, lastActiveAt=now,
        branchedFrom="src", lineageKind="handoff",
        handoff=HandoffDoc(document="doc", generatedAt=now),
        handedOffTo="next",
    )
    s = _chat_to_summary(chat)
    assert s.lineageKind == "handoff"
    assert s.hasHandoff is True
    assert s.handedOffTo == "next"


def test_fork_from_bead_stamps_branch_kind(storage):
    """The branch endpoint's record is the third lineage kind; it must be
    distinguishable from a fork/handoff by the discriminator alone."""
    import inspect
    from app.api import beads as beads_api
    src = inspect.getsource(beads_api)
    # Seam assertion on the record literal: the endpoint's written record
    # includes the discriminator next to the other branch stamps.
    assert '"lineageKind": "branch"' in src
