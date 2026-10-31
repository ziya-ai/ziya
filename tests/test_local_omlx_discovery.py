"""
oMLX (Apple-silicon MLX server) as a local endpoint.

oMLX defaults to :8000, which the scan already covers, so before this change
it surfaced as a nameless "Local · :8000" endpoint whose picker listed its
embedding/reranker/MarkItDown entries beside the chat models and whose
context window was whatever the placeholder said.  This pins down:

* identification from ``owned_by: "omlx"`` (omlx/api/openai_models.py);
* the configured-port recovery (``OMLX_PORT`` / ``~/.omlx/settings.json``)
  so a server moved off :8000 is still found;
* ``/v1/models/status`` enrichment -- context, output cap, vision, loaded,
  reasoning ladder -- landing in MODEL_CONFIGS through register_local_endpoints;
* non-chat model types filtered out of the catalog, failing OPEN when the
  status endpoint is unavailable.

Response shapes are copied from omlx/server.py ``list_models`` /
``list_models_status`` and omlx/engine_pool.py ``get_status`` as of
2026-10-18; no live oMLX was available.

Run:
    pytest tests/test_local_omlx_discovery.py -v
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.utils import local_models as lm

# /v1/models entries: the alias is what the listing shows (display_id).
OMLX_LISTING = [
    {"id": "qwen3-8b", "object": "model", "created": 1, "owned_by": "omlx", "max_model_len": 40960},
    {"id": "Qwen3-VL-8B-4bit", "object": "model", "created": 1, "owned_by": "omlx", "max_model_len": None},
    {"id": "bge-m3", "object": "model", "created": 1, "owned_by": "omlx", "max_model_len": None},
    {"id": "MarkItDown", "object": "model", "created": 1, "owned_by": "omlx", "max_model_len": None},
]

# /v1/models/status entries (subset of get_status fields + server additions).
OMLX_STATUS = [
    {"id": "Qwen3-8B-4bit", "model_alias": "qwen3-8b", "loaded": True, "model_type": "llm",
     "model_context_length": 40960, "max_context_window": 32768, "max_tokens": 4096,
     "thinking_default": True, "reasoning_efforts": ["low", "medium", "high"]},
    {"id": "Qwen3-VL-8B-4bit", "loaded": False, "model_type": "vlm",
     "model_context_length": 262144, "max_context_window": None, "max_tokens": 8192,
     "thinking_default": None, "reasoning_efforts": None},
    {"id": "bge-m3", "loaded": False, "model_type": "embedding",
     "model_context_length": 8192, "max_context_window": None, "max_tokens": None},
    {"id": "MarkItDown", "loaded": True, "model_type": "markitdown",
     "max_context_window": None, "max_tokens": None},
]


def _handler(listing, status, api_key=None):
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
            # Mirrors verify_api_key: with a key configured, BOTH /v1/models
            # and /v1/models/status refuse anything but the right bearer.
            if api_key and self.headers.get("Authorization") != f"Bearer {api_key}":
                self._send(401, {"detail": "API key required"})
                return
            if p == "/v1/models":
                self._send(200, {"object": "list", "data": list(listing)})
            elif p == "/v1/models/status" and status is not None:
                self._send(200, {"model_count": len(status), "loaded_count": 1, "models": list(status)})
            elif p == "/v1/models/status":
                self._send(401, {"detail": "Invalid API key"})
            else:
                self._send(404, {"error": "not found"})
    return _H


def _serve(listing, status, api_key=None):
    srv = HTTPServer(("127.0.0.1", 0), _handler(listing, status, api_key))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


KEY = "sk-omlx-test-key"


@pytest.fixture
def omlx_keyed():
    """An oMLX with ``--api-key`` set: unauthenticated /v1/models is 401."""
    srv, port = _serve(OMLX_LISTING, OMLX_STATUS, api_key=KEY)
    yield port
    srv.shutdown()


@pytest.fixture
def omlx():
    srv, port = _serve(OMLX_LISTING, OMLX_STATUS)
    yield port
    srv.shutdown()


@pytest.fixture
def omlx_no_status():
    """A build whose status endpoint sits behind the main API key."""
    srv, port = _serve(OMLX_LISTING, None)
    yield port
    srv.shutdown()


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for k in ("ZIYA_LOCAL_MODEL", "ZIYA_LOCAL_MODEL_URL", "ZIYA_LOCAL_RUNTIME",
              "ZIYA_LOCAL_TOKEN_LIMIT", "ZIYA_LOCAL_API_KEY", "ZIYA_ENDPOINT",
              "OMLX_PORT", "OMLX_BASE_PATH", "OMLX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", ())
    # Keep the real ~/.omlx (if any) out of the scan.
    monkeypatch.setenv("OMLX_BASE_PATH", str(tmp_path / "nonexistent"))
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


# ── identification ──────────────────────────────────────────────────────

def test_owned_by_omlx_names_the_runtime(monkeypatch, omlx):
    _ports(monkeypatch, omlx)
    (s,) = lm.scan_local_servers(refresh=True)
    assert s.runtime == "omlx"
    assert s.endpoint_id == "local-omlx"
    assert s.label == f"Local · oMLX (:{omlx})"


def test_catalog_offers_only_chat_models(monkeypatch, omlx):
    """Embedding and MarkItDown entries are on /v1/models but cannot chat."""
    _ports(monkeypatch, omlx)
    (s,) = lm.scan_local_servers(refresh=True)
    assert list(s.model_ids) == ["qwen3-8b", "Qwen3-VL-8B-4bit"]


def test_filter_fails_open_without_status(monkeypatch, omlx_no_status):
    """No status => no type information => every listed id is kept.  Hiding
    a model the server does serve is the worse error."""
    _ports(monkeypatch, omlx_no_status)
    (s,) = lm.scan_local_servers(refresh=True)
    assert s.runtime == "omlx"
    assert list(s.model_ids) == [e["id"] for e in OMLX_LISTING]


# ── port recovery ───────────────────────────────────────────────────────

def test_omlx_port_from_env(monkeypatch):
    monkeypatch.setenv("OMLX_PORT", "11435")
    assert lm.omlx_configured_port() == 11435
    assert lm.extra_scan_ports() == [11435]


def test_omlx_port_from_settings_json(tmp_path, monkeypatch):
    base = tmp_path / "omlx-base"
    base.mkdir()
    (base / "settings.json").write_text(json.dumps({"server": {"host": "127.0.0.1", "port": 18000}}))
    monkeypatch.setenv("OMLX_BASE_PATH", str(base))
    assert lm.omlx_configured_port() == 18000


def test_omlx_port_from_mac_app_bootstrap_file(tmp_path, monkeypatch):
    """The macOS app can relocate the data root; it records where in a
    one-line file under ~/Library/Application Support/oMLX."""
    monkeypatch.delenv("OMLX_BASE_PATH", raising=False)
    home = tmp_path / "home"
    moved = tmp_path / "moved-root"
    moved.mkdir()
    (moved / "settings.json").write_text(json.dumps({"server": {"port": 9123}}))
    boot = home / "Library" / "Application Support" / "oMLX"
    boot.mkdir(parents=True)
    (boot / "base-path").write_text(f"{moved}\n")
    assert lm.omlx_configured_port(environ={}, home=str(home)) == 9123


def test_omlx_port_default_is_not_duplicated(tmp_path, monkeypatch):
    """A settings.json saying :8000 adds nothing: that port is already scanned."""
    base = tmp_path / "b"; base.mkdir()
    (base / "settings.json").write_text(json.dumps({"server": {"port": 8000}}))
    monkeypatch.setenv("OMLX_BASE_PATH", str(base))
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", (11434, 8000, 1234, 8080))
    assert lm.omlx_configured_port() == 8000
    assert lm.extra_scan_ports() == []


@pytest.mark.parametrize("body", ["{not json", json.dumps({"server": {"port": "eight"}}),
                                  json.dumps([1, 2]), json.dumps({"server": {"port": 70000}})])
def test_omlx_port_malformed_settings_is_none(tmp_path, monkeypatch, body):
    base = tmp_path / "b"; base.mkdir()
    (base / "settings.json").write_text(body)
    monkeypatch.setenv("OMLX_BASE_PATH", str(base))
    assert lm.omlx_configured_port() is None


def test_configured_port_is_scanned(monkeypatch, omlx):
    """The seam: a moved oMLX is found with NO known port in the scan list."""
    monkeypatch.setenv("OMLX_PORT", str(omlx))
    lm.reset_discovery_cache_for_tests()
    (s,) = lm.scan_local_servers(refresh=True)
    assert s.runtime == "omlx" and s.root.endswith(f":{omlx}")


# ── API key: a keyed oMLX must not vanish ───────────────────────────────

def test_keyed_server_is_invisible_without_a_key(monkeypatch, omlx_keyed, caplog):
    """The reported bug: /v1/models says "API key required", the scan read
    the 401 as "nothing here", and the server never reached the picker.
    Still invisible without a key -- but now it says so instead of skipping
    the port silently."""
    _ports(monkeypatch, omlx_keyed)
    with caplog.at_level(logging.WARNING, logger="app.utils.local_models"):
        assert lm.scan_local_servers(refresh=True) == []
    assert "require an API key" in caplog.text
    assert f":{omlx_keyed}" in caplog.text


def test_key_from_omlx_settings_json_unlocks_the_scan(tmp_path, monkeypatch, omlx_keyed):
    """Zero-config on the host running oMLX: the key is read from where oMLX
    keeps it (auth.api_key), used for the scan, the status probe and
    remembered for chat requests."""
    from app.config import models_config as mc
    base = tmp_path / "omlx-base"; base.mkdir()
    (base / "settings.json").write_text(json.dumps(
        {"server": {"port": omlx_keyed}, "auth": {"api_key": KEY}}))
    monkeypatch.setenv("OMLX_BASE_PATH", str(base))
    lm.reset_discovery_cache_for_tests()

    (s,) = lm.scan_local_servers(refresh=True)
    assert s.runtime == "omlx" and s.api_key == KEY
    # Status probe ran with the key: non-chat models filtered, not failed-open.
    assert list(s.model_ids) == ["qwen3-8b", "Qwen3-VL-8B-4bit"]

    ids = lm.register_local_endpoints(refresh=True)
    assert ids == ["local-omlx"]
    # Enrichment reached /v1/models/status (needs the key): effective window.
    assert mc.MODEL_CONFIGS["local-omlx"]["qwen3-8b"]["token_limit"] == 32768
    # What the providers must send instead of the placeholder.
    assert lm.local_endpoint_api_key("local-omlx") == KEY
    assert lm.local_endpoint_api_key("local") == KEY


def test_omlx_api_key_env_wins_over_settings(tmp_path, monkeypatch):
    base = tmp_path / "b"; base.mkdir()
    (base / "settings.json").write_text(json.dumps({"auth": {"api_key": "from-file"}}))
    monkeypatch.setenv("OMLX_BASE_PATH", str(base))
    assert lm.omlx_configured_api_key() == "from-file"
    monkeypatch.setenv("OMLX_API_KEY", "from-env")
    assert lm.omlx_configured_api_key() == "from-env"


def test_ziya_local_api_key_works_for_any_keyed_server(monkeypatch, omlx_keyed):
    monkeypatch.setenv("ZIYA_LOCAL_API_KEY", KEY)
    _ports(monkeypatch, omlx_keyed)
    (s,) = lm.scan_local_servers(refresh=True)
    assert s.api_key == KEY
    assert lm.local_endpoint_api_key(s.endpoint_id) == KEY


def test_wrong_key_is_refused_not_misread(monkeypatch, omlx_keyed):
    monkeypatch.setenv("ZIYA_LOCAL_API_KEY", "nope")
    _ports(monkeypatch, omlx_keyed)
    assert lm.scan_local_servers(refresh=True) == []


def test_keyless_server_sends_no_key_and_gets_placeholder(monkeypatch, omlx):
    _ports(monkeypatch, omlx)
    (s,) = lm.scan_local_servers(refresh=True)
    assert s.api_key is None
    assert lm.local_endpoint_api_key(s.endpoint_id) == lm.LOCAL_PLACEHOLDER_API_KEY


def test_providers_use_the_endpoint_key_not_the_placeholder():
    """Seam: every place that builds an OpenAI client for a local endpoint
    must ask local_endpoint_api_key(); a literal placeholder there would
    send 'local' to a keyed server and 401 on the first chat request."""
    import inspect
    import app.providers.factory as factory
    import app.services.model_resolver as resolver
    import app.agents.models as models
    for mod in (factory, resolver, models):
        src = inspect.getsource(mod)
        assert "local_endpoint_api_key" in src, mod.__name__
        assert "api_key=LOCAL_PLACEHOLDER_API_KEY" not in src, mod.__name__
        assert "api_key or LOCAL_PLACEHOLDER_API_KEY" not in src, mod.__name__


# ── per-model enrichment through the public registration path ───────────

def test_register_enriches_from_status(monkeypatch, omlx):
    from app.config import models_config as mc
    _ports(monkeypatch, omlx)
    ids = lm.register_local_endpoints(refresh=True)
    assert ids == ["local-omlx"]
    table = mc.MODEL_CONFIGS["local-omlx"]
    assert set(table) == {"qwen3-8b", "Qwen3-VL-8B-4bit"}

    q = table["qwen3-8b"]
    # Effective window (32768) wins over max_model_len (40960) from the listing.
    assert q["token_limit"] == 32768
    # Output cap is the server's max_tokens, not the derived half-window.
    assert q["max_output_tokens"] == 4096
    assert q["supports_thinking"] is True
    assert q["supports_reasoning_effort"] is True
    # The server's own ladder, not the DwarfStar one.
    assert q["supported_efforts"] == ["low", "medium", "high"]
    assert q["thinking_effort_default"] == "medium"
    # Not Ollama: no num_ctx body, no text-tool-call recovery.
    assert "request_extra_body" not in q
    assert "recover_text_tool_calls" not in q

    v = table["Qwen3-VL-8B-4bit"]
    # No configured window, listing max_model_len null -> checkpoint length.
    assert v["token_limit"] == 262144
    assert v["supports_vision"] is True
    assert "supported_efforts" not in v


def test_probe_matches_alias_and_reports_loaded(omlx):
    root = f"http://127.0.0.1:{omlx}"
    found = lm.discover_local_model("qwen3-8b", root, "omlx")
    assert found is not None and found.runtime == "omlx"
    assert found.loaded is True and found.context_length == 32768
    assert found.supported_efforts == ("low", "medium", "high")
    assert lm.discover_local_model("no-such-model", root, "omlx") is None


def test_probe_falls_back_to_listing_without_status(omlx_no_status):
    """Status 401 -> the /v1/models entry (max_model_len) still gives context."""
    root = f"http://127.0.0.1:{omlx_no_status}"
    found = lm.discover_local_model("qwen3-8b", root, "omlx")
    assert found is not None
    assert found.context_length == 40960
    assert found.loaded is None


def test_forced_runtime_env_accepts_omlx(monkeypatch, omlx):
    monkeypatch.setenv("ZIYA_LOCAL_RUNTIME", "omlx")
    found = lm.discover_local_model("qwen3-8b", f"http://127.0.0.1:{omlx}", None)
    assert found is not None and found.runtime == "omlx" and found.loaded is True


def test_apply_discovery_server_ladder_without_medium():
    """A ladder lacking 'medium' defaults to its first rung, not to a level
    the server would reject."""
    entry = lm.synthesize_local_model_entry("m")
    lm.apply_discovery(entry, lm.DiscoveredModel(
        runtime="omlx", name="m", context_length=8192, supports_tools=None,
        supports_vision=None, supports_thinking=None,
        supports_reasoning_effort=True, supported_efforts=("minimal", "max")))
    assert entry["supported_efforts"] == ["minimal", "max"]
    assert entry["thinking_effort_default"] == "minimal"
