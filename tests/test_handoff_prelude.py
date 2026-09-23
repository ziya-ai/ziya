"""
Tests for the handoff prelude (app/utils/handoff_prelude.py) and its seam
into prompt construction.  See design/conversation-handoff.md, build step 2.

What matters here, in order:

1. SEAM — a handoff child's assembled system-prompt addition contains the
   document, the predecessor ids, and the open beads read from the SHARED
   lineage tree (b2), not from the child's own record.  Completing a bead
   on the root must drop it from the child's next prelude.
2. Editing the stored document changes the next prelude — no message is
   created or read.
3. Non-handoff conversations get exactly the input back (the common path
   is a no-op).

Storage is a real ChatStorage on tmp_path and is passed explicitly, so the
request-context resolution is bypassed except in the one test that covers
it.
"""
import json
import time
from unittest.mock import patch

import pytest

from app.models.bead import Bead, BeadTree
from app.storage.beads import load_bead_tree, save_bead_tree
from app.storage.chats import ChatStorage
from app.utils.handoff_prelude import (
    append_handoff_prelude,
    build_handoff_prelude,
    predecessor_chain,
    render_handoff_prelude,
)


@pytest.fixture
def storage(tmp_path):
    proj = tmp_path / "proj"
    (proj / "chats").mkdir(parents=True)
    return ChatStorage(proj)


def _write(storage, chat_id, **extra):
    now = int(time.time() * 1000)
    rec = {"id": chat_id, "title": extra.pop("title", f"chat {chat_id}"),
           "messages": extra.pop("messages", []),
           "createdAt": now, "lastActiveAt": now, "_version": now}
    rec.update(extra)
    storage._write_json(storage._chat_file(chat_id), rec)
    return rec


def _handoff_doc(text="Objective: finish the BFD flap root-cause.", **over):
    d = {"document": text, "generatedAt": 1_700_000_000_000,
         "sourceMessageCount": 62}
    d.update(over)
    return d


# ── lineage: source (root) → child (handoff) sharing one bead tree ─────

@pytest.fixture
def lineage(storage):
    """Root 'src' with two open beads and one completed; child 'cont' is a
    handoff from it and shares the tree via lineageRootId."""
    _write(storage, "src", title="ISL BFD flap investigation",
           messages=[{"id": f"m{i}", "role": "human", "content": "x",
                      "timestamp": 1} for i in range(5)],
           handedOffTo="cont")
    save_bead_tree(BeadTree(beads=[
        Bead(id="b_active", content="verify FDB learns payload as SMAC", status="active"),
        Bead(id="b_parked", content="check df_mgr gate ordering", status="parked"),
        Bead(id="b_done", content="rule out Guardian BPF", status="completed"),
    ]), chat_storage=storage, conversation_id="src")
    _write(storage, "cont", title="ISL BFD flap investigation (2)",
           branchedFrom="src", lineageKind="handoff", lineageRootId="src",
           handoff=_handoff_doc())
    return storage


class TestSeam:
    def test_handoff_child_prelude_has_document_ids_and_shared_open_beads(self, lineage):
        out = append_handoff_prelude("[Active Skill: x]\nbody", "cont", storage=lineage)
        # The caller's existing addition is preserved, prelude appended.
        assert out.startswith("[Active Skill: x]\nbody")
        assert "[Conversation handoff]" in out
        assert "Objective: finish the BFD flap root-cause." in out
        # Predecessor id + retrieval affordances the model is told to use.
        assert "src" in out
        assert "5 messages" in out
        assert "chat_read" in out and "conversation_ids" in out
        # Open beads are VOLATILE (they change whenever a bead opens or
        # closes) and must NOT be in the system addition — that would bust
        # the cached prefix on every bead change.  They ride on the turn part.
        assert "Open threads" not in out
        assert "verify FDB learns payload as SMAC" not in out
        from app.utils.handoff_prelude import split_handoff_prelude
        _, turn = split_handoff_prelude("cont", storage=lineage)
        assert "[active] verify FDB learns payload as SMAC" in turn
        assert "[parked] check df_mgr gate ordering" in turn
        assert "rule out Guardian BPF" not in turn
        # The combined view still has everything.
        combined = build_handoff_prelude("cont", storage=lineage)
        assert "[Conversation handoff]" in combined and "[active] verify FDB" in combined

    def test_closing_a_bead_on_the_root_drops_it_from_the_child_prelude(self, lineage):
        before = build_handoff_prelude("cont", storage=lineage)
        assert "check df_mgr gate ordering" in before
        # Close it via the CHILD (b2: resolves to the shared root tree).
        tree = load_bead_tree(chat_storage=lineage, conversation_id="cont")
        for b in tree.beads:
            if b.id == "b_parked":
                b.status = "completed"
        save_bead_tree(tree, chat_storage=lineage, conversation_id="cont")
        after = build_handoff_prelude("cont", storage=lineage)
        assert "check df_mgr gate ordering" not in after
        assert "verify FDB learns payload as SMAC" in after  # still open

    def test_editing_the_document_changes_the_next_prelude_without_a_message(self, lineage):
        rec = lineage._read_json(lineage._chat_file("cont"))
        rec["handoff"]["document"] = "Objective: REVISED by user."
        rec["handoff"]["editedAt"] = 1_700_000_100_000
        lineage._write_json(lineage._chat_file("cont"), rec)
        out = build_handoff_prelude("cont", storage=lineage)
        assert "Objective: REVISED by user." in out
        assert "finish the BFD flap root-cause" not in out
        assert "edited by the user" in out
        assert lineage._read_json(lineage._chat_file("cont"))["messages"] == []

    def test_non_handoff_conversation_is_a_no_op(self, lineage):
        assert append_handoff_prelude("keep me", "src", storage=lineage) == "keep me"
        assert build_handoff_prelude("src", storage=lineage) == ""

    def test_unknown_or_missing_conversation_is_a_no_op(self, storage):
        assert append_handoff_prelude("keep me", "nope", storage=storage) == "keep me"
        assert append_handoff_prelude("keep me", None, storage=storage) == "keep me"
        assert append_handoff_prelude("", None, storage=storage) == ""


class TestPredecessorChain:
    def test_multi_hop_handoff_chain_newest_first(self, storage):
        _write(storage, "a", title="seg 1")
        _write(storage, "b", title="seg 2", branchedFrom="a", lineageKind="handoff")
        rec_c = _write(storage, "c", title="seg 3", branchedFrom="b",
                       lineageKind="handoff", handoff=_handoff_doc())
        chain = predecessor_chain(storage, rec_c)
        assert [p["id"] for p in chain] == ["b", "a"]
        out = render_handoff_prelude(rec_c, chain, [])
        assert "Default conversation_ids for chat_search: ['b', 'a']" in out

    def test_walk_stops_at_a_non_handoff_ancestor(self, storage):
        # trunk ← fork ← handoff: the fork copied trunk's transcript, so the
        # trunk is not a separate segment of this track.
        _write(storage, "trunk")
        _write(storage, "fork", branchedFrom="trunk", lineageKind="fork")
        rec = _write(storage, "h", branchedFrom="fork", lineageKind="handoff",
                     handoff=_handoff_doc())
        assert [p["id"] for p in predecessor_chain(storage, rec)] == ["fork"]

    def test_missing_ancestor_yields_id_only_placeholder(self, storage):
        rec = _write(storage, "h", branchedFrom="gone", lineageKind="handoff",
                     handoff=_handoff_doc())
        chain = predecessor_chain(storage, rec)
        assert chain == [{"id": "gone"}]
        assert "gone" in render_handoff_prelude(rec, chain, [])

    def test_cycle_is_bounded(self, storage):
        _write(storage, "x", branchedFrom="y", lineageKind="handoff")
        rec = _write(storage, "y", branchedFrom="x", lineageKind="handoff",
                     handoff=_handoff_doc())
        ids = [p["id"] for p in predecessor_chain(storage, rec)]
        assert ids == ["x"]


class TestRender:
    def test_empty_or_missing_document_renders_nothing(self):
        assert render_handoff_prelude({"id": "c"}, [], []) == ""
        assert render_handoff_prelude({"id": "c", "handoff": {"document": "  "}}, [], []) == ""
        assert render_handoff_prelude({"id": "c", "handoff": "not a dict"}, [], []) == ""

    def test_no_open_beads_is_explicit(self):
        out = render_handoff_prelude({"id": "c", "handoff": _handoff_doc()}, [], [])
        assert "(none open)" in out
        assert "(source id unavailable)" in out


# ── request-context resolution (the path server.py actually takes) ─────

def test_resolves_storage_from_request_root(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    pdir = ziya_home / "projects" / "p_h"
    (pdir / "chats").mkdir(parents=True)
    now = int(time.time() * 1000)
    (pdir / "project.json").write_text(json.dumps({
        "id": "p_h", "name": "H", "path": str(project_root.resolve()),
        "createdAt": now, "lastAccessedAt": now,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []}}))
    (ziya_home / "projects" / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): "p_h"}))
    storage = ChatStorage(pdir)
    _write(storage, "src")
    _write(storage, "cont", branchedFrom="src", lineageKind="handoff",
           lineageRootId="src", handoff=_handoff_doc("Doc via request root."))
    with patch("app.utils.paths.get_ziya_home", return_value=ziya_home), \
         patch("app.context.get_project_root_or_none",
               return_value=str(project_root.resolve())):
        out = append_handoff_prelude("", "cont")
    assert "Doc via request root." in out
