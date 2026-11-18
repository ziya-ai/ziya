"""The token panel's "tools" figure follows bench placement.

Three defects, reported 2026-11-14 after the deferred-tools baseline fix:

1. With no learned baseline, POST /api/context-estimate priced every tool of
   every connected server, whatever its placement.
2. The learned baseline is one number per model family.  A project or
   conversation with different placements (or tools loaded from within
   reach) showed the figure of whichever request measured last.
3. (frontend, tokenPanelToolsPlacement.test.ts) with no files selected the
   panel skipped the estimate and showed every enabled server's weight.

Unit: ``request_tool_defs.in_context`` and ``estimate_overhead``.
Seams: resolver -> bench filter, route -> resolver, calibrator persistence,
executor -> calibrator (source assertion; the block runs in a live stream).
"""
import json
import math
from pathlib import Path

from app.utils import request_tool_defs as rtd
from app.utils.mcp_ondemand import ALL, LOADER_TOOL, SEARCH_TOOL, mcp_filter

ROOT = Path(__file__).resolve().parents[1]


def _d(name, size=200):
    return {"name": name, "description": "d" * size, "input_schema": {"type": "object"}}


ROWS = [("hand", _d("h1")), ("reach_idle", _d("r1")), ("reach_all", _d("a1")),
        ("reach_some", _d("s1")), ("reach_some", _d("s2")), ("shelf", _d("x1")),
        (None, _d("dyn"))]
PLACEMENTS = {"hand": "always", "reach_idle": "ondemand", "reach_all": "ondemand",
              "reach_some": "ondemand", "shelf": "off"}
LOADED = {"reach_all": ALL, "reach_some": ["s1"]}


def _names(defs):
    return [d["name"] for d in defs]


def test_without_deferral_only_on_hand_and_loaded_tools_are_in_context():
    filt = mcp_filter(PLACEMENTS, LOADED)
    assert _names(rtd.in_context(ROWS, filt, False)) == ["h1", "a1", "s1", "dyn"]


def test_with_deferral_every_within_reach_server_is_out_even_when_loaded():
    filt = mcp_filter(PLACEMENTS, LOADED)
    assert _names(rtd.in_context(ROWS, filt, True)) == ["h1", "dyn"]


def test_no_bench_information_keeps_everything():
    assert _names(rtd.in_context(ROWS, None, True)) == _names(d for _s, d in ROWS)
    assert _names(rtd.in_context(ROWS, mcp_filter({}, {}), False)) == _names(d for _s, d in ROWS)


def _wire(monkeypatch, *, deferral, filt=None, project="p1"):
    from app.utils import bench_prompt
    seen = {}
    monkeypatch.setattr(rtd, "mcp_rows", lambda mgr=None: list(ROWS))
    monkeypatch.setattr(rtd, "builtin_defs", lambda: [_d("file_read"), _d(LOADER_TOOL), _d(SEARCH_TOOL)])
    monkeypatch.setattr(bench_prompt, "project_id_for_root", lambda root: project)

    def _filter(root, conv, **kw):
        seen["filter_args"] = (root, conv)
        if isinstance(filt, Exception):
            raise filt
        return filt if filt is not None else mcp_filter(PLACEMENTS, LOADED)
    monkeypatch.setattr(bench_prompt, "mcp_filter_for_root", _filter)

    def _deferral(model=None):
        seen["model"] = model
        return deferral
    monkeypatch.setattr(rtd, "deferral_for_model", _deferral)
    return seen


def test_resolver_uses_the_request_filter_for_this_project_and_conversation(monkeypatch):
    seen = _wire(monkeypatch, deferral=False)
    ctx, builtins, known = rtd.request_tool_definitions("/proj", "conv-1", model="m1")
    assert seen == {"filter_args": ("/proj", "conv-1"), "model": "m1"}
    assert _names(ctx) == ["h1", "a1", "s1", "dyn"]
    assert _names(known) == _names(d for _s, d in ROWS)
    # Ziya's loader is in the tool list on this path.
    assert LOADER_TOOL in _names(builtins)


def test_resolver_on_a_deferring_model_drops_reach_servers_and_the_loader(monkeypatch):
    _wire(monkeypatch, deferral=True)
    ctx, builtins, _known = rtd.request_tool_definitions("/proj", "conv-1")
    assert _names(ctx) == ["h1", "dyn"]
    assert _names(builtins) == ["file_read"]


def test_resolver_fails_open(monkeypatch):
    _wire(monkeypatch, deferral=True, filt=RuntimeError("bench down"))
    ctx, builtins, _known = rtd.request_tool_definitions("/proj", "conv-1")
    assert len(ctx) == len(ROWS) and len(builtins) == 3
    # No project: the filter is never consulted.
    seen = _wire(monkeypatch, deferral=True, project=None)
    ctx, _b, _k = rtd.request_tool_definitions("/elsewhere", None)
    assert len(ctx) == len(ROWS) and "filter_args" not in seen


def test_disabled_tools_are_not_counted():
    perms = {"defaults": {"tool": "enabled"},
             "servers": {"s": {"tools": {"off": {"permission": "disabled"}}}}}
    assert rtd._permitted(perms, "s", "on") is True
    assert rtd._permitted(perms, "s", "off") is False
    assert rtd._permitted({}, "s", "anything") is True


# -- estimate_overhead ---------------------------------------------------------

class _Cal:
    def __init__(self, baseline=0, names=None, ratio=3.0):
        self.baseline, self.names, self.ratio = baseline, names, ratio

    def get_display_ratio(self, ext, model_family=None, model_id=None):
        return self.ratio, "global"

    def get_baseline_overhead(self, family):
        return self.baseline

    def get_baseline_tool_names(self, family):
        return self.names


def _price(defs, ratio=3.0):
    return math.ceil(len(json.dumps(defs)) / (ratio * 0.85))


def test_schema_estimate_prices_only_what_it_is_given():
    """Defect 1: the route now passes in-context defs, so this is the figure."""
    from app.services.context_estimate import estimate_overhead
    ctx, known = [_d("h1")], [_d("h1"), _d("r1", 5000)]
    tokens, src = estimate_overhead(_Cal(), "claude", None, ctx, [], known_mcp_tool_defs=known)
    assert (tokens, src) == (_price(ctx), "schema_chars")


def test_baseline_is_adjusted_for_tools_that_left_or_joined_context():
    from app.services.context_estimate import estimate_overhead
    h1, r1, n1 = _d("h1"), _d("mcp_r1", 5000), _d("n1", 900)
    known = [h1, r1, n1]
    # Measured with h1 + r1 in context (names are stored normalized).
    cal = _Cal(baseline=50_000, names=["h1", "r1", "file_read"])

    same = estimate_overhead(cal, "claude", None, [h1, r1], [], known_mcp_tool_defs=known)
    assert same == (50_000, "learned_baseline")

    # r1's server moved within reach; n1's server was placed on hand.
    tokens, src = estimate_overhead(cal, "claude", None, [h1, n1], [], known_mcp_tool_defs=known)
    assert src == "learned_baseline_adjusted"
    assert tokens == 50_000 + _price([n1]) - _price([r1])
    assert tokens < 50_000

    # A builtin missing from the current list is not an MCP tool: no adjustment for it.
    assert estimate_overhead(cal, "claude", None, [h1, r1], [_d("other")],
                             known_mcp_tool_defs=known) == (50_000, "learned_baseline")


def test_a_baseline_recorded_without_names_is_used_as_is():
    from app.services.context_estimate import estimate_overhead
    assert estimate_overhead(_Cal(baseline=7000, names=None), "claude", None, [_d("h1")], [],
                             known_mcp_tool_defs=[_d("h1"), _d("r1")]) == (7000, "learned_baseline")

    class _Old:                       # a calibrator without the accessor at all
        def get_display_ratio(self, *a, **k): return 3.0, "global"
        def get_baseline_overhead(self, family): return 7000
    assert estimate_overhead(_Old(), "claude", None, [_d("h1")], []) == (7000, "learned_baseline")


# -- seams ---------------------------------------------------------------------

def test_calibrator_persists_the_names_the_baseline_was_measured_with(tmp_path):
    from app.utils.token_calibrator import TokenCalibrator
    path = str(tmp_path / "cal.json")
    cal = TokenCalibrator(cache_file=path)
    assert cal.get_baseline_tool_names("claude") is None
    cal.baseline_overhead_tokens["claude"] = 1234
    cal.baselines_measured.add("claude")
    cal.record_baseline_tool_names("claude", [{"name": "mcp_b"}, {"name": "a"}, {"name": "mcp_b"}, {}])
    assert cal.get_baseline_tool_names("claude") == ["a", "b"]
    cal._save_calibration_data()

    again = TokenCalibrator(cache_file=path)
    assert again.get_baseline_overhead("claude") == 1234          # the file was read
    assert again.get_baseline_tool_names("claude") == ["a", "b"]
    assert again.get_baseline_tool_names("nova") is None


def test_route_resolves_tools_for_the_request_and_returns_the_adjusted_figure(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes import token_routes
    from app.utils import token_calibrator
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    h1, r1 = _d("h1"), _d("r1", 5000)
    seen = {}

    def _defs(root, conv, model=None):
        seen["args"] = (root, conv, model)
        return [h1], [_d("file_read")], [h1, r1]
    monkeypatch.setattr(token_routes, "_current_tool_definitions", _defs)
    monkeypatch.setattr(token_routes, "_resolve_model_identity", lambda m: ("x.claude-fable-5-2", "claude"))
    cal = _Cal(baseline=40_000, names=["h1", "r1", "file_read"])
    cal._normalize_model_key = lambda mid: mid
    monkeypatch.setattr(token_calibrator, "get_token_calibrator", lambda: cal)

    app = FastAPI(); app.include_router(token_routes.router)
    r = TestClient(app).post("/api/context-estimate",
                             json={"files": [], "conversation_id": "conv-9", "model": "fable5.2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen["args"] == (str(tmp_path), "conv-9", "fable5.2")
    assert body["file_tokens"] == 0                                # no files is a valid request
    assert body["overhead_source"] == "learned_baseline_adjusted"
    assert body["overhead_tokens"] == 40_000 - _price([r1])
    assert body["mcp_tool_count"] == 1 and body["builtin_tool_count"] == 1


def test_route_helper_delegates_to_the_resolver():
    src = (ROOT / "app" / "routes" / "token_routes.py").read_text()
    body = src[src.index("def _current_tool_definitions("):src.index("def _resolve_model_identity(")]
    assert "request_tool_definitions(project_root, conversation_id, model=model)" in body
    assert "get_all_tools()" not in body


def test_executor_records_the_names_with_the_baseline():
    src = (ROOT / "app" / "streaming_tool_executor.py").read_text()
    start = src.index("if should_establish_baseline:")
    block = src[start:src.index("calibrator.request_save()", start)]
    assert "calibrator.record_baseline_tool_names(model_family, _baseline_tools)" in block
    # A baseline measured before names were kept is re-measured once.
    check = src[src.index("should_establish_baseline = False"):src.index("hallucination_retries = 0")]
    assert "or calibrator.get_baseline_tool_names(model_family) is None" in check
