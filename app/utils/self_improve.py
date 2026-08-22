"""
Self-improving task cards — patch guard, lesson ledger, budgets.

When a container block (group / repeat / until / parallel) carries
``self_improve=True``, the block executor runs the block, asks a judge
whether a *tangible, outcome-affecting* text improvement exists, and if
so applies a field-whitelisted patch to the card and restarts that
level.  This module owns everything about that flow which is NOT the
model call or the executor loop:

  * the patch whitelist and its validation/application
    (``IMPROVABLE_TEXT_FIELDS``, ``validate_improve_patch``,
    ``apply_improve_patch``);
  * the structure fingerprint asserting that a patch changed text and
    ONLY text (``structure_fingerprint``) — the "text but not
    privilege" invariant, enforced belt-and-braces on top of the field
    whitelist;
  * the durable lesson ledger (``LessonLedger``) that carries what was
    learned across runs, so the judge on run N+1 sees run N's lessons
    instead of re-deriving (and possibly re-reverting) them;
  * revision budgets (``resolve_improve_max``, ``run_improve_ceiling``).

Design constraints this encodes (see design/task-cards.md
§Self-improvement):

  * Patches are keyed by EXISTING block id and may touch only
    ``instructions`` and ``state_context``.  Never a tree replacement —
    a tree replacement through TaskCardStorage.update would mint fresh
    ids for any block whose id was dropped, silently orphaning signed
    scope approvals (scope_approvals keys by block id) and dropping the
    block to the permission floor.  A patch that cannot change ids
    cannot orphan an approval; a patch that cannot touch ``scope``
    cannot widen privilege, and the Ed25519 approval hash
    (scope_canonical.task_scope_hash) covers only privilege-bearing
    fields, so a text-only patch keeps existing approvals valid.
  * Oscillation guard: a patch whose canonical hash was already applied
    for the same (card, block) is refused, so the loop cannot thrash
    A→B→A→B across runs.
  * Budgets bound the multiplicative cost of nested self-improving
    levels: per-block ``improve_max`` (user-settable; default
    DEFAULT_IMPROVE_MAX) plus a run-wide ceiling
    (ZIYA_TASK_IMPROVE_RUN_MAX).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger

# The ONLY fields a self-improvement patch may modify.  Everything else
# on a block — scope, ids, counts, conditions, structure — is out of
# bounds in v1.  Deliberately narrow: each additional field is more
# power to fix a weakness AND more surface for the model to weaken its
# own loop (e.g. lowering repeat_count to make the card look cheaper).
IMPROVABLE_TEXT_FIELDS = ("instructions", "state_context")

DEFAULT_IMPROVE_MAX = 2
"""Default per-block revision budget when ``improve_max`` is unset."""

DEFAULT_RUN_IMPROVE_CEILING = 10
"""Default run-wide cap on card edits, across ALL improving levels.

Nested self-improving levels multiply: 3 revisions inside 3 revisions
is 9 executions of the inner subtree.  The per-block budget bounds each
level; this bounds the product."""

MAX_RETAINED_LESSONS = 2000
"""Ledger cap — oldest records dropped past this (same pattern as
task_card_refusals.MAX_RETAINED_REFUSALS)."""

LESSONS_FILENAME = "task_card_lessons.jsonl"


# ── Canonicalization ────────────────────────────────────────────

def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def patch_hash(patch: Dict[str, Dict[str, str]]) -> str:
    """Stable hash of a patch's content, for the oscillation guard."""
    return hashlib.sha256(_canonical(patch)).hexdigest()


# ── Block-tree helpers (operate on plain dicts) ─────────────────

def collect_blocks_by_id(root: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Flatten a block tree (dict form) into {id: block_dict}."""
    out: Dict[str, Dict[str, Any]] = {}

    def _walk(b: Dict[str, Any]) -> None:
        if not isinstance(b, dict):
            return
        bid = b.get("id")
        if bid:
            out[bid] = b
        for child in b.get("body") or []:
            _walk(child)

    _walk(root)
    return out


def structure_fingerprint(root: Dict[str, Any]) -> str:
    """Hash of the tree with the improvable text fields stripped.

    Equal fingerprints before and after a patch prove the patch changed
    text and only text: ids, scopes, structure, counts, and conditions
    are all inside the fingerprint.  ``apply_improve_patch`` can only
    write whitelisted fields by construction; this is the independent
    check that stays true even if that function regresses.
    """
    stripped = copy.deepcopy(root)

    def _strip(b: Dict[str, Any]) -> None:
        if not isinstance(b, dict):
            return
        for f in IMPROVABLE_TEXT_FIELDS:
            b.pop(f, None)
        for child in b.get("body") or []:
            _strip(child)

    _strip(stripped)
    return hashlib.sha256(_canonical(stripped)).hexdigest()


def validate_improve_patch(
    patch: Any, subtree_root: Dict[str, Any],
) -> List[str]:
    """Return a list of errors; empty list == valid.

    Rules:
      * patch is {block_id: {field: str}} — non-empty
      * every block id must already exist in ``subtree_root`` (the
        improving block's own subtree — a level may only rewrite
        itself, not siblings or ancestors)
      * every field must be in IMPROVABLE_TEXT_FIELDS
      * every value must be a non-empty string
      * at least one field must actually differ from the current text
        (a no-op patch is an authoring/judging defect, not a revision)
    """
    errors: List[str] = []
    if not isinstance(patch, dict) or not patch:
        return ["patch must be a non-empty object of {block_id: {field: text}}"]
    blocks = collect_blocks_by_id(subtree_root)
    any_change = False
    for bid, fields in patch.items():
        if bid not in blocks:
            errors.append(f"unknown block id (or outside this level): {bid!r}")
            continue
        if not isinstance(fields, dict) or not fields:
            errors.append(f"patch for {bid!r} must be a non-empty field map")
            continue
        for fname, value in fields.items():
            if fname not in IMPROVABLE_TEXT_FIELDS:
                errors.append(
                    f"field {fname!r} on {bid!r} is not improvable "
                    f"(allowed: {', '.join(IMPROVABLE_TEXT_FIELDS)})")
                continue
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{bid}.{fname} must be a non-empty string")
                continue
            if (blocks[bid].get(fname) or "") != value:
                any_change = True
    if not errors and not any_change:
        errors.append("patch changes nothing (all values equal current text)")
    return errors


def apply_improve_patch(
    root: Dict[str, Any], patch: Dict[str, Dict[str, str]],
) -> int:
    """Apply a (pre-validated) patch to a block tree in place.

    Only whitelisted string fields on existing blocks are written;
    anything else in the patch is ignored (validation reports it — this
    function is deliberately safe to call on a best-effort basis against
    the LIVE card, whose tree may have drifted from the snapshot the
    run executed, in which case unmatched ids simply don't apply).

    Returns the number of fields actually changed.
    """
    blocks = collect_blocks_by_id(root)
    changed = 0
    for bid, fields in (patch or {}).items():
        target = blocks.get(bid)
        if target is None or not isinstance(fields, dict):
            continue
        for fname, value in fields.items():
            if fname not in IMPROVABLE_TEXT_FIELDS:
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            if (target.get(fname) or "") != value:
                target[fname] = value
                changed += 1
    return changed


# ── Budgets ─────────────────────────────────────────────────────

def resolve_improve_max(block_value: Optional[int]) -> int:
    """Per-block revision budget.  None → DEFAULT_IMPROVE_MAX; values
    are clamped to >= 0 (0 means: judge and record lessons, never
    edit — observation mode)."""
    if block_value is None:
        return DEFAULT_IMPROVE_MAX
    try:
        return max(0, int(block_value))
    except (TypeError, ValueError):
        return DEFAULT_IMPROVE_MAX


def run_improve_ceiling() -> int:
    """Run-wide cap on card edits across every improving level."""
    raw = os.environ.get("ZIYA_TASK_IMPROVE_RUN_MAX", "")
    try:
        v = int(raw)
        if v >= 0:
            return v
    except (TypeError, ValueError):
        pass
    return DEFAULT_RUN_IMPROVE_CEILING


# ── Lesson ledger ───────────────────────────────────────────────

class LessonLedger:
    """Append-only JSONL ledger of improvement verdicts and lessons.

    One file per project: ``{project_dir}/task_card_lessons.jsonl``.
    Same storage shape as task_card_refusals: read-modify-write per
    append with an oldest-dropped cap so the rewrite cost stays
    bounded.  Writes are best-effort and never raise — a failed ledger
    write must not fail the run that produced the lesson.

    The ledger is what makes self-improvement DURABLE rather than
    per-run: the judge on a later run receives the recent lessons for
    the same (card, block), so it refines rather than re-derives, and
    the ``seen_patch_hash`` check is what stops an A→B→A oscillation
    across runs.
    """

    def __init__(self, project_dir: Path):
        self.path = Path(project_dir) / LESSONS_FILENAME

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.warning(f"LessonLedger: unreadable {self.path}: {e}")
        return out

    def record(self, rec: Dict[str, Any]) -> None:
        """Append a record (best-effort; never raises)."""
        try:
            rec = dict(rec)
            rec.setdefault("ts", time.time())
            records = self._read_all()
            records.append(rec)
            if len(records) > MAX_RETAINED_LESSONS:
                records = records[-MAX_RETAINED_LESSONS:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001 — sink must not raise
            logger.warning(f"LessonLedger: record failed (non-fatal): {e}")

    def for_block(
        self, card_id: str, block_id: str, limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """Most recent records for (card, block), oldest first."""
        matches = [
            r for r in self._read_all()
            if r.get("card_id") == card_id and r.get("block_id") == block_id
        ]
        return matches[-limit:]

    def seen_patch_hash(self, card_id: str, block_id: str, h: str) -> bool:
        """True if this exact patch content was already applied for
        this (card, block) — the oscillation guard."""
        if not h:
            return False
        return any(
            r.get("patch_hash") == h and r.get("applied")
            for r in self._read_all()
            if r.get("card_id") == card_id and r.get("block_id") == block_id
        )


# ── Card persistence ────────────────────────────────────────────

def persist_patch_to_card(
    project_id: Optional[str], card_id: Optional[str],
    patch: Dict[str, Dict[str, str]],
) -> bool:
    """Apply a text patch to the LIVE card definition and save it.

    Returns True when at least one field changed and the card was
    written.  Best-effort against drift: the run executes a snapshot,
    and the live card may have been edited since launch — ids that no
    longer exist simply don't apply (the in-run re-execution still uses
    the patched snapshot, so the current run improves either way; only
    durability is reduced, and the lesson ledger records that).

    The structure fingerprint is asserted around the application so a
    regression in apply_improve_patch can never silently reach the
    saved card as a structural or scope change.
    """
    if not project_id or not card_id or not patch:
        return False
    try:
        from app.models.task_card import TaskCardUpdate
        from app.storage.task_cards import TaskCardStorage
        from app.utils.paths import get_project_dir

        storage = TaskCardStorage(get_project_dir(project_id))
        card = storage.get(card_id)
        if not card:
            logger.warning(
                f"self_improve: card {card_id[:8]} not found; patch not persisted")
            return False
        root = card.root.model_dump()
        before = structure_fingerprint(root)
        changed = apply_improve_patch(root, patch)
        if not changed:
            return False
        after = structure_fingerprint(root)
        if before != after:
            logger.error(
                "self_improve: patch altered non-text structure — refusing "
                f"to persist (card {card_id[:8]})")
            return False
        storage.update(card_id, TaskCardUpdate(root=root))
        return True
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        logger.warning(f"self_improve: persist failed (non-fatal): {e}")
        return False
