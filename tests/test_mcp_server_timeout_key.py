"""
The per-server ``timeout`` key in mcp_config.json must govern how long the
client waits for a tools/call response.

The key has always been accepted by config_validation._KNOWN_SERVER_KEYS and
is written by the manager for npx/uvx servers, but nothing read it: every
non-"external"-named server was cut off at 30 s. leolens-mcp streams from
AgentCore for 20-60 s, so real answers were discarded mid-arrival while the
server's own log showed query_completed. Fails against the pre-fix client
(the late reply is never awaited).
"""
import asyncio
import json

import pytest

from app.mcp import config_validation
from app.mcp.client import MCPClient


class _Pipe:
    """Minimal stdin: absorb writes, drain immediately."""

    def write(self, _data):
        pass

    async def drain(self):
        pass


class _LateStdout:
    """Answers the first request after ``delay`` seconds, then EOF."""

    def __init__(self, delay):
        self._delay = delay
        self._sent = False

    async def readline(self):
        if self._sent:
            await asyncio.sleep(3600)  # keep the reader parked; test ends first
        self._sent = True
        await asyncio.sleep(self._delay)
        return (json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) + "\n").encode()


class _Process:
    returncode = None

    def __init__(self, delay):
        self.stdin = _Pipe()
        self.stdout = _LateStdout(delay)


def _client(config, delay, monkeypatch):
    # Shrink the built-in 30 s default so the test is fast; the config key
    # must still win over it.
    c = MCPClient(dict(config))
    c.process = _Process(delay)
    c.is_connected = True
    monkeypatch.setattr(c, "_is_process_healthy", lambda: True)
    return c


def test_timeout_is_a_known_config_key():
    assert "timeout" in config_validation._KNOWN_SERVER_KEYS


@pytest.mark.asyncio
async def test_config_timeout_keeps_a_slow_tool_call_alive(monkeypatch):
    # Default ceiling for a plainly named server is 30 s; a reply at 31 s is
    # dropped without the key. Use asyncio's own clock-free approach: patch
    # wait_for's timeout by observing what the client asks for.
    asked = {}
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(fut, timeout):
        asked["timeout"] = timeout
        return await real_wait_for(fut, timeout)

    monkeypatch.setattr(asyncio, "wait_for", spy_wait_for)

    c = _client({"name": "leolens_mcp_server", "timeout": 180}, delay=0.01, monkeypatch=monkeypatch)
    result = await c._send_request("tools/call", {"name": "ask_leolens", "arguments": {"prompt": "x"}})

    assert result == {"ok": True}
    assert asked["timeout"] == 180.0, "config 'timeout' must be the ceiling actually awaited"


@pytest.mark.asyncio
async def test_without_key_the_default_ceiling_applies(monkeypatch):
    asked = {}
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(fut, timeout):
        asked["timeout"] = timeout
        return await real_wait_for(fut, timeout)

    monkeypatch.setattr(asyncio, "wait_for", spy_wait_for)

    c = _client({"name": "leolens_mcp_server"}, delay=0.01, monkeypatch=monkeypatch)
    await c._send_request("tools/call", {"name": "ask_leolens", "arguments": {"prompt": "x"}})
    assert asked["timeout"] == 30.0


@pytest.mark.asyncio
async def test_config_timeout_never_lowers_the_default(monkeypatch):
    asked = {}
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(fut, timeout):
        asked["timeout"] = timeout
        return await real_wait_for(fut, timeout)

    monkeypatch.setattr(asyncio, "wait_for", spy_wait_for)

    c = _client({"name": "fetch", "timeout": 5}, delay=0.01, monkeypatch=monkeypatch)
    await c._send_request("tools/call", {"name": "t", "arguments": {}})
    assert asked["timeout"] == 60.0, "external-server default (60 s) is a floor, not overridden downward"


@pytest.mark.asyncio
async def test_non_numeric_timeout_is_ignored_not_fatal(monkeypatch):
    c = _client({"name": "s", "timeout": "soon"}, delay=0.01, monkeypatch=monkeypatch)
    result = await c._send_request("tools/call", {"name": "t", "arguments": {}})
    assert result == {"ok": True}
