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
            },
            ...
        },
    }
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
) -> None:
    """Recursively populate ``out`` with one entry per block whose
    EFFECTIVE scope (deck + card + every ancestor + its own, merged
    additively) is non-empty.  Empty-effective-scope blocks are
    skipped to keep the snapshot small and post-mortem-readable.

    ``ancestor_layers`` accumulates root→this-block's-parent scopes;
    the caller seeds it with [deck_scope, card_scope].
    """
    own_scope = getattr(block, "scope", None)
    effective = merge_scopes(*ancestor_layers, own_scope)
    scope_dict = _scope_to_dict(effective)
    if scope_dict is not None and (
        scope_dict["paths"] or scope_dict["tools"]
        or scope_dict["skills"] or scope_dict["shell_commands"]
        or scope_dict["cwd"]
    ):
        out[block.id] = {
            "block_name": block.name or "",
            "block_type": block.block_type,
            **scope_dict,
        }
    child_layers = ancestor_layers + [own_scope]
    for child in getattr(block, "body", []) or []:
        _walk_blocks(child, out, child_layers)


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
    """
    block_scopes: Dict[str, Dict[str, Any]] = {}
    _walk_blocks(root_block, block_scopes, [deck_scope, card_scope])
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": int(time.time() * 1000),
        "project_root": project_root,
        "base_policy": _base_policy_snapshot(),
        "block_scopes": block_scopes,
    }
