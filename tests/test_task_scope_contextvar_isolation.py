"""Task-scope isolation across concurrent tasks (ShellWriteChecker).

``ShellWriteChecker._task_scope`` used to be a mutable instance attribute.
The shell server shares ONE checker across every request (shell_server.py
builds it at startup), which is safe only while the request loop is
strictly serial. Once requests are dispatched concurrently, an instance
attribute leaks across conversations:

  * A's ``set_task_scope(grant)`` becomes visible to B's write/allowlist
    checks -- B silently inherits write paths and ``shell_commands`` it was
    never granted.
  * A's ``clear_task_scope()`` in a ``finally`` block erases the scope out
    from under B *mid-validation*, so B's later checks in the same command
    disagree with its earlier ones.

Both are authorization defects, not throughput races. The state now lives
in a module-level ContextVar, which is per-task.

These tests pin that property. They are written so they FAIL against the
old instance-attribute implementation -- see
``test_shared_instance_attribute_would_leak`` for the explicit negative
control that demonstrates the bug the ContextVar prevents.
"""

import asyncio

import pytest

from app.mcp_servers.write_policy import ShellWriteChecker, _TASK_SCOPE


class _FakePolicyManager:
    """Minimal WritePolicyManager stand-in.

    The isolation property under test concerns only where the task scope is
    STORED, so the base policy is irrelevant here; the real policy paths are
    covered by tests/test_shell_write_checker.py.
    """

    policy = {"safe_write_paths": [], "allowed_write_patterns": []}

    def get_effective_policy(self):
        return self.policy


@pytest.fixture
def checker():
    return ShellWriteChecker(_FakePolicyManager())


@pytest.fixture(autouse=True)
def _clean_scope():
    """Reset the ContextVar around every test.

    Without this, a grant set by one test survives into the next: the scope
    no longer lives on the instance, so a fresh checker does not by itself
    guarantee a clean slate in the *same* context. (``__init__`` clears the
    current context, but a test that sets a scope AFTER construction would
    still bleed.)
    """
    token = _TASK_SCOPE.set(None)
    yield
    _TASK_SCOPE.reset(token)


def _grant(cmd, path):
    return {
        "writable": [{"path": path, "is_dir": True}],
        "shell_commands": [cmd],
        "project_root": "/proj",
    }


class TestConstructionInvariant:
    def test_fresh_checker_has_no_scope(self, checker):
        assert checker._task_scope == {}

    def test_construction_clears_current_context(self):
        """A new checker must not inherit a scope set via an earlier one.

        Moving the state to a module-level ContextVar removed the old
        "instance attribute initialized to {}" guarantee; ``__init__``
        restores it. This is what keeps per-test fixtures and the
        server's startup construction honest.
        """
        first = ShellWriteChecker(_FakePolicyManager())
        first.set_task_scope(_grant("rm", "a/"))
        assert first._task_scope != {}

        second = ShellWriteChecker(_FakePolicyManager())
        assert second._task_scope == {}, "new checker inherited a stale grant"

    def test_clear_resets_to_empty(self, checker):
        checker.set_task_scope(_grant("curl", "a/"))
        assert checker._task_scope != {}
        checker.clear_task_scope()
        assert checker._task_scope == {}

    def test_set_none_is_treated_as_empty(self, checker):
        checker.set_task_scope(_grant("curl", "a/"))
        checker.set_task_scope(None)
        assert checker._task_scope == {}

    def test_property_is_read_only(self, checker):
        """The attribute must not be assignable -- direct assignment would
        bypass the ContextVar and silently reintroduce shared state."""
        with pytest.raises(AttributeError):
            checker._task_scope = _grant("rm", "a/")


class TestConcurrentIsolation:
    async def test_two_tasks_do_not_see_each_others_grant(self, checker):
        """The core property: one shared checker, two concurrent tasks,
        each seeing only its own grant.

        Each task sets its grant, yields (forcing interleaving -- so this
        would fail with a shared instance attribute), then re-reads.
        """
        observed = {}

        async def worker(name, cmd, path):
            checker.set_task_scope(_grant(cmd, path))
            # Yield twice so both tasks have definitely set their scope
            # before either re-reads it.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            scope = checker._task_scope
            observed[name] = (
                scope.get("shell_commands"),
                [e["path"] for e in scope.get("writable", [])],
            )

        await asyncio.gather(
            worker("A", "curl", "a/"),
            worker("B", "rm", "b/"),
        )

        assert observed["A"] == (["curl"], ["a/"]), f"A saw {observed['A']}"
        assert observed["B"] == (["rm"], ["b/"]), f"B saw {observed['B']}"

    async def test_clear_in_one_task_does_not_affect_another(self, checker):
        """A's ``finally: clear_task_scope()`` must not strip B's grant.

        This is the mid-validation erasure case: B checks its scope, A
        finishes and clears, B checks again and must still see its grant.
        """
        b_before = {}
        b_after = {}
        a_cleared = asyncio.Event()

        async def task_a():
            checker.set_task_scope(_grant("curl", "a/"))
            await asyncio.sleep(0.01)
            checker.clear_task_scope()
            a_cleared.set()

        async def task_b():
            checker.set_task_scope(_grant("rm", "b/"))
            b_before.update(checker._task_scope)
            await a_cleared.wait()
            b_after.update(checker._task_scope)

        await asyncio.gather(task_a(), task_b())

        assert b_before.get("shell_commands") == ["rm"]
        assert b_after.get("shell_commands") == ["rm"], (
            "task A's clear_task_scope() erased task B's grant"
        )

    async def test_ungranted_task_sees_no_scope(self, checker):
        """A task that never sets a scope must see none, even while a
        concurrent task holds a grant. This is the privilege-escalation
        case: an ungranted conversation inheriting a granted one's paths."""
        seen = {}
        granted_ready = asyncio.Event()

        async def granted():
            checker.set_task_scope(_grant("rm", "secret/"))
            granted_ready.set()
            await asyncio.sleep(0.02)

        async def ungranted():
            await granted_ready.wait()
            seen["scope"] = checker._task_scope

        await asyncio.gather(granted(), ungranted())

        assert seen["scope"] == {}, (
            f"ungranted task inherited a grant: {seen['scope']}"
        )

    async def test_many_tasks_stay_isolated(self, checker):
        """Scale the property past two tasks to catch any accidental
        module-level aggregation."""
        n = 12
        results = {}

        async def worker(i):
            checker.set_task_scope(_grant(f"cmd{i}", f"p{i}/"))
            await asyncio.sleep(0)
            results[i] = checker._task_scope.get("shell_commands")

        await asyncio.gather(*(worker(i) for i in range(n)))

        for i in range(n):
            assert results[i] == [f"cmd{i}"], f"task {i} saw {results[i]}"


class TestNegativeControl:
    async def test_shared_instance_attribute_would_leak(self):
        """Demonstrate the bug the ContextVar fixes.

        This models the OLD implementation (scope on the instance) and
        asserts that it DOES leak. If this test ever fails, the model no
        longer reflects the old design and the control above is vacuous --
        which is exactly what we want to be told about.
        """

        class OldStyleChecker:
            def __init__(self):
                self._task_scope = {}

            def set_task_scope(self, scope):
                self._task_scope = scope or {}

            def clear_task_scope(self):
                self._task_scope = {}

        old = OldStyleChecker()
        observed = {}

        async def worker(name, cmd):
            old.set_task_scope({"shell_commands": [cmd]})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            observed[name] = old._task_scope.get("shell_commands")

        await asyncio.gather(worker("A", "curl"), worker("B", "rm"))

        # Both tasks observe whichever grant was written last -> a leak.
        assert observed["A"] == observed["B"], (
            "instance-attribute model no longer leaks; this negative "
            "control is stale and must be revisited"
        )
