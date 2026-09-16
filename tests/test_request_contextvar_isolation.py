"""Request-scoped ContextVars must not leak between tests.

``app.context`` keeps the per-request project root / conversation id / task
grants in module-level ContextVars.  A sync fixture that calls
``set_project_root`` without resetting the token leaves the value in the
main thread's context for the rest of the session -- which is how
``test_chat_history_tools`` made ``test_write_policy_prompt_consistency``
describe the wrong project's write policy, but only when both ran in one
session in that order.

Two layers are pinned here:

* the autouse ``_restore_request_contextvars`` guard in ``conftest.py``, by
  running an isolated two-test session (fixed order, randomly disabled) in
  which the first test leaks and the second asserts a clean context; the
  session is run once WITH the guard and once WITHOUT, and only the guarded
  run may pass -- so a guard that silently stopped working fails here;
* the original leaking fixture, which must reset its own tokens rather than
  rely on the guard.
"""
from __future__ import annotations

import contextvars
import inspect
import textwrap

import pytest

from app import context as ctx

pytest_plugins = ["pytester"]


_LEAK_PAIR = textwrap.dedent(
    """
    from app import context as ctx

    def test_1_leak_on_purpose():
        ctx.set_project_root("/leaked/by/test_1")
        ctx.set_conversation_id("leaked-conv")

    def test_2_sees_a_clean_context():
        assert ctx.get_project_root_or_none() is None, "project root leaked"
        assert ctx._request_conversation_id.get() is None, "conversation id leaked"
    """
)


def _guard_source() -> str:
    import tests.conftest as conftest
    fn = conftest._restore_request_contextvars
    # pytest 8.4+ wraps fixtures in a definition object; unwrap if so.
    fn = getattr(fn, "_get_wrapped_function", lambda: fn)()
    fn = getattr(fn, "__wrapped__", fn)
    return "import pytest\n\n" + textwrap.dedent(inspect.getsource(fn))


def test_guard_restores_a_leak(pytester):
    pytester.makeconftest(_guard_source())
    pytester.makepyfile(test_pair=_LEAK_PAIR)
    result = pytester.runpytest_inprocess("-p", "no:randomly", "-p", "no:cacheprovider", "-q")
    result.assert_outcomes(passed=2)


def test_leak_is_real_without_the_guard(pytester):
    """Positive control: the same pair, no conftest, must fail on test_2.
    If this ever passes, the pair no longer demonstrates a leak and the
    guarded test above proves nothing."""
    pytester.makepyfile(test_pair=_LEAK_PAIR)
    result = pytester.runpytest_inprocess("-p", "no:randomly", "-p", "no:cacheprovider", "-q")
    result.assert_outcomes(passed=1, failed=1)


def test_every_context_var_is_a_module_attribute():
    """The guard restores by introspecting ``app.context``, so a new
    ContextVar added there is covered automatically -- but only if it is a
    module attribute.  A refactor into a container object would silently
    shrink the guard; pin the shape."""
    found = [v for v in vars(ctx).values() if isinstance(v, contextvars.ContextVar)]
    assert len(found) >= 9, found
    assert ctx._request_project_root in found
    assert ctx._request_conversation_id in found


def test_chat_history_env_fixture_resets_its_own_vars():
    """The source is fixed too, not just papered over by the guard."""
    import tests.test_chat_history_tools as mod

    src = inspect.getsource(mod)
    assert "_request_project_root.reset(" in src, (
        "test_chat_history_tools.env no longer resets the project root it sets; "
        "the suite would be relying on the conftest guard alone"
    )
    assert "_request_conversation_id.reset(" in src
