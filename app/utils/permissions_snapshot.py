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
from typing import Any, Dict, Optional

from ..models.task_card import Block


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


def _walk_blocks(block: Block, out: Dict[str, Dict[str, Any]]) -> None:
    """Recursively populate ``out`` with one entry per block that has
    a non-empty scope.  Empty-scope blocks are skipped to keep the
    snapshot small and post-mortem-readable."""
    scope_dict = _scope_to_dict(getattr(block, "scope", None))
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
    for child in getattr(block, "body", []) or []:
        _walk_blocks(child, out)


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
) -> Dict[str, Any]:
    """Build the full permissions snapshot for a launching TaskRun.

    Captures effective state at launch time.  Caller persists the
    return value on TaskRun.permissions_snapshot.
    """
    block_scopes: Dict[str, Dict[str, Any]] = {}
    _walk_blocks(root_block, block_scopes)
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": int(time.time() * 1000),
        "project_root": project_root,
        "base_policy": _base_policy_snapshot(),
        "block_scopes": block_scopes,
    }
