"""
Regression guard for cross-test event-loop pollution.

``asyncio.run()`` closes the loop it creates.  Test files that instead use
``asyncio.get_event_loop().run_until_complete(...)`` then fail with "There
is no current event loop" -- but ONLY when they happen to run after a
closer.  With pytest-randomly installed the breakage is seed-dependent,
which is how it survived: each file passes in isolation.

The ``_ensure_open_event_loop`` autouse fixture in conftest.py repairs the
loop before every test.  These tests fail if that fixture is removed or
stops working, and the ordering pair below reproduces the original hazard
directly.
"""

import asyncio
import warnings


async def _noop():
    return "ok"


class TestLoopIsUsableAfterAClose:
    """Ordering is alphabetical-within-class under -p no:randomly, but the
    fixture makes each test independent of order either way."""

    def test_a_closes_the_loop_like_asyncio_run_does(self):
        # Exactly what the polluting files do.
        assert asyncio.run(_noop()) == "ok"

    def test_b_legacy_idiom_still_works_afterwards(self):
        # The victim idiom, used by 8 test files.  Without the conftest
        # fixture this raises RuntimeError when it follows the test above.
        assert asyncio.get_event_loop().run_until_complete(_noop()) == "ok"


class TestFixtureContract:
    def test_a_loop_is_installed_and_open(self):
        loop = asyncio.get_event_loop()
        assert not loop.is_closed(), (
            "every test must start with an open loop installed"
        )

    def test_explicitly_closing_does_not_break_the_next_test(self):
        # A test that closes the loop itself must not poison its successor;
        # the fixture's teardown reinstalls a fresh one.
        loop = asyncio.get_event_loop()
        loop.close()
        assert loop.is_closed()

    def test_z_successor_of_a_closing_test_is_healthy(self):
        assert asyncio.get_event_loop().run_until_complete(_noop()) == "ok"


class TestAsyncTestsStillWork:
    """The fixture must not interfere with pytest-asyncio, which manages
    its own loop for ``async def`` tests (asyncio_mode = auto)."""

    async def test_native_async_test_runs(self):
        assert await _noop() == "ok"

    async def test_native_async_sees_a_running_loop(self):
        assert asyncio.get_running_loop() is not None


class TestFixtureIsQuiet:
    """The fixture must not itself emit the DeprecationWarning it exists to
    work around.  A fixture that warns on every test in the suite trains
    readers to ignore warnings — the same failure mode as an
    intermittently-red suite, just quieter."""

    def test_probe_does_not_warn_after_a_closed_loop(self):
        # Reproduce the fixture's own probe under -W error semantics.
        asyncio.run(_noop())  # close the loop, as the polluters do
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                try:
                    prev = asyncio.get_event_loop_policy().get_event_loop()
                except RuntimeError:
                    prev = None
            if prev is None or prev.is_closed():
                asyncio.set_event_loop(asyncio.new_event_loop())
        # And the repaired loop is usable.
        assert asyncio.get_event_loop().run_until_complete(_noop()) == "ok"
