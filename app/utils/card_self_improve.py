"""
Task-card self-improvement — critic, text patch, and lesson ledger.

When a block carries ``self_improve=True``, the block executor runs the
block, then asks a critic model whether the card's TEXT could be edited
to tangibly improve the outcome, and — on a ``revise`` verdict — applies
a field-whitelisted patch and re-executes that level.  See
design/task-card-self-improvement.md.

The privilege fence
-------------------
``scope_canonical.task_escalation_block`` hashes only shell_commands and
writable paths, so an instructions-only rewrite keeps a signed scope
approval valid — and any privilege widening fails closed at execution
time because the agent never holds the signing key.  This module's job
is to make the *text* write path equally safe:

  * ``apply_text_patch`` only ever assigns fields in PATCHABLE_FIELDS
    (instructions, state_context) on blocks whose ids ALREADY exist.
    It never inserts, removes, or reorders blocks and never touches
    ``scope`` — so block ids are never reassigned and approvals keyed
    by ``(block_id, scope_hash)`` cannot be orphaned by an improvement.
  * Persistence goes through ``TaskCardStorage.update`` with a patched
    copy of the card's own tree.  Every id is present, so the storage
    layer's fill-only ``_assign_block_ids`` is a no-op.

Deliberately NOT built on ``task_card_write`` (the MCP tool), whose
whole-root replacement semantics can silently mint fresh block ids.

Durability is two artifacts:
  * the patched live card — what makes run N+1 better;
  * the append-only lesson ledger (``task_card_lessons.jsonl`` under
    the project dir, modeled on task_card_refusals) — what stops the
    critic re-deriving the same lesson every run, and what detects
    A→B→A oscillation via patch hashes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


PATCHABLE_FIELDS = ("instructions", "state_context")
"""The ONLY fields a self-improvement patch may write.  Everything else
— scope, loop counts, conditions, structure — is out of reach in v1."""

DEFAULT_IMPROVE_MAX = 3
"""Per-block rewrite budget when ``improve_max`` is unset (None)."""

DEFAULT_RUN_IMPROVE_CEILING = 10
"""Run-wide rewrite ceiling across ALL improving blocks (nested levels
multiply; this bounds the product).  Env: ZIYA_TASK_IMPROVE_CEILING."""

MAX_RETAINED_LESSONS = 5000
LESSONS_FILENAME = "task_card_lessons.jsonl"

_VALID_VERDICTS = ("accept", "no_improvement", "revise")


# ── Tree walking (Block instances and plain dicts) ──────────────

def _get(node: Any, field: str, default: Any = None) -> Any:
    if isinstance(node, dict):
        return node.get(field, default)
    return getattr(node, field, default)


def _set(node: Any, field: str, value: Any) -> None:
    if isinstance(node, dict):
        node[field] = value
    else:
        setattr(node, field, value)


def _iter_nodes(root: Any):
    """Yield every node in a block tree, depth-first.  Accepts either a
    pydantic Block or its ``model_dump()`` dict form."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        yield node
        body = _get(node, "body") or []
        # reversed → preserve document order in the yielded sequence
        stack.extend(reversed(list(body)))


def collect_patchable_ids(root: Any) -> Dict[str, str]:
    """Map of block id → block_type for every id-bearing node in the
    subtree.  Used to bound which ids a patch may name."""
    out: Dict[str, str] = {}
    for node in _iter_nodes(root):
        bid = _get(node, "id") or ""
        if bid:
            out[bid] = _get(node, "block_type") or ""
    return out


# ── Patch application ───────────────────────────────────────────

def apply_text_patch(
    root: Any,
    patch: Dict[str, Dict[str, str]],
    allowed_ids: Optional[set] = None,
) -> List[str]:
    """Validate then apply a text patch to a block tree, in place.

    ``patch`` shape: {block_id: {field: full replacement text}}.
    Returns a list of error strings; empty list means the patch was
    fully applied.  All-or-nothing: any validation error means NOTHING
    was written.

    ``allowed_ids`` further restricts which ids may be patched (the
    improving block's own subtree) — a critic hallucinating an id from
    elsewhere in the card must not reach outside its level.
    """
    if not isinstance(patch, dict) or not patch:
        return ["patch must be a non-empty object of block_id → fields"]
    ids = collect_patchable_ids(root)
    allowed = set(allowed_ids) if allowed_ids is not None else set(ids)
    errors: List[str] = []
    for bid, fields in patch.items():
        if bid not in ids:
            errors.append(f"unknown block id {bid!r} — ids are never created by a patch")
            continue
        if bid not in allowed:
            errors.append(f"block {bid!r} is outside the improving subtree")
            continue
        if not isinstance(fields, dict) or not fields:
            errors.append(f"patch for {bid!r} must be a non-empty object of field → text")
            continue
        for fname, value in fields.items():
            if fname not in PATCHABLE_FIELDS:
                errors.append(
                    f"field {fname!r} on {bid!r} is not patchable "
                    f"(allowed: {', '.join(PATCHABLE_FIELDS)})")
            elif not isinstance(value, str) or not value.strip():
                errors.append(f"{bid}.{fname} must be a non-empty string")
    if errors:
        return errors
    node_by_id = {(_get(n, "id") or ""): n for n in _iter_nodes(root)}
    for bid, fields in patch.items():
        for fname, value in fields.items():
            _set(node_by_id[bid], fname, value)
    return []


def patch_hash(patch: Dict[str, Any]) -> str:
    """Stable hash of a patch's content — the oscillation key.  Two
    revisions proposing byte-identical text edits share a hash."""
    canon = json.dumps(patch, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def persist_card_text_patch(
    project_id: str,
    card_id: str,
    patch: Dict[str, Dict[str, str]],
    allowed_ids: Optional[set] = None,
) -> Optional[str]:
    """Apply ``patch`` to the LIVE card on disk.  Returns an error
    string, or None on success.

    Re-validates against the live card's tree (not the run's snapshot):
    if the card was edited since launch and an id no longer exists, the
    persist is refused rather than guessed at.  The in-run improvement
    still proceeds either way — persistence is the durability layer,
    not the correctness layer.
    """
    try:
        from app.utils.paths import get_project_dir
        from app.storage.task_cards import TaskCardStorage
        from app.models.task_card import TaskCardUpdate
        storage = TaskCardStorage(get_project_dir(project_id))
        card = storage.get(card_id)
        if not card:
            return f"card {card_id!r} not found"
        root_dict = card.root.model_dump()
        errs = apply_text_patch(root_dict, patch, allowed_ids)
        if errs:
            return "; ".join(errs)
        storage.update(card_id, TaskCardUpdate(root=root_dict))
        return None
    except Exception as e:  # noqa: BLE001 — durability is best-effort
        logger.warning(f"self-improve: card persist failed: {e}")
        return str(e)


# ── Lesson ledger ───────────────────────────────────────────────

class LessonLedger:
    """Append-only JSONL record of critic verdicts and applied patches.

    One file per project (``task_card_lessons.jsonl``), following the
    refusal log's shape: read-modify-write per append, capped at
    MAX_RETAINED_LESSONS (oldest dropped).  Best-effort by construction
    — a ledger fault must never fail a run.

    Entry fields: at, card_id, block_id, run_id, verdict, lesson,
    patch_hash (revise only), applied (bool), error (optional).
    """

    def __init__(self, project_dir: Path):
        self.path = Path(project_dir) / LESSONS_FILENAME

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.warning(f"self-improve: ledger unreadable: {e}")
        return out

    def record(self, entry: Dict[str, Any]) -> None:
        """Append an entry, enforcing the retention cap.  Never raises."""
        try:
            entry = dict(entry)
            entry.setdefault("at", time.time())
            entries = self._read_all()
            entries.append(entry)
            if len(entries) > MAX_RETAINED_LESSONS:
                entries = entries[-MAX_RETAINED_LESSONS:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"self-improve: ledger write failed: {e}")

    def lessons_for(
        self, card_id: str, block_id: Optional[str] = None, limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Most recent lessons for a card (optionally one block),
        oldest-first within the returned window."""
        matched = [
            e for e in self._read_all()
            if e.get("card_id") == card_id
            and (block_id is None or e.get("block_id") == block_id)
        ]
        return matched[-limit:]

    def seen_patch(self, card_id: str, block_id: str, phash: str) -> bool:
        """True if this exact patch content was applied before for this
        card+block — the A→B→A oscillation detector."""
        for e in self._read_all():
            if (e.get("card_id") == card_id
                    and e.get("block_id") == block_id
                    and e.get("patch_hash") == phash
                    and e.get("applied")):
                return True
        return False


# ── Critic ──────────────────────────────────────────────────────

_CRITIC_SYSTEM_PROMPT = """\
You review ONE completed execution of a task-card block and decide
whether editing the card's TEXT would tangibly improve future runs.

Verdicts (choose exactly one):
- "accept" — the outcome meets the criterion.  No edit needed.
- "no_improvement" — the criterion is unmet or partially met, but no
  text edit would meaningfully change the outcome (the fault is
  environmental, transient, or the text is already right).
- "revise" — you can name a concrete weakness in the card's text that
  caused or contributed to the shortfall, and a specific edit that
  would tangibly, meaningfully improve the outcome.

The bar for "revise" is high: do NOT rewrite for style, tone, or
marginal clarity.  Do NOT expand the card's scope or ambition beyond
its stated objective — improvement means better at the SAME job, never
a bigger job.  If prior lessons show an edit was already tried and did
not help, do not propose it again.

A patch may only touch the fields "instructions" and "state_context",
and only on the block ids listed in the BLOCKS section.  Patch values
are FULL replacement text, not diffs.

Reply with STRICT JSON only, no prose around it:
{"verdict": "accept" | "no_improvement" | "revise",
 "lesson": "<one sentence, <=300 chars: what was learned>",
 "patch": {"<block_id>": {"instructions": "..."}}}
Omit "patch" (or use {}) unless verdict is "revise"."""


def render_block_text(root: Any, max_field_chars: int = 2000) -> str:
    """Human/model-readable dump of a subtree's patchable text, with ids."""
    lines: List[str] = []

    def walk(node: Any, depth: int) -> None:
        pad = "  " * depth
        bid = _get(node, "id") or "(no id)"
        btype = _get(node, "block_type") or "?"
        name = _get(node, "name") or ""
        lines.append(f"{pad}- [{bid}] {btype}" + (f" — {name}" if name else ""))
        for fname in PATCHABLE_FIELDS:
            val = _get(node, fname)
            if val:
                txt = str(val)
                if len(txt) > max_field_chars:
                    txt = txt[:max_field_chars] + " …(truncated)"
                lines.append(f"{pad}  {fname}: {txt}")
        cond = _get(node, "until_condition") or _get(node, "repeat_until")
        if cond:
            lines.append(f"{pad}  (loop condition, NOT patchable): {cond}")
        for child in _get(node, "body") or []:
            walk(child, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


def _artifact_digest(artifact: Any, max_chars: int = 3000) -> str:
    summary = (getattr(artifact, "summary", "") or "(no summary)")[:max_chars]
    decisions = getattr(artifact, "decisions", None) or []
    failed = bool(getattr(artifact, "failed", False))
    sa = getattr(artifact, "self_assessment", None) or {}
    parts = [f"FAILED: {'yes' if failed else 'no'}", f"SUMMARY:\n{summary}"]
    if decisions:
        parts.append("KEY DECISIONS:\n" + "\n".join(f"- {d}" for d in decisions[:10]))
    if sa:
        parts.append(f"SELF-ASSESSMENT: objective_met={sa.get('objective_met')}"
                     + (f" — {sa.get('rationale')}" if sa.get("rationale") else ""))
    return "\n\n".join(parts)


def _render_lessons(lessons: List[Dict[str, Any]]) -> str:
    if not lessons:
        return "(none yet)"
    out = []
    for e in lessons:
        verdict = e.get("verdict", "?")
        applied = " (patch applied)" if e.get("applied") else ""
        out.append(f"- [{verdict}{applied}] {e.get('lesson', '')}")
    return "\n".join(out)


def parse_critic_reply(text: Optional[str]) -> Dict[str, Any]:
    """Parse the critic's JSON reply defensively.  Anything unparseable
    resolves to no_improvement — the loop conservatively stops rather
    than rewriting on an ambiguous instruction."""
    fallback = {"verdict": "no_improvement",
                "lesson": "critic reply unparseable", "patch": {}}
    if not text:
        return fallback
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return fallback
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return fallback
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in _VALID_VERDICTS:
        return fallback
    patch = obj.get("patch") if isinstance(obj.get("patch"), dict) else {}
    if verdict != "revise":
        patch = {}
    return {
        "verdict": verdict,
        "lesson": str(obj.get("lesson", ""))[:300],
        "patch": patch,
    }


async def critique(
    block: Any,
    artifact: Any,
    *,
    criterion: Optional[str],
    prior_lessons: List[Dict[str, Any]],
    rewrites_used: int,
    rewrites_budget: int,
) -> Dict[str, Any]:
    """Ask the critic model for a verdict on one completed execution.

    Transport failures resolve to no_improvement (stop, don't rewrite).
    Model category "task_improvement" falls back to the endpoint's
    default cheap model; override with ZIYA_TASK_IMPROVEMENT_MODEL.
    """
    crit = (criterion or "").strip() or (
        "(no authored criterion — judge whether the outcome fulfils the "
        "block's own instructions)")
    user_message = (
        f"CRITERION:\n{crit}\n\n"
        f"BLOCKS (patchable text, by id):\n{render_block_text(block)}\n\n"
        f"RESULT OF THIS EXECUTION:\n{_artifact_digest(artifact)}\n\n"
        f"PRIOR LESSONS FOR THIS CARD:\n{_render_lessons(prior_lessons)}\n\n"
        f"REWRITE BUDGET: {rewrites_used} of {rewrites_budget} used.\n\n"
        f"Reply with the strict JSON object only."
    )
    try:
        from app.services.model_resolver import call_service_model
        out = await call_service_model(
            category="task_improvement",
            system_prompt=_CRITIC_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=2048,
            temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"self-improve: critic transport failed (→ stop): {e}")
        return {"verdict": "no_improvement",
                "lesson": f"critic unavailable: {e}", "patch": {}}
    return parse_critic_reply(out)
