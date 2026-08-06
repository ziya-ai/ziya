"""
Permissions snapshot — captures the effective permissions a TaskRun
was granted at launch so post-mortem analysis can reconstruct what
the agent could and could not do.

Captured at launch (not lazily) because the underlying card scope
can be edited after the run ends, which would otherwise destroy the
historical record.  Stored as a free-form dict on TaskRun so the
schema can evolve without migrations — the frontend treats unknown
keys as opaque.

Snapshot shape (versioned via ``schema_version``):

    {
        "schema_version": 1,
        "captured_at": <unix_ms>,
        "project_root": "/abs/path" | None,
        "base_policy": {
            "safe_write_paths": [...],
            "allowed_write_patterns": [...],
            "direct_write_mode": "off" | "claude" | "all",
        },
        "block_scopes": {
            "<block_id>": {
                "block_name": "...",
                "block_type": "task" | ...,
                "paths": [{"path": ..., "is_dir": ..., "read": ..., "write": ..., "context": ...}, ...],
                "tools": [...],
                "skills": [...],
                "shell_commands": [...],
                "cwd": "..." | None,
                # Present only on blocks that entered the run through a
                # Call block.  Their scope comes from the CALLEE's own
                # hierarchy, not the caller's — see the module note below.
                "via_call": {"call_block_id": ..., "target": ..., "kind": ...},
            },
            ...
        },
    }

Call blocks are the one shape this cannot capture at launch.  A Call names
its target and resolves it at run time, so the callee's blocks are not in
the tree being walked here and its ``body`` is empty.  They are appended
when the call actually executes, via ``build_block_scopes`` +
``TaskRunStorage.record_call``.  That is recording history, not rewriting
it: existing entries are never overwritten, so the "captured once at
launch" guarantee still holds for everything the launch could see.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..models.task_card import Block, TaskScope, merge_scopes


SCHEMA_VERSION = 1


def _scope_to_dict(scope) -> Optional[Dict[str, Any]]:
    """Serialize a TaskScope (or None) into a plain dict."""
    if scope is None:
        return None
    return {
        "paths": [
            {
                "path": e.path,
                "is_dir": bool(e.is_dir),
                "read": bool(e.read),
                "write": bool(e.write),
                "context": bool(e.context),
            }
            for e in (scope.paths or [])
        ],
        "tools": list(scope.tools or []),
        "skills": list(scope.skills or []),
        "shell_commands": list(scope.shell_commands or []),
        "cwd": scope.cwd,
    }


def _walk_blocks(
    block: Block,
    out: Dict[str, Dict[str, Any]],
    ancestor_layers: List[Optional[TaskScope]],
    via_call: Optional[Dict[str, Any]] = None,
) -> None:
    """Recursively populate ``out`` with one entry per block whose
    EFFECTIVE scope (deck + card + every ancestor + its own, merged
    additively) is non-empty.  Empty-effective-scope blocks are
    skipped to keep the snapshot small and post-mortem-readable.

    ``ancestor_layers`` accumulates root→this-block's-parent scopes;
    the caller seeds it with [deck_scope, card_scope].

    ``via_call`` is stamped onto every entry when the subtree was reached
    through a Call block, so an auditor can tell a block the card itself
    declares from one a callee contributed.
    """
    own_scope = getattr(block, "scope", None)
    effective = merge_scopes(*ancestor_layers, own_scope)
    scope_dict = _scope_to_dict(effective)
    if scope_dict is not None and (
        scope_dict["paths"] or scope_dict["tools"]
        or scope_dict["skills"] or scope_dict["shell_commands"]
        or scope_dict["cwd"]
    ):
        entry = {
            "block_name": block.name or "",
            "block_type": block.block_type,
            **scope_dict,
        }
        if via_call:
            entry["via_call"] = dict(via_call)
        out[block.id] = entry
    child_layers = ancestor_layers + [own_scope]
    for child in getattr(block, "body", []) or []:
        _walk_blocks(child, out, child_layers, via_call=via_call)


def build_block_scopes(
    root_block: Block,
    *,
    deck_scope: Optional[TaskScope] = None,
    card_scope: Optional[TaskScope] = None,
    via_call: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Effective per-block scopes for a subtree, as ``block_scopes`` entries.

    Split out of ``build_permissions_snapshot`` so a Call block can compute
    the SAME shape for its callee at run time.  ``card_scope`` must be the
    CALLEE's own card scope, not the caller's — permissions do not cross a
    call boundary (see block_executor._execute_call), and recording the
    caller's would misdescribe what the callee was permitted to do.
    """
    out: Dict[str, Dict[str, Any]] = {}
    _walk_blocks(root_block, out, [deck_scope, card_scope], via_call=via_call)
    return out


def synthesize_grant_scope(
    block: Block,
    *,
    shell_commands: Optional[List[str]] = None,
    write_patterns: Optional[List[Dict[str, Any]]] = None,
    via_call: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """A ``block_scopes`` entry for grants that are not a ``TaskScope``.

    A file-task callee's ``allow`` block is authorized against the CLI
    ledger and passed to the executor as raw grant lists, deliberately
    never as a TaskScope (a scope would be re-hashed against a synthetic
    block id and denied).  It therefore has no scope for
    ``build_block_scopes`` to walk, and without this the block would run
    holding real write access while the audit trail recorded none.

    ``write_patterns`` are fnmatch globs, kept under their own key rather
    than coerced into ``paths``: a glob is not a path, and flattening it
    into one would make the audit claim a specific file was granted when
    a pattern was.  Consumers treat the key as an independent write
    signal (see run_outcome.summarize_side_effects).
    """
    patterns = [
        str(e.get("pattern")) for e in (write_patterns or [])
        if isinstance(e, dict) and e.get("pattern")
    ]
    cmds = [c for c in (shell_commands or []) if c]
    if not patterns and not cmds:
        return {}
    entry: Dict[str, Any] = {
        "block_name": block.name or "",
        "block_type": block.block_type,
        "paths": [],
        "tools": [],
        "skills": [],
        "shell_commands": cmds,
        "write_patterns": patterns,
        "cwd": None,
    }
    if via_call:
        entry["via_call"] = dict(via_call)
    return {block.id: entry} if block.id else {}


def _base_policy_snapshot() -> Dict[str, Any]:
    """Pull the three fields we care about from the active
    WritePolicyManager.  Defensive against import-order issues —
    if the policy isn't initialised we return an empty dict and
    the caller treats it as "default everything"."""
    try:
        from ..config.write_policy import get_write_policy_manager
        policy = get_write_policy_manager().get_policy()
        return {
            "safe_write_paths": list(policy.get("safe_write_paths", []) or []),
            "allowed_write_patterns": list(policy.get("allowed_write_patterns", []) or []),
            "direct_write_mode": policy.get("direct_write_mode", "none"),
        }
    except Exception:
        return {}


def build_permissions_snapshot(
    *,
    root_block: Block,
    project_root: Optional[str],
    deck_scope: Optional[TaskScope] = None,
    card_scope: Optional[TaskScope] = None,
) -> Dict[str, Any]:
    """Build the full permissions snapshot for a launching TaskRun.

    ``block_scopes`` records each block's EFFECTIVE (merged) scope —
    deck + card + every ancestor block's own scope + the block's own —
    matching exactly what the executor grants at run time (see
    app.agents.block_executor.ExecutionContext.effective_scope).
    Caller persists the return value on TaskRun.permissions_snapshot.

    A Call block's callee is NOT covered here — it is named, not inlined,
    so its tree does not exist until run time.  ``_execute_call`` appends
    it via ``build_block_scopes``; see the module docstring.
    """
    block_scopes = build_block_scopes(
        root_block, deck_scope=deck_scope, card_scope=card_scope,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": int(time.time() * 1000),
        "project_root": project_root,
        "base_policy": _base_policy_snapshot(),
        "block_scopes": block_scopes,
    }
