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

# Verdict recorded when the judge itself failed (transport error,
# unparseable reply, unknown verdict).  Recorded as its own verdict —
# never as "accept" — so a judge outage cannot masquerade as a
# considered approval in the ledger or the UI.
JUDGE_ERROR_VERDICT = "error"

# Verdicts whose lesson is worth showing a later run's judge.  An
# "accept" lesson is self-congratulation ("this structure reliably
# produces...") and primes the next judge toward accept; an error
# record carries no lesson at all.  Only a revise (a weakness found)
# or a stop (a non-text cause found) teaches anything.
PRIOR_LESSON_VERDICTS = ("revise", "stop")

# Rationale string the pre-fix evaluator recorded (under verdict
# "accept") whenever it failed.  Records carrying it are re-labelled
# at read time so historical judge failures stop counting as accepts.
LEGACY_FALLBACK_RATIONALE = "judge unavailable or unparseable — no revision"

LESSONS_FILENAME = "task_card_lessons.jsonl"

# Targeted patch ops a judge may use instead of a full-field
# replacement.  A full replacement of a 1,100-token field, JSON-escaped,
# plus rationale and lesson, does not fit a 2,000-token reply — which is
# the structural reason a revise reply could arrive truncated and be
# recorded as a judge failure.  Ops are resolved to full text by
# ``resolve_improve_patch`` BEFORE validation, so hashing (the
# oscillation guard), pre-image capture, apply and persist never see
# them: the two formats converge to the same record.
PATCH_OPS = ("replace", "append")

# Output ceiling for the judge reply.  Sized so a revise carrying a
# full replacement of the largest observed field (~1,100 tokens raw,
# ~1,500 escaped) plus a second field still fits; targeted ops need a
# fraction of this.
JUDGE_MAX_TOKENS = 8000

# Per-stage summary cap on a container artifact's ``stages`` evidence.
# Long enough to carry a triage-style summary's substance, short enough
# that a 20-iteration repeat stays a few KB.
STAGE_SUMMARY_CAP = 600

# A block whose judge has said "stop" (deficient for a non-text reason)
# this many runs in a row has an environment problem nobody has fixed.
# At or past this the ledger surfaces the streak as one aggregate so
# six rows saying "stale render server" six ways read as one finding.
STOP_STREAK_MIN = 2

# How many stage lines / output names the judge prompt lists in full
# before eliding.  Failed stages are always listed regardless.
JUDGE_STAGE_LIST_CAP = 24
JUDGE_OUTPUT_NAME_CAP = 12


# ── Stage evidence (what a container artifact shows the judge) ──

def stage_evidence(
    label: str, artifact: Any, *, index: Optional[int] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """One entry of a container artifact's ``stages`` list.

    A repeat/until/parallel records one per iteration or branch; a
    group one per child.  This is the evidence the self-improvement
    judge was starved of: a repeat's artifact carried only the LAST
    iteration's summary, so a judge asked "did every engine meet the
    criterion?" saw one engine's self-report and nothing about the
    other nineteen.  ``status`` overrides the artifact's own failed
    flag for outcomes the artifact cannot express (``cancelled``,
    ``replayed``).
    """
    summary = getattr(artifact, "summary", "") or ""
    if len(summary) > STAGE_SUMMARY_CAP:
        summary = summary[:STAGE_SUMMARY_CAP] + " […]"
    return {
        "index": index,
        "label": str(label or ""),
        "status": status or (
            "failed" if getattr(artifact, "failed", False) else "passed"),
        "summary": summary,
        "self_assessment": getattr(artifact, "self_assessment", None),
        "outputs": len(getattr(artifact, "outputs", None) or []),
    }


def stage_counts(stages: List[Dict[str, Any]]) -> Dict[str, int]:
    """{total, passed, failed, other} over a ``stages`` list."""
    out = {"total": len(stages), "passed": 0, "failed": 0, "other": 0}
    for s in stages:
        st = s.get("status")
        if st == "passed":
            out["passed"] += 1
        elif st == "failed":
            out["failed"] += 1
        else:
            out["other"] += 1
    return out


def render_stages_for_judge(stages: List[Dict[str, Any]]) -> str:
    """The OUTCOME → stages section of the judge prompt.

    Every non-passed stage is listed in full; passed stages are listed
    up to JUDGE_STAGE_LIST_CAP and then counted, so a wide fan-out
    cannot push its own failures out of the prompt.
    """
    if not stages:
        return "(no per-stage evidence — single-stage or legacy artifact)"
    c = stage_counts(stages)
    head = (f"{c['total']} stage(s): {c['passed']} passed, "
            f"{c['failed']} failed, {c['other']} cancelled/other")
    lines: List[str] = []
    passed_shown = 0
    passed_elided = 0
    for s in stages:
        st = s.get("status") or "?"
        if st == "passed":
            if passed_shown >= JUDGE_STAGE_LIST_CAP:
                passed_elided += 1
                continue
            passed_shown += 1
        idx = s.get("index")
        tag = f"[{idx}] " if idx is not None else ""
        sa = s.get("self_assessment") or {}
        sa_txt = ""
        if sa:
            sa_txt = (f" | self_assessment: objective_met="
                      f"{sa.get('objective_met', '?')}")
            if sa.get("rationale"):
                sa_txt += f" — {str(sa['rationale'])[:200]}"
        lines.append(
            f"  {tag}{st.upper()} {s.get('label') or ''}: "
            f"{s.get('summary') or '(no summary)'}{sa_txt}"
        )
    if passed_elided:
        lines.append(f"  … {passed_elided} more passed stage(s) not listed")
    return head + "\n" + "\n".join(lines)


def render_outputs_for_judge(outputs: List[Any]) -> str:
    """One line naming what the level actually produced.

    The judge's criterion typically references on-disk deliverables
    (triage files, spec directories); the names of the file parts are
    the closest thing to evidence of them the artifact carries.
    """
    if not outputs:
        return "(none)"
    files: List[str] = []
    n_text = n_data = 0
    for p in outputs:
        pt = getattr(p, "part_type", None) or (
            p.get("part_type") if isinstance(p, dict) else None)
        uri = getattr(p, "file_uri", None) or (
            p.get("file_uri") if isinstance(p, dict) else None)
        if pt == "file" or uri:
            files.append(str(uri or "(unnamed file)"))
        elif pt == "data":
            n_data += 1
        else:
            n_text += 1
    parts = [f"{len(outputs)} part(s)"]
    if files:
        shown = ", ".join(files[:JUDGE_OUTPUT_NAME_CAP])
        more = (f" (+{len(files) - JUDGE_OUTPUT_NAME_CAP} more)"
                if len(files) > JUDGE_OUTPUT_NAME_CAP else "")
        parts.append(f"{len(files)} file(s): {shown}{more}")
    if n_text:
        parts.append(f"{n_text} text")
    if n_data:
        parts.append(f"{n_data} data")
    return "; ".join(parts)


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


def _apply_ops_to_text(
    current: str, ops: List[Any], where: str,
) -> "tuple[Optional[str], List[str]]":
    """Resolve a list of targeted ops against one field's current text.

    Returns (new_text, errors).  Ops apply in order, each against the
    text as edited so far.  ``replace`` requires its ``find`` string to
    occur EXACTLY once in the current text: zero matches means the
    judge is editing text it did not read (or that a prior op already
    changed), and more than one means the edit is ambiguous — either
    way the patch is refused rather than guessed at.
    """
    errors: List[str] = []
    text = current or ""
    for n, op in enumerate(ops):
        if not isinstance(op, dict):
            errors.append(f"{where} op[{n}] must be an object")
            continue
        kind = op.get("op")
        if kind == "replace":
            find = op.get("find")
            repl = op.get("with")
            if not isinstance(find, str) or not find:
                errors.append(f"{where} op[{n}] replace: 'find' must be a non-empty string")
                continue
            if not isinstance(repl, str):
                errors.append(f"{where} op[{n}] replace: 'with' must be a string")
                continue
            hits = text.count(find)
            if hits != 1:
                errors.append(
                    f"{where} op[{n}] replace: 'find' occurs {hits} time(s) "
                    f"in the current text; it must occur exactly once")
                continue
            text = text.replace(find, repl, 1)
        elif kind == "append":
            add = op.get("text")
            if not isinstance(add, str) or not add.strip():
                errors.append(f"{where} op[{n}] append: 'text' must be a non-empty string")
                continue
            text = (text.rstrip("\n") + "\n\n" + add.strip("\n")) if text else add
        else:
            errors.append(
                f"{where} op[{n}] has unknown op {kind!r} "
                f"(allowed: {', '.join(PATCH_OPS)})")
    return (None if errors else text), errors


def resolve_improve_patch(
    patch: Any, subtree_root: Dict[str, Any],
) -> "tuple[Dict[str, Dict[str, str]], List[str]]":
    """Normalise a judge patch to the full-text form.

    Each ``{block_id: {field: value}}`` value may be a string (full
    replacement — passed through untouched) or a list of op objects
    (see PATCH_OPS; a single op object is accepted as a one-element
    list).  Ops are resolved against the CURRENT field text in
    ``subtree_root``.  Returns ``(full_text_patch, errors)``; on any
    error the returned patch is whatever resolved cleanly and the
    caller must treat the errors as fatal — a partially resolved patch
    is never applied.

    Unknown block ids and non-improvable fields are left in place for
    ``validate_improve_patch`` to report, so the two functions never
    disagree about what is wrong.
    """
    errors: List[str] = []
    if not isinstance(patch, dict):
        return {}, ["patch must be an object of {block_id: {field: text|ops}}"]
    blocks = collect_blocks_by_id(subtree_root)
    out: Dict[str, Dict[str, str]] = {}
    for bid, fields in patch.items():
        if not isinstance(fields, dict):
            out[bid] = fields  # type: ignore[assignment]  # validator reports it
            continue
        out_fields: Dict[str, Any] = {}
        for fname, value in fields.items():
            if isinstance(value, dict):
                value = [value]
            if isinstance(value, list):
                if bid not in blocks or fname not in IMPROVABLE_TEXT_FIELDS:
                    # Let the validator produce its own message for the
                    # id/field problem; resolving against nothing would
                    # only add a confusing second error.
                    out_fields[fname] = ""
                    continue
                text, errs = _apply_ops_to_text(
                    blocks[bid].get(fname) or "", value, f"{bid}.{fname}")
                errors.extend(errs)
                out_fields[fname] = text if text is not None else ""
            else:
                out_fields[fname] = value
        out[bid] = out_fields
    return out, errors


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

def normalize_lesson_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Re-label a pre-fix judge-failure record as a judge error.

    The original evaluator resolved every failure to verdict "accept"
    with LEGACY_FALLBACK_RATIONALE, so the ledger on disk holds judge
    outages counted as approvals.  Relabelling happens on read (and is
    therefore persisted by the next append's read-modify-write); it is
    idempotent, so a relabelled record passing through again is a
    no-op.  Records that already carry an error verdict, or any other
    rationale, are returned untouched.
    """
    if (rec.get("verdict") == "accept"
            and rec.get("rationale") == LEGACY_FALLBACK_RATIONALE):
        rec = dict(rec)
        rec["verdict"] = JUDGE_ERROR_VERDICT
        rec["error"] = "legacy_fallback"
    return rec


def stop_streaks(
    records: List[Dict[str, Any]], min_len: int = STOP_STREAK_MIN,
) -> Dict[str, Dict[str, Any]]:
    """Per block, the TRAILING run of consecutive ``stop`` verdicts.

    ``records`` is one card's ledger slice, oldest first.  Walking per
    block: a ``stop`` extends the streak, an ``error`` is skipped (it
    says nothing about the task — the judge failed, not the run), and
    any ``accept``/``revise`` resets it.  Only streaks whose length is
    >= ``min_len`` are returned, so a single stop is just a row.

    Grouping is positional, not textual.  The six GFX2 stops that
    motivated this describe one cause (a stale headless render server)
    in six wordings; no string compare would group them, and a model
    call to cluster ledger rows is not worth its cost.  "This block has
    stopped N runs in a row and no run has passed since" is the finding.

    Returns {block_id: {count, first_ts, last_ts, run_ids, rationales}}
    with ``rationales`` newest first, capped to five.
    """
    per_block: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        bid = r.get("block_id")
        if not bid:
            continue
        v = r.get("verdict")
        if v == JUDGE_ERROR_VERDICT:
            continue
        run = per_block.setdefault(bid, [])
        if v == "stop":
            run.append(r)
        else:
            run.clear()
    out: Dict[str, Dict[str, Any]] = {}
    for bid, run in per_block.items():
        if len(run) < min_len:
            continue
        ts = [r.get("ts") for r in run if isinstance(r.get("ts"), (int, float))]
        out[bid] = {
            "count": len(run),
            "first_ts": min(ts) if ts else None,
            "last_ts": max(ts) if ts else None,
            "run_ids": [r.get("run_id") for r in run],
            "rationales": [
                (r.get("lesson") or r.get("rationale") or "")
                for r in reversed(run)
            ][:5],
        }
    return out


def prior_lessons_for_judge(
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """The subset of ledger records a later run's judge should see.

    Keeps only PRIOR_LESSON_VERDICTS records that carry a lesson or
    rationale.  Accept lessons are dropped because they are the
    judge praising its own predecessor and measurably prime the next
    verdict toward accept; error records are dropped because they
    describe the judge, not the task.
    """
    return [
        r for r in records
        if r.get("verdict") in PRIOR_LESSON_VERDICTS
        and (r.get("lesson") or r.get("rationale"))
    ]


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
        return [normalize_lesson_record(r) for r in out]

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

    def for_card(
        self, card_id: str, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Most recent records for a card across all its blocks, oldest
        first.  Backs the GET /task-cards/{id}/lessons surface — the
        card-level learning history a user reviews and reverts from."""
        matches = [
            r for r in self._read_all() if r.get("card_id") == card_id
        ]
        return matches[-limit:]

    def summary_by_card(self) -> Dict[str, Dict[str, Any]]:
        """One-read aggregate for the deck list's 🌱 badge:
        {card_id: {count, edits_applied, last_ts}}.

        Exists so the card list can badge every card from a SINGLE
        ledger read — a per-card ``for_card`` fetch would be an
        N-request burst on every deck open, the same shape the deck
        already avoids for run status (see TaskCardsLibrary's
        project-wide run index).
        """
        out: Dict[str, Dict[str, Any]] = {}
        by_card: Dict[str, List[Dict[str, Any]]] = {}
        for r in self._read_all():
            cid = r.get("card_id")
            if not cid:
                continue
            by_card.setdefault(cid, []).append(r)
            agg = out.setdefault(cid, {
                "count": 0, "edits_applied": 0, "judge_errors": 0,
                "stop_streak": 0, "last_ts": 0.0,
            })
            agg["count"] += 1
            if r.get("applied"):
                agg["edits_applied"] += 1
            if r.get("verdict") == JUDGE_ERROR_VERDICT:
                agg["judge_errors"] += 1
            ts = r.get("ts") or 0.0
            if isinstance(ts, (int, float)) and ts > agg["last_ts"]:
                agg["last_ts"] = ts
        # Longest trailing stop streak across the card's blocks — the
        # deck badge marks a card whose environment has blocked it for
        # several runs, without a per-card fetch.
        for cid, recs in by_card.items():
            streaks = stop_streaks(recs)
            out[cid]["stop_streak"] = max(
                (s["count"] for s in streaks.values()), default=0)
        return out

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


def extract_pre_image(
    patch: Dict[str, Dict[str, str]], root: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """Capture the CURRENT text of every field a patch is about to
    replace, in the same {block_id: {field: text}} shape as the patch.

    Recorded on the lesson-ledger entry BEFORE apply_improve_patch
    mutates the tree, so a persisted revision is revertable: the
    pre-image is itself a valid patch and flows back through the same
    guarded path (persist_patch_to_card), keeping ids and scope bytes
    untouched by construction.  Must be called before application —
    afterwards the old text exists nowhere.

    Only whitelisted fields on existing blocks are captured, mirroring
    what apply_improve_patch would actually write; an absent field is
    recorded as "" (apply skips empty replacements, so a revert of a
    field that had no prior text is a recorded no-op, not a crash).
    """
    blocks = collect_blocks_by_id(root)
    out: Dict[str, Dict[str, str]] = {}
    for bid, fields in (patch or {}).items():
        target = blocks.get(bid)
        if target is None or not isinstance(fields, dict):
            continue
        for fname in fields:
            if fname not in IMPROVABLE_TEXT_FIELDS:
                continue
            out.setdefault(bid, {})[fname] = str(target.get(fname) or "")
    return out


def revert_lesson_patch(
    project_id: Optional[str], card_id: Optional[str],
    record: Dict[str, Any],
) -> bool:
    """Write a ledger record's pre-image back onto the live card.

    The one-click "un-teach" affordance: a pre-image is a text patch
    like any other, so it rides persist_patch_to_card and inherits every
    guard (field whitelist, existing-id keying, structure fingerprint).
    Returns False when the record carries no pre-image (written before
    pre-image capture existed, or nothing was applied) or when no field
    changed — e.g. the card was already hand-edited back.
    """
    pre = record.get("pre_image")
    if not isinstance(pre, dict) or not pre:
        return False
    return persist_patch_to_card(project_id, card_id, pre)
