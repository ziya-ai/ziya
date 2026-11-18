"""
A server whose install directory was removed underneath it (an upgrade
pruning the old version) must say so, not retry and report throttling.

Live string (2026-10-28, the old version's venv pruned by an upgrade while
the server ran):

    SSL validation failed for https://bedrock-runtime.us-west-2.amazonaws.com/
    model/.../invoke-with-response-stream [Errno 2] No such file or directory

Before the fix this was retried 5x as READ_TIMEOUT and surfaced as a
'throttling_error'.  Positive control: a real TLS drop ("SSL validation
failed ... EOF occurred in violation of protocol") is still retried.
"""
import ast
import inspect
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import ErrorType
from app.utils import install_integrity as ii

LIVE = ("SSL validation failed for https://bedrock-runtime.us-west-2.amazonaws.com/"
        "model/global.anthropic.claude-fable-5-1/invoke-with-response-stream "
        "[Errno 2] No such file or directory")
TLS_DROP = ("SSL validation failed for https://bedrock-runtime.us-west-2.amazonaws.com/"
            "model/x/invoke-with-response-stream EOF occurred in violation of protocol")


# ── helper ────────────────────────────────────────────────────────────────

def test_detects_live_string_and_not_a_tls_drop():
    assert ii.is_missing_ca_file_error(LIVE) is True
    assert ii.is_missing_ca_file_error(TLS_DROP) is False
    assert ii.is_missing_ca_file_error("open: No such file or directory") is False
    assert ii.missing_install_message(TLS_DROP) is None


def test_install_dir_missing_reports_vanished_package_dir(tmp_path):
    fake = types.ModuleType("app")
    fake.__file__ = str(tmp_path / "gone" / "app" / "__init__.py")
    with patch.dict(sys.modules, {"app": fake}):
        assert ii.install_dir_missing() == str(tmp_path / "gone" / "app")
        msg = ii.missing_install_message(LIVE)
    assert "was removed while this server was running" in msg
    assert "Restart ziya" in msg


def test_install_dir_present_is_none():
    assert ii.install_dir_missing() is None   # this test run's own install


def test_missing_ca_bundle_env_is_named(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_CA_BUNDLE", str(tmp_path / "nope.pem"))
    assert ii.ca_bundle_missing() == str(tmp_path / "nope.pem")
    msg = ii.missing_install_message(LIVE)
    assert str(tmp_path / "nope.pem") in msg


# ── callers import it at module level (a lazy import would fail when the
#    install directory is gone, which is exactly when it is needed) ──────

@pytest.mark.parametrize("modname", ["app.streaming_tool_executor", "app.providers.bedrock"])
def test_install_integrity_imported_at_module_level(modname):
    import importlib
    tree = ast.parse(inspect.getsource(importlib.import_module(modname)))
    top = [n for n in tree.body if isinstance(n, ast.ImportFrom)
           and n.module == "app.utils.install_integrity"]
    assert top, f"{modname} must import app.utils.install_integrity at module level"


# ── bedrock provider classification ───────────────────────────────────────

def test_bedrock_live_string_is_not_retryable():
    from app.providers.bedrock import BedrockProvider
    assert BedrockProvider._classify_error(LIVE) == ErrorType.UNKNOWN


def test_bedrock_tls_drop_still_retryable():
    from app.providers.bedrock import BedrockProvider
    assert BedrockProvider._classify_error(TLS_DROP) == ErrorType.READ_TIMEOUT


# ── orchestrator classification (what the browser is shown) ─────────────

def _classify(err_str):
    from app.streaming_tool_executor import StreamingToolExecutor
    ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    throttle = {'retry_count': 0, 'max_retries': 5, 'base_delay': 2,
                'last_cache_efficiency': 0.0, 'cache_working': None,
                'output_tokens_reduction_factor': 1.0,
                'max_output_tokens_override': None,
                'model_output_retries': 0, 'max_model_output_retries': 1}
    delay = {'current': 0.1, 'min': 0.1, 'max': 3.0, 'decay_factor': 0.6,
             'growth_factor': 2.5, 'last_was_throttled': False}
    cfg = MagicMock(max_output_tokens=8192)
    with patch('app.plugins.get_active_auth_provider') as auth:
        auth.return_value = MagicMock(is_auth_error=MagicMock(return_value=False))
        return ex._classify_and_handle_error(
            Exception(err_str), err_str, 0, [], throttle, delay, [], cfg)


def test_orchestrator_does_not_retry_and_says_restart(tmp_path):
    # Patch the probe rather than sys.modules['app']: a fake 'app' module
    # breaks the executor's own lazy imports, which is not what is under test.
    gone = str(tmp_path / "gone" / "app")
    with patch.object(ii, "install_dir_missing", return_value=gone):
        res = _classify(LIVE)
    assert res['should_retry'] is False
    chunk = res['error_chunk']
    assert chunk['error_type'] == 'installation_changed'
    assert 'throttl' not in str(chunk).lower()
    assert "Restart ziya" in chunk['content']
    assert chunk['detail'] == LIVE
    assert 'toolbox' not in chunk['content'].lower()   # public build: no internal tool names


def test_orchestrator_tls_drop_still_retried():
    res = _classify(TLS_DROP)
    assert res['type'] == 'read_timeout' and res['should_retry'] is True


# ── stream ErrorEvent branch (reported 2026-11-11: users saw
#    "No module named 'app.utils.provider_error_chunks'" after an upgrade,
#    because that module was imported lazily in this branch) ─────────────

def test_provider_error_chunks_imported_at_module_level():
    import app.streaming_tool_executor as ste
    tree = ast.parse(inspect.getsource(ste))
    top = [n for n in tree.body if isinstance(n, ast.ImportFrom)
           and n.module == "app.utils.provider_error_chunks"]
    assert top, "streaming_tool_executor must import provider_error_chunks at module level"


async def _run_error_event(message):
    """Drive the real stream_with_tools loop; the provider yields one
    non-retryable ErrorEvent.  provider_error_chunks is made unloadable
    (sys.modules entry None) as a pruned install makes it."""
    import os
    from unittest.mock import AsyncMock
    from app.providers.base import ErrorEvent

    class _P:
        provider_name = "scripted"
        async def stream_response(self, *a, **k):
            yield ErrorEvent(message=message, error_type=ErrorType.UNKNOWN, retryable=False)
        def supports_feature(self, n): return False
        def prepare_cache_control(self, m, *a, **k): return m
        def build_assistant_message(self, text, tool_uses, *a, **k):
            return {"role": "assistant", "content": [{"type": "text", "text": text}]}
        def build_tool_result_message(self, r): return {"role": "user", "content": []}

    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.7"}):
        from app.streaming_tool_executor import StreamingToolExecutor
        ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex.model_id, ex.bedrock, ex.provider = "m", None, _P()
    ex.model_config = {"family": "claude", "max_output_tokens": 8192}
    ex._block_opening_buffer = ""
    ex._normalize_fence_spacing = lambda t, tr, **k: t
    ex._update_code_block_tracker = lambda t, tr: None
    opt = MagicMock()
    opt.add_content.side_effect = lambda t: [t] if t else []
    opt.flush_remaining.return_value = ""
    ex._content_optimizer = opt
    ex._build_conversation_from_messages = MagicMock(return_value=(
        [{"role": "user", "content": [{"type": "text", "text": "Go"}]}], "sys"))
    ex._format_tools_for_api = MagicMock(return_value=[])
    ex._build_provider_config = MagicMock(return_value=MagicMock(max_output_tokens=8192))
    ex._handle_usage_event = MagicMock()
    ex._load_and_prepare_tools = AsyncMock(return_value=([], [], set(), set(), set()))
    mgr = MagicMock(is_initialized=True)
    events = []
    with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
         patch.dict(sys.modules, {"app.utils.provider_error_chunks": None}):
        async for ev in ex.stream_with_tools(messages=[{"role": "user", "content": "Go"}], tools=[]):
            events.append(ev)
            if len(events) > 50:
                break
    return [e for e in events if e.get("type") == "error"]


@pytest.mark.asyncio
async def test_stream_error_with_pruned_install_says_restart(tmp_path):
    gone = str(tmp_path / "gone" / "app")
    with patch.object(ii, "install_dir_missing", return_value=gone):
        errors = await _run_error_event(LIVE)
    assert errors, "no error chunk was yielded"
    text = " ".join(str(e) for e in errors)
    assert "No module named" not in text and "halted" not in text
    assert errors[-1]["error_type"] == "installation_changed"
    assert "Restart ziya" in errors[-1]["content"]


@pytest.mark.asyncio
async def test_stream_error_ordinary_still_uses_provider_chunk():
    # Positive control: an unrelated error still goes through
    # build_provider_error_chunk (now bound at import time).
    errors = await _run_error_event("oMLX prefill memory guard rejected this prompt")
    assert errors and errors[-1].get("error_type") != "installation_changed"
    assert "No module named" not in str(errors) and "halted" not in str(errors)
    assert "memory guard" in str(errors[-1])
