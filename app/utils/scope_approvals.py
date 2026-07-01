"""
Signed task-scope approval store (ASR F-001, design doc §4.1/§4.2).

Authorization ledger for *task* escalations (Task Cards and CLI tasks): the
privilege-bearing fields of a task's scope — ``shell_commands`` and writable
``paths`` — take effect at execution time ONLY if a signed approval record
exists whose ``scope_hash`` matches the task's *current* scope.

Design (dcohn's decision, task-authorization-design.md §4.1/§4.2): a SEPARATE
store keyed by ``(task_id, scope_hash)``, NOT a signature embedded on the card.
Task-definition files therefore stay plain and hand-editable, approvals are
portable, the whole gate can be disabled by clearing this store, and it is the
single signed audit ledger of authorized escalations. Records are signed by the
root-owned Ed25519 key via ``ziya-approve`` and verified with the world-readable
public key. The store lives in plain ``~/.ziya/scope_approvals/`` — the agent may
read or even delete records but cannot forge one (it never holds the private
key, §4.4).

Fail-closed: no record, a record whose ``scope_hash`` no longer matches (scope
edited), or a record whose signature does not verify -> escalation NOT
authorized; the task runs at the default floor.

Note: lives in ``app/utils/`` alongside the ``ziya-approve`` signer (rather than
``app/storage/``) so the signer and the gate are co-located; the design doc
names ``app/storage/`` but the placement is cosmetic — integrity comes from the
signature, not the module path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import scope_canonical as sc
from app.utils.logging_utils import logger


def _store_dir() -> Path:
    """The approval-record directory. Honors ZIYA_SCOPE_APPROVALS_DIR for tests."""
    override = os.environ.get("ZIYA_SCOPE_APPROVALS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".ziya" / "scope_approvals"


def _record_path(task_id: str) -> Path:
    # task_id is a block id like "b-1a2b3c4d"; guard against path traversal from
    # a hand-edited / malicious card id.
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in ("-", "_"))
    if not safe:
        safe = "_invalid_"
    return _store_dir() / f"{safe}.json"


def get_record(task_id: str) -> Optional[Dict[str, Any]]:
    """Return the stored approval record for *task_id*, or None."""
    path = _record_path(task_id)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"scope_approvals: unreadable record {path}: {e}")
        return None


def save_record(record: Dict[str, Any]) -> Path:
    """Persist an already-signed approval record. Used by ziya-approve."""
    path = _record_path(record["task_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
    return path


def is_scope_authorized(task_id: str, scope: Any,
                        public_key_path: Optional[str] = None) -> bool:
    """True iff *scope*'s escalation is authorized for *task_id*.

    Returns True when the scope carries no escalation at all (nothing to
    authorize — it runs at the floor regardless). Otherwise requires a stored
    record whose signature verifies AND whose ``scope_hash`` equals the scope's
    CURRENT hash. Any mismatch / missing / unverifiable record -> False.
    """
    current_hash = sc.task_scope_hash(scope)
    if not current_hash:
        return True  # no privilege-bearing escalation; nothing to authorize

    record = get_record(task_id)
    if record is None:
        logger.info(f"🔒 SCOPE_AUTHZ: no approval record for task {task_id!r} "
                    f"(hash {current_hash[:12]}…) — escalation denied")
        return False

    if record.get("scope_hash") != current_hash:
        logger.info(f"🔒 SCOPE_AUTHZ: approval for task {task_id!r} is for a "
                    f"different scope (stored {str(record.get('scope_hash'))[:12]}… "
                    f"!= current {current_hash[:12]}…) — escalation denied")
        return False

    if not sc.verify_approval_record(record, public_key_path):
        logger.warning(f"🔒 SCOPE_AUTHZ: approval record for task {task_id!r} "
                       f"failed signature verification — escalation denied")
        return False

    return True


class _UnauthorizedScope:
    """A scope stand-in that grants no escalation (default-deny / floor).

    Mirrors the TaskScope read-surface ``execute_task_block`` consumes:
    ``shell_commands`` empty and every path stripped of its write flag, while
    non-privilege fields (tools, skills, cwd, readable paths) fall through to the
    original so the task still runs, just un-escalated.
    """

    def __init__(self, original: Any):
        self._original = original

    def __getattr__(self, name: str) -> Any:
        if name == "shell_commands":
            return []
        return getattr(self._original, name)

    @property
    def paths(self):
        out = []
        for e in (getattr(self._original, "paths", []) or []):
            if getattr(e, "write", False):
                try:
                    out.append(e.model_copy(update={"write": False}))
                except AttributeError:
                    out.append(e)  # non-pydantic; best effort
            else:
                out.append(e)
        return out


def authorize_scope(task_id: str, scope: Any,
                    public_key_path: Optional[str] = None) -> Any:
    """Single chokepoint: return *scope* if authorized, else a floor-only scope.

    Both the CLI (``apply_task_permissions``) and card (``execute_task_block``)
    paths call this before activating any escalation. A scope with no escalation
    is returned unchanged. An escalating scope is returned only if a valid signed
    approval matches its current hash; otherwise an ``_UnauthorizedScope`` that
    strips the privilege-bearing grants is returned so the task runs at the
    floor rather than failing outright.
    """
    if is_scope_authorized(task_id, scope, public_key_path):
        return scope
    return _UnauthorizedScope(scope)


# ── CLI-task (tasks.yaml) authorization ───────────────────────────────────────
# CLI tasks escalate via an ``allow`` block (commands / git_operations /
# write_patterns) keyed by name within a tasks.yaml file, NOT via a card scope.
# They authorize through the SAME signed store and chokepoint as cards (design
# §6 unification) — only the hash projection differs (cli_task_hash over the
# ``allow`` shape vs task_scope_hash over the card scope). The store key is
# ``cli:<realpath-of-tasks-file>#<name>`` so it is stable regardless of which
# symlinked path the user cd'd through to reach the project (computed identically
# at mint time in ziya-approve and at verify time in cmd_task).


def cli_task_key(tasks_file: str, name: str) -> str:
    """Canonical approval-store key for a CLI task.

    ``os.path.realpath`` collapses symlinks so the same physical tasks file
    yields one key no matter which alias path reached it.
    """
    return f"cli:{os.path.realpath(tasks_file)}#{name}"


def is_cli_task_authorized(task_key: str, allow: Any,
                           public_key_path: Optional[str] = None) -> bool:
    """True iff the CLI task's ``allow`` escalation is authorized for *task_key*.

    Returns True when ``allow`` carries no escalation (nothing to authorize — it
    runs at the floor regardless). Otherwise requires a stored record whose
    signature verifies AND whose ``scope_hash`` equals the allow block's CURRENT
    hash. Any mismatch / missing / unverifiable record -> False (fail-closed).
    """
    current_hash = sc.cli_task_hash(allow)
    if not current_hash:
        return True  # no escalation in the allow block

    record = get_record(task_key)
    if record is None:
        logger.info(f"🔒 SCOPE_AUTHZ: no approval record for CLI task {task_key!r} "
                    f"(hash {current_hash[:12]}…) — escalation denied")
        return False
    if record.get("scope_hash") != current_hash:
        logger.info(f"🔒 SCOPE_AUTHZ: approval for CLI task {task_key!r} is for a "
                    f"different allow block (stored "
                    f"{str(record.get('scope_hash'))[:12]}… != current "
                    f"{current_hash[:12]}…) — escalation denied")
        return False
    if not sc.verify_approval_record(record, public_key_path):
        logger.warning(f"🔒 SCOPE_AUTHZ: approval record for CLI task {task_key!r} "
                       f"failed signature verification — escalation denied")
        return False
    return True
