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
