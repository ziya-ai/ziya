"""
Tests for shared-root bead inheritance (design/bead-branching.md, "b2").

A forked conversation carries ``lineageRootId`` pointing at the root of
its fork lineage.  Beads live on the ROOT's chat record; every conversation
in the lineage resolves to that one shared tree (state-synced — a write in
the fork is visible in the parent and vice-versa).  A conversation with no
``lineageRootId`` is its own root and owns its beads directly, so the
non-fork path is byte-for-byte the prior behavior.

These run against a real ChatStorage on tmp_path (the autouse conftest
fixture forces the Noop embedding provider).  Beads are read/written via
the public load_bead_tree / save_bead_tree, passing chat_storage explicitly
so _resolve_chat_storage (ContextVar) is bypassed.

Split by expected state vs. the production diff:
  - The NON-FORK tests pass against current code (resolution is a no-op for
    self-root conversations).
  - The FORK tests require the lineage-root resolution diff in
    app/storage/beads.py; until it lands they fail (load/save hit the
    fork's own empty record instead of the root).  Not marked xfail —
    they are the honest signal the diff isn't applied.
"""
import time

import pytest

from app.models.bead import Bead, BeadTree
from app.storage.beads import load_bead_tree, save_bead_tree
from app.storage.chats import ChatStorage


@pytest.fixture
def storage(tmp_path):
    proj = tmp_path / "proj"
    (proj / "chats").mkdir(parents=True)
    return ChatStorage(proj)


def _write_chat(storage, chat_id, *, beads=None, lineage_root=None):
    now = int(time.time() * 1000)
    rec = {
        "id": chat_id,
        "title": f"chat {chat_id}",
        "messages": [],
        "createdAt": now,
        "lastActiveAt": now,
        "_version": now,
    }
    if beads is not None:
        rec["_beads"] = beads
    if lineage_root is not None:
        rec["lineageRootId"] = lineage_root
    storage._write_json(storage._chat_file(chat_id), rec)


def _bead(bid, content, status="parked"):
    return {"id": bid, "content": content, "status": status,
            "parent_id": None, "message_index": None, "created_at": 1}


def _raw_beads(storage, chat_id):
    """Return the raw _beads list persisted on a chat record (or [])."""
    raw = storage._read_json(storage._chat_file(chat_id))
    return (raw or {}).get("_beads") or []


# ── Non-fork path: unchanged behavior (passes against current code) ──

def test_self_root_loads_own_beads(storage):
    _write_chat(storage, "root", beads=[_bead("b1", "root task")])
    tree = load_bead_tree(chat_storage=storage, conversation_id="root")
    assert [b.id for b in tree.beads] == ["b1"]


def test_self_root_saves_to_own_record(storage):
    _write_chat(storage, "root", beads=[])
    tree = BeadTree(beads=[Bead(id="b1", content="new", status="active")])
    save_bead_tree(tree, chat_storage=storage, conversation_id="root")
    assert {b["id"] for b in _raw_beads(storage, "root")} == {"b1"}


def test_no_lineage_field_is_self_root(storage):
    # A record with no lineageRootId at all behaves exactly as before.
    _write_chat(storage, "plain", beads=[_bead("x", "thread")])
    tree = load_bead_tree(chat_storage=storage, conversation_id="plain")
    assert [b.id for b in tree.beads] == ["x"]


# ── Fork path: requires the lineage-root resolution diff ────────────

def test_fork_loads_root_beads(storage):
    # Root owns the beads; fork has none of its own but points at root.
    _write_chat(storage, "root", beads=[_bead("b1", "shared task"),
                                        _bead("b2", "second")])
    _write_chat(storage, "fork", beads=[], lineage_root="root")
    tree = load_bead_tree(chat_storage=storage, conversation_id="fork")
    assert {b.id for b in tree.beads} == {"b1", "b2"}


def test_fork_save_writes_to_root_not_fork(storage):
    _write_chat(storage, "root", beads=[_bead("b1", "existing")])
    _write_chat(storage, "fork", beads=[], lineage_root="root")
    # Mutate via the fork id: add a bead.
    tree = load_bead_tree(chat_storage=storage, conversation_id="fork")
    tree.beads.append(Bead(id="b2", content="added via fork", status="active"))
    save_bead_tree(tree, chat_storage=storage, conversation_id="fork")
    # The write landed on ROOT's record …
    assert {b["id"] for b in _raw_beads(storage, "root")} == {"b1", "b2"}
    # … and the fork's own record stayed empty (no divergent copy).
    assert _raw_beads(storage, "fork") == []


def test_fork_and_root_share_one_tree(storage):
    # State sync: a write through the root is visible through the fork.
    _write_chat(storage, "root", beads=[])
    _write_chat(storage, "fork", beads=[], lineage_root="root")
    save_bead_tree(
        BeadTree(beads=[Bead(id="b1", content="via root", status="active")]),
        chat_storage=storage, conversation_id="root",
    )
    via_fork = load_bead_tree(chat_storage=storage, conversation_id="fork")
    assert [b.id for b in via_fork.beads] == ["b1"]


def test_fork_with_missing_root_falls_back_to_own_record(storage):
    # Root deleted/never-synced: don't strand — resolve to the fork's own
    # record rather than raising or losing the write.
    _write_chat(storage, "fork", beads=[_bead("own", "fork-local")],
                lineage_root="ghost-root-that-does-not-exist")
    tree = load_bead_tree(chat_storage=storage, conversation_id="fork")
    assert [b.id for b in tree.beads] == ["own"]


def test_fork_of_fork_resolves_to_root_single_hop(storage):
    # b2 stamps lineageRootId = source.lineageRootId || source.id, so a
    # fork-of-a-fork points DIRECTLY at the ultimate root (flat, no chain).
    _write_chat(storage, "root", beads=[_bead("b1", "root-owned")])
    _write_chat(storage, "fork1", beads=[], lineage_root="root")
    _write_chat(storage, "fork2", beads=[], lineage_root="root")  # not "fork1"
    assert {b.id for b in load_bead_tree(chat_storage=storage, conversation_id="fork2").beads} == {"b1"}
