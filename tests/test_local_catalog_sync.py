"""
One local endpoint per running server; the server decides which models exist.

History this pins down. The first live run against DwarfStar ``ds4-server``
pointed at Ollama's :11434 and named ``qwen2.5-coder:7b`` from an env
default although the only model on ``:8000`` was ``deepseek-v4-flash``. A
port scan then found BOTH servers and had to guess. Now every answering
server is its own endpoint -- ``local-dwarfstar``, ``local-ollama`` -- with
that server's ``/v1/models`` as its catalog, so choosing a model in the
picker chooses the server, and nothing guesses.

Tiny in-process HTTP servers stand in for the runtimes so the tests are
deterministic and need no Ollama/ds4 installed.

Run:
    pytest tests/test_local_catalog_sync.py -v
"""
from __future__ import annotations

import inspect
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.utils import local_models as lm

# The entry ds4-server returned live on 2026-09-09 (with --ctx 300000).
DS4_FLASH = {
    "id": "deepseek-v4-flash", "object": "model", "created": 1767225600,
    "owned_by": "ds4.c", "name": "DeepSeek V4 Flash", "context_length": 300000,
    "top_provider": {"context_length": 300000, "max_completion_tokens": 300000,
                     "is_moderated": False},
    "supported_parameters": ["tools", "tool_choice", "max_tokens", "temperature",
                             "top_p", "top_k", "min_p", "ignore_eos", "stop",
                             "seed", "stream", "reasoning_effort"],
}
DS4_PRO_ALIAS = {**DS4_FLASH, "id": "deepseek-v4-pro"}   # same loaded GGUF
OLLAMA_QWEN = {"id": "qwen2.5-coder:7b", "object": "model",
               "created": 1788832257, "owned_by": "library"}


def _make_handler(entries, ollama_tags=False):
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
                self._send(200, {"object": "list", "data": list(entries)})
            elif p == "/api/tags" and ollama_tags:
                self._send(200, {"models": [{"name": e["id"]} for e in entries]})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            p = self.path.rstrip("/")
            if p == "/api/show" and ollama_tags:
                self._send(200, {"capabilities": ["completion", "tools"],
                                 "model_info": {"general.architecture": "qwen2",
                                                "qwen2.context_length": 32768}})
            else:
                self._send(404, {"error": "not found"})
    return _H


def _serve(entries, ollama_tags=False):
    srv = HTTPServer(("127.0.0.1", 0), _make_handler(entries, ollama_tags))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.fixture
def ds4():
    srv, port = _serve([DS4_FLASH, DS4_PRO_ALIAS])
    yield port
    srv.shutdown()


@pytest.fixture
def ollama():
    srv, port = _serve([OLLAMA_QWEN], ollama_tags=True)
    yield port
    srv.shutdown()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ZIYA_LOCAL_MODEL", "ZIYA_LOCAL_MODEL_URL", "ZIYA_LOCAL_RUNTIME",
              "ZIYA_LOCAL_TOKEN_LIMIT", "ZIYA_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", ())   # no real ports in tests
    lm.reset_discovery_cache_for_tests()
    from app.config import models_config as mc
    saved = {
        "cfg": {k: dict(v) for k, v in mc.MODEL_CONFIGS.items()},
        "epd": {k: dict(v) for k, v in mc.ENDPOINT_DEFAULTS.items()},
        "dm": dict(mc.DEFAULT_MODELS), "dsm": dict(mc.DEFAULT_SERVICE_MODELS),
        "syn": {k: set(v) for k, v in lm._synthesized.items()},
    }
    yield
    mc.MODEL_CONFIGS.clear(); mc.MODEL_CONFIGS.update(saved["cfg"])
    mc.ENDPOINT_DEFAULTS.clear(); mc.ENDPOINT_DEFAULTS.update(saved["epd"])
    mc.DEFAULT_MODELS.clear(); mc.DEFAULT_MODELS.update(saved["dm"])
    mc.DEFAULT_SERVICE_MODELS.clear(); mc.DEFAULT_SERVICE_MODELS.update(saved["dsm"])
    lm._synthesized.clear(); lm._synthesized.update(saved["syn"])
    lm.reset_discovery_cache_for_tests()


def _ports(monkeypatch, *ports):
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", tuple(ports))
    lm.reset_discovery_cache_for_tests()


# ── identification from /v1/models ─────────────────────────────────────

def test_ds4_entry_yields_full_capabilities():
    """owned_by, context (the served --ctx), tools and reasoning_effort all
    come from the one /v1/models entry -- no native probe needed."""
    found = lm.model_from_v1_entry("dwarfstar", DS4_FLASH)
    assert found.context_length == 300000
    assert found.supports_tools is True
    assert found.supports_reasoning_effort is True
    assert found.max_output_tokens == 300000
    entry = lm.apply_discovery(lm.synthesize_local_model_entry("deepseek-v4-flash"), found)
    assert entry["token_limit"] == 300000
    assert entry["native_function_calling"] is True
    assert entry["supports_reasoning_effort"] is True
    assert entry["supports_thinking"] is True
    assert "request_extra_body" not in entry, "ds4 fixes ctx at launch; no num_ctx"


def test_unknown_owned_by_is_generic_not_misattributed():
    found = lm.model_from_v1_entry("openai-compatible", {"id": "m", "owned_by": "whoever"})
    assert found.supports_tools is None and found.context_length is None


# ── scan: one endpoint per server ──────────────────────────────────────

def test_two_servers_become_two_endpoints(monkeypatch, ds4, ollama):
    _ports(monkeypatch, ollama, ds4)
    servers = lm.scan_local_servers(refresh=True)
    by_id = {s.endpoint_id: s for s in servers}
    assert set(by_id) == {"local-ollama", "local-dwarfstar"}
    assert by_id["local-ollama"].model_ids == ("qwen2.5-coder:7b",)
    assert by_id["local-dwarfstar"].label.startswith("Local · DwarfStar (:")
    assert by_id["local-ollama"].label.startswith("Local · Ollama (:")


def test_ds4_compat_aliases_collapse_to_one_model(monkeypatch, ds4):
    """deepseek-v4-pro is an alias for the same loaded GGUF (same name)."""
    _ports(monkeypatch, ds4)
    (s,) = lm.scan_local_servers(refresh=True)
    assert s.model_ids == ("deepseek-v4-flash",)


def test_same_runtime_twice_gets_port_suffix(monkeypatch):
    a, pa = _serve([DS4_FLASH]); b, pb = _serve([DS4_FLASH])
    try:
        _ports(monkeypatch, pa, pb)
        ids = sorted(s.endpoint_id for s in lm.scan_local_servers(refresh=True))
        assert ids == sorted([f"local-dwarfstar-{pa}", f"local-dwarfstar-{pb}"])
    finally:
        a.shutdown(); b.shutdown()


def test_generic_server_is_labelled_by_port(monkeypatch):
    srv, port = _serve([{"id": "m", "owned_by": "someone"}])
    try:
        _ports(monkeypatch, port)
        (s,) = lm.scan_local_servers(refresh=True)
        assert s.endpoint_id == f"local-{port}"
        assert s.label == f"Local · :{port}"
    finally:
        srv.shutdown()


def test_configured_url_adds_a_server_not_scanned(monkeypatch, ds4):
    _ports(monkeypatch)  # scan nothing
    monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", f"http://127.0.0.1:{ds4}")
    (s,) = lm.scan_local_servers(refresh=True)
    assert s.endpoint_id == "local-dwarfstar"


# ── registration into the config tables ────────────────────────────────

def test_register_creates_endpoint_tables(monkeypatch, ds4, ollama):
    _ports(monkeypatch, ollama, ds4)
    from app.config import models_config as mc
    ids = lm.register_local_endpoints(refresh=True)
    assert set(ids) == {"local-ollama", "local-dwarfstar"}
    assert set(mc.MODEL_CONFIGS["local-dwarfstar"]) == {"deepseek-v4-flash"}
    assert set(mc.MODEL_CONFIGS["local-ollama"]) == {"qwen2.5-coder:7b"}
    assert mc.DEFAULT_MODELS["local-dwarfstar"] == "deepseek-v4-flash"
    assert mc.DEFAULT_SERVICE_MODELS["local-ollama"] == "qwen2.5-coder:7b"
    assert mc.ENDPOINT_DEFAULTS["local-dwarfstar"]["base_url"] == f"http://127.0.0.1:{ds4}/v1"
    assert mc.ENDPOINT_DEFAULTS["local-dwarfstar"]["runtime"] == "dwarfstar"
    # Enriched: ds4 from its /v1 entry, ollama from /api/show.
    assert mc.MODEL_CONFIGS["local-dwarfstar"]["deepseek-v4-flash"]["token_limit"] == 300000
    qwen = mc.MODEL_CONFIGS["local-ollama"]["qwen2.5-coder:7b"]
    assert qwen["token_limit"] == 32768
    assert qwen["request_extra_body"] == {"options": {"num_ctx": 32768}}


def test_model_name_selects_its_server(monkeypatch, ds4, ollama):
    """The picker sends only a model id; set-model finds the endpoint by
    searching MODEL_CONFIGS. With per-server tables the model's own server
    is the only hit."""
    _ports(monkeypatch, ollama, ds4)
    from app.config import models_config as mc
    lm.register_local_endpoints(refresh=True)
    hits = [ep for ep, t in mc.MODEL_CONFIGS.items() if "deepseek-v4-flash" in t]
    assert hits == ["local-dwarfstar"]


def test_stale_endpoint_is_removed_but_active_one_kept(monkeypatch, ds4, ollama):
    from app.config import models_config as mc
    _ports(monkeypatch, ollama, ds4)
    lm.register_local_endpoints(refresh=True)
    monkeypatch.setenv("ZIYA_ENDPOINT", "local-ollama")
    _ports(monkeypatch, ds4)   # ollama "went away"
    lm.register_local_endpoints(refresh=True)
    assert "local-ollama" in mc.MODEL_CONFIGS, "the running endpoint must not vanish"
    monkeypatch.delenv("ZIYA_ENDPOINT")
    lm.register_local_endpoints(refresh=True)
    assert "local-ollama" not in mc.MODEL_CONFIGS


def test_alias_resolves_to_sole_server(monkeypatch, ds4):
    _ports(monkeypatch, ds4)
    assert lm.resolve_local_alias("local") == "local-dwarfstar"
    assert lm.local_endpoint_base_url("local") == f"http://127.0.0.1:{ds4}/v1"


def test_alias_with_two_servers_picks_first_and_names_the_other(monkeypatch, ds4, ollama, caplog):
    _ports(monkeypatch, ollama, ds4)
    with caplog.at_level(logging.WARNING, logger="app.utils.local_models"):
        assert lm.resolve_local_alias("local") == "local-ollama"
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "local-dwarfstar" in msg and "--endpoint" in msg


def test_alias_honours_configured_url_among_several(monkeypatch, ds4, ollama):
    _ports(monkeypatch, ollama, ds4)
    monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", f"http://127.0.0.1:{ds4}")
    lm.reset_discovery_cache_for_tests()
    assert lm.resolve_local_alias("local") == "local-dwarfstar"


def test_no_server_keeps_alias_and_placeholder(monkeypatch):
    _ports(monkeypatch)
    from app.config import models_config as mc
    assert lm.register_local_endpoints(refresh=True) == []
    assert lm.local_endpoint_ids_for_listing() == ["local"]
    assert lm.resolve_local_alias("local") == "local"
    assert lm.DEFAULT_LOCAL_MODEL in mc.MODEL_CONFIGS["local"]


def test_listing_hides_alias_when_servers_exist(monkeypatch, ds4):
    _ports(monkeypatch, ds4)
    assert lm.local_endpoint_ids_for_listing() == ["local-dwarfstar"]
    assert lm.local_endpoint_label("local-dwarfstar").startswith("Local · DwarfStar")


def test_availability_is_reachability(monkeypatch, ds4):
    _ports(monkeypatch, ds4)
    import app.utils.provider_detection as pd
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    avail = pd.detect_available_providers()
    assert avail["local-dwarfstar"] is True
    assert avail["local"] is True, "alias available when any server answers"


# ── the seams ──────────────────────────────────────────────────────────

def test_environment_setup_pins_alias_to_concrete_id():
    from app.config import environment
    src = inspect.getsource(environment)
    assert "resolve_local_alias" in src and "register_local_endpoints" in src


def test_model_manager_registers_before_resolving_name():
    from app.agents.models import ModelManager
    src = inspect.getsource(ModelManager.initialize_model)
    i_reg = src.find("register_local_endpoints")
    i_name = src.find('ziya_env("ZIYA_MODEL") or cls.DEFAULT_MODELS')
    assert 0 <= i_reg < i_name, "registration must precede model-name resolution"


def test_routes_register_and_label():
    from app.routes import model_routes
    assert "register_local_endpoints" in inspect.getsource(model_routes.get_available_models)
    src = inspect.getsource(model_routes.get_endpoints)
    assert "register_local_endpoints" in src and '"label"' in src


def test_factory_and_resolver_use_endpoint_url(monkeypatch, ds4):
    _ports(monkeypatch, ds4)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.config import models_config as mc
    from app.providers.factory import create_provider, is_endpoint_supported
    lm.register_local_endpoints(refresh=True)
    assert is_endpoint_supported("local-dwarfstar")
    cfg = mc.MODEL_CONFIGS["local-dwarfstar"]["deepseek-v4-flash"]
    p = create_provider(endpoint="local-dwarfstar", model_id="deepseek-v4-flash", model_config=cfg)
    assert str(p.client.base_url).rstrip("/") == f"http://127.0.0.1:{ds4}/v1"
    assert p.model_config["token_limit"] == 300000
    from app.services.model_resolver import resolve_service_model
    monkeypatch.setenv("ZIYA_ENDPOINT", "local-dwarfstar")
    svc = resolve_service_model("default")
    assert svc["endpoint"] == "local-dwarfstar" and svc["model_id"] == "deepseek-v4-flash"


def test_frontend_renders_label():
    import pathlib
    src = pathlib.Path("frontend/src/components/ModelConfigModal.tsx").read_text()
    assert "ep.label || ep.id" in src


def test_openai_is_a_declared_dependency():
    import pathlib
    assert "\nopenai = " in pathlib.Path("pyproject.toml").read_text()


def test_main_preflights_openai_sdk():
    from app import main
    assert "openai_sdk_missing_message" in inspect.getsource(main)


def test_openai_sdk_preflight_message():
    import builtins
    import unittest.mock as um
    real_import = builtins.__import__

    def _no_openai(name, *a, **k):
        if name == "openai":
            raise ImportError("no")
        return real_import(name, *a, **k)

    with um.patch.object(builtins, "__import__", _no_openai):
        msg = lm.openai_sdk_missing_message("local-dwarfstar")
    assert msg and "pip install openai" in msg
    assert lm.openai_sdk_missing_message("local") is None
