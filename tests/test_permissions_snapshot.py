"""Tests for the permissions-snapshot helper used by TaskRun A3."""
from __future__ import annotations

import pytest

from app.models.task_card import Block, TaskScope, ScopeEntry
from app.utils.permissions_snapshot import (
    build_permissions_snapshot,
    SCHEMA_VERSION,
)


def _task(id_: str, name: str = "", scope: TaskScope = None, body=None) -> Block:
    return Block(
        id=id_,
        name=name,
        block_type="task",
        instructions="…",
        scope=scope,
        body=body or [],
    )


def _parallel(id_: str, body) -> Block:
    return Block(id=id_, name="par", block_type="parallel", body=body)


# ── shape / version ───────────────────────────────────────────────


def test_snapshot_has_required_top_level_keys():
    snap = build_permissions_snapshot(root_block=_task("a"), project_root="/p")
    assert snap["schema_version"] == SCHEMA_VERSION
    assert isinstance(snap["captured_at"], int) and snap["captured_at"] > 0
    assert snap["project_root"] == "/p"
    assert "base_policy" in snap
    assert "block_scopes" in snap


def test_project_root_none_preserved():
    snap = build_permissions_snapshot(root_block=_task("a"), project_root=None)
    assert snap["project_root"] is None


# ── empty-scope behavior ───────────────────────────────────────────


def test_block_with_no_scope_omitted():
    snap = build_permissions_snapshot(root_block=_task("a"), project_root="/p")
    assert snap["block_scopes"] == {}


def test_block_with_empty_scope_omitted():
    """A scope object with all-empty fields is indistinguishable from
    no scope and should be omitted to keep the snapshot small."""
    empty = TaskScope(paths=[], tools=[], skills=[], shell_commands=[])
    snap = build_permissions_snapshot(
        root_block=_task("a", scope=empty), project_root="/p")
    assert snap["block_scopes"] == {}


# ── single-block scope capture ─────────────────────────────────────


def test_paths_serialised_with_all_flags():
    scope = TaskScope(paths=[
        ScopeEntry(path="/p/src", is_dir=True, read=True, write=True, context=False),
        ScopeEntry(path="/p/notes.md", is_dir=False, read=True, write=False, context=True),
    ])
    snap = build_permissions_snapshot(
        root_block=_task("a", "Edit", scope=scope), project_root="/p")
    bs = snap["block_scopes"]["a"]
    assert bs["block_name"] == "Edit"
    assert bs["block_type"] == "task"
    assert bs["paths"] == [
        {"path": "/p/src", "is_dir": True, "read": True,
         "write": True, "context": False},
        {"path": "/p/notes.md", "is_dir": False, "read": True,
         "write": False, "context": True},
    ]


def test_tools_skills_shell_grants_captured():
    scope = TaskScope(
        tools=["file_write", "file_read"],
        skills=["debug_mode"],
        shell_commands=["pytest", "re:^make\\s+test$"],
    )
    snap = build_permissions_snapshot(
        root_block=_task("a", scope=scope), project_root="/p")
    bs = snap["block_scopes"]["a"]
    assert bs["tools"] == ["file_write", "file_read"]
    assert bs["skills"] == ["debug_mode"]
    assert bs["shell_commands"] == ["pytest", "re:^make\\s+test$"]


def test_cwd_captured_when_set():
    scope = TaskScope(paths=[], cwd="/sub/dir")
    snap = build_permissions_snapshot(
        root_block=_task("a", scope=scope), project_root="/p")
    assert snap["block_scopes"]["a"]["cwd"] == "/sub/dir"


# ── recursion through body ─────────────────────────────────────────


def test_walks_nested_body_collecting_all_scoped_blocks():
    inner_scope = TaskScope(paths=[
        ScopeEntry(path="/p/x", is_dir=False, read=True, write=True),
    ])
    outer_scope = TaskScope(tools=["file_read"])
    root = _parallel("root", body=[
        _task("child1", scope=inner_scope),
        _task("child2"),                     # no scope — omitted
        _parallel("child3", body=[
            _task("grandchild", scope=outer_scope),
        ]),
    ])
    snap = build_permissions_snapshot(root_block=root, project_root="/p")
    assert set(snap["block_scopes"].keys()) == {"child1", "grandchild"}
    assert snap["block_scopes"]["child1"]["paths"][0]["write"] is True
    assert snap["block_scopes"]["grandchild"]["tools"] == ["file_read"]


def test_root_block_with_scope_is_captured():
    scope = TaskScope(tools=["file_read"])
    root = _task("root", scope=scope)
    snap = build_permissions_snapshot(root_block=root, project_root="/p")
    assert "root" in snap["block_scopes"]


# ── base policy snapshot ───────────────────────────────────────────


def test_base_policy_includes_safe_paths_and_patterns(monkeypatch):
    """The snapshot pulls the live WritePolicyManager state."""
    from app.config import write_policy as wp_mod

    class FakeMgr:
        def get_policy(self):
            return {
                "safe_write_paths": [".ziya/", "/tmp/"],
                "allowed_write_patterns": ["*.md", "tests/**"],
                "direct_write_mode": "claude",
            }

    monkeypatch.setattr(
        wp_mod, "get_write_policy_manager", lambda: FakeMgr())
    snap = build_permissions_snapshot(root_block=_task("a"), project_root="/p")
    assert snap["base_policy"] == {
        "safe_write_paths": [".ziya/", "/tmp/"],
        "allowed_write_patterns": ["*.md", "tests/**"],
        "direct_write_mode": "claude",
    }


def test_base_policy_resilient_to_manager_failure(monkeypatch):
    """If the policy manager raises, the snapshot still returns a
    well-formed dict with an empty base_policy — the run shouldn't
    fail to launch over a snapshot bug."""
    from app.config import write_policy as wp_mod

    def boom():
        raise RuntimeError("uninitialised")

    monkeypatch.setattr(wp_mod, "get_write_policy_manager", boom)
    snap = build_permissions_snapshot(root_block=_task("a"), project_root="/p")
    assert snap["base_policy"] == {}
    # Other top-level fields still populated.
    assert snap["schema_version"] == SCHEMA_VERSION
    assert snap["project_root"] == "/p"


# ── shape regressions ─────────────────────────────────────────────


def test_paths_default_flags_normalised():
    """ScopeEntry has defaults read=True, write=False, context=False
    — verify those round-trip as bools (not None / missing)."""
    scope = TaskScope(paths=[ScopeEntry(path="/p/file.txt")])
    snap = build_permissions_snapshot(
        root_block=_task("a", scope=scope), project_root="/p")
    e = snap["block_scopes"]["a"]["paths"][0]
    assert e["read"] is True
    assert e["write"] is False
    assert e["context"] is False
    assert e["is_dir"] is False


def test_lists_are_actual_lists_not_pydantic_internal_types():
    """Snapshot must JSON-serialise cleanly; nested pydantic types
    would break that."""
    import json
    scope = TaskScope(
        paths=[ScopeEntry(path="/p/x", read=True, write=True)],
        tools=["t1"], skills=["s1"], shell_commands=["pytest"],
    )
    snap = build_permissions_snapshot(
        root_block=_task("a", scope=scope), project_root="/p")
    # If any field is non-JSON, this raises.
    json.dumps(snap)


# ── deck / card scope hierarchy ────────────────────────────────────


def test_deck_scope_alone_makes_scopeless_block_appear():
    """A block with no scope of its own is still captured when the
    deck (project-wide) scope grants something — the snapshot records
    the EFFECTIVE scope, matching what the executor actually grants."""
    deck = TaskScope(tools=["deck-tool"])
    snap = build_permissions_snapshot(
        root_block=_task("a"), project_root="/p", deck_scope=deck)
    assert snap["block_scopes"]["a"]["tools"] == ["deck-tool"]


def test_card_scope_alone_makes_scopeless_block_appear():
    card = TaskScope(shell_commands=["pytest"])
    snap = build_permissions_snapshot(
        root_block=_task("a"), project_root="/p", card_scope=card)
    assert snap["block_scopes"]["a"]["shell_commands"] == ["pytest"]


def test_deck_card_and_leaf_scopes_merge_additively():
    deck = TaskScope(tools=["deck-tool"])
    card = TaskScope(skills=["debug_mode"])
    leaf_scope = TaskScope(shell_commands=["pytest"])
    snap = build_permissions_snapshot(
        root_block=_task("a", scope=leaf_scope), project_root="/p",
        deck_scope=deck, card_scope=card)
    bs = snap["block_scopes"]["a"]
    assert bs["tools"] == ["deck-tool"]
    assert bs["skills"] == ["debug_mode"]
    assert bs["shell_commands"] == ["pytest"]


def test_ancestor_scope_flows_to_nested_leaf():
    """A container block's own scope must be captured on every leaf
    beneath it, not just re-recorded once for the container itself."""
    inner_scope = TaskScope(tools=["leaf-tool"])
    outer_scope = TaskScope(shell_commands=["make test"])
    root = _parallel("root", body=[
        _task("child", scope=inner_scope),
    ])
    root.scope = outer_scope
    snap = build_permissions_snapshot(root_block=root, project_root="/p")
    assert snap["block_scopes"]["child"]["tools"] == ["leaf-tool"]
    assert snap["block_scopes"]["child"]["shell_commands"] == ["make test"]
    # The container itself is ALSO captured (its own scope is non-empty).
    assert snap["block_scopes"]["root"]["shell_commands"] == ["make test"]


def test_no_deck_or_card_scope_preserves_prior_behavior():
    """Omitting deck_scope/card_scope (the default) must reproduce the
    exact pre-hierarchy behavior — a purely additive change."""
    scope = TaskScope(tools=["file_read"])
    snap = build_permissions_snapshot(
        root_block=_task("a", scope=scope), project_root="/p")
    assert snap["block_scopes"]["a"]["tools"] == ["file_read"]
