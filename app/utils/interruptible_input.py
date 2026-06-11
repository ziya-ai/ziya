"""
Interruptible blocking input for CLI code running on an asyncio event loop.

The CLI's event loop registers a custom SIGINT handler via
loop.add_signal_handler(), which replaces the Python-level handler with a
no-op (the loop dispatches the signal via its self-pipe instead).  While
the loop thread is blocked in input() it can't run that dispatch, so ^C
does nothing at all.  These helpers temporarily restore the default SIGINT
handler so ^C raises KeyboardInterrupt inside input(), letting callers
handle cancellation normally.
"""
import contextlib
import signal


@contextlib.contextmanager
def interruptible_sigint():
    """Temporarily restore the default SIGINT handler around blocking input().

    Swaps in signal.default_int_handler so ^C raises KeyboardInterrupt in
    the blocked input() call, then restores the previous handler (typically
    the asyncio loop's no-op) on exit.  Degrades to a no-op when called off
    the main thread, where signal.signal() is not permitted.
    """
    try:
        prev = signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError:
        # Not on the main thread — input() runs un-instrumented; nothing to do.
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, prev)


def interruptible_input(prompt: str = "") -> str:
    """input() that responds to ^C even under an asyncio SIGINT handler.

    Raises KeyboardInterrupt on ^C (and EOFError on ^D), exactly like
    plain input() does without a custom signal handler installed.
    """
    with interruptible_sigint():
        return input(prompt)
