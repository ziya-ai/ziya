"""
ASR VAL-01 — a ``tool_input`` wrapper must not skip schema validation.

Models sometimes wrap their arguments as ``{"tool_input": {...}}``. The client
unwraps that, then *skipped* validation with the rationale that the inner
payload would not match the outer wrapper schema. But the unwrap has already
replaced ``arguments`` with the inner payload by that point, so the tool schema
is exactly the right thing to check it against.

The effect was that a model could evade every declared parameter constraint by
wrapping its arguments. Correctly bounded by the reviewer: the shell floor and
write policy still enforce, so this evades *schema* constraints, not the
escalation gate.
"""

import pytest

from app.mcp.client import MCPClient, MCPTool

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "count": {"type": "integer"},
        "recursive": {"type": "boolean"},
    },
    "required": ["path"],
}


@pytest.fixture
def client():
    c = MCPClient({"name": "local-test", "command": ["true"]})
    c.tools = [MCPTool(name="demo_tool", description="d", inputSchema=SCHEMA)]
    c.is_connected = True
    return c


@pytest.fixture
def dispatched(client, monkeypatch):
    """Capture what actually reaches the wire."""
    seen = []

    async def _send(method, params):
        seen.append((method, params))
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(client, "_send_request", _send)
    return seen


class TestWrappedArgumentsAreValidated:
    async def test_missing_required_field_rejected_when_wrapped(
        self, client, dispatched
    ):
        result = await client.call_tool("demo_tool", {"tool_input": {}})
        assert result["error"] is True
        assert result["code"] == -32602

    async def test_invalid_wrapped_payload_never_dispatched(
        self, client, dispatched
    ):
        """The impact assertion: before the fix this reached the server with
        arguments the tool had declared invalid."""
        await client.call_tool("demo_tool", {"tool_input": {}})
        assert dispatched == []

    async def test_wrapped_and_unwrapped_are_validated_identically(
        self, client, dispatched
    ):
        """Parity is the property that matters -- an attacker only needs one
        shape to be laxer than the other."""
        wrapped = await client.call_tool("demo_tool", {"tool_input": {}})
        unwrapped = await client.call_tool("demo_tool", {})
        assert wrapped["code"] == unwrapped["code"] == -32602
        assert dispatched == []

    async def test_json_string_wrapper_also_validated(self, client, dispatched):
        """``tool_input`` sometimes arrives as a JSON *string*; it is parsed and
        must then be validated like any other payload."""
        result = await client.call_tool("demo_tool", {"tool_input": "{}"})
        assert result["error"] is True
        assert result["code"] == -32602
        assert dispatched == []

    async def test_malformed_json_string_wrapper_rejected(self, client, dispatched):
        result = await client.call_tool("demo_tool", {"tool_input": "{not json"})
        assert result["error"] is True
        assert result["code"] == -32602
        assert dispatched == []


class TestValidWrappedCallsStillWork:
    """Positive controls. Validating the wrapper's contents is only safe if
    legitimate wrapped calls -- the reason the unwrap exists -- keep working."""

    async def test_valid_wrapped_payload_dispatched(self, client, dispatched):
        result = await client.call_tool(
            "demo_tool", {"tool_input": {"path": "src/main.py"}}
        )
        assert not result.get("error")
        assert len(dispatched) == 1

    async def test_wrapper_key_stripped_before_dispatch(self, client, dispatched):
        """The server must receive the inner payload, not the envelope."""
        await client.call_tool("demo_tool", {"tool_input": {"path": "x"}})
        _, params = dispatched[0]
        assert params["name"] == "demo_tool"
        assert "tool_input" not in params["arguments"]
        assert params["arguments"]["path"] == "x"

    async def test_type_coercion_still_applies_through_the_wrapper(
        self, client, dispatched
    ):
        """Validation also *converts* -- a model sending "5" for an integer
        field is normalized rather than refused. Skipping validation skipped
        the coercion too, so this is a correctness win as well as a security
        one."""
        await client.call_tool(
            "demo_tool", {"tool_input": {"path": "x", "count": "5"}}
        )
        _, params = dispatched[0]
        assert params["arguments"]["count"] == 5

    async def test_unwrapped_valid_call_unaffected(self, client, dispatched):
        result = await client.call_tool("demo_tool", {"path": "x"})
        assert not result.get("error")
        assert len(dispatched) == 1


class TestRoutingKeysUnaffected:
    """Side-channel keys ride along on tool arguments and are not in any
    schema; the stricter validation must not start rejecting them."""

    async def test_conversation_id_alongside_wrapper_ok(self, client, dispatched):
        result = await client.call_tool(
            "demo_tool",
            {"tool_input": {"path": "x"}, "conversation_id": "conv-1"},
        )
        assert not result.get("error")
        assert len(dispatched) == 1

    async def test_task_scope_passes_through(self, client, dispatched):
        result = await client.call_tool(
            "demo_tool", {"path": "x", "_task_scope": {"commands": ["ls"]}}
        )
        assert not result.get("error")


class TestUnknownToolStillDispatches:
    """No schema means nothing to validate against -- the client must not start
    refusing tools it has no local definition for."""

    async def test_tool_without_schema_not_blocked(self, client, dispatched):
        result = await client.call_tool("not_a_known_tool", {"anything": 1})
        assert not result.get("error")
        assert len(dispatched) == 1
