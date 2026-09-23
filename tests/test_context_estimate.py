"""
POST /api/context-estimate prices files as the prompt builder renders them.

The seam that matters is render_prompt_file == get_combined_docs_from_files,
byte for byte: the calibrator learns chars/token on the sent prompt, so the
estimate must price the same bytes.  The other cases pin the defects that
let the gauge read <200k against an 878k prompt: an ignored (no tree node)
file is counted, an unreadable path is reported not zeroed, prefixes cost.
"""
import json
import math
import os

import pytest

from app.services.context_estimate import (
    ContextEstimate, estimate_context, estimate_files, estimate_overhead, render_prompt_file,
)

SRC = "def f(x):\n    return `x` + 1\n\n# end\n"


class _Cal:
    def __init__(self, ratio=2.0, source="learned_model_type", baseline=0):
        self.ratio, self.source, self.baseline = ratio, source, baseline
        self.asked = []

    def get_display_ratio(self, ext, model_family=None, model_id=None):
        self.asked.append((ext, model_family, model_id))
        return self.ratio, self.source

    def get_baseline_overhead(self, family):
        return self.baseline

    def _normalize_model_key(self, mid):
        return mid.lower().removeprefix("us.")


def test_render_matches_prompt_builder_bytes(tmp_path, monkeypatch):
    """Fails if the prompt builder's per-file format changes without this module."""
    from app.agents.agent import get_combined_docs_from_files, file_state_manager
    (tmp_path / "a.py").write_text(SRC)
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.delenv("ZIYA_INCLUDE_ONLY_DIRS", raising=False)
    conv = "ctx-est-seam"
    file_state_manager.conversation_states.pop(conv, None)
    try:
        real = get_combined_docs_from_files(["a.py"], conv)
    finally:
        file_state_manager.conversation_states.pop(conv, None)
    assert real == render_prompt_file("a.py", SRC)
    assert "[001 ] def f(x):" in real
    assert "\\`x\\`" in real, "backtick escaping is part of the rendered bytes"


def test_ignored_file_is_counted_and_prefixes_cost(tmp_path):
    (tmp_path / ".gitignore").write_text("vendored\n")
    d = tmp_path / "vendored"; d.mkdir()
    (d / "big.py").write_text("x = 1\n" * 1000)
    cal = _Cal(ratio=2.0)
    fes = estimate_files(["vendored/big.py"], str(tmp_path), cal, "claude", "us.anthropic.claude-fable-5-1")
    f = fes[0]
    assert f.status == "ok"
    assert f.raw_chars == 6000
    # prefix "[NNN ] " is 7 chars for lines 1-999 and 8 for "[1000 ] "; content
    # "x = 1" is 5; lines joined by \n; header + trailing \n\n
    assert f.chars == len("File: vendored/big.py\n") + 999 * (7 + 5) + (8 + 5) + 999 + 2
    assert f.tokens == -(-f.chars // 2)
    assert cal.asked[0][0] == ".py"
    assert cal.asked[0][2] == "us.anthropic.claude-fable-5-1", "model id reaches the calibrator"


def test_unreadable_paths_are_reported_not_zeroed(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    cal = _Cal()
    est = estimate_context(["gone.py", "sub", "img.png"], str(tmp_path), cal,
                           model_family="claude", model_id=None, mcp_tool_defs=[], builtin_tool_defs=[])
    by = {f.path: f.status for f in est.files}
    assert by == {"gone.py": "missing", "sub": "directory", "img.png": "binary"}
    assert est.file_tokens == 0
    assert sorted(est.unreadable) == ["gone.py", "img.png", "sub"]


def test_prefix_tokens_is_the_annotation_share(tmp_path):
    (tmp_path / "a.py").write_text("y\n" * 100)   # 200 raw chars, 100 lines
    est = estimate_context(["a.py"], str(tmp_path), _Cal(ratio=1.0),
                           model_family="claude", model_id=None, mcp_tool_defs=[], builtin_tool_defs=[])
    f = est.files[0]
    assert est.prefix_tokens == f.chars - f.raw_chars
    assert est.prefix_tokens > f.raw_chars, "at 1 char/token the prefixes outweigh a one-char-per-line file"


def test_global_tier_applies_type_multiplier_but_learned_does_not(tmp_path):
    (tmp_path / "d.json").write_text("{}" * 500)  # .json multiplier is 1.2
    learned = estimate_files(["d.json"], str(tmp_path), _Cal(2.0, "learned_type"), "claude", None)[0]
    glob = estimate_files(["d.json"], str(tmp_path), _Cal(2.0, "global"), "claude", None)[0]
    assert glob.tokens > learned.tokens
    assert glob.tokens == pytest.approx(learned.tokens * 1.2, rel=0.01)


def test_overhead_prefers_learned_baseline_then_schema_chars():
    defs = [{"name": "t", "description": "d" * 100, "input_schema": {"type": "object"}}]
    assert estimate_overhead(_Cal(baseline=168066), "claude", None, defs, []) == (168066, "learned_baseline")
    tokens, src = estimate_overhead(_Cal(ratio=3.0, baseline=0), "claude", None, defs, defs)
    assert src == "schema_chars"
    assert tokens == math.ceil(len(json.dumps(defs + defs)) / (3.0 * 0.85))
    assert estimate_overhead(_Cal(baseline=0), "claude", None, [], []) == (0, "none")


def test_to_dict_is_json_and_totals(tmp_path):
    (tmp_path / "a.py").write_text(SRC)
    est = estimate_context(["a.py"], str(tmp_path), _Cal(ratio=2.0, baseline=1000),
                           model_family="claude", model_id="us.anthropic.claude-fable-5-1",
                           mcp_tool_defs=[{}] * 3, builtin_tool_defs=[{}] * 2)
    d = json.loads(json.dumps(est.to_dict()))
    assert d["total_tokens"] == d["file_tokens"] + 1000
    assert d["mcp_tool_count"] == 3 and d["builtin_tool_count"] == 2
    assert d["model_key"] == "anthropic.claude-fable-5-1"
    assert d["files"][0]["status"] == "ok"


def test_route_returns_estimate_for_project_root(tmp_path, monkeypatch):
    """Seam: request body -> project root -> service -> response shape."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes import token_routes
    (tmp_path / "a.py").write_text(SRC)
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.setattr(token_routes, "_current_tool_definitions", lambda: ([{"name": "m"}], [{"name": "b"}]))
    monkeypatch.setattr(token_routes, "_resolve_model_identity", lambda m: ("us.anthropic.claude-fable-5-1", "claude"))
    app = FastAPI(); app.include_router(token_routes.router)
    r = TestClient(app).post("/api/context-estimate", json={"files": ["a.py", "nope.py"]})
    assert r.status_code == 200, r.text
    body = r.json()
    statuses = {f["path"]: f["status"] for f in body["files"]}
    assert statuses == {"a.py": "ok", "nope.py": "missing"}
    assert body["file_tokens"] > 0
    assert body["unreadable"] == ["nope.py"]
    assert body["mcp_tool_count"] == 1 and body["builtin_tool_count"] == 1
    assert body["total_tokens"] == body["file_tokens"] + body["overhead_tokens"]
