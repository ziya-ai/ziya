"""The model picker sees what the local server has NOW, and which model is loaded.

Observed 2026-10-20 on oMLX: models added to the oMLX library did not show
up in the model modal -- closing and reopening it, or switching to another
endpoint and back, changed nothing.  ``scan_local_servers`` kept a non-empty
scan for the life of the process (only an EMPTY scan had a TTL), and the
``/api/available-models`` route asked for the memoised registration.  At the
same time ``/v1/models/status`` ``loaded`` was discovered and logged but
never written to the model entry, never sent to the frontend, and ignored
when choosing the endpoint's default model.

Pinned here against an in-process oMLX stand-in whose catalog and loaded
state change between calls:

  * a registration asked for with ``max_age`` re-reads the server when the
    cached scan is older than that, and NOT when it is younger (so the
    several requests one modal open makes share one scan);
  * the request path (no ``max_age``) keeps the old never-expires contract;
  * ``loaded`` lands on the MODEL_CONFIGS entry, follows the server when it
    changes, and ``DEFAULT_MODELS`` prefers the loaded model unless
    ZIYA_LOCAL_MODEL says otherwise;
  * the route relays ``loaded`` per model (source contract).

Run:
    pytest tests/test_local_picker_refresh.py -v
"""
from __future__ import annotations

import inspect
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.utils import local_models as lm

A, B, C = "GLM-5.3-Flash", "Qwen3.8-Flash-Next", "Kimi-K2.5"


class _State:
    def __init__(self):
        self.models = [A, B]
        self.loaded = {B}
        self.hits = {"models": 0, "status": 0}


def _handler(state: _State):
    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            p = self.path.rstrip("/")
            if p == "/v1/models":
                state.hits["models"] += 1
                self._send({"object": "list", "data": [
                    {"id": m, "object": "model", "owned_by": "omlx", "max_model_len": 131072}
                    for m in state.models]})
            elif p == "/v1/models/status":
                state.hits["status"] += 1
                self._send({"models": [
                    {"id": m, "loaded": m in state.loaded, "model_type": "llm",
                     "max_context_window": 131072, "max_tokens": 8192}
                    for m in state.models]})
            else:
                self.send_response(404); self.end_headers()
    return _H


@pytest.fixture
def omlx(monkeypatch, tmp_path):
    state = _State()
    srv = HTTPServer(("127.0.0.1", 0), _handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k in ("ZIYA_LOCAL_MODEL", "ZIYA_LOCAL_MODEL_URL", "ZIYA_LOCAL_API_KEY",
              "ZIYA_ENDPOINT", "OMLX_PORT", "OMLX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OMLX_BASE_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", (srv.server_address[1],))
    lm.reset_discovery_cache_for_tests()
    from app.config import models_config as mc
    saved = {
        "cfg": {k: dict(v) for k, v in mc.MODEL_CONFIGS.items()},
        "epd": {k: dict(v) for k, v in mc.ENDPOINT_DEFAULTS.items()},
        "dm": dict(mc.DEFAULT_MODELS), "dsm": dict(mc.DEFAULT_SERVICE_MODELS),
        "syn": {k: set(v) for k, v in lm._synthesized.items()},
    }
    yield state
    srv.shutdown()
    mc.MODEL_CONFIGS.clear(); mc.MODEL_CONFIGS.update(saved["cfg"])
    mc.ENDPOINT_DEFAULTS.clear(); mc.ENDPOINT_DEFAULTS.update(saved["epd"])
    mc.DEFAULT_MODELS.clear(); mc.DEFAULT_MODELS.update(saved["dm"])
    mc.DEFAULT_SERVICE_MODELS.clear(); mc.DEFAULT_SERVICE_MODELS.update(saved["dsm"])
    lm._synthesized.clear(); lm._synthesized.update(saved["syn"])
    lm.reset_discovery_cache_for_tests()


def _age_scan(seconds: float) -> None:
    """Back-date the cached scan as if ``seconds`` had passed."""
    found, when = lm._scan_cache
    lm._scan_cache = (found, when - seconds)


# ── refresh ─────────────────────────────────────────────────────────────

def test_new_model_appears_when_picker_asks_for_a_fresh_scan(omlx):
    from app.config import models_config as mc
    assert lm.register_local_endpoints() == ["local-omlx"]
    assert set(mc.MODEL_CONFIGS["local-omlx"]) == {A, B}

    omlx.models.append(C)                       # user adds a model to oMLX
    # Request path: the never-expires contract still holds.
    lm.register_local_endpoints()
    assert C not in mc.MODEL_CONFIGS["local-omlx"]
    # Picker path, scan still young: shares the scan, no re-read.
    before = omlx.hits["models"]
    lm.register_local_endpoints(max_age=lm.SCAN_UI_MAX_AGE_S)
    assert omlx.hits["models"] == before
    assert C not in mc.MODEL_CONFIGS["local-omlx"]
    # Picker path, scan older than max_age: re-read, model shows up.
    _age_scan(lm.SCAN_UI_MAX_AGE_S + 1)
    lm.register_local_endpoints(max_age=lm.SCAN_UI_MAX_AGE_S)
    assert omlx.hits["models"] == before + 1
    assert set(mc.MODEL_CONFIGS["local-omlx"]) == {A, B, C}
    assert mc.MODEL_CONFIGS["local-omlx"][C]["token_limit"] == 131072   # enriched, not a bare placeholder


def test_scan_is_stale_contract(omlx):
    assert lm.scan_is_stale(None) is True                # nothing cached yet
    lm.scan_local_servers()
    assert lm.scan_is_stale(None) is False               # a hit never ages on the request path
    assert lm.scan_is_stale(lm.SCAN_UI_MAX_AGE_S) is False
    _age_scan(lm.SCAN_UI_MAX_AGE_S + 1)
    assert lm.scan_is_stale(lm.SCAN_UI_MAX_AGE_S) is True
    assert lm.scan_is_stale(None) is False


# ── loaded state ────────────────────────────────────────────────────────

def test_loaded_lands_on_the_entry_and_picks_the_default(omlx):
    from app.config import models_config as mc
    lm.register_local_endpoints()
    table = mc.MODEL_CONFIGS["local-omlx"]
    assert table[A]["loaded"] is False
    assert table[B]["loaded"] is True
    # Server lists A first; the loaded model B is the default anyway.
    assert mc.DEFAULT_MODELS["local-omlx"] == B
    assert mc.DEFAULT_SERVICE_MODELS["local-omlx"] == B


def test_loaded_follows_the_server_on_a_fresh_scan_with_unchanged_catalog(omlx):
    """Same model list, different model loaded: the signature shortcut must
    not skip re-discovery."""
    from app.config import models_config as mc
    lm.register_local_endpoints()
    assert mc.MODEL_CONFIGS["local-omlx"][B]["loaded"] is True

    omlx.loaded = {A}                            # user loaded A in oMLX
    _age_scan(lm.SCAN_UI_MAX_AGE_S + 1)
    lm.register_local_endpoints(max_age=lm.SCAN_UI_MAX_AGE_S)
    table = mc.MODEL_CONFIGS["local-omlx"]
    assert table[A]["loaded"] is True
    assert table[B]["loaded"] is False
    assert mc.DEFAULT_MODELS["local-omlx"] == A


def test_explicit_local_model_beats_loaded(omlx, monkeypatch):
    from app.config import models_config as mc
    monkeypatch.setenv("ZIYA_LOCAL_MODEL", A)
    lm.register_local_endpoints(refresh=True)
    assert mc.DEFAULT_MODELS["local-omlx"] == A


def test_choose_default_model_prefers_loaded_in_listing_order(monkeypatch):
    monkeypatch.delenv("ZIYA_LOCAL_MODEL", raising=False)
    ids = [A, B, C]
    assert lm.choose_default_model(ids, [C, B]) == C
    assert lm.choose_default_model(ids, []) == ids[0]
    assert lm.choose_default_model(ids, None) == ids[0]
    assert lm.choose_default_model(ids, ["not-on-this-server"]) == ids[0]
    assert lm.choose_default_model([A], [B]) == A           # one model: that one


# ── route seam ──────────────────────────────────────────────────────────

def test_routes_ask_for_a_fresh_scan_and_relay_loaded():
    from app.routes import model_routes
    src = inspect.getsource(model_routes.get_available_models)
    assert "register_local_endpoints(max_age=SCAN_UI_MAX_AGE_S)" in src
    assert '"loaded": config.get("loaded")' in src
    assert "register_local_endpoints(max_age=SCAN_UI_MAX_AGE_S)" in \
        inspect.getsource(model_routes.get_endpoints)
