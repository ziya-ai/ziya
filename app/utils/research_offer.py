"""Turn-end backstop for "I could look into X, want me to?".

The base prompt (app/agents/prompts.py, INVESTIGATIONS RUN TO COMPLETION)
tells the model to follow every investigative thread instead of ending the
turn with an offer to follow it.  This module is the harness half: when a
turn ends cleanly on such an offer anyway, the executor re-prompts once so
the offered work is done in the same turn.

Two stages, the same shape as app/services/intent_judge.py:

* ``looks_like_research_offer`` is a cheap regex prefilter over the last
  paragraph.  It is deliberately loose about what is being offered.
* ``judge_research_offer`` asks a small model whether the ending is an
  offer of *information-gathering* the assistant could do itself.  An offer
  of an action that changes something (post, send, file, commit, apply,
  delete, deploy), a choice between outcomes, or a request for something
  only the user has must end the turn: continuing there would act without
  the approval the model correctly asked for.

Polarity: any failure or ambiguity is False (end the turn), as in the
intent judge.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Consecutive no-progress auto-continues allowed for an offer.  Tighter than
# the intent cap: a model that re-offers without calling a tool is not going
# to be talked out of it by a third note.
RESEARCH_OFFER_STALL_CAP = 2

RESEARCH_OFFER_CONTINUE_NOTE = (
    "[System: You ended by offering, or asking permission, to investigate "
    "further. Standing instruction from the user: follow every useful thread "
    "of an investigation without asking. Do the investigation you offered "
    "now, with your tools, and then report the complete findings. Do not "
    "repeat the offer. Do not take any action that changes something "
    "(posting, sending, filing, committing, deleting, or writing outside the "
    "write policy); if only such an action remains, say so and stop.]"
)

_PREFILTER_TAIL_CHARS = 600

_VERBS = (
    r"dig|look|check|trace|investigat\w*|continue|keep (?:going|digging|looking)|"
    r"go (?:deeper|further|ahead|through)|follow|pull|read|run|verify|confirm|"
    r"search|proceed|chase|drill|explore|examine|inspect|query|grep|test|"
    r"reproduce|compare|analy[sz]e|review|find|track|pin|narrow|walk|map|audit"
)

_OFFER_RE = re.compile(
    r"\b(?:like|want|need) me to\b"
    r"|\b(?:should|shall) i (?:also |now |next |first |then )?(?:" + _VERBS + r")\b"
    r"|\bif you(?:'d|’d| would)? (?:like|want|prefer)\b[^.?!\n]{0,60}\bi(?: can| could| will|'ll|’ll)\b"
    r"|\bi (?:can|could)(?: also| then| next)? (?:" + _VERBS + r")\b[^?!\n]{0,200}"
    r"\bif (?:you(?:'d|’d| would)? (?:like|want|prefer)|that(?:'s|’s| is| would be) (?:useful|helpful))"
    r"|\b(?:happy|glad) to (?:" + _VERBS + r")\b"
    r"|\b(?:say the word|just say so|let me know)\b[^.?!\n]{0,80}\bi(?:'ll|’ll| will| can)\b",
    re.IGNORECASE,
)

_YES_RE = re.compile(r"^\s*(yes|y|true)\b", re.IGNORECASE)

_SYSTEM_PROMPT = """\
You are a binary classifier.  You will receive the ENDING of a message
written by an AI assistant that has tools for reading files, searching,
running read-only commands and querying data.

Answer "yes" only if the message ends with the assistant offering, or asking
permission, to do further INFORMATION-GATHERING work that it could do itself
with those tools: looking into, digging, checking, tracing, reading,
searching, verifying, confirming, reproducing, testing a hypothesis,
comparing, or continuing an investigation
(e.g. "Want me to trace where that flag is set?", "I can check the other
three call sites if you'd like.", "Should I keep digging into the timeout?").

Answer "no" if the ending is any of the following:
- an offer or question about an action that CHANGES something: posting,
  sending, replying, filing or updating a ticket, committing, pushing,
  deleting, deploying, launching, applying a change, implementing a fix,
  writing or editing a file, saving something
- a question asking the user to choose between alternatives, designs or
  outcomes, or to state a preference or intent
- a request for information, access or material only the user has
- inside quoted or drafted text composed for someone else to send
- conditional on the user doing something first
- an ordinary conclusion with no offer in it

If the ending mixes an information-gathering offer with an offer of an
action that changes something, answer "no".

Reply with exactly one token: "yes" or "no".  No punctuation.  No
explanation.  If you cannot tell, reply "no"."""


def looks_like_research_offer(text: Optional[str]) -> bool:
    """Cheap prefilter: the last paragraph contains an offer/permission shape."""
    if not text or not text.strip():
        return False
    paragraphs = [p for p in text.strip().split("\n\n") if p.strip()]
    tail = paragraphs[-1][-_PREFILTER_TAIL_CHARS:]
    return bool(_OFFER_RE.search(tail))


async def judge_research_offer(assistant_text: str) -> bool:
    """True iff the cheap model judges the ending to be an offer of
    information-gathering the assistant could simply do.  False on failure."""
    from app.services.intent_judge import extract_tail
    tail = extract_tail(assistant_text)
    if not tail:
        return False
    try:
        from app.services.model_resolver import call_service_model
        out = await call_service_model(
            category="intent_judge",
            system_prompt=_SYSTEM_PROMPT,
            user_message=f"MESSAGE ENDING:\n{tail}\n\nReply yes or no.",
            max_tokens=4,
            temperature=0.0,
        )
    except BaseException as e:
        # ServiceInfraError is a BaseException; cancellation and interpreter
        # exit are re-raised.
        if not isinstance(e, Exception) and not getattr(e, "infra_kind", ""):
            raise
        logger.warning(f"research-offer judge transport failed (→ no / end turn): {e}")
        return False
    return bool(out and _YES_RE.search(out))
