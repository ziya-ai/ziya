"""Augment ``file_write`` rejection messages with a diff-fallback hint.

The d3 task post-mortem showed an agent abandoning a real fix after
a single ``file_write`` rejection: the agent saw ``resolved path
escapes project root: ...`` and concluded the file was unreachable,
so it pivoted to a workaround instead of using the diff fallback
the system actually supports.

The session-context system message already mentions the diff
fallback, but at the moment of a blocked write the immediate tool
result doesn't reinforce it — and that's the moment of choice.
This helper appends a short, actionable hint to every rejection
so the agent's next turn has a clear next step where it just failed.

Pure function, no I/O — tested in
``tests/test_write_rejection_hint.py``.
"""

from __future__ import annotations

# Wording is deliberately concrete: the agent should know
#   (a) what to do — emit a git diff in its *response* text,
#   (b) when — only when the target is outside its writable scope,
#   (c) why — file_write is denied for that path so retrying it
#       won't help.
# Keep the hint short; verbose hints get ignored.
DIFF_FALLBACK_HINT: str = (
    "Hint: this path is outside your writable scope, so file_write "
    "cannot be retried for it. To propose a change, emit a git diff "
    "in your response text (the host applies diffs even for paths "
    "outside the writable scope). Use file_write only for paths "
    "explicitly inside your writable scope."
)


def augment_rejection(message) -> str:
    """Return *message* with the diff-fallback hint appended.

    Idempotent: if *message* already contains the hint (e.g. a
    wrapper pre-formatted it), the input is returned unchanged.
    Non-string and empty inputs return ``""`` so callers can pass
    tool results unconditionally without type-checking.
    """
    if not isinstance(message, str) or not message:
        return ""
    if DIFF_FALLBACK_HINT in message:
        return message
    # Trim trailing whitespace on the original so the spacing
    # between message and hint is exactly one blank line — keeps
    # the agent's view consistent regardless of caller-side
    # newline conventions.
    head = message.rstrip()
    return head + "\n\n" + DIFF_FALLBACK_HINT
