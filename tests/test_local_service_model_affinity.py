"""
Service calls on a local endpoint must use the model the chat is running.

Observed 2026-10-19 on a Mac running oMLX: query 1 ran on
Qwen3.8-Flash-Next (94 GB, loaded by hand).  The post-turn memory triage
resolved ``memory_extraction`` to DEFAULT_SERVICE_MODELS["local-omlx"] --
the server's FIRST listed model, GLM-5.3-Flash -- so oMLX evicted Qwen to
load GLM, and query 2 failed with 507 "Cannot load Qwen3.8 ... current:
107.86GB, model: 94.11GB".  A cloud endpoint shrugs off a different
service model; a local server that holds one large model at a time
swaps 100 GB after every reply.

Both resolution paths are pinned: the endpoint-default path
(``memory_extraction``) and the tier-declared path (``improve_judge``,
which rounds through resolve_tier_model to the same server default).
Env overrides (ZIYA_<CATEGORY>_MODEL) must still win.

Run:
    pytest tests/test_local_service_model_affinity.py -v
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.utils import local_models as lm

# Server order as oMLX lists it: alphabetical, so GLM precedes Qwen.
FIRST, CHAT = "GLM-5.3-Flash-oQ2e-mtp", "Qwen3.8-Flash-Next-oQ3.5e-mtp"
LISTING = [{"id": m, "object": "model", "owned_by": "omlx", "max_model_len": 131072}
           for m in (FIRST, CHAT)]
STATUS = [{"id": m, "loaded": m == CHAT, "model_type": "llm",
           "max_context_window": 131072, "max_tokens": 8192} for m in (FIRST, CHAT)]


def _handler():
    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            p = self.path.rstrip("/")
            if p == "/v1/models":
                self._send(200, {"object": "list", "data": LISTING})
            elif p == "/v1/models/status":
                self._send(200, {"models": STATUS})
            else:
                self._send(404, {})
    return _H


@pytest.fixture
def omlx(monkeypatch, tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), _handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    for k in ("ZIYA_LOCAL_MODEL", "ZIYA_LOCAL_MODEL_URL", "ZIYA_LOCAL_API_KEY",
              "ZIYA_MEMORY_EXTRACTION_MODEL", "ZIYA_IMPROVE_JUDGE_MODEL",
              "OMLX_PORT", "OMLX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OMLX_BASE_PATH", str(tmp_path / "none"))
    # The endpoint default now prefers the LOADED model (CHAT), which would
    # make the resolver tests below pass trivially.  Pin the default to
    # FIRST via the explicit override, which wins over loaded state, so the
    # "service call drifts to the server default" bug stays reproducible.
    monkeypatch.setenv("ZIYA_LOCAL_MODEL", FIRST)
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", (port,))
    lm.reset_discovery_cache_for_tests()
    from app.config import models_config as mc
    saved = {
        "cfg": {k: dict(v) for k, v in mc.MODEL_CONFIGS.items()},
        "epd": {k: dict(v) for k, v in mc.ENDPOINT_DEFAULTS.items()},
        "dm": dict(mc.DEFAULT_MODELS), "dsm": dict(mc.DEFAULT_SERVICE_MODELS),
        "syn": {k: set(v) for k, v in lm._synthesized.items()},
    }
    ids = lm.register_local_endpoints(refresh=True)
    assert ids == ["local-omlx"]
    monkeypatch.setenv("ZIYA_ENDPOINT", "local-omlx")
    monkeypatch.setenv("ZIYA_MODEL", CHAT)
    yield port
    srv.shutdown()
    mc.MODEL_CONFIGS.clear(); mc.MODEL_CONFIGS.update(saved["cfg"])
    mc.ENDPOINT_DEFAULTS.clear(); mc.ENDPOINT_DEFAULTS.update(saved["epd"])
    mc.DEFAULT_MODELS.clear(); mc.DEFAULT_MODELS.update(saved["dm"])
    mc.DEFAULT_SERVICE_MODELS.clear(); mc.DEFAULT_SERVICE_MODELS.update(saved["dsm"])
    lm._synthesized.clear(); lm._synthesized.update(saved["syn"])
    lm.reset_discovery_cache_for_tests()


def test_server_default_is_the_other_model(omlx):
    """Precondition for the bug: the server default is NOT the chat model."""
    from app.config.models_config import DEFAULT_SERVICE_MODELS
    assert DEFAULT_SERVICE_MODELS["local-omlx"] == FIRST


def test_endpoint_default_category_follows_the_chat_model(omlx):
    from app.services.model_resolver import resolve_service_model
    cfg = resolve_service_model("memory_extraction")
    assert cfg["endpoint"] == "local-omlx"
    assert cfg["model_id"] == CHAT, "memory triage would evict the chat model"


def test_tier_declared_category_follows_the_chat_model(omlx):
    from app.services.model_resolver import resolve_service_model
    cfg = resolve_service_model("improve_judge")
    assert cfg["endpoint"] == "local-omlx"
    assert cfg["model_id"] == CHAT


def test_alias_endpoint_resolves_to_the_chat_model(omlx, monkeypatch):
    monkeypatch.setenv("ZIYA_ENDPOINT", "local")
    from app.services.model_resolver import resolve_service_model
    assert resolve_service_model("memory_extraction")["model_id"] == CHAT


def test_env_override_still_wins(omlx, monkeypatch):
    monkeypatch.setenv("ZIYA_MEMORY_EXTRACTION_MODEL", FIRST)
    from app.services.model_resolver import resolve_service_model
    assert resolve_service_model("memory_extraction")["model_id"] == FIRST


def test_chat_model_not_on_server_falls_back_to_default(omlx, monkeypatch):
    """A ZIYA_MODEL the server does not list cannot be sent to it."""
    monkeypatch.setenv("ZIYA_MODEL", "not-on-this-server")
    from app.services.model_resolver import resolve_service_model
    assert resolve_service_model("memory_extraction")["model_id"] == FIRST
