"""
Per-task shell timeout grants (E2).

A frontend production build exceeds both base ceilings
(MAX_COMMAND_TIMEOUT and TOOL_EXEC_TIMEOUT, 300 s each), so a card whose
loop rebuilt a bundle fought the timeout on every iteration and
occasionally handed a downstream step a build that had never completed.
A scope grant must lift the ceiling AND the default, and must never
shorten either.
"""

import pytest

from app.models.task_card import ScopeEntry, TaskScope, merge_scopes


class TestScopeField:
    def test_defaults_to_none(self):
        assert TaskScope().shell_timeout_secs is None

    def test_round_trips(self):
        scope = TaskScope(shell_timeout_secs=1200)
        assert TaskScope(**scope.model_dump()).shell_timeout_secs == 1200

    def test_numeric_string_is_coerced_by_pydantic(self):
        # The field is typed Optional[int], so pydantic coerces a numeric
        # string at construction.  Worth pinning: it means the int()
        # guard in merge_scopes defends UNVALIDATED shapes only, which is
        # why the malformed-value test below uses model_construct.
        assert TaskScope(shell_timeout_secs="1200").shell_timeout_secs == 1200


class TestMergeSemantics:
    def test_maximum_wins_not_last(self):
        # Additive-grant rule: an inner layer may only add.  Last-wins
        # would let an inner 60 s revoke an outer 1200 s.
        merged = merge_scopes(
            TaskScope(shell_timeout_secs=1200),
            TaskScope(shell_timeout_secs=60),
        )
        assert merged.shell_timeout_secs == 1200

    def test_inner_may_raise(self):
        merged = merge_scopes(
            TaskScope(shell_timeout_secs=60),
            TaskScope(shell_timeout_secs=1200),
        )
        assert merged.shell_timeout_secs == 1200

    def test_absent_layers_do_not_clear_a_grant(self):
        merged = merge_scopes(
            TaskScope(shell_timeout_secs=900),
            TaskScope(),
            TaskScope(paths=[ScopeEntry(path="a.py")]),
        )
        assert merged.shell_timeout_secs == 900

    def test_no_grant_anywhere_stays_none(self):
        merged = merge_scopes(TaskScope(), TaskScope())
        assert merged.shell_timeout_secs is None

    def test_malformed_value_is_ignored(self):
        # Reached via model_construct because pydantic REJECTS a
        # non-numeric string at construction, so the only way a bad
        # value arrives at merge_scopes is a validation-skipping path
        # (model_construct, or a hand-built record from disk).  That is
        # precisely what the int() guard exists to survive.
        merged = merge_scopes(
            TaskScope(shell_timeout_secs=600),
            TaskScope.model_construct(shell_timeout_secs="not-a-number"),
        )
        assert merged.shell_timeout_secs == 600

    def test_does_not_disturb_other_fields(self):
        merged = merge_scopes(
            TaskScope(shell_timeout_secs=600, tools=["a"]),
            TaskScope(tools=["b"], cwd="/x"),
        )
        assert merged.tools == ["a", "b"]
        assert merged.cwd == "/x"
        assert merged.shell_timeout_secs == 600


class TestContextVar:
    def test_set_get_reset(self):
        from app.context import (
            get_task_shell_timeout, reset_task_shell_timeout,
            set_task_shell_timeout,
        )
        token = set_task_shell_timeout(1200)
        try:
            assert get_task_shell_timeout() == 1200
        finally:
            reset_task_shell_timeout(token)
        assert get_task_shell_timeout() is None

    @pytest.mark.parametrize("value", [None, 0, -5, "", "abc"])
    def test_non_grants_normalise_to_none(self, value):
        from app.context import (
            get_task_shell_timeout, reset_task_shell_timeout,
            set_task_shell_timeout,
        )
        token = set_task_shell_timeout(value)
        try:
            assert get_task_shell_timeout() is None
        finally:
            reset_task_shell_timeout(token)


class TestEnvelope:
    def test_grant_alone_produces_an_envelope(self):
        # A timeout-only scope must still travel: before this, the
        # envelope builder returned None unless a path/command grant
        # existed, so a timeout-only card sent nothing.
        from app.context import (
            reset_task_shell_timeout, set_task_shell_timeout,
        )
        from app.mcp.manager import build_task_scope_envelope
        token = set_task_shell_timeout(1200)
        try:
            env = build_task_scope_envelope()
        finally:
            reset_task_shell_timeout(token)
        assert env is not None
        assert env["shell_timeout_secs"] == 1200

    def test_no_grants_still_returns_none(self):
        from app.mcp.manager import build_task_scope_envelope
        assert build_task_scope_envelope() is None


class TestShellServerBounds:
    @pytest.fixture()
    def server(self):
        from app.mcp_servers.shell_server import ShellServer
        return ShellServer()

    def test_init_completes_fully(self, server):
        # Regression guard: _timeout_bounds was first added INSIDE
        # __init__, immediately after self.max_timeout.  Its ``return``
        # made every later assignment unreachable, so the server came up
        # without safe_command_patterns and every tools/list raised
        # AttributeError — yet the bounds tests below still passed,
        # because both attributes they read are set BEFORE the insertion
        # point.  Assert on an attribute assigned at the very END of
        # __init__ so that class of truncation cannot pass again.
        assert hasattr(server, 'safe_command_patterns')
        assert server.safe_command_patterns, 'command patterns must be built'
        assert hasattr(server, 'max_output_bytes')

    def test_tools_list_still_works(self, server):
        # The user-visible symptom of the truncation above.
        import asyncio
        resp = asyncio.run(server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        ))
        assert 'result' in resp, resp

    def test_no_grant_uses_base_values(self, server):
        default, ceiling = server._timeout_bounds(None)
        assert default == server.command_timeout
        assert ceiling == server.max_timeout

    def test_grant_raises_both(self, server):
        # Raising the ceiling alone was insufficient: with the default
        # left at 30 s, a model that omitted `timeout` still got 30 s.
        default, ceiling = server._timeout_bounds(
            {"shell_timeout_secs": 1200}
        )
        assert default == 1200
        assert ceiling == 1200

    def test_grant_below_base_cannot_lower_ceiling(self, server):
        _, ceiling = server._timeout_bounds({"shell_timeout_secs": 10})
        assert ceiling == server.max_timeout

    @pytest.mark.parametrize(
        "scope",
        [{}, {"shell_timeout_secs": None}, {"shell_timeout_secs": 0},
         {"shell_timeout_secs": -1}, {"shell_timeout_secs": "abc"},
         "not-a-dict"],
    )
    def test_malformed_grants_fall_back_to_base(self, server, scope):
        default, ceiling = server._timeout_bounds(scope)
        assert default == server.command_timeout
        assert ceiling == server.max_timeout

    def test_string_grant_is_coerced(self, server):
        # The envelope crosses JSON-RPC, so a numeric string is a
        # realistic shape rather than a hypothetical one.
        default, ceiling = server._timeout_bounds(
            {"shell_timeout_secs": "1200"}
        )
        assert default == 1200
        assert ceiling == 1200
