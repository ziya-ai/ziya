#!/usr/bin/env python3
"""
Provider Token Accuracy Tests

Calls the actual count_tokens APIs for each provider and compares against
our internal estimates.  Failures here mean the frontend token display is
lying to the user.

Run:
    python -m pytest tests/backend_system_tests/token/test_provider_token_accuracy.py -v -s
    # or directly:
    python tests/backend_system_tests/token/test_provider_token_accuracy.py

Environment:
    ANTHROPIC_API_KEY  — required for Anthropic direct tests
    AWS_PROFILE        — required for Bedrock tests (default: ziya)
    AWS_REGION         — Bedrock region (default: us-east-1)
"""

import json
import os
import sys
import unittest
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sample_tools(count: int) -> List[Dict[str, Any]]:
    """Generate realistic tool definitions for testing."""
    tools = []
    tools.append({
        "name": "run_shell_command",
        "description": (
            "Execute a shell command in the user's workspace. "
            "The command runs in a sandboxed environment with configurable "
            "timeouts and safety restrictions. Only allowed commands can be "
            "executed. Git operations are supported for safe read-only commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in seconds (default: 30, max: 300)"},
            },
            "required": ["command"],
        },
    })

    short_desc = "Performs a quick lookup operation."
    medium_desc = (
        "Searches across the workspace for files, symbols, and content "
        "matching the query. Supports regex patterns, file type filters, "
        "and path exclusions. Results include file path, line number, and "
        "surrounding context. Maximum 50 results returned per query."
    )
    long_desc = (
        "A comprehensive tool for managing and analyzing build pipelines. "
        "Supports the following operations:\n"
        "- get_status: Returns current build status\n"
        "- get_history: Returns build history for the last N days\n"
        "- analyze_failures: Analyzes recent failures and suggests fixes\n"
        "- get_dependencies: Returns dependency graph for the package\n"
        "- check_compatibility: Checks version compatibility across deps\n\n"
        "The tool connects to the internal build system and requires valid "
        "authentication. Results are cached for 5 minutes to reduce load."
    )

    descriptions = [short_desc, medium_desc, long_desc]

    for i in range(1, count):
        desc_idx = i % len(descriptions)
        n_props = 1 + (i % 5)
        properties = {}
        required = []
        for p in range(n_props):
            pname = f"param_{p}" if p > 0 else "query"
            properties[pname] = {"type": "string", "description": f"Parameter {p} for tool_{i}"}
            if p == 0:
                required.append(pname)

        tools.append({
            "name": f"mcp_tool_{i}",
            "description": descriptions[desc_idx],
            "input_schema": {"type": "object", "properties": properties, "required": required},
        })
    return tools


def _make_system_prompt(size: str = "medium") -> str:
    base = "You are an expert coding assistant. Help the user with their tasks.\n\n"
    if size == "small":
        return base
    elif size == "medium":
        return base + ("Follow best practices. " * 200) + "\n"
    elif size == "large":
        fake_file = "def example():\n    pass\n" * 100
        return base + f"\nFile: example.py\n{fake_file}\n" * 10
    return base


def _make_messages(turns: int = 1) -> List[Dict[str, Any]]:
    messages = []
    for i in range(turns):
        messages.append({"role": "user", "content": f"Help with task {i + 1}."})
        if i < turns - 1:
            messages.append({"role": "assistant", "content": f"Working on task {i + 1}."})
    return messages


# ---------------------------------------------------------------------------
# count_tokens wrappers
# ---------------------------------------------------------------------------

def _anthropic_count_tokens(model_id, messages, system=None, tools=None):
    import anthropic
    client = anthropic.Anthropic()
    kwargs = {"model": model_id, "messages": messages}
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    return client.messages.count_tokens(**kwargs).input_tokens


def _bedrock_count_tokens(model_id, messages, system=None, tools=None,
                          profile="ziya", region="us-east-1"):
    import boto3
    session = boto3.Session(profile_name=profile, region_name=region)
    client = session.client("bedrock-runtime", region_name=region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 100,
        "messages": messages,
    }
    if system:
        body["system"] = [{"type": "text", "text": system}]
    if tools:
        body["tools"] = tools
    resp = client.count_tokens(
        modelId=model_id,
        input={"invokeModel": {"body": json.dumps(body).encode()}},
    )
    return resp["totalTokens"]["inputTokens"]


def _our_raw_estimate(messages, system=None, tools=None):
    """Raw tiktoken estimate (no multiplier) — what the frontend currently does."""
    from app.agents.agent import estimate_token_count

    total = 0
    if system:
        total += estimate_token_count(system)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_token_count(content)
    if tools:
        for t in tools:
            total += estimate_token_count(t.get("name", ""))
            total += estimate_token_count(t.get("description", ""))
            schema = t.get("input_schema", {})
            total += estimate_token_count(json.dumps(schema, separators=(",", ":")))
    return total


def _our_estimate_with_multiplier(messages, system=None, tools=None, multiplier=2.8):
    """Estimate with tool overhead multiplier applied."""
    from app.agents.agent import estimate_token_count

    total = 0
    if system:
        total += estimate_token_count(system)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_token_count(content)
    if tools:
        raw_tools = 0
        for t in tools:
            raw_tools += estimate_token_count(t.get("name", ""))
            raw_tools += estimate_token_count(t.get("description", ""))
            schema = t.get("input_schema", {})
            raw_tools += estimate_token_count(json.dumps(schema, separators=(",", ":")))
        total += int(raw_tools * multiplier)
    return total


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    {"name": "no_tools_short_prompt", "tools": 0, "system": "small", "turns": 1},
    {"name": "no_tools_medium_prompt", "tools": 0, "system": "medium", "turns": 1},
    {"name": "5_tools_medium_prompt", "tools": 5, "system": "medium", "turns": 1},
    {"name": "20_tools_medium_prompt", "tools": 20, "system": "medium", "turns": 1},
    {"name": "50_tools_medium_prompt", "tools": 50, "system": "medium", "turns": 1},
    {"name": "69_tools_medium_prompt", "tools": 69, "system": "medium", "turns": 1},
    {"name": "69_tools_large_prompt", "tools": 69, "system": "large", "turns": 1},
    {"name": "69_tools_large_5_turns", "tools": 69, "system": "large", "turns": 5},
    {"name": "tools_only_no_system", "tools": 69, "system": None, "turns": 1},
]

# Maximum acceptable error percentage for the multiplier-adjusted estimate.
# The test fails if any scenario exceeds this, indicating the multiplier
# needs recalibration.
MAX_ERROR_PCT = 30


# ---------------------------------------------------------------------------
# Anthropic Direct tests
# ---------------------------------------------------------------------------

class TestAnthropicTokenAccuracy(unittest.TestCase):
    """Compare our estimates against Anthropic's count_tokens API."""

    @classmethod
    def setUpClass(cls):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise unittest.SkipTest("ANTHROPIC_API_KEY not set")
        # Use a known Anthropic direct API model ID, not the Bedrock alias
        # (ZIYA_MODEL may be "opus4.6" which the direct API doesn't recognize)
        cls.model_id = os.environ.get("ANTHROPIC_TEST_MODEL", "claude-sonnet-4-6")

    def test_scenarios(self):
        print("\n" + "=" * 90)
        print("ANTHROPIC DIRECT — Token Accuracy Report")
        print("=" * 90)
        hdr = (f"{'Scenario':<35} {'Raw':>8} {'×2.8':>8} "
               f"{'Actual':>8} {'RawErr':>7} {'AdjErr':>7}")
        print(hdr)
        print("-" * 90)

        worst_adj_err = 0

        for scenario in SCENARIOS:
            tools = _make_sample_tools(scenario["tools"]) if scenario["tools"] else None
            system = _make_system_prompt(scenario["system"]) if scenario["system"] else None
            messages = _make_messages(scenario["turns"])

            raw = _our_raw_estimate(messages, system, tools)
            adjusted = _our_estimate_with_multiplier(messages, system, tools)
            try:
                actual = _anthropic_count_tokens(self.model_id, messages, system, tools)
            except Exception as e:
                print(f"  {scenario['name']:<35} SKIPPED: {e}")
                continue

            raw_err = ((raw - actual) / actual * 100) if actual else 0
            adj_err = ((adjusted - actual) / actual * 100) if actual else 0
            worst_adj_err = max(worst_adj_err, abs(adj_err))

            status = "✅" if abs(adj_err) < 15 else "⚠️" if abs(adj_err) < MAX_ERROR_PCT else "❌"
            print(
                f"{status} {scenario['name']:<33} {raw:>8,} {adjusted:>8,} "
                f"{actual:>8,} {raw_err:>+6.1f}% {adj_err:>+6.1f}%"
            )

        print("-" * 90)
        print(f"Worst adjusted error: {worst_adj_err:.1f}%  (threshold: {MAX_ERROR_PCT}%)")
        print("=" * 90)

        self.assertLess(
            worst_adj_err, MAX_ERROR_PCT,
            f"Adjusted estimate is off by {worst_adj_err:.1f}% — "
            f"TOOL_OVERHEAD_MULTIPLIER in mcp_routes.py needs recalibration",
        )

    def test_tool_overhead_calibration(self):
        """Measure the actual per-tool overhead for calibration."""
        print("\n" + "=" * 70)
        print("TOOL OVERHEAD CALIBRATION")
        print("=" * 70)

        msgs = [{"role": "user", "content": "Hello"}]
        baseline = _anthropic_count_tokens(self.model_id, msgs)

        for n in [1, 5, 10, 20, 50, 69]:
            tools = _make_sample_tools(n)
            actual = _anthropic_count_tokens(self.model_id, msgs, tools=tools)
            tool_cost = actual - baseline
            raw = _our_raw_estimate(msgs, tools=tools)
            # Subtract baseline raw (just the "Hello" message)
            raw_tools_only = raw - _our_raw_estimate(msgs)
            mult = tool_cost / raw_tools_only if raw_tools_only else 0
            per_tool = tool_cost / n if n else 0

            print(
                f"  {n:>3} tools: raw_tools={raw_tools_only:>6,}  "
                f"actual_overhead={tool_cost:>6,}  "
                f"multiplier={mult:.2f}x  per_tool={per_tool:.0f}"
            )

        print("=" * 70)


# ---------------------------------------------------------------------------
# Bedrock tests
# ---------------------------------------------------------------------------

class TestBedrockTokenAccuracy(unittest.TestCase):
    """Compare our estimates against Bedrock's count_tokens API."""

    @classmethod
    def setUpClass(cls):
        cls.profile = os.environ.get("AWS_PROFILE", "ziya")
        cls.region = os.environ.get("AWS_REGION", "us-east-1")
        cls.model_id = "us.anthropic.claude-sonnet-4-6"
        try:
            import boto3
            session = boto3.Session(profile_name=cls.profile, region_name=cls.region)
            session.client("sts").get_caller_identity()
        except Exception as e:
            raise unittest.SkipTest(f"Bedrock not available: {e}")

    def test_scenarios(self):
        print("\n" + "=" * 90)
        print("BEDROCK — Token Accuracy Report")
        print("=" * 90)
        hdr = (f"{'Scenario':<35} {'Raw':>8} {'×2.8':>8} "
               f"{'Actual':>8} {'RawErr':>7} {'AdjErr':>7}")
        print(hdr)
        print("-" * 90)

        for scenario in SCENARIOS:
            tools = _make_sample_tools(scenario["tools"]) if scenario["tools"] else None
            system = _make_system_prompt(scenario["system"]) if scenario["system"] else None
            messages = _make_messages(scenario["turns"])

            raw = _our_raw_estimate(messages, system, tools)
            adjusted = _our_estimate_with_multiplier(messages, system, tools)
            try:
                actual = _bedrock_count_tokens(
                    self.model_id, messages, system, tools, self.profile, self.region
                )
            except Exception as e:
                print(f"  {scenario['name']:<35} SKIPPED: {e}")
                continue

            raw_err = ((raw - actual) / actual * 100) if actual else 0
            adj_err = ((adjusted - actual) / actual * 100) if actual else 0

            status = "✅" if abs(adj_err) < 15 else "⚠️" if abs(adj_err) < MAX_ERROR_PCT else "❌"
            print(
                f"{status} {scenario['name']:<33} {raw:>8,} {adjusted:>8,} "
                f"{actual:>8,} {raw_err:>+6.1f}% {adj_err:>+6.1f}%"
            )

        print("=" * 90)


# ---------------------------------------------------------------------------
# Cross-provider parity
# ---------------------------------------------------------------------------

class TestProviderParity(unittest.TestCase):
    """Verify Bedrock and Anthropic direct agree on token counts."""

    @classmethod
    def setUpClass(cls):
        cls.anthropic_model = os.environ.get("ANTHROPIC_TEST_MODEL", "claude-sonnet-4-6")
        cls.bedrock_model = "us.anthropic.claude-sonnet-4-6"
        cls.profile = os.environ.get("AWS_PROFILE", "ziya")
        cls.region = os.environ.get("AWS_REGION", "us-east-1")

        has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_bedrock = False
        try:
            import boto3
            session = boto3.Session(profile_name=cls.profile, region_name=cls.region)
            session.client("sts").get_caller_identity()
            has_bedrock = True
        except Exception:
            pass
        if not (has_anthropic and has_bedrock):
            raise unittest.SkipTest("Need both ANTHROPIC_API_KEY and AWS credentials")

    def test_parity(self):
        print("\n" + "=" * 70)
        print("PROVIDER PARITY — Bedrock vs Anthropic Direct")
        print("=" * 70)
        print(f"{'Scenario':<35} {'Bedrock':>8} {'Anthro':>8} {'Diff':>8} {'Pct':>7}")
        print("-" * 70)

        for scenario in SCENARIOS:
            tools = _make_sample_tools(scenario["tools"]) if scenario["tools"] else None
            system = _make_system_prompt(scenario["system"]) if scenario["system"] else None
            messages = _make_messages(scenario["turns"])

            try:
                b = _bedrock_count_tokens(
                    self.bedrock_model, messages, system, tools, self.profile, self.region
                )
                a = _anthropic_count_tokens(self.anthropic_model, messages, system, tools)
            except Exception as e:
                print(f"  {scenario['name']:<35} SKIPPED: {e}")
                continue

            diff = a - b
            pct = (diff / b * 100) if b else 0
            status = "✅" if abs(pct) < 5 else "⚠️" if abs(pct) < 15 else "❌"
            print(f"{status} {scenario['name']:<33} {b:>8,} {a:>8,} {diff:>+8,} {pct:>+6.1f}%")

            if b > 100:
                self.assertLess(
                    abs(pct), 5,
                    f"Parity broken for '{scenario['name']}': {pct:+.1f}%",
                )

        print("=" * 70)


if __name__ == "__main__":
    unittest.main(verbosity=2)
