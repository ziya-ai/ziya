"""
Backend cross-project surfacing of folder-inherited global chats.

Mirrors the frontend folderIsEffectivelyGlobal model on the server side:
a chat is surfaced to other projects if its own isGlobal flag is set OR
its containing group is effectively global (own flag OR any ancestor group
global).  Before this, collect_global_chat_summaries / collect_global_chats
filtered on the chat's own isGlobal only, so a chat that was global purely
by folder inheritance never crossed to other projects — the folder appeared
but read as empty.

These write PLAIN-JSON chat/_groups files (is_encrypted() returns False for
plain JSON, so the read path skips decryption) into a tmp ziya-home layout.
"""
import json
from pathlib import Path

import pytest

import app.storage.global_items as gi


def _write_chat(chats_dir: Path, cid: str, *, is_global=False, group_id=None,
                title="t"):
    rec = {
        "id": cid,
        "title": title,
        "createdAt": 1,
        "lastActiveAt": 2,
        "messages": [],
    }
    if is_global:
        rec["isGlobal"] = True
    if group_id is not None:
        rec["groupId"] = group_id
    (chats_dir / f"{cid}.json").write_text(json.dumps(rec))


def _write_groups(chats_dir: Path, groups):
    """groups: list of (id, parentId, isGlobal)."""
    payload = {
        "version": 1,
        "groups": [
            {
                "id": gid,
                "name": gid,
                "createdAt": 1,
                "parentId": parent,
                "isGlobal": glob,
            }
            for (gid, parent, glob) in groups
        ],
    }
    (chats_dir / "_groups.json").write_text(json.dumps(payload))


@pytest.fixture
def home(tmp_path):
    # Fresh module-level caches per test so prior tests don't leak decisions.
    gi._summary_cache.clear()
    gi._full_cache.clear()
    gi._group_global_cache.clear()
    (tmp_path / "projects").mkdir()
    return tmp_path


def _project(home: Path, pid: str) -> Path:
    cdir = home / "projects" / pid / "chats"
    cdir.mkdir(parents=True)
    return cdir


# -- _effective_global_group_ids --------------------------------------

def test_effective_group_own_flag(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("g1", None, True), ("g2", None, False)])
    eff = gi._effective_global_group_ids(home / "projects" / "P")
    assert eff == frozenset({"g1"})


def test_effective_group_inherits_parent(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("root", None, True), ("child", "root", False)])
    eff = gi._effective_global_group_ids(home / "projects" / "P")
    assert eff == frozenset({"root", "child"})


def test_effective_group_inherits_grandparent_full_chain(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [
        ("root", None, True),
        ("mid", "root", False),
        ("leaf", "mid", False),
    ])
    eff = gi._effective_global_group_ids(home / "projects" / "P")
    assert eff == frozenset({"root", "mid", "leaf"})


def test_effective_group_no_global_anywhere(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("a", None, False), ("b", "a", False)])
    eff = gi._effective_global_group_ids(home / "projects" / "P")
    assert eff == frozenset()


def test_effective_group_cycle_safe(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("a", "b", False), ("b", "a", False)])
    eff = gi._effective_global_group_ids(home / "projects" / "P")
    assert eff == frozenset()


def test_effective_group_cycle_with_global_resolves(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("a", "b", False), ("b", "a", True)])
    eff = gi._effective_global_group_ids(home / "projects" / "P")
    # both reachable to a global node before the cycle closes
    assert eff == frozenset({"a", "b"})


def test_effective_group_missing_file(home):
    _project(home, "P")  # no _groups.json
    eff = gi._effective_global_group_ids(home / "projects" / "P")
    assert eff == frozenset()


# -- collect_global_chat_summaries ------------------------------------

def test_summary_own_global_surfaces(home):
    cdir = _project(home, "P")
    _write_chat(cdir, "c1", is_global=True)
    out = gi.collect_global_chat_summaries(home, exclude_project_id="OTHER")
    assert [s.id for s in out] == ["c1"]


def test_summary_inherited_global_surfaces(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("root", None, True), ("child", "root", False)])
    # chat owns no global flag, but sits in an inherited-global child folder
    _write_chat(cdir, "c1", is_global=False, group_id="child")
    out = gi.collect_global_chat_summaries(home, exclude_project_id="OTHER")
    assert [s.id for s in out] == ["c1"]


def test_summary_non_global_folder_not_surfaced(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("plain", None, False)])
    _write_chat(cdir, "c1", is_global=False, group_id="plain")
    out = gi.collect_global_chat_summaries(home, exclude_project_id="OTHER")
    assert out == []


def test_summary_excludes_requesting_project(home):
    cdir = _project(home, "P")
    _write_chat(cdir, "c1", is_global=True)
    out = gi.collect_global_chat_summaries(home, exclude_project_id="P")
    assert out == []


def test_summary_folder_toggle_takes_effect_without_chat_write(home):
    # The cache-coherency case: a folder-global toggle rewrites _groups.json
    # but NOT the chat file.  The chat must start surfacing anyway.
    cdir = _project(home, "P")
    _write_groups(cdir, [("f", None, False)])
    _write_chat(cdir, "c1", is_global=False, group_id="f")

    # First scan: folder not global -> chat not surfaced (and its decision
    # inputs get cached against the unchanged chat-file mtime).
    assert gi.collect_global_chat_summaries(home, exclude_project_id="O") == []

    # Toggle the folder global — rewrites _groups.json only.
    _write_groups(cdir, [("f", None, True)])

    # Chat file mtime is unchanged, so the per-chat cache "hits" — but the
    # surfacing decision must be recomputed against the new group set.
    out = gi.collect_global_chat_summaries(home, exclude_project_id="O")
    assert [s.id for s in out] == ["c1"]


def test_summary_folder_untoggle_stops_surfacing(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("f", None, True)])
    _write_chat(cdir, "c1", is_global=False, group_id="f")
    assert [s.id for s in gi.collect_global_chat_summaries(home, "O")] == ["c1"]
    # un-global the folder
    _write_groups(cdir, [("f", None, False)])
    assert gi.collect_global_chat_summaries(home, "O") == []


# -- collect_global_chats (full objects) ------------------------------

def test_full_inherited_global_surfaces(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("root", None, True), ("mid", "root", False)])
    _write_chat(cdir, "c1", is_global=False, group_id="mid")
    out = gi.collect_global_chats(home, exclude_project_id="O")
    assert [c.id for c in out] == ["c1"]


def test_full_folder_toggle_takes_effect_without_chat_write(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("f", None, False)])
    _write_chat(cdir, "c1", is_global=False, group_id="f")
    assert gi.collect_global_chats(home, "O") == []
    _write_groups(cdir, [("f", None, True)])
    out = gi.collect_global_chats(home, "O")
    assert [c.id for c in out] == ["c1"]


# -- collect_global_groups --------------------------------------------

def test_groups_inherited_child_surfaces(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("root", None, True), ("child", "root", False)])
    out = gi.collect_global_groups(home, exclude_project_id="O")
    ids = sorted(g.id for g in out)
    # both the own-global root and the inherited-global child cross over,
    # so the child can nest under its parent in the other project
    assert ids == ["child", "root"]


def test_groups_non_global_not_surfaced(home):
    cdir = _project(home, "P")
    _write_groups(cdir, [("a", None, False), ("b", "a", False)])
    out = gi.collect_global_groups(home, exclude_project_id="O")
    assert out == []
