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

The result still goes through ``task_card_write_guard`` and the normal
save, so the permission invariant is enforced on the merged tree
rather than re-implemented here.  This module is pure: dicts in, dict
out, no storage.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

# Fields that describe STRUCTURE or IDENTITY; an edit may not touch them.
# Restructuring is what the whole-``root`` path is for, and ``id`` is the
# key of the signed-approval ledger.
_STRUCTURAL_FIELDS = frozenset({"id", "body", "block_type", "scope"})

# The only dotted key allowed: ``cwd`` is the one scope field that is a
# setting rather than a grant (see task_card_write_guard._normalize_scope).
_ALLOWED_DOTTED = frozenset({"scope.cwd"})


def _block_fields() -> frozenset:
    from app.models.task_card import Block
    return frozenset(Block.model_fields)


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
    if key in _ALLOWED_DOTTED:
        head, _, leaf = key.partition(".")
        sub = block.get(head)
        if not isinstance(sub, dict):
            sub = {}
            block[head] = sub
        sub[leaf] = value
        return None
    if "." in key:
        return (f"{key!r}: only {sorted(_ALLOWED_DOTTED)} may be set with a "
                f"dotted key; other scope fields are permissions and are "
                f"not editable here")
    if key in _STRUCTURAL_FIELDS:
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
    they intersect).  ``set`` keys are Block fields, or ``scope.cwd``.
    An edit that matches no block, or sets a disallowed field, is a
    problem; when there is any problem the ORIGINAL root is returned
    untouched, so a partially applied edit list can never be saved.
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
