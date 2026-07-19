"""
Dangling-intent judge.

When a non-prefill model ends a turn cleanly with text that matched the
cheap intent-phrase prefilter (streaming_tool_executor._INTENT_PHRASES),
ask a small/cheap model whether the response actually ENDS with the
assistant announcing an action it will itself perform next — as opposed
to a phrase inside quoted/drafted content ("...before writing anything"
in a message composed for a third party), a conditional-on-the-user
future ("once you apply the diff, I'll run the suite"), a negation, or
past tense.  Substring matching cannot make those distinctions; this
judge exists precisely because it false-positived on all of them.

Polarity: any parsing/transport failure or ambiguity resolves to False
(end the turn).  This is the OPPOSITE default from
app/agents/until_evaluator.py, deliberately: there the conservative
failure is "keep iterating"; here the safe failure is a clean stop —
a wrong "continue" costs a full-context primary-model round trip,
while a wrong "end" costs at most one lost auto-continuation that the
user can trivially re-prompt.

Cost profile: consulted only when the phrase prefilter already fired on
a clean, tool-free, non-question turn end — single-digit occurrences
per session, ~300-500 tokens each on the cheap tier.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_YES_RE = re.compile(r"^\s*(yes|y|true)\b", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*(no|n|false)\b", re.IGNORECASE)

# Final chunk of the response handed to the judge.  Two paragraphs is
# enough to see whether an intent phrase is the closing announcement or
# mid-text content the writer already moved past; hard char cap bounds
# the spend regardless of paragraph size.
_TAIL_PARAGRAPHS = 2
_TAIL_MAX_CHARS = 1500

_SYSTEM_PROMPT = """\
You are a binary classifier.  You will receive the ENDING of a message
written by an AI assistant.  Decide whether the message ENDS with the
assistant announcing an action that the assistant itself is about to
perform next, unconditionally, in this same session.

Answer "no" if the action-like language is any of the following:
- inside quoted or drafted text composed for someone else to send or use
- conditional on the user doing something first ("once you apply it,
  I'll run the tests")
- a question or a request for the user to decide
- negated ("I won't check...") or past tense ("I checked before writing")
- advice about what the user or someone else should do

Answer "yes" only if the assistant is clearly declaring its own
immediate next action and then stopped without performing it
(e.g. "Let me read that file first." as the final sentence).

Reply with exactly one token: "yes" or "no".  No punctuation.  No
explanation.  If you cannot tell, reply "no"."""


def extract_tail(text: str) -> str:
    """Final paragraphs of the response, char-capped, for the judge."""
    paragraphs = [p for p in text.strip().split('\n\n') if p.strip()]
    tail = '\n\n'.join(paragraphs[-_TAIL_PARAGRAPHS:]) if paragraphs else ''
    return tail[-_TAIL_MAX_CHARS:]


def _parse_yes_no(text: Optional[str]) -> bool:
    if not text:
        return False
    if _YES_RE.search(text):
        return True
    if _NO_RE.search(text):
        return False
    logger.debug(f"intent judge: ambiguous reply {text!r}; defaulting to no (end turn)")
    return False


async def judge_dangling_intent(assistant_text: str) -> bool:
    """True iff the cheap model judges the response to end with an
    unexecuted self-action announcement.  False on any failure."""
    tail = extract_tail(assistant_text)
    if not tail:
        return False
    try:
        from .model_resolver import call_service_model
        out = await call_service_model(
            category="intent_judge",
            system_prompt=_SYSTEM_PROMPT,
            user_message=f"MESSAGE ENDING:\n{tail}\n\nReply yes or no.",
            max_tokens=4,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"intent judge transport failed (→ no / end turn): {e}")
        return False
    return _parse_yes_no(out)
