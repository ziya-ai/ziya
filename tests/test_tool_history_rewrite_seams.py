"""
Seam tests for the tool-history rewrite: the module is only useful if
(a) build_messages_for_streaming actually applies it to assistant history
and (b) the providers place a sticky cross-turn cache breakpoint on the
resulting stable prefix.  These assert on the outermost surfaces —
the message list the model receives, and the cache_control stamps the
provider emits — not on the helper functions.
"""

import re
import sys
from unittest.mock import MagicMock, patch

import pytest

F4 = "`" * 4
FENCE_RE = re.compile(r"(?m)^`{3,}tool:")


def _fence(tool, header, syntax, body):
    return f"{F4}tool:{tool}|{header}|{syntax}\n{body}\n{F4}"


def _text_of(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


# ---------------------------------------------------------------------------
# Server hook
# ---------------------------------------------------------------------------

class TestServerHook:

    def test_build_messages_rewrites_assistant_tool_blocks(self):
        from app.server import build_messages_for_streaming
        body = "\n".join(f"l{i}" for i in range(80))
        blk = _fence("mcp_file_read", "🔐 file read: x.py", "python", body)
        hist = []
        for i in range(5):
            hist.append({"type": "human", "content": f"q{i}"})
            hist.append({"type": "ai", "content": f"a{i}\n\n{blk}\n\nend{i}"})
        msgs = build_messages_for_streaming("new question", hist, [], "conv-seam-1")
        assistant = [m for m in msgs if m.get("role") == "assistant"]
        assert len(assistant) == 5
        for m in assistant:
            txt = _text_of(m["content"])
            assert not FENCE_RE.search(txt), "rendered tool fence reached the model"
            assert 'tool="mcp_file_read"' in txt
            assert body in txt, "body must be preserved in full"

    def test_user_messages_untouched_by_hook(self):
        from app.server import build_messages_for_streaming
        pasted = _fence("mcp_file_read", "read", "text", "user pasted this")
        hist = [{"type": "human", "content": "look: " + pasted},
                {"type": "ai", "content": "ok"}]
        msgs = build_messages_for_streaming("q", hist, [], "conv-seam-2")
        users = [m for m in msgs if m.get("role") == "user"]
        assert any(pasted in _text_of(m["content"]) for m in users)


# ---------------------------------------------------------------------------
# Provider sticky breakpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def anthropic_provider():
    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = MagicMock()
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        yield AnthropicDirectProvider(
            model_id="claude-sonnet-4-20250514",
            model_config={"family": "claude"},
            api_key="sk-test-key",
        )


@pytest.fixture
def bedrock_provider():
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as g:
        g.return_value = MagicMock()
        from app.providers.bedrock import BedrockProvider
        yield BedrockProvider(
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            model_config={"family": "claude"},
            aws_profile="test",
            region="us-west-2",
        )


def _turns(n):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"q{i+1}"})
        if i < n - 1:
            out.append({"role": "assistant", "content": f"a{i+1}"})
    return out


def _stamped(messages):
    out = []
    for i, m in enumerate(messages):
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(b, dict) and "cache_control" in b for b in c):
            out.append(i)
    return out


PROVIDERS = ["anthropic_provider", "bedrock_provider"]


class TestProviderStickyBreakpoint:

    @pytest.mark.parametrize("prov", PROVIDERS)
    def test_iteration_zero_stamps_sticky_user_message(self, prov, request, monkeypatch):
        monkeypatch.setenv("ZIYA_STICKY_CACHE_EVERY_TURNS", "4")
        p = request.getfixturevalue(prov)
        msgs = _turns(6)                   # prior user turns 1..5; q4 at index 6
        out = p.prepare_cache_control(msgs, iteration=0)
        assert _stamped(out) == [6]
        assert out[6]["content"][0]["text"] == "q4"

    @pytest.mark.parametrize("prov", PROVIDERS)
    def test_sticky_position_unchanged_across_consecutive_turns(self, prov, request, monkeypatch):
        monkeypatch.setenv("ZIYA_STICKY_CACHE_EVERY_TURNS", "4")
        p = request.getfixturevalue(prov)
        picks = [_stamped(p.prepare_cache_control(_turns(n), iteration=0)) for n in (5, 6, 7, 8)]
        assert picks == [[6], [6], [6], [6]]
        assert _stamped(p.prepare_cache_control(_turns(9), iteration=0)) == [14]

    @pytest.mark.parametrize("prov", PROVIDERS)
    def test_too_few_turns_leaves_iteration_zero_unchanged(self, prov, request, monkeypatch):
        monkeypatch.setenv("ZIYA_STICKY_CACHE_EVERY_TURNS", "4")
        p = request.getfixturevalue(prov)
        msgs = _turns(3)
        assert p.prepare_cache_control(msgs, iteration=0) == msgs

    @pytest.mark.parametrize("prov,tail_from_end", [("anthropic_provider", 2), ("bedrock_provider", 4)])
    def test_tool_loop_has_sticky_plus_sliding_boundary(self, prov, tail_from_end, request, monkeypatch):
        monkeypatch.setenv("ZIYA_STICKY_CACHE_EVERY_TURNS", "4")
        p = request.getfixturevalue(prov)
        msgs = _turns(8)                   # 15 messages; sticky at q4 = index 6
        out = p.prepare_cache_control(msgs, iteration=2)
        assert _stamped(out) == [6, len(msgs) - tail_from_end]

    @pytest.mark.parametrize("prov", PROVIDERS)
    def test_sticky_skipped_when_it_would_duplicate_tail_boundary(self, prov, request, monkeypatch):
        # every=1 makes the sticky pick the last prior user message, which
        # sits at or after the sliding boundary; one stamp, not two.
        monkeypatch.setenv("ZIYA_STICKY_CACHE_EVERY_TURNS", "1")
        p = request.getfixturevalue(prov)
        msgs = _turns(8)
        out = p.prepare_cache_control(msgs, iteration=2)
        assert len(_stamped(out)) <= 2
        assert len(set(_stamped(out))) == len(_stamped(out))

    @pytest.mark.parametrize("prov", PROVIDERS)
    def test_kill_switch_disables_everything(self, prov, request, monkeypatch):
        monkeypatch.setenv("ZIYA_DISABLE_PROMPT_CACHE", "1")
        p = request.getfixturevalue(prov)
        msgs = _turns(9)
        assert p.prepare_cache_control(msgs, iteration=0) == msgs
        assert p.prepare_cache_control(msgs, iteration=3) == msgs

    @pytest.mark.parametrize("prov", PROVIDERS)
    def test_input_not_mutated(self, prov, request, monkeypatch):
        monkeypatch.setenv("ZIYA_STICKY_CACHE_EVERY_TURNS", "4")
        p = request.getfixturevalue(prov)
        msgs = _turns(6)
        p.prepare_cache_control(msgs, iteration=0)
        assert isinstance(msgs[6]["content"], str)
