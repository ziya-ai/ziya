#!/usr/bin/env python3
"""
Token counting calibration tests.

Compares our internal token estimates (tiktoken cl100k_base) against the
actual token counts returned by provider APIs (Anthropic count_tokens,
Bedrock count_tokens) across different content types.

Key findings (2025-07 calibration):
  - Bedrock and Anthropic direct use the SAME tokenizer (identical counts)
  - Bedrock succeeds with large requests only because it auto-upgrades
    to 1M extended context, not because it counts differently
  - tiktoken cl100k_base consistently underestimates:
      * Line-numbered code: ~19% under
      * Tool definitions:   ~8% under
      * Chat with tool_use/tool_result blocks: ~66% under
      * Full realistic request: ~16% under

Usage:
    # Requires ANTHROPIC_API_KEY and/or AWS credentials
    python -m pytest tests/backend_system_tests/token/test_tool_token_overhead.py -v

    # Run the calibration report (prints comparison table)
    python tests/backend_system_tests/token/test_tool_token_overhead.py --report
"""

import json
import os
import sys
import pytest
import tiktoken

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

enc = tiktoken.get_encoding("cl100k_base")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tiktoken_count(text: str) -> int:
    return len(enc.encode(text))


def _tiktoken_request(system, messages, tools=None):
    """Estimate tokens the way the frontend does (tiktoken on text content only)."""
    total = 0
    if system:
        if isinstance(system, str):
            total += _tiktoken_count(system)
        elif isinstance(system, list):
            for b in system:
                total += _tiktoken_count(b.get("text", ""))
    for msg in messages:
        c = msg["content"]
        if isinstance(c, str):
            total += _tiktoken_count(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict):
                    total += _tiktoken_count(block.get("text", "") or block.get("content", ""))
                    if "input" in block:
                        total += _tiktoken_count(json.dumps(block["input"]))
    if tools:
        for t in tools:
            total += _tiktoken_count(t.get("name", ""))
            total += _tiktoken_count(t.get("description", ""))
            total += _tiktoken_count(json.dumps(t.get("input_schema", {})))
    return total


def _our_estimate(tools):
    """Call the same function the frontend uses for MCP tool tokens."""
    from app.routes.mcp_routes import count_server_tool_tokens
    tools_dict = [
        {"name": t["name"], "description": t.get("description", ""),
         "inputSchema": t.get("input_schema", {})}
        for t in tools
    ]
    return count_server_tool_tokens(tools_dict)


def _anthropic_count(system, messages, tools=None):
    import anthropic
    client = anthropic.Anthropic()
    kwargs = {"model": "claude-sonnet-4-20250514", "messages": messages}
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    return client.messages.count_tokens(**kwargs).input_tokens


def _bedrock_count(system, messages, tools=None):
    import boto3
    session = boto3.Session(profile_name="ziya")
    client = session.client("bedrock-runtime", region_name="us-east-1")
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 100,
        "messages": messages,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools
        body["tool_choice"] = {"type": "auto"}
    # Bedrock count_tokens requires bare model ID (not us. prefix)
    resp = client.count_tokens(
        modelId="anthropic.claude-sonnet-4-20250514-v1:0",
        input={"invokeModel": {"body": json.dumps(body).encode("utf-8")}},
    )
    return resp["inputTokens"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_tools(count, desc_length="long"):
    """Generate synthetic tools with realistic schema structure."""
    descs = {
        "short": "A tool that does something useful.",
        "medium": "Search internal resources for information. " * 15,
        "long": "Search internal websites for information. " * 50,
    }
    tools = []
    for i in range(count):
        tools.append({
            "name": f"mcp_tool_{i}",
            "description": descs.get(desc_length, descs["long"]),
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool_input": {
                        "anyOf": [{"type": "string"},
                                  {"additionalProperties": True, "type": "object"}],
                        "title": "Tool Input",
                    },
                    "conversation_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Conversation Id",
                    },
                },
                "required": ["tool_input"],
                "title": f"mcp_tool_{i}",
                "type": "object",
            },
        })
    return tools


def _make_code_system(lines=2000):
    """Generate a system prompt with line-numbered code (Ziya format)."""
    code = ""
    for i in range(lines):
        code += f"[{i + 1:03d} ] def function_{i}(self, data, options=None):\n"
        code += f"[{i + 1:03d} ]     return data + {i}\n"
    return "You are an excellent coder.\n\n" + code


def _make_chat_with_tools(rounds=10):
    """Generate chat history with tool_use / tool_result blocks."""
    msgs = []
    for i in range(rounds):
        msgs.append({"role": "user", "content": f"Check function_{i} in the code."})
        msgs.append({"role": "assistant", "content": [
            {"type": "text", "text": f"Let me examine function_{i}. Checking patterns." * 5},
            {"type": "tool_use", "id": f"toolu_{i}a", "name": "mcp_run_shell_command",
             "input": {"command": f"grep -n def_function_{i} app/mod.py"}},
        ]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{i}a",
             "content": f"42:def function_{i}(self):\n" * 20},
        ]})
    msgs.append({"role": "user", "content": "What else?"})
    return msgs


# ---------------------------------------------------------------------------
# Tests: Bedrock == Anthropic (provider parity)
# ---------------------------------------------------------------------------

class TestProviderParity:
    """Verify Bedrock and Anthropic direct count tokens identically."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_creds(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")

    def test_baseline_parity(self):
        msgs = [{"role": "user", "content": "Hello"}]
        br = _bedrock_count("You are helpful.", msgs)
        an = _anthropic_count("You are helpful.", msgs)
        assert br == an, f"Bedrock={br} != Anthropic={an}"

    def test_code_parity(self):
        sys_text = _make_code_system(500)
        msgs = [{"role": "user", "content": "Hi"}]
        br = _bedrock_count(sys_text, msgs)
        an = _anthropic_count(sys_text, msgs)
        assert br == an, f"Bedrock={br} != Anthropic={an}"

    def test_tools_parity(self):
        tools = _make_tools(30)
        msgs = [{"role": "user", "content": "Hi"}]
        br = _bedrock_count("Short.", msgs, tools)
        an = _anthropic_count("Short.", msgs, tools)
        assert br == an, f"Bedrock={br} != Anthropic={an}"

    def test_chat_with_tools_parity(self):
        chat = _make_chat_with_tools(5)
        br = _bedrock_count("Short.", chat)
        an = _anthropic_count("Short.", chat)
        assert br == an, f"Bedrock={br} != Anthropic={an}"

    def test_full_request_parity(self):
        sys_text = _make_code_system(1000)
        tools = _make_tools(30)
        chat = _make_chat_with_tools(5)
        br = _bedrock_count(sys_text, chat, tools)
        an = _anthropic_count(sys_text, chat, tools)
        assert br == an, f"Bedrock={br} != Anthropic={an}"


# ---------------------------------------------------------------------------
# Tests: tiktoken accuracy bounds
# ---------------------------------------------------------------------------

class TestTiktokenAccuracy:
    """Verify tiktoken estimates are within known bounds of API actuals."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_creds(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")

    def test_code_ratio(self):
        """Line-numbered code: tiktoken underestimates by ~19%."""
        sys_text = _make_code_system(2000)
        msgs = [{"role": "user", "content": "Hi"}]
        tik = _tiktoken_request(sys_text, msgs)
        actual = _anthropic_count(sys_text, msgs)
        ratio = actual / tik
        assert 1.10 <= ratio <= 1.30, (
            f"Code ratio {ratio:.3f} outside [1.10, 1.30]. "
            f"tiktoken={tik:,} actual={actual:,}"
        )

    def test_tools_ratio(self):
        """Tool definitions: tiktoken underestimates by ~8%."""
        tools = _make_tools(69)
        msgs = [{"role": "user", "content": "Hi"}]
        tik = _tiktoken_request("Short.", msgs, tools)
        actual = _anthropic_count("Short.", msgs, tools)
        ratio = actual / tik
        assert 1.00 <= ratio <= 1.20, (
            f"Tools ratio {ratio:.3f} outside [1.00, 1.20]. "
            f"tiktoken={tik:,} actual={actual:,}"
        )

    def test_chat_structural_overhead(self):
        """Chat with tool_use/result: tiktoken underestimates by ~48-66%."""
        chat = _make_chat_with_tools(10)
        tik = _tiktoken_request("Short.", chat)
        actual = _anthropic_count("Short.", chat)
        ratio = actual / tik
        assert 1.30 <= ratio <= 2.00, (
            f"Chat ratio {ratio:.3f} outside [1.30, 2.00]. "
            f"tiktoken={tik:,} actual={actual:,}"
        )

    def test_full_request_ratio(self):
        """Full request: tiktoken underestimates by ~16%."""
        sys_text = _make_code_system(2000)
        tools = _make_tools(69)
        chat = _make_chat_with_tools(10)
        tik = _tiktoken_request(sys_text, chat, tools)
        actual = _anthropic_count(sys_text, chat, tools)
        ratio = actual / tik
        assert 1.05 <= ratio <= 1.35, (
            f"Full ratio {ratio:.3f} outside [1.05, 1.35]. "
            f"tiktoken={tik:,} actual={actual:,}"
        )


# ---------------------------------------------------------------------------
# Tests: our server-side estimate (count_server_tool_tokens)
# ---------------------------------------------------------------------------

class TestOurToolEstimate:
    """Verify count_server_tool_tokens is within ±15% of API actual."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_creds(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")

    def _tools_only_actual(self, tools):
        """Get API token count for tools only (subtract baseline)."""
        msgs = [{"role": "user", "content": "Hi"}]
        baseline = _anthropic_count("Short.", msgs)
        with_tools = _anthropic_count("Short.", msgs, tools)
        return with_tools - baseline

    def test_69_tools_estimate(self):
        tools = _make_tools(69)
        our = _our_estimate(tools)
        actual = self._tools_only_actual(tools)
        error_pct = abs(our - actual) / actual * 100
        assert error_pct < 15, (
            f"Tool estimate off by {error_pct:.1f}%: "
            f"ours={our:,} actual={actual:,}"
        )

    def test_10_tools_estimate(self):
        tools = _make_tools(10)
        our = _our_estimate(tools)
        actual = self._tools_only_actual(tools)
        error_pct = abs(our - actual) / actual * 100
        assert error_pct < 20, (
            f"Tool estimate off by {error_pct:.1f}%: "
            f"ours={our:,} actual={actual:,}"
        )


# ---------------------------------------------------------------------------
# CLI: calibration report
# ---------------------------------------------------------------------------

def _run_report():
    """Print a calibration comparison table."""
    print("=" * 95)
    print("TOKEN COUNTING CALIBRATION REPORT")
    print("=" * 95)

    msgs_simple = [{"role": "user", "content": "Hello"}]
    chat = _make_chat_with_tools(10)
    code_sys = _make_code_system(2000)

    tests = [
        ("Baseline", "You are helpful.", msgs_simple, None),
        ("Code (4k lines)", code_sys, msgs_simple, None),
        ("Tools (69, long)", "Short.", msgs_simple, _make_tools(69)),
        ("Tools (69, short)", "Short.", msgs_simple, _make_tools(69, "short")),
        ("Chat (10 tool rounds)", "Short.", chat, None),
        ("Full request", code_sys, chat, _make_tools(69)),
    ]

    print(f"\n{'Test':<25} {'tiktoken':>10} {'Bedrock':>10} {'Anthropic':>10} {'Ratio':>8} {'Match':>6}")
    print("-" * 75)

    for name, system, messages, tools in tests:
        tik = _tiktoken_request(system, messages, tools)
        try:
            br = _bedrock_count(system, messages, tools)
        except Exception as e:
            br = None
        try:
            an = _anthropic_count(system, messages, tools)
        except Exception as e:
            an = None

        actual = an or br
        ratio = f"{actual / tik:.3f}x" if actual and tik else "—"
        match = "✓" if (br is not None and an is not None and br == an) else "—"
        br_str = f"{br:>10,}" if br else f"{'—':>10}"
        an_str = f"{an:>10,}" if an else f"{'—':>10}"

        print(f"{name:<25} {tik:>10,} {br_str} {an_str} {ratio:>8} {match:>6}")

    print("\n" + "=" * 95)
    print("INTERPRETATION:")
    print("  Ratio = API actual / tiktoken estimate")
    print("  Match = Bedrock count == Anthropic count (same tokenizer)")
    print("  Bedrock succeeds with large requests only because it auto-upgrades")
    print("  to 1M extended context, NOT because it counts differently.")
    print("=" * 95)


if __name__ == "__main__":
    if "--report" in sys.argv:
        _run_report()
    else:
        pytest.main([__file__, "-v"] + sys.argv[1:])
