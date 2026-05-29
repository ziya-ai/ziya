"""
Task self-assessment helper (B1).

The d3 task post-mortem revealed that a task can stream cleanly to
``stream_end`` while having abandoned its stated objective mid-run
(e.g. fell back to a different renderer when blocked from patching
the real one).  The runner's previous "ok = stream ended without
errors" heuristic conflated stream health with task success.

This module supplies the lightest meaningful upgrade: require the
agent to emit, as the final element of its response, a structured
self-assessment of whether the task's objective was met.  The
runner parses that verdict and uses it (rather than stream cleanness)
to set the ``ok`` flag on ``task_finished``.

Self-assessment is not a proof of correctness — the model can still
be wrong about its own work — but it forces a conscious final
evaluation against the original instructions, which is exactly the
step the d3 agent skipped.

The marker is XML-shaped on purpose: tolerant of attribute order and
whitespace, but unambiguous to parse.  Models tend to honor explicit
formatting requirements when given a literal example.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

# Recognised verdict values; anything else is normalised to ``unknown``.
_VERDICT_VALUES = {"true", "false", "partial", "unknown"}

# Tag is intentionally lower-snake for parser robustness.
SELF_ASSESSMENT_TAG = "self_assessment"

# Prompt snippet appended to the task system message.  Kept small —
# every byte spent here is paid on every iteration of the model's
# context window.
SELF_ASSESSMENT_INSTRUCTION = """
## Final Self-Assessment (REQUIRED)

After all other output, on a new line, emit a single self-assessment
tag evaluating whether you met the task's objective as stated in the
instructions:

  <self_assessment objective_met="true|false|partial" rationale="one sentence" />

Rules:
  * ``true`` — every requested deliverable was produced as specified.
  * ``partial`` — some progress, but a stated requirement was not met
    (e.g. you worked around a blocker instead of solving it, or skipped
    part of the request).
  * ``false`` — the objective was not achieved.
  * ``rationale`` is one short sentence, grounded in the instructions
    you were given.  Be honest; the answer is not used to grade you,
    it is used to surface failures the user would otherwise miss.
""".strip()


# Permissive regex: tolerates attribute reordering, single or double
# quotes, surrounding whitespace, and self-closing or paired tag
# forms.  Uses non-greedy captures to avoid eating subsequent content.
_TAG_RE = re.compile(
    r"<\s*self_assessment\b([^>]*?)/?\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(
    r"""(\w+)\s*=\s*(?P<q>["'])(.*?)(?P=q)""",
    re.DOTALL,
)


def parse_self_assessment(text: str) -> Optional[Dict[str, str]]:
    """Extract the final ``<self_assessment ... />`` from a model
    response.

    Returns a dict ``{"objective_met": ..., "rationale": ...}`` if a
    well-formed tag is found, else ``None``.  ``objective_met`` is
    normalised to one of ``true``/``false``/``partial``/``unknown``.
    ``rationale`` is the literal string the model supplied (may be
    empty).

    If multiple tags appear we take the *last* one — the instruction
    asks for it to be the final element, so any earlier occurrence
    is treated as a rehearsal.
    """
    if not text:
        return None
    matches = list(_TAG_RE.finditer(text))
    if not matches:
        return None
    attrs_blob = matches[-1].group(1) or ""
    attrs: Dict[str, str] = {}
    for m in _ATTR_RE.finditer(attrs_blob):
        attrs[m.group(1).lower()] = m.group(3)
    objective = (attrs.get("objective_met") or "").strip().lower()
    if objective not in _VERDICT_VALUES:
        objective = "unknown"
    rationale = (attrs.get("rationale") or "").strip()
    return {"objective_met": objective, "rationale": rationale}


def is_failure(assessment: Optional[Dict[str, str]]) -> bool:
    """Decision rule used by the runner to flip ``ok`` to False.

    Treat ``false`` and ``partial`` as failures — the user almost
    always wants to know when the task was abandoned mid-run, even
    if the model believes it produced something useful.  ``unknown``
    (malformed verdict) is *not* counted as failure here, since we
    can't distinguish "model lied about success" from "model honestly
    failed to format the tag" — that's surfaced separately as a
    missing-assessment warning rather than a hard fail.
    """
    if not assessment:
        return False
    return assessment.get("objective_met") in ("false", "partial")


def signature_for(assessment: Optional[Dict[str, str]]) -> Optional[str]:
    """Stable signature for failure clustering.

    Returns ``None`` if the assessment didn't indicate failure, so
    successful runs don't pick up a misleading signature.
    """
    if not is_failure(assessment):
        return None
    verdict = assessment.get("objective_met", "unknown") if assessment else "unknown"
    return f"self_assessment_{verdict}"


def strip_assessment_tag(text: str) -> str:
    """Remove the trailing ``<self_assessment .../>`` from artifact
    text so the rendered summary does not include the meta tag.
    Idempotent and safe to call when no tag is present."""
    if not text:
        return text
    return _TAG_RE.sub("", text).rstrip()
