"""
Let a long *waiting* tool call give way when the user sends a message.

A chat message sent while a turn is running is queued as feedback and is
only delivered at a tool boundary.  ``job_wait`` / ``shadow_wait`` (and
``job_start`` with ``wait_s``) can sit inside one tool call for up to an
hour, so a question asked while a backgrounded job ran was held until the
job finished -- which defeats the point of backgrounding it.

Those calls only observe: the job keeps running whether or not anyone is
waiting on it, and their results carry a cursor (\`\`next_seq\`\`) to resume
from.  So when feedback arrives mid-wait, the executor
(app/tool_execution._await_tool_result_unheld) sets the event bound here,
the wait loop sees \`\`requested()\`\` at its next poll, and returns its
normal partial result marked with \`\`reason="user_message"\`\`.  The
post-tool feedback drain then hands the message to the model.

Tools that do not check \`\`requested()\`\` are unaffected: setting the event
does nothing to them.  A stop directive is handled separately (it abandons
the call outright).

The event is a \`\`threading.Event\`\` because the waits poll from worker
threads; \`\`asyncio.to_thread\`\` copies the context, so \`\`requested()\`\`
works there too.
"""
import threading
from contextvars import ContextVar, Token
from typing import Any, Dict, Optional, Tuple

REASON = "user_message"

_current: ContextVar[Optional[threading.Event]] = ContextVar("ziya_tool_interrupt", default=None)


def bind() -> Tuple[threading.Event, Token]:
    """Bind a fresh event for the tool call about to run."""
    ev = threading.Event()
    return ev, _current.set(ev)


def reset(token: Token) -> None:
    _current.reset(token)


def requested() -> bool:
    """True once the user has sent a message during this tool call."""
    ev = _current.get()
    return ev is not None and ev.is_set()


def mark(out: Dict[str, Any], what: str) -> Dict[str, Any]:
    """Annotate a wait result that ended because the user sent a message."""
    out["reason"] = REASON
    out["interrupted_by_user"] = True
    out["message"] = (
        f"Stopped waiting because the user sent a message. {what} is still "
        f"running. Read and answer the user's message first; resume waiting "
        f"with 'next' only if it still makes sense."
    )
    return out
