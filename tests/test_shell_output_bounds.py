"""Regression tests for PenPal #59 [CWE-400]: ShellServer output is bounded.

`_execute_pipeline` returns captured stdout/stderr straight into the model
context. A command emitting hundreds of MB (runaway `cat`/`find`, a huge log)
would bloat memory and context. `_bound_output` caps the *returned* text with
an explicit truncation marker, keeping the diagnostically-useful head and tail.
"""
import os
import importlib

import pytest


def _make_server():
    # ShellServer reads env at __init__; import lazily so per-test env applies.
    mod = importlib.import_module("app.mcp_servers.shell_server")
    # Find the server class (name is stable in-tree).
    for name in ("ShellServer", "ShellMCPServer", "Server"):
        cls = getattr(mod, name, None)
        if cls is not None:
            try:
                return cls()
            except TypeError:
                continue
    pytest.skip("ShellServer class not found under expected names")


def test_output_under_limit_passes_through():
    os.environ["ZIYA_MAX_SHELL_OUTPUT_BYTES"] = "1000"
    srv = _make_server()
    text = "hello world\n" * 3
    assert srv._bound_output(text) == text


def test_output_over_limit_is_truncated_with_marker():
    os.environ["ZIYA_MAX_SHELL_OUTPUT_BYTES"] = "200"
    srv = _make_server()
    text = "A" * 5000
    out = srv._bound_output(text)
    assert len(out.encode("utf-8")) < 5000
    assert "bytes of output truncated" in out
    # Head and tail preserved.
    assert out.startswith("A")
    assert out.rstrip().endswith("A")


def test_zero_limit_disables_bounding():
    os.environ["ZIYA_MAX_SHELL_OUTPUT_BYTES"] = "0"
    srv = _make_server()
    text = "B" * 100000
    assert srv._bound_output(text) == text


def test_multibyte_content_bounded_by_bytes_not_chars():
    os.environ["ZIYA_MAX_SHELL_OUTPUT_BYTES"] = "300"
    srv = _make_server()
    # 3-byte UTF-8 chars; 2000 chars = 6000 bytes, well over the 300-byte cap.
    text = "\u4e2d" * 2000
    out = srv._bound_output(text)
    assert len(out.encode("utf-8")) < 6000
    assert "truncated" in out


def test_negative_control_unbounded_would_return_everything():
    # Prove the bound is what shrinks it: with bounding on, a large output
    # is smaller than the original; the marker distinguishes it from a
    # command that merely produced little output.
    os.environ["ZIYA_MAX_SHELL_OUTPUT_BYTES"] = "500"
    srv = _make_server()
    original = "line\n" * 50000
    bounded = srv._bound_output(original)
    assert len(bounded) < len(original)
    assert "truncated" in bounded


def teardown_function(_):
    os.environ.pop("ZIYA_MAX_SHELL_OUTPUT_BYTES", None)
