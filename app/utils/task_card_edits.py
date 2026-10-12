"""
Targeted block edits for the agent-facing ``task_card_write`` tool.

``task_card_write`` originally accepted only a whole replacement ``root``.
Observed on a real nightly card (2026-10-09): to set ``scope.cwd`` on
each task and reword one preamble, the model dumped the ~100 KB card to
/tmp from a shell, stripped defaults with a second script to get it to
64 KB, and then had to paste that back as a tool argument -- a round
trip through two processes to change a dozen short fields, with every
byte of the unchanged tree a chance to drop an id and orphan a signed
approval.  An ``edits`` list applied server-side to the STORED tree
removes the round trip and, because the tree the model never mentioned
is never rewritten, removes that accident too.

Scope fields (2026-10-11).  The first version let ``edits`` set only
``scope.cwd``; every other scope field was refused with "ask the user".
Observed consequence: a model that needed to change a block's scope
concluded the sanctioned tool could not do it and reached for a
``python3`` one-liner to rewrite the deck file on disk.  That gains
nothing -- the deck file is not the floor.  Privilege-bearing grants
(``shell_commands`` and out-of-floor writable ``paths``) take effect
only when a root-signed approval record matches the block's *current*
escalation hash (app/storage/scope_approvals.py), so an unsigned edit
simply runs at the floor.  The refusal was therefore protecting
nothing while teaching the model to go around the tool.

The rule now is the one the signing hash already encodes: an edit may
set any ``TaskScope`` field, and the resulting tree may leave each leaf
task's escalation unchanged or NARROW it, but may never WIDEN it.
Fields the hash does not cover (``tools``, ``skills``, ``model_*``,
``service_tier``, ``shell_timeout_secs``, ``cwd``, read-only or
floor-covered paths) are plain settings and save without ceremony.  A
narrowing edit saves too, and ``escalation_changes`` reports the blocks
whose existing approval no longer matches so the caller can say so.

The result still goes through the normal save.  This module is pure:
dicts in, dict out, no storage.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Fields that describe STRUCTURE or IDENTITY; an edit may not touch them.
# Restructuring is what the whole-``root`` path is for, and ``id`` is the
# key of the signed-approval ledger.  ``scope`` as a whole is refused
# so a sparse replacement cannot silently blank fields the model did
# not mention; its fields are set one at a time with ``scope.<field>``.
_STRUCTURAL_FIELDS = frozenset({"id", "body", "block_type", "scope"})

# The sanctioned route for a grant the model may not write itself.
# Quoted in refusals so the model learns the mechanism instead of
# inferring that the tool is simply broken.
SIGNING_NOTE = (
    "Grants are not authorized by the card file: shell_commands and "
    "writable paths outside the default safe-write paths take effect only "
    "when a root-signed approval (sudo ziya-approve, via the Task Card "
    "editor's escalation panel) matches the block's current scope, and an "
    "unsigned grant runs at the permission floor.  Editing the deck file "
    "by any other means therefore gains nothing.  Ask the user to add the "
    "grant in the Task Card editor and sign it."
)


def _block_fields() -> frozenset:
    from app.models.task_card import Block
    return frozenset(Block.model_fields)


def _scope_fields() -> frozenset:
    from app.models.task_card import TaskScope
    return frozenset(TaskScope.model_fields)


def _walk(root: Dict[str, Any]):
    yield root
    for child in root.get("body") or []:
        if isinstance(child, dict):
            yield from _walk(child)


def _targets(root: Dict[str, Any], edit: Dict[str, Any]) -> List[Dict[str, Any]]:
    bid = edit.get("block_id")
    btype = edit.get("block_type")
    out = []
    for b in _walk(root):
        if bid is not None and b.get("id") != bid:
            continue
        if btype is not None and b.get("block_type") != btype:
            continue
        out.append(b)
    return out


def _set_field(block: Dict[str, Any], key: str, value: Any) -> Optional[str]:
    """Apply one ``key: value``; return a problem string or None."""
    if "." in key:
        head, _, leaf = key.partition(".")
        if head != "scope" or not leaf:
            return (f"{key!r}: the only dotted keys are 'scope.<field>' "
                    f"(one of {sorted(_scope_fields())})")
        if leaf not in _scope_fields():
            return (f"{key!r}: {leaf!r} is not a TaskScope field "
                    f"(one of {sorted(_scope_fields())})")
        sub = block.get("scope")
        if not isinstance(sub, dict):
            sub = {}
        candidate = dict(sub)
        candidate[leaf] = value
        try:
            from app.models.task_card import TaskScope
            TaskScope(**candidate)
        except Exception as exc:  # noqa: BLE001 — report the validator's words
            return f"{key!r}: invalid value for {leaf!r}: {exc}"
        block["scope"] = candidate
        return None
    if key in _STRUCTURAL_FIELDS:
        if key == "scope":
            return ("'scope': set individual fields with 'scope.<field>' "
                    "keys rather than replacing the whole scope")
        return (f"{key!r} is structural; submit a replacement root to "
                f"change it")
    if key not in _block_fields():
        return f"{key!r} is not a Block field (check the spelling)"
    block[key] = value
    return None


def apply_block_edits(
    root: Dict[str, Any], edits: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], int, List[str]]:
    """Return ``(new_root, blocks_touched, problems)``.

    Each edit is ``{"block_id": ..., "set": {...}}`` or
    ``{"block_type": ..., "set": {...}}`` (both selectors may be given;
    they intersect).  ``set`` keys are Block fields, or ``scope.<field>``
    for any TaskScope field.  An edit that matches no block, or sets a
    disallowed field, is a problem; when there is any problem the
    ORIGINAL root is returned untouched, so a partially applied edit
    list can never be saved.

    This function applies edits only; whether the resulting scopes are
    permitted is ``escalation_changes``'s job.
    """
    new_root = copy.deepcopy(root)
    problems: List[str] = []
    touched = 0
    for i, edit in enumerate(edits or []):
        if not isinstance(edit, dict):
            problems.append(f"edits[{i}] is not an object")
            continue
        if edit.get("block_id") is None and edit.get("block_type") is None:
            problems.append(f"edits[{i}] needs block_id or block_type")
            continue
        fields = edit.get("set")
        if not isinstance(fields, dict) or not fields:
            problems.append(f"edits[{i}] has no non-empty 'set' object")
            continue
        targets = _targets(new_root, edit)
        if not targets:
            sel = edit.get("block_id") or f"block_type={edit.get('block_type')}"
            problems.append(f"edits[{i}]: no block matches {sel!r}")
            continue
        for b in targets:
            for key, value in fields.items():
                problem = _set_field(b, str(key), value)
                if problem:
                    problems.append(f"edits[{i}]: {problem}")
            touched += 1
    if problems:
        return root, 0, problems
    return new_root, touched, []


# ── Escalation comparison ───────────────────────────────────────

def _leaf_escalations(root: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{leaf task id: escalation block} over the block's own scope and
    every ancestor's, mirroring what the runtime gate hashes under that
    id (app/api/task_cards._escalation_rows).  Deck- and card-level
    scopes are not in the tree and cannot be changed by ``edits``, so
    leaving them out does not affect the comparison."""
    from app.config import scope_canonical as sc
    from app.models.task_card import TaskScope

    def _scope(b: Dict[str, Any]) -> Optional[TaskScope]:
        s = b.get("scope")
        if not isinstance(s, dict) or not s:
            return None
        try:
            return TaskScope(**s)
        except Exception:  # noqa: BLE001 — apply_block_edits validated already
            return None

    out: Dict[str, Dict[str, Any]] = {}

    def _visit(b: Dict[str, Any], ancestors: List[Optional[TaskScope]]) -> None:
        chain = ancestors + [_scope(b)]
        if b.get("block_type") == "task":
            out[str(b.get("id") or "")] = sc.task_escalation_block(*chain)
        for child in b.get("body") or []:
            if isinstance(child, dict):
                _visit(child, chain)

    _visit(root, [])
    return out


def escalation_changes(
    old_root: Dict[str, Any], new_root: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    """Return ``(widened, narrowed)`` -- findings and block ids.

    ``widened`` holds one finding per leaf task whose escalation after
    the edit grants a shell command or writable path it did not grant
    before; saving such a tree is refused because the tool's charter
    does not include authoring privilege.  ``narrowed`` holds the ids
    of leaf tasks whose escalation changed without widening and is
    still non-empty: strictly less privilege, saved, but the existing
    approval record no longer matches and the block runs at the floor
    until it is re-signed.  A block whose escalation became empty needs
    nothing and is reported in neither list.
    """
    before = _leaf_escalations(old_root)
    after = _leaf_escalations(new_root)
    widened: List[str] = []
    narrowed: List[str] = []
    for bid, new in after.items():
        old = before.get(bid, {})
        if new == old:
            continue
        extra_cmds = sorted(set(new.get("shell_commands", []))
                            - set(old.get("shell_commands", [])))
        extra_paths = sorted(set(new.get("writable_paths", []))
                             - set(old.get("writable_paths", [])))
        if extra_cmds or extra_paths:
            parts = []
            if extra_cmds:
                parts.append(f"shell_commands {extra_cmds}")
            if extra_paths:
                parts.append(f"writable paths {extra_paths}")
            widened.append(
                f"task '{bid}' would gain {' and '.join(parts)} it is not "
                f"granted today"
            )
        elif new:
            narrowed.append(bid)
    return widened, narrowed
