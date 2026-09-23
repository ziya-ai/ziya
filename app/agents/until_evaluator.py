"""
Until-condition evaluator.

Given a natural-language condition and a finished iteration's
Artifact, ask a small/cheap model whether the condition is true.
Returns a strict bool.  Any parsing/transport failure resolves to
False — the loop conservatively continues rather than terminating
on an ambiguous response.

The model is shown EVIDENCE, not only the agent's summary: the
iteration's per-stage outcomes (with a flag on stages that ran no
tool), the data parts the body emitted, and the file parts it named.
GFX Stage 2 run 3068d3d0 was asked whether "the ledger reports no
unverified defect" while the evaluator could see only a summary; four
child stages had executed nothing and nothing in the prompt showed it.
"""

import logging
import re
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

Reply with exactly one token: "yes" or "no".  No punctuation.  No
explanation.  No preamble.  If you cannot tell, reply "no"."""


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
        f"Reply yes or no."
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


async def evaluate_condition(condition: str, artifact: Artifact) -> bool:
    """Return True iff the model judges `condition` satisfied by `artifact`."""
    if not condition.strip():
        return False
    try:
        from ..services.model_resolver import call_service_model
        out = await call_service_model(
            category="memory_extraction",  # cheap-tier router; no dedicated category yet
            system_prompt=_SYSTEM_PROMPT,
            user_message=_build_user_message(condition, artifact),
            max_tokens=4,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"until evaluator transport failed (→ False): {e}")
        return False
    return _parse_yes_no(out)
