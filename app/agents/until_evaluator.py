"""
Until-condition evaluator.

Given a natural-language condition and a finished iteration's
Artifact, ask a small/cheap model whether the condition is true.
Returns a strict bool.  Any parsing/transport failure resolves to
False — the loop conservatively continues rather than terminating
on an ambiguous response.
"""

import logging
import re
from typing import Optional

from ..models.task_card import Artifact

logger = logging.getLogger(__name__)


_YES_RE = re.compile(r"^\s*(yes|y|true|done|satisfied|met)\b", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*(no|n|false|not[\s-]?yet|incomplete|continue)\b", re.IGNORECASE)


_SYSTEM_PROMPT = """\
You are a binary classifier.  You will receive a CONDITION and a brief
SUMMARY of work that was just performed.  Decide whether the condition
is true given the summary.

Reply with exactly one token: "yes" or "no".  No punctuation.  No
explanation.  No preamble.  If you cannot tell, reply "no"."""


def _build_user_message(condition: str, artifact: Artifact) -> str:
    decisions = "\n".join(f"- {d}" for d in (artifact.decisions or [])[:5])
    return (
        f"CONDITION: {condition}\n\n"
        f"SUMMARY:\n{artifact.summary or '(no summary)'}\n\n"
        f"KEY DECISIONS:\n{decisions or '(none)'}\n\n"
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
