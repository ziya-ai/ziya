"""
Self-improvement judge for Task Cards.

Given a completed self-improving level (a container block carrying
``self_improve=True``), its outcome Artifact, the level's editable
text, and prior lessons from the ledger, ask a model ONE question:
does a tangible, outcome-affecting text improvement exist?

Verdicts:
  * accept — outcome adequate, or no text change would meaningfully
    affect the next run.
  * revise — a specific weakness in the task text tangibly harmed the
    outcome AND a concrete patch would meaningfully improve the next
    run.  Carries the patch.
  * stop — outcome deficient but NOT fixable by editing task text
    (permissions, environment, external state).  Ends the loop; the
    recorded reason is visible to later runs' judges via the ledger.
  * error — the judge itself failed (transport, unparseable reply,
    unknown verdict).  Ends the loop with the artifact unchanged, like
    accept, but is recorded as a judge failure with a bounded excerpt
    of the raw reply so the cause is diagnosable.  Formerly these were
    recorded as "accept", which let a judge outage read as approval.

The bar for "revise" is deliberately high — stylistic preference is
not grounds to revise.  Cards should converge, not wander.

The judge cannot widen privilege even if it tries: its patch is
validated against IMPROVABLE_TEXT_FIELDS and the structure fingerprint
before application (app/utils/self_improve.py), and the scope-approval
hash (scope_canonical.task_scope_hash) covers only privilege-bearing
fields, so a text-only patch keeps signed approvals valid.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..models.task_card import Artifact, Block
from app.utils.self_improve import (
    IMPROVABLE_TEXT_FIELDS, JUDGE_ERROR_VERDICT, JUDGE_MAX_TOKENS,
    prior_lessons_for_judge, render_outputs_for_judge, render_stages_for_judge,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You judge whether a just-completed task-card level should be revised.

You receive the level's OBJECTIVE (an explicit acceptance criterion
when the author wrote one, otherwise infer it from the task text), the
EDITABLE TEXT of the blocks at this level, the OUTCOME of the run just
completed, prior LESSONS from earlier runs, and a DRIFT policy.

Decide exactly one verdict:
  "accept" — the outcome is adequate, OR no change to the task text
      would tangibly and meaningfully affect the next run's outcome.
  "revise" — a specific weakness in the task text caused a real
      deficiency in this outcome, and you can state a concrete text
      change that would meaningfully improve the next run.
  "stop" — the outcome is deficient for reasons text edits cannot fix
      (missing permissions, environment failures, external state).

THE BAR FOR "revise" IS HIGH.  Do not revise for style, tone, or
phrasing preference.  Revise only when the change would tangibly
affect the outcome.

WEIGHING THE OUTCOME.  The level's "failed" flag and "summary" reflect
only its LAST stage; the STAGES list is the evidence.  Any stage that
FAILED or was cancelled is a deficiency of the level regardless of what
the final summary claims.  A stage's summary is the agent's own report
of its work, not a measurement — treat unverified claims of success
with corresponding skepticism, and prefer the OUTPUTS list (what was
actually produced) over prose when they disagree.  When the objective
names deliverables and OUTPUTS shows none, the objective was not met.

DRIFT POLICY:
  conservative — corrections must stay within the stated objective.
      Do not expand scope, add capabilities, or make the task more
      ambitious than what was asked.
  expansive — you may strengthen the task beyond the original ask
      when doing so serves the objective.

When revising, emit a patch touching ONLY the fields "instructions"
and/or "state_context" of the listed blocks, keyed by their EXACT
ids.  A patched field's value is EITHER a list of targeted ops OR a
full replacement string.  PREFER OPS: they are small, so the reply
cannot be cut off, and they change only what you mean to change.
  {"op": "replace", "find": "<exact substring of the current text>",
   "with": "<new text>"}   — "find" must occur exactly once in the
                             field; copy it verbatim from EDITABLE BLOCKS
  {"op": "append", "text": "<text to add at the end>"}
Ops apply in order, each against the text as edited so far.  Use a
full replacement string only when most of the field must change.

Reply with STRICT JSON only — no prose, no code fences:
{"verdict": "accept|revise|stop",
 "rationale": "<one or two sentences>",
 "lesson": "<one durable sentence future runs should know>",
 "patch": {"<block_id>": {"instructions": [
     {"op": "replace", "find": "...", "with": "..."},
     {"op": "append", "text": "..."}]}}}
"patch" must be {} unless verdict is "revise"."""


def _editable_blocks(block: Block) -> List[Dict[str, Any]]:
    """The subtree's blocks that carry improvable text, as prompt-ready
    dicts.  Only blocks with at least one non-empty whitelisted field
    are listed — the judge can only patch what it is shown."""
    out: List[Dict[str, Any]] = []

    def _walk(b: Block) -> None:
        entry: Dict[str, Any] = {
            "id": b.id, "block_type": b.block_type, "name": b.name,
        }
        has_text = False
        for f in IMPROVABLE_TEXT_FIELDS:
            v = getattr(b, f, None)
            if v:
                entry[f] = v
                has_text = True
        if has_text:
            out.append(entry)
        for child in b.body or []:
            _walk(child)

    _walk(block)
    return out


def _build_user_message(
    block: Block, artifact: Artifact, criterion: str, drift: str,
    lessons: List[Dict[str, Any]], revision: int,
) -> str:
    objective = criterion.strip() if criterion else (
        "(no explicit criterion — infer the objective from the task text)")
    blocks_json = json.dumps(_editable_blocks(block), indent=1,
                             ensure_ascii=False)
    decisions = "\n".join(f"- {d}" for d in (artifact.decisions or [])[:8])
    sa = getattr(artifact, "self_assessment", None) or {}
    stages_txt = render_stages_for_judge(getattr(artifact, "stages", None) or [])
    outputs_txt = render_outputs_for_judge(artifact.outputs or [])
    # Only revise/stop records teach anything; an accept lesson is the
    # prior judge praising its predecessor and primes this one toward
    # accept, and an error record describes the judge, not the task.
    lesson_lines = "\n".join(
        f"- [{r.get('verdict', '?')}] {r.get('lesson') or r.get('rationale', '')}"
        for r in prior_lessons_for_judge(lessons)
    )
    return (
        f"OBJECTIVE: {objective}\n\n"
        f"DRIFT POLICY: {drift}\n\n"
        f"REVISION: this run has already been revised {revision} time(s) "
        f"at this level.\n\n"
        f"EDITABLE BLOCKS:\n{blocks_json}\n\n"
        f"OUTCOME:\n"
        f"failed: {bool(artifact.failed)}  (last stage only — see STAGES)\n"
        f"STAGES: {stages_txt}\n"
        f"summary (last stage): {artifact.summary or '(no summary)'}\n"
        f"OUTPUTS: {outputs_txt}\n"
        f"key decisions:\n{decisions or '(none)'}\n"
        f"self_assessment: {json.dumps(sa) if sa else '(none)'}\n\n"
        f"PRIOR LESSONS (oldest first):\n{lesson_lines or '(none)'}\n\n"
        f"Reply with the strict JSON object now."
    )


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


def _extract_json(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of one JSON object from a model reply."""
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


_EXCERPT_HEAD = 300
_EXCERPT_TAIL = 200


def _reply_excerpt(text: Optional[str]) -> str:
    """Head + tail of a raw reply, bounded.  The head shows what the
    judge was trying to say; the tail shows whether it was cut off."""
    if not text:
        return ""
    if len(text) <= _EXCERPT_HEAD + _EXCERPT_TAIL:
        return text
    return text[:_EXCERPT_HEAD] + " […] " + text[-_EXCERPT_TAIL:]


def _error(kind: str, detail: str, reply: Optional[str] = None) -> Dict[str, Any]:
    """A judge-failure verdict.  Never "accept": the loop ends the same
    way, but the ledger and UI must not count an outage as approval."""
    return {
        "verdict": JUDGE_ERROR_VERDICT, "error": kind,
        "rationale": f"judge {kind}: {detail}"[:500], "lesson": "",
        "patch": {}, "reply_excerpt": _reply_excerpt(reply),
        "reply_len": len(reply) if reply else 0,
    }


async def evaluate_improvement(
    block: Block, artifact: Artifact, *,
    criterion: str = "", drift: str = "conservative",
    lessons: Optional[List[Dict[str, Any]]] = None, revision: int = 0,
) -> Dict[str, Any]:
    """Judge a completed level; return verdict/rationale/lesson/patch.

    Never raises.  Every failure path resolves to an ``error`` verdict,
    which ends the improvement loop with the artifact unchanged (so a
    flaky judge can never spin a card through edits) but is recorded
    as a judge failure rather than an approval.
    """
    try:
        from ..services.model_resolver import call_service_model
        out = await call_service_model(
            # Tier-declared category (SERVICE_MODEL_TIERS: "medium"):
            # a revise verdict authors the card's durable instruction
            # text, so generation quality is the product.  Override
            # via ZIYA_IMPROVE_JUDGE_MODEL.
            category="improve_judge",
            system_prompt=_SYSTEM_PROMPT,
            user_message=_build_user_message(
                block, artifact, criterion, drift, lessons or [], revision),
            max_tokens=JUDGE_MAX_TOKENS,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001 — transport must not fail the run
        logger.warning(f"improve judge transport failed (→ error): {e}")
        return _error("transport", f"{type(e).__name__}: {e}")
    parsed = _extract_json(out)
    if not parsed:
        logger.warning("improve judge reply unparseable (→ error)")
        return _error("unparseable", "no JSON object in reply", out)
    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in ("accept", "revise", "stop"):
        return _error("bad_verdict", f"unknown verdict {verdict!r}", out)
    patch = parsed.get("patch")
    if verdict != "revise" or not isinstance(patch, dict):
        patch = {}
    return {
        "verdict": verdict,
        "rationale": str(parsed.get("rationale") or ""),
        "lesson": str(parsed.get("lesson") or ""),
        "patch": patch,
    }
