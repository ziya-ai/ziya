"""Resolution of Call-block targets.

A Call block names a unit of work defined elsewhere.  Two namespaces are
supported, and they authorize through two DIFFERENT signed-ledger
projections, which is the whole reason this module exists:

* ``card``      — another task card in the same project.  Its blocks carry
  their own ``scope``, authorized per-block by
  ``scope_approvals.authorize_scope(block.id, scope)`` inside
  ``execute_task_block``.  Nothing needs doing here: returning the card's
  own root plus its own ``card_scope`` is sufficient, PROVIDED the caller
  resets the scope frame (see ``_execute_call``).

* ``file_task`` — a named entry in the merged ``tasks.yaml`` set.  Its
  escalation is an ``allow`` block authorized under the key
  ``cli:<realpath>#<name>`` via ``is_cli_task_authorized`` — a different
  hash projection over a different shape.  That check is performed HERE,
  at resolution time, and the result is returned as an explicit
  pre-authorized grant set.  It deliberately does NOT become a
  ``TaskScope`` on the synthetic block: a scope would be re-hashed by
  ``authorize_scope`` against the synthetic block's id, for which no
  approval record can possibly exist, so an approved file task would be
  demoted to the floor by the very gate meant to protect it.

File tasks are call TARGETS only.  ``ziya task <name>`` runs a single
conversational turn (``CLI.ask``) and never enters the block engine, so
there is no dispatcher on that side for a call to hang off.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models.task_card import Block, TaskScope

logger = logging.getLogger(__name__)


class CallResolutionError(Exception):
    """A call target could not be resolved to something runnable."""


@dataclass
class ResolvedCall:
    """A call target reduced to an executable tree plus its own grants."""

    kind: str
    #: Canonical identity, used for cycle detection.  Keyed by card ID (not
    #: name) so calling the same card under two different names is still
    #: recognised as the same node in the call graph.
    key: str
    label: str
    root: Block
    #: The callee's own card-level scope.  Replaces — never merges with —
    #: the caller's for the duration of the call.
    card_scope: Optional[TaskScope] = None
    #: Grants already authorized against the CLI ledger by this module.
    #: Empty for card targets, which authorize per-block downstream.
    shell_grants: List[str] = field(default_factory=list)
    writable_grants: List[Dict[str, Any]] = field(default_factory=list)
    #: Advisory notes to fold into the call block's artifact decisions —
    #: e.g. "ran at the floor because the escalation is unapproved".
    notes: List[str] = field(default_factory=list)


def resolve_call_target(
    target: str,
    kind: Optional[str],
    *,
    project_id: Optional[str],
    project_root: Optional[str],
) -> ResolvedCall:
    """Resolve ``target`` in the namespace named by ``kind``.

    ``kind`` of None means ``"card"``.  Raises ``CallResolutionError`` with
    an operator-readable message on any failure; the caller turns that into
    a failed artifact rather than an exception, so ``on_failure`` policy
    governs what a bad call does to the surrounding sequence.
    """
    target = (target or "").strip()
    if not target:
        raise CallResolutionError("call block has no call_target")
    resolved_kind = (kind or "card").strip() or "card"
    if resolved_kind == "card":
        return _resolve_card(target, project_id)
    if resolved_kind == "file_task":
        return _resolve_file_task(target, project_root)
    raise CallResolutionError(
        f"unknown call_target_kind {resolved_kind!r} "
        f"(expected 'card' or 'file_task')"
    )


def _resolve_card(target: str, project_id: Optional[str]) -> ResolvedCall:
    if not project_id:
        raise CallResolutionError(
            "calling a task card requires a project id; this run has none"
        )
    from ..storage.task_cards import TaskCardStorage
    from ..utils.paths import get_project_dir

    storage = TaskCardStorage(get_project_dir(project_id))
    card = storage.get(target)
    if card is None:
        # Name fallback, matching how the deck labels a card and how
        # ``cli_card_runner.resolve_card`` addresses one.
        wanted = target.lower()
        for candidate in storage.list():
            if candidate.name.lower() == wanted:
                card = candidate
                break
    if card is None:
        raise CallResolutionError(f"no task card matches {target!r}")
    if card.root is None:
        raise CallResolutionError(f"task card {card.name!r} has no root block")
    return ResolvedCall(
        kind="card",
        key=f"card:{card.id}",
        label=card.name or card.id,
        root=card.root,
        card_scope=card.scope,
    )


def _resolve_file_task(target: str, project_root: Optional[str]) -> ResolvedCall:
    from ..task_runner import (
        allow_to_task_scope,
        load_tasks,
        resolve_task_source_file,
        validate_task_allow,
    )

    root = project_root or os.getcwd()
    tasks = load_tasks(root)
    task_def = tasks.get(target)
    if task_def is None:
        raise CallResolutionError(
            f"no file task named {target!r} in tasks.yaml for {root}"
        )
    prompt = (task_def.get("prompt") or "").strip()
    if not prompt:
        raise CallResolutionError(f"file task {target!r} has an empty prompt")

    notes: List[str] = []
    allow = task_def.get("allow")
    if allow:
        errors = validate_task_allow(task_def)
        if errors:
            # Invalid, not merely unapproved: refuse rather than silently
            # running a task whose stated permissions were unparseable.
            raise CallResolutionError(
                f"file task {target!r} has an invalid allow block: "
                + "; ".join(errors)
            )
        from ..utils.scope_approvals import cli_task_key, is_cli_task_authorized

        src = resolve_task_source_file(target, root)
        key = cli_task_key(str(src), target) if src else f"cli:?#{target}"
        if not is_cli_task_authorized(key, allow):
            logger.warning(
                "🔒 CALL: escalation for file task %r is not approved — "
                "running at the default floor", target,
            )
            notes.append(
                f"call: escalation for file task {target!r} is not approved "
                f"— ran at the default floor"
            )
            allow = None

    shell_grants, writable_grants = allow_to_task_scope(allow)
    # A fresh id per call.  It must NOT collide with a real card block id,
    # because a scope-approval record is keyed by block id and a collision
    # would let this synthetic block borrow someone else's approval.
    synthetic = Block(
        block_type="task",
        id=f"call-{uuid.uuid4().hex[:8]}",
        name=f"file task: {target}",
        instructions=prompt,
    )
    return ResolvedCall(
        kind="file_task",
        key=f"file_task:{target}",
        label=target,
        root=synthetic,
        shell_grants=shell_grants,
        writable_grants=writable_grants,
        notes=notes,
    )
