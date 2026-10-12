"""
Until-condition evaluator.

Given a natural-language condition and a finished iteration's
Artifact, ask a small/cheap model whether the condition is true, and
WHY NOT when it is not.  Any parsing/transport failure resolves to
"not satisfied" -- the loop conservatively continues rather than
terminating on an ambiguous response.

The model is shown EVIDENCE, not only the agent's summary: the
iteration's per-stage outcomes (with a flag on stages that ran no
tool), the data parts the body emitted, and the file parts it named.
GFX Stage 2 run 3068d3d0 was asked whether "the ledger reports no
unverified defect" while the evaluator could see only a summary; four
child stages had executed nothing and nothing in the prompt showed it.

The rationale exists because of run 7dfb90f6 (KuiperEAR-switch-mgr,
2026-10-10): three iterations each passed, the evaluator said "no"
three times, and nothing anywhere recorded why.  The body was never told
what the evaluator objected to, so iterations 2 and 3 re-did iteration
1, and the block's failure_reason was the bare string "failed".  The
verdict now carries a one-sentence reason that the executor records on
the block, emits as an ``until_evaluated`` event, and feeds into the
next iteration's instructions (``{{until.last_rationale}}``).
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ..models.task_card import Artifact
from ..utils.self_improve import (
    render_data_parts_for_judge,
    render_outputs_for_judge,
    render_stages_for_judge,
)

logger = logging.getLogger(__name__)


_YES_RE = re.compile(r"^\s*(yes|y|true|done|satisfied|met)\b", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*(no|n|false|not[\s-]?yet|incomplete|continue)\b", re.IGNORECASE)

# The rationale is one sentence; anything longer is the model
# explaining itself, which the loop does not need and the iteration
# context should not carry.
_RATIONALE_MAX_CHARS = 400


@dataclass(frozen=True)
class UntilVerdict:
    """One evaluation of an Until condition against one iteration.

    ``rationale`` is the model's one-sentence reason when the condition
    is NOT satisfied (what evidence was missing or contradicted it).  It
    is empty when satisfied, and empty when the model gave none, so a
    caller can tell "told the body why" from "had nothing to tell it".
    """
    satisfied: bool
    rationale: str = ""


_SYSTEM_PROMPT = """\
You are a binary classifier.  You will receive a CONDITION and evidence
about one pass of work: the agent's SUMMARY, the per-stage outcomes
(STAGES), the structured DATA the pass emitted, and the OUTPUT files it
named.  Decide whether the condition is true.

Rules of evidence:
- The SUMMARY is the agent's CLAIM about what happened, not a
  measurement.  Do not answer yes on the summary alone when the
  condition names something checkable.
- A stage marked NO TOOL CALLS executed nothing; a claim of building,
  running, rendering or verifying in such a stage is unsupported.
- If the condition refers to a count, a status, a report or a file, it
  is true only if DATA or OUTPUTS shows it.  Absent evidence means no.

Reply format, exactly two lines:
  line 1: the single token "yes" or "no".  No punctuation.
  line 2: ONE sentence.  If "no": the specific evidence that is missing
          or contradicts the condition (name the count, status, file or
          stage), so the next pass can address it.  If "yes": leave the
          line empty.
No preamble.  If you cannot tell, reply "no"."""


def _build_user_message(condition: str, artifact: Artifact) -> str:
    # Reuses the self-improve judge's renderers so the two judges read
    # the same evidence the same way: STAGES carries the NO TOOL CALLS
    # flag, DATA serializes emitted data parts as JSON (a condition that
    # names a count is checkable only from these), OUTPUTS names files.
    decisions = "\n".join(f"- {d}" for d in (artifact.decisions or [])[:5])
    stages = getattr(artifact, "stages", None) or []
    outputs = artifact.outputs or []
    return (
        f"CONDITION: {condition}\n\n"
        f"SUMMARY (the agent's claim):\n{artifact.summary or '(no summary)'}\n\n"
        f"KEY DECISIONS:\n{decisions or '(none)'}\n\n"
        f"STAGES:\n{render_stages_for_judge(stages)}\n\n"
        f"DATA (emitted by the pass):\n{render_data_parts_for_judge(outputs)}\n\n"
        f"OUTPUTS:\n{render_outputs_for_judge(outputs)}\n\n"
        f"Reply yes or no on the first line, then one sentence of reason."
    )


def _parse_yes_no(text: Optional[str]) -> bool:
    if not text:
        return False
    if _YES_RE.search(text):
        return True
    if _NO_RE.search(text):
        return False
    # Ambiguous → conservative no (keep iterating).
    logger.debug(f"until evaluator: ambiguous reply {text!r}; defaulting to no")
    return False


def parse_verdict(text: Optional[str]) -> UntilVerdict:
    """Parse the two-line reply.  Tolerates a bare one-token reply
    (older prompt, or a model that ignored line 2) and a reply that put
    the reason on the same line as the token ("no - the log has one
    failure")."""
    if not text:
        return UntilVerdict(False, "")
    raw = text.strip()
    satisfied = _parse_yes_no(raw)
    if satisfied:
        return UntilVerdict(True, "")
    first, _, rest = raw.partition("\n")
    # Reason on the first line after the token, e.g. "no: ..." / "no - ...".
    m = _NO_RE.match(first)
    inline = first[m.end():].lstrip(" \t:.,;-–—") if m else ""
    rationale = (rest.strip() or inline).split("\n", 1)[0].strip()
    if len(rationale) > _RATIONALE_MAX_CHARS:
        rationale = rationale[:_RATIONALE_MAX_CHARS - 1].rstrip() + "…"
    return UntilVerdict(False, rationale)


async def evaluate_condition_verdict(condition: str, artifact: Artifact) -> UntilVerdict:
    """Judge ``condition`` against ``artifact``; carries the reason when unmet."""
    if not condition.strip():
        return UntilVerdict(False, "")
    try:
        from ..services.model_resolver import call_service_model
        out = await call_service_model(
            category="memory_extraction",  # cheap-tier router; no dedicated category yet
            system_prompt=_SYSTEM_PROMPT,
            user_message=_build_user_message(condition, artifact),
            # Token on line 1 plus one sentence on line 2.  The previous
            # cap of 4 made a reason impossible to return at all.
            max_tokens=160,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"until evaluator transport failed (→ False): {e}")
        return UntilVerdict(False, "")
    return parse_verdict(out)


async def evaluate_condition(condition: str, artifact: Artifact) -> bool:
    """Return True iff the model judges `condition` satisfied by `artifact`."""
    return (await evaluate_condition_verdict(condition, artifact)).satisfied
