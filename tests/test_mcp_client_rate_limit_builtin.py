"""The MCP client's per-tool rate limit must not tax the builtin shell.

``_default_rate_limit`` was 2.0 s for every server and nothing ever
configured it, so the second shell call within 2 s of the first slept for
the remainder -- measured as a ~2 s "tool duration" on ~50 ms greps.  The
shell server is ours and already concurrent; external servers keep the
courtesy default unless their config says otherwise.
"""

from app.mcp.client import MCPClient


def _client(**cfg):
    base = {"name": "t", "command": "true", "args": []}
    base.update(cfg)
    return MCPClient(base)


def test_builtin_server_has_no_rate_limit():
    c = _client(builtin=True)
    assert c._default_rate_limit == 0.0
    c._tool_call_timestamps["run_shell_command"] = 1e12  # "just called"
    assert c._check_rate_limit("run_shell_command") is None


def test_external_server_keeps_the_courtesy_default():
    assert _client()._default_rate_limit == 2.0


def test_config_can_override_either_way():
    assert _client(builtin=True, rate_limit_seconds=1.5)._default_rate_limit == 1.5
    assert _client(rate_limit_seconds=0)._default_rate_limit == 0.0
