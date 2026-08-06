"""Classification of mid-stream user feedback as a stop directive.

Feedback sent while a turn is streaming has two possible meanings: "abandon
this turn" or "keep going, but differently".  These need opposite handling —
the first ends the stream, the second is injected into the conversation — and
the cost of confusing them is asymmetric: mistaking redirection for a stop
throws away the whole turn.

The original test was a substring scan::

    any(w in msg.lower() for w in ['stop', 'halt', 'abort', 'cancel', 'quit'])

which terminated the turn for "don't stop, keep going with the second file"
and "stop reading that file and check the tests instead" — both of which are
redirection, and the second of which is the single most natural thing a user
types at a running tool chain.

This module requires the message to be a stop directive *in its entirety*
rather than merely to contain a stop word, and refuses outright when a
negation is present.  A message that says stop AND says something else is
treated as feedback: the model can then decide, which is the safer default
because it is recoverable.  A genuine unconditional stop is better served by
the explicit ``interrupt`` message type the Stop button sends.
"""

import re

# Complete messages that mean "end this turn".  Matched after filler removal,
# so "please stop now" reduces to "stop".
_STOP_PHRASES = frozenset({
    "stop", "halt", "abort", "cancel", "quit",
    "stop it", "stop that", "stop this",
    "cancel it", "cancel that", "abort it",
    "nevermind", "never mind", "forget it",
})

# Words carrying no directive content, dropped before matching.
_FILLERS = frozenset({
    "please", "just", "now", "ok", "okay", "yeah", "yes",
    "hey", "yo", "immediately", "right",
})

# A negation anywhere flips the meaning ("don't stop", "no need to abort").
_NEGATION = re.compile(
    r"\b(?:dont|do\s+not|doesnt|does\s+not|no\s+need|not|never|"
    r"instead|without|rather\s+than|keep|continue)\b"
)


def is_stop_directive(message: str) -> bool:
    """True when *message* is, in full, a request to end the current turn.

    Deliberately conservative: anything with extra content or a negation is
    feedback, not a stop.  Callers wanting an unconditional halt should use
    the ``interrupt`` message type instead of relying on text matching.
    """
    if not message or not isinstance(message, str):
        return False

    # Collapse to bare words: apostrophes are dropped rather than treated as
    # separators so "don't" becomes the single token "dont" that _NEGATION
    # matches, instead of splitting into "don" + "t".
    lowered = message.lower().replace("'", "").replace("\u2019", "")
    normalized = re.sub(r"[^a-z\s]+", " ", lowered)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False

    if _NEGATION.search(normalized):
        return False

    tokens = [t for t in normalized.split() if t not in _FILLERS]
    if not tokens:
        return False

    return " ".join(tokens) in _STOP_PHRASES


def is_stop_feedback(item: dict) -> bool:
    """True for a pending-feedback item that should end the turn.

    Covers both the explicit ``interrupt`` type and a text message that is
    itself a stop directive, so callers need one predicate rather than
    repeating the pairing at each of the six drain points.
    """
    if not isinstance(item, dict):
        return False
    if item.get("type") == "interrupt":
        return True
    return is_stop_directive(item.get("message", ""))
