"""
Local-model endpoints: one Ziya endpoint per running local inference server.

Ollama, LM Studio, llama.cpp ``llama-server``, vLLM, DwarfStar ``ds4-server``
and friends all speak OpenAI ``/v1/chat/completions``, so the transport is
``OpenAIDirectProvider`` / ``DirectOpenAIModel`` unchanged (as ``zai`` and
``meta`` use). What differs per server is *which models exist*, *what they
support*, and *where the server is* -- and none of that is configuration
here: it is read from the server.

Design (design/local-models.md): the well-known loopback ports are scanned
at startup and each answering server becomes its own endpoint,
``local-<runtime>`` (``local-dwarfstar``, ``local-ollama``, ...), with a
human label for the model picker. The endpoint's model catalog is that
server's ``GET /v1/models``; each entry is then enriched from whatever the
runtime exposes (Ollama ``/api/show``, LM Studio ``/api/v0/models``, or the
extra fields DwarfStar/vLLM put in the ``/v1/models`` entry itself). Picking
a model in the UI therefore picks the server it lives on with no further
configuration. ``--endpoint local`` remains as an alias for "the local
server" and resolves to a concrete id.

Two environment variables remain, both optional:

* ``ZIYA_LOCAL_MODEL_URL`` -- ADD a server that the port scan cannot see
  (another port, another host). It never chooses between scanned servers.
* ``ZIYA_LOCAL_TOKEN_LIMIT`` -- CEILING on the context window when the
  server's full window does not fit in memory. Never raises the limit.

Import is side-effect free (no network); everything network-bound runs at
model initialisation and is memoised per process.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Ollama's default port; used only when the scan finds nothing and no URL is
# set, so that ``--endpoint local`` has *some* address to fail against with
# a real connection error naming the port.
DEFAULT_LOCAL_URL = "http://localhost:11434"

# Loopback ports scanned when ZIYA_LOCAL_MODEL_URL is unset: Ollama, DwarfStar
# ds4-server / vLLM / SGLang, LM Studio, llama-server. Every answering port
# becomes an endpoint; the order only affects which is listed first.
KNOWN_LOCAL_PORTS: tuple = (11434, 8000, 1234, 8080)

# Reference model for a multi-model server (Ollama) when ZIYA_LOCAL_MODEL is
# unset and the server has it. Never sent to a server that lacks it.
DEFAULT_LOCAL_MODEL = "qwen2.5-coder:7b"

# Placeholder context used ONLY between synthesis and discovery; the
# server's answer replaces it before the first request.
UNPROBED_LOCAL_TOKEN_LIMIT = 32768

# Below this the builtin tool surface (~4k tokens) plus the system prompt
# leave too little room to be useful; the user is told, not second-guessed.
USABLE_CONTEXT_FLOOR = 16384

# Output caps derived from the context window. Ollama maps max_tokens to
# num_predict, whose default is unbounded, so an explicit cap is needed.
MAX_OUTPUT_CEILING = 32768
DEFAULT_OUTPUT_CEILING = 8192

PROBE_TIMEOUT_S = 3.0      # per-model metadata probes
SCAN_TIMEOUT_S = 1.0       # per-port /v1/models during the scan
SCAN_MISS_TTL_S = 30.0     # a scan that found nothing is retried after this

# The OpenAI SDK refuses to construct a client with no key even though local
# servers ignore it.
LOCAL_PLACEHOLDER_API_KEY = "local"

LOCAL_ALIAS = "local"
LOCAL_PREFIX = "local-"

# Runtime identification from the ``owned_by`` field of a /v1/models entry.
# "observed": seen live in this project. "documented": from the runtime's
# docs, not verified here -- an unlisted or wrong value degrades to a
# port-labelled endpoint, never to a misattributed one.
_OWNED_BY_RUNTIME: Dict[str, str] = {
    "ds4.c": "dwarfstar",        # observed 2026-09-09
    "library": "ollama",         # observed 2026-09-09 (also identified via /api/tags)
    "vllm": "vllm",              # documented
    "llamacpp": "llamacpp",      # documented
    "llama.cpp": "llamacpp",     # documented (LocalAI backend naming)
    "lmstudio": "lmstudio",      # documented (also identified via /api/v0/models)
    "sglang": "sglang",          # documented, unverified
    "localai": "localai",        # documented
}

RUNTIME_LABELS: Dict[str, str] = {
    "dwarfstar": "DwarfStar",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "llamacpp": "llama.cpp",
    "vllm": "vLLM",
    "sglang": "SGLang",
    "localai": "LocalAI",
    "openai-compatible": "OpenAI-compatible",
}

# Runtimes whose context window is fixed at launch/load and NOT taken per
# request. Only Ollama takes options.num_ctx.
_RUNTIMES_TAKING_NUM_CTX = frozenset({"ollama"})


# ---------------------------------------------------------------------------
# Endpoint ids, URLs
# ---------------------------------------------------------------------------

def is_local_endpoint(endpoint: Optional[str]) -> bool:
    """``local`` (the alias) or any ``local-<runtime>`` id."""
    return bool(endpoint) and (endpoint == LOCAL_ALIAS or endpoint.startswith(LOCAL_PREFIX))


def _with_v1(raw: str) -> str:
    base = raw.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def is_loopback_url(url: str) -> bool:
    """True when ``url`` addresses this machine (localhost, 127/8, ::1).

    An unparseable or hostless URL is NOT loopback: the enterprise endpoint
    exemption that consumes this must fail closed, so "we could not tell" has
    to read as "not exempt", never the reverse.
    """
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in ("localhost", "::1", "0:0:0:0:0:0:0:1"):
        return True
    if host.startswith("127."):
        parts = host.split(".")
        return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    return False


def configured_local_url() -> Optional[str]:
    """``ZIYA_LOCAL_MODEL_URL`` with ``/v1`` derived, or None when unset."""
    raw = (os.environ.get("ZIYA_LOCAL_MODEL_URL") or "").strip()
    return _with_v1(raw) if raw else None


def local_model_name() -> str:
    """``ZIYA_LOCAL_MODEL``, or the reference model when unset."""
    return (os.environ.get("ZIYA_LOCAL_MODEL") or "").strip() or DEFAULT_LOCAL_MODEL


def _env_int(name: str) -> Optional[int]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def local_token_cap() -> Optional[int]:
    """Optional CEILING on the context window (``ZIYA_LOCAL_TOKEN_LIMIT``)."""
    return _env_int("ZIYA_LOCAL_TOKEN_LIMIT")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_json(method: str, url: str, body: Optional[dict] = None,
               timeout: float = PROBE_TIMEOUT_S) -> Optional[Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "null")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("local model probe %s %s failed: %s", method, url, exc)
        return None


def _list_v1_models(base_url: str, timeout: float = PROBE_TIMEOUT_S) -> Optional[list]:
    """``GET /v1/models`` -> the ``data`` list, or None if the server did not
    answer with an OpenAI-shaped model list."""
    body = _http_json("GET", f"{base_url.rstrip('/')}/models", timeout=timeout)
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        return body["data"]
    if isinstance(body, list):
        return body
    return None


def list_server_models(base_url: str) -> List[str]:
    """Model ids the server reports, in server order. [] when unreachable."""
    return _ids_from_entries(_list_v1_models(base_url) or [])


def _ids_from_entries(entries: list) -> List[str]:
    ids: List[str] = []
    for item in entries:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str) and mid and mid not in ids:
            ids.append(mid)
    return ids


# ---------------------------------------------------------------------------
# Server scan and identification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LocalServer:
    """One running local inference server, as an endpoint."""
    endpoint_id: str        # "local-dwarfstar"
    runtime: str            # key of RUNTIME_LABELS
    label: str              # "Local · DwarfStar (:8000)"
    base_url: str           # ".../v1"
    model_ids: tuple        # server order
    entries: tuple          # raw /v1/models entries, for per-model enrichment

    @property
    def root(self) -> str:
        return self.base_url[: -len("/v1")]

    @property
    def is_loopback(self) -> bool:
        return is_loopback_url(self.base_url)


def _identify_runtime(root: str, entries: list) -> str:
    """Name the runtime behind ``root``. Native APIs first (they cannot be
    confused with each other), then ``owned_by``, else generic."""
    tags = _http_json("GET", f"{root}/api/tags", timeout=SCAN_TIMEOUT_S)
    if isinstance(tags, dict) and isinstance(tags.get("models"), list):
        return "ollama"
    lms = _http_json("GET", f"{root}/api/v0/models", timeout=SCAN_TIMEOUT_S)
    if isinstance(lms, dict) and isinstance(lms.get("data"), list):
        return "lmstudio"
    for item in entries:
        if isinstance(item, dict):
            owner = str(item.get("owned_by") or "").strip().lower()
            if owner in _OWNED_BY_RUNTIME:
                return _OWNED_BY_RUNTIME[owner]
    return "openai-compatible"


def _port_of(url: str) -> Optional[int]:
    try:
        return urlparse(url).port
    except ValueError:
        return None


def _make_label(runtime: str, base_url: str) -> str:
    port = _port_of(base_url)
    where = f":{port}" if is_loopback_url(base_url) and port else (urlparse(base_url).netloc or base_url)
    if runtime == "openai-compatible":
        return f"Local · {where}"
    return f"Local · {RUNTIME_LABELS.get(runtime, runtime)} ({where})"


def _assign_ids(servers: List[dict]) -> List[LocalServer]:
    """``local-<runtime>``; on a runtime collision, or for a generic server,
    the port disambiguates (``local-ollama-11435``, ``local-8080``)."""
    by_runtime: Dict[str, int] = {}
    for s in servers:
        by_runtime[s["runtime"]] = by_runtime.get(s["runtime"], 0) + 1
    out: List[LocalServer] = []
    for s in servers:
        runtime, base = s["runtime"], s["base_url"]
        port = _port_of(base) or 0
        if runtime == "openai-compatible":
            eid = f"{LOCAL_PREFIX}{port}"
        elif by_runtime[runtime] > 1:
            eid = f"{LOCAL_PREFIX}{runtime}-{port}"
        else:
            eid = f"{LOCAL_PREFIX}{runtime}"
        out.append(LocalServer(
            endpoint_id=eid, runtime=runtime, label=_make_label(runtime, base),
            base_url=base, model_ids=tuple(s["ids"]), entries=tuple(s["entries"]),
        ))
    return out


def _dedupe_aliases(runtime: str, entries: list, ids: List[str]) -> List[str]:
    """DwarfStar lists compatibility aliases (``deepseek-v4-pro`` next to
    ``deepseek-v4-flash``) for the ONE loaded GGUF, distinguishable only by a
    shared ``name``. Keep the first id per name so the picker is truthful."""
    if runtime != "dwarfstar":
        return ids
    seen_names: set = set()
    kept: List[str] = []
    for item in entries:
        if not isinstance(item, dict) or item.get("id") not in ids:
            continue
        name = item.get("name") or item.get("id")
        if name in seen_names:
            continue
        seen_names.add(name)
        kept.append(item["id"])
    return kept or ids


_scan_cache: Optional[tuple] = None   # (List[LocalServer], monotonic time)


def scan_local_servers(refresh: bool = False) -> List[LocalServer]:
    """Every local server that answers ``GET /v1/models``: the well-known
    loopback ports plus ``ZIYA_LOCAL_MODEL_URL`` if set. Memoised; an empty
    result is retried after SCAN_MISS_TTL_S so a server started after Ziya
    is picked up without a restart."""
    global _scan_cache
    import time
    now = time.monotonic()
    if not refresh and _scan_cache is not None:
        found, when = _scan_cache
        if found or now - when < SCAN_MISS_TTL_S:
            return list(found)

    candidates: List[str] = []
    configured = configured_local_url()
    if configured:
        candidates.append(configured)
    for port in KNOWN_LOCAL_PORTS:
        url = f"http://127.0.0.1:{port}/v1"
        if url not in candidates and not (
            configured and _port_of(configured) == port and is_loopback_url(configured)
        ):
            candidates.append(url)

    raw: List[dict] = []
    for base in candidates:
        entries = _list_v1_models(base, timeout=SCAN_TIMEOUT_S)
        if entries is None:
            continue
        root = base[: -len("/v1")]
        runtime = _identify_runtime(root, entries)
        ids = _dedupe_aliases(runtime, entries, _ids_from_entries(entries))
        raw.append({"runtime": runtime, "base_url": base, "ids": ids, "entries": entries})

    servers = _assign_ids(raw)
    _scan_cache = (servers, now)
    if servers:
        logger.info("local: %d server(s): %s", len(servers),
                    "; ".join(f"{s.endpoint_id} {s.base_url} {list(s.model_ids)}" for s in servers))
    else:
        logger.info("local: no OpenAI-compatible server answered on ports %s%s",
                    list(KNOWN_LOCAL_PORTS), f" or at {configured}" if configured else "")
    return list(servers)


def find_local_server(refresh: bool = False) -> Optional[str]:
    """Root URL (no ``/v1``) of the first scanned server, or None."""
    servers = scan_local_servers(refresh=refresh)
    return servers[0].root if servers else None


def local_servers_by_id() -> Dict[str, LocalServer]:
    return {s.endpoint_id: s for s in scan_local_servers()}


def resolve_local_alias(endpoint: str) -> str:
    """``local`` -> a concrete ``local-<runtime>`` id.

    One server: that one. Several: ``ZIYA_LOCAL_MODEL_URL``'s server if set,
    else the first scanned, with the alternatives named in the log so the
    user can pass ``--endpoint <id>`` or switch in the model picker. None:
    the alias is returned unchanged and the first request fails with a
    connection error naming DEFAULT_LOCAL_URL.
    """
    if endpoint != LOCAL_ALIAS:
        return endpoint
    servers = scan_local_servers()
    if not servers:
        return endpoint
    if len(servers) == 1:
        return servers[0].endpoint_id
    configured = configured_local_url()
    if configured:
        for s in servers:
            if s.base_url == configured:
                return s.endpoint_id
    chosen = servers[0]
    logger.warning(
        "local: %d servers found; '--endpoint local' resolves to %s (%s). "
        "Alternatives: %s -- pass one as --endpoint, or switch in the model picker.",
        len(servers), chosen.endpoint_id, chosen.label,
        ", ".join(f"{s.endpoint_id} ({s.label})" for s in servers[1:]),
    )
    return chosen.endpoint_id


def local_endpoint_base_url(endpoint: str) -> str:
    """The ``/v1`` base URL for a local endpoint id (or the alias)."""
    endpoint = resolve_local_alias(endpoint)
    server = local_servers_by_id().get(endpoint)
    if server:
        return server.base_url
    try:
        from app.config.models_config import ENDPOINT_DEFAULTS
        url = ENDPOINT_DEFAULTS.get(endpoint, {}).get("base_url")
        if url:
            return url
    except ImportError:
        pass
    return configured_local_url() or _with_v1(DEFAULT_LOCAL_URL)


def local_endpoint_runtime(endpoint: str) -> str:
    endpoint = resolve_local_alias(endpoint)
    server = local_servers_by_id().get(endpoint)
    if server:
        return server.runtime
    try:
        from app.config.models_config import ENDPOINT_DEFAULTS
        return ENDPOINT_DEFAULTS.get(endpoint, {}).get("runtime", "openai-compatible")
    except ImportError:
        return "openai-compatible"


def local_endpoint_is_loopback(endpoint: str) -> bool:
    """Whether this local endpoint's server is on this machine. Fails closed
    (False) for an id the scan does not know."""
    endpoint = resolve_local_alias(endpoint)
    server = local_servers_by_id().get(endpoint)
    if server:
        return server.is_loopback
    if endpoint == LOCAL_ALIAS:
        return is_loopback_url(configured_local_url() or DEFAULT_LOCAL_URL)
    return False


# Backwards-compatible names used by earlier seams and tests.
def local_base_url() -> str:
    return local_endpoint_base_url(LOCAL_ALIAS)


def local_server_root() -> str:
    return local_base_url()[: -len("/v1")]


def local_server_is_loopback() -> bool:
    return local_endpoint_is_loopback(LOCAL_ALIAS)


# ---------------------------------------------------------------------------
# Per-model discovery
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveredModel:
    """What the running server says about one model."""
    runtime: str
    name: str
    context_length: Optional[int]        # None when the runtime does not report it
    supports_tools: Optional[bool]       # None = runtime does not say
    supports_vision: Optional[bool]
    supports_thinking: Optional[bool]
    loaded: Optional[bool] = None
    supports_reasoning_effort: Optional[bool] = None
    max_output_tokens: Optional[int] = None


# Keys under which /v1/models entries have been seen to carry the served
# context window (DwarfStar: context_length, observed; vLLM: max_model_len,
# documented). llama-server's meta.n_ctx_train is the TRAINING length and is
# deliberately not read. Checked top-level and one level down.
_CTX_KEYS = ("context_length", "context_window", "max_context_length",
             "max_model_len", "n_ctx", "ctx")


def _int_in(scopes: list, keys: tuple) -> Optional[int]:
    for scope in scopes:
        for key in keys:
            v = scope.get(key)
            if isinstance(v, int) and v > 0:
                return v
    return None


def model_from_v1_entry(runtime: str, item: dict) -> DiscoveredModel:
    """Everything a ``/v1/models`` entry can tell us. DwarfStar reports the
    served ``--ctx`` as ``context_length`` (verified against a 300k launch),
    ``top_provider.max_completion_tokens``, and ``supported_parameters``
    naming ``tools`` / ``reasoning_effort``."""
    scopes = [item] + [v for v in item.values() if isinstance(v, dict)]
    params = item.get("supported_parameters")
    tools: Optional[bool] = None
    effort: Optional[bool] = None
    if isinstance(params, list):
        tools = "tools" in params
        effort = "reasoning_effort" in params
    return DiscoveredModel(
        runtime=runtime, name=item.get("id", ""),
        context_length=_int_in(scopes, _CTX_KEYS),
        supports_tools=tools, supports_vision=None, supports_thinking=None,
        supports_reasoning_effort=effort,
        max_output_tokens=_int_in(scopes, ("max_completion_tokens", "max_output_tokens")),
    )


def _probe_v1_models(root: str, name: str, runtime: str = "openai-compatible") -> Optional[DiscoveredModel]:
    data = _list_v1_models(_with_v1(root))
    if data is None:
        return None
    for item in data:
        if isinstance(item, dict) and item.get("id") == name:
            return model_from_v1_entry(runtime, item)
    return None


def _probe_ollama(root: str, name: str) -> Optional[DiscoveredModel]:
    """``POST /api/show`` -- context length under ``model_info.<arch>.context_length``,
    capabilities as a list (``completion``, ``tools``, ``vision``, ``thinking``)."""
    info = _http_json("POST", f"{root}/api/show", {"model": name})
    if not isinstance(info, dict) or ("model_info" not in info and "capabilities" not in info):
        return None
    model_info = info.get("model_info") or {}
    ctx: Optional[int] = None
    arch = model_info.get("general.architecture")
    if arch and isinstance(model_info.get(f"{arch}.context_length"), int):
        ctx = model_info[f"{arch}.context_length"]
    else:
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                ctx = value
                break
    caps = info.get("capabilities")
    if isinstance(caps, list):
        return DiscoveredModel(
            runtime="ollama", name=name, context_length=ctx,
            supports_tools="tools" in caps, supports_vision="vision" in caps,
            supports_thinking="thinking" in caps,
        )
    return DiscoveredModel(runtime="ollama", name=name, context_length=ctx,
                           supports_tools=None, supports_vision=None, supports_thinking=None)


def _probe_lmstudio(root: str, name: str) -> Optional[DiscoveredModel]:
    """``GET /api/v0/models/{name}`` -- ``max_context_length``, ``state``, ``type``."""
    info = _http_json("GET", f"{root}/api/v0/models/{name}")
    if not isinstance(info, dict) or "max_context_length" not in info:
        return None
    ctx = info.get("max_context_length")
    return DiscoveredModel(
        runtime="lmstudio", name=name,
        context_length=ctx if isinstance(ctx, int) else None,
        supports_tools=None,
        supports_vision=(info.get("type") == "vlm") or None,
        supports_thinking=None,
        loaded=(info.get("state") == "loaded") if "state" in info else None,
    )


def discover_local_model(name: str, root: str, runtime: Optional[str] = None) -> Optional[DiscoveredModel]:
    """Ask the server at ``root`` about ``name``, using its native API when
    the runtime has one and the ``/v1/models`` entry otherwise."""
    forced = (os.environ.get("ZIYA_LOCAL_RUNTIME") or "").strip().lower()
    runtime = forced or runtime
    if runtime == "ollama":
        return _probe_ollama(root, name) or _probe_v1_models(root, name, "ollama")
    if runtime == "lmstudio":
        return _probe_lmstudio(root, name) or _probe_v1_models(root, name, "lmstudio")
    if runtime:
        return _probe_v1_models(root, name, runtime)
    return _probe_ollama(root, name) or _probe_lmstudio(root, name) or _probe_v1_models(root, name)


# ---------------------------------------------------------------------------
# Config synthesis and in-place update
# ---------------------------------------------------------------------------

def _output_caps(token_limit: int) -> tuple:
    max_out = max(1024, min(MAX_OUTPUT_CEILING, token_limit // 2))
    default_out = min(DEFAULT_OUTPUT_CEILING, max_out)
    return max_out, default_out


def synthesize_local_model_entry(name: str) -> Dict[str, Any]:
    """Pre-discovery MODEL_CONFIGS entry; every field is replaced by
    ``apply_discovery`` before the model is used."""
    token_limit = UNPROBED_LOCAL_TOKEN_LIMIT
    cap = local_token_cap()
    if cap:
        token_limit = min(token_limit, cap)
    max_out, default_out = _output_caps(token_limit)
    return {
        "model_id": name,
        "family": "local",
        "token_limit": token_limit,
        "max_output_tokens": max_out,
        "default_max_output_tokens": default_out,
        "native_function_calling": True,
    }


# Names this module put into MODEL_CONFIGS (vs. user entries from
# ~/.ziya/models.json, which are never removed), keyed by endpoint id.
_synthesized: Dict[str, set] = {}


def build_local_model_configs() -> Dict[str, Dict[str, Any]]:
    """The import-time ``MODEL_CONFIGS["local"]`` table: one placeholder so
    the alias endpoint is valid before any scan has run. No network."""
    name = local_model_name()
    _synthesized.setdefault(LOCAL_ALIAS, set()).add(name)
    return {name: synthesize_local_model_entry(name)}


def apply_discovery(entry: Dict[str, Any], found: DiscoveredModel) -> Dict[str, Any]:
    """Update one MODEL_CONFIGS entry in place from a discovery result. Pure
    with respect to I/O so the mapping is testable with a hand-built
    DiscoveredModel."""
    if found.context_length:
        limit = found.context_length
        cap = local_token_cap()
        if cap and cap < limit:
            logger.info("local model %s: context %d capped to %d by ZIYA_LOCAL_TOKEN_LIMIT",
                        found.name, limit, cap)
            limit = cap
        entry["token_limit"] = limit
        max_out, default_out = _output_caps(limit)
        if found.max_output_tokens:
            max_out = min(max_out, found.max_output_tokens)
            default_out = min(default_out, max_out)
        entry["max_output_tokens"] = max_out
        entry["default_max_output_tokens"] = default_out
        if found.runtime in _RUNTIMES_TAKING_NUM_CTX:
            entry["request_extra_body"] = {"options": {"num_ctx": limit}}
        if limit < USABLE_CONTEXT_FLOOR:
            logger.warning(
                "local model %s has a %d-token context window; Ziya's builtin "
                "tool schemas alone need ~4k tokens, so expect truncation.",
                found.name, limit,
            )
    if found.runtime == "ollama":
        # Ollama forwards a tool call the model emits without its template's
        # <tool_call> wrapper as plain text (observed: qwen2.5-coder:7b).
        # OpenAIDirectProvider recovers such calls only for models carrying
        # this flag; servers that parse their own templates (DwarfStar,
        # vLLM, LM Studio) never leak them and are left alone.
        entry["recover_text_tool_calls"] = True
    if found.supports_tools is not None:
        entry["native_function_calling"] = found.supports_tools
        if not found.supports_tools:
            logger.warning(
                "local model %s does not support tool calling per the server; "
                "Ziya's agent loop (file tools, AST, shell) will be unavailable.",
                found.name,
            )
    if found.supports_vision is not None:
        entry["supports_vision"] = found.supports_vision
    if found.supports_thinking is not None:
        entry["supports_thinking"] = found.supports_thinking
    if found.supports_reasoning_effort is not None:
        entry["supports_reasoning_effort"] = found.supports_reasoning_effort
        if found.supports_reasoning_effort:
            # DwarfStar: thinking on by default, effort low..xhigh, `max` =
            # Think Max (falls back to normal thinking when ctx is short).
            entry.setdefault("supports_thinking", True)
            entry.setdefault("supported_efforts", ["low", "medium", "high", "xhigh", "max"])
            entry.setdefault("thinking_effort_default", "medium")
            # A server that advertises reasoning_effort streams reasoning on
            # delta.reasoning_content, and the DeepSeek family REQUIRES that
            # text echoed back on assistant history when tools are present
            # (ds4 CLIENTS.md requiresReasoningContentOnAssistantMessages) or
            # it re-renders the whole prefix each turn. Never a vanilla-OpenAI
            # server on a local endpoint, so the flag is safe to set here.
            entry.setdefault("replay_reasoning_content", True)
    return entry


_discovery_cache: Dict[tuple, Optional[DiscoveredModel]] = {}


def discover_and_apply(name: Optional[str] = None, *, endpoint: Optional[str] = None,
                       refresh: bool = False) -> Optional[DiscoveredModel]:
    """Probe the server behind ``endpoint`` for ``name`` (memoised) and update
    ``MODEL_CONFIGS[endpoint][name]`` in place. Safe when the server is down:
    the placeholder stays and the first request fails with the real error."""
    from app.config.models_config import MODEL_CONFIGS  # lazy: avoid import cycle

    endpoint = resolve_local_alias(endpoint or LOCAL_ALIAS)
    name = name or local_model_name()
    root = local_endpoint_base_url(endpoint)[: -len("/v1")]
    runtime = local_endpoint_runtime(endpoint)
    key = (root, name)
    if refresh or key not in _discovery_cache:
        found = discover_local_model(name, root, runtime)
        _discovery_cache[key] = found
        if found is None:
            logger.info("local model %s at %s: no metadata answered; placeholder context "
                        "of %d tokens applies (ZIYA_LOCAL_TOKEN_LIMIT / models.json override)",
                        name, root, UNPROBED_LOCAL_TOKEN_LIMIT)
        else:
            logger.info("local model %s via %s: context_length=%s tools=%s vision=%s "
                        "thinking=%s reasoning_effort=%s%s",
                        name, found.runtime, found.context_length, found.supports_tools,
                        found.supports_vision, found.supports_thinking,
                        found.supports_reasoning_effort,
                        "" if found.loaded is None else f" loaded={found.loaded}")
    found = _discovery_cache[key]
    table = MODEL_CONFIGS.setdefault(endpoint, {})
    entry = table.setdefault(name, synthesize_local_model_entry(name))
    if found is not None:
        apply_discovery(entry, found)
    return found


# ---------------------------------------------------------------------------
# Endpoint registration: one endpoint per server
# ---------------------------------------------------------------------------

def choose_default_model(server_ids: list) -> str:
    """Default model for one server: ZIYA_LOCAL_MODEL if the server has it;
    a single-model server's one model; the reference model if present; else
    the first."""
    explicit = (os.environ.get("ZIYA_LOCAL_MODEL") or "").strip()
    if explicit and explicit in server_ids:
        return explicit
    if not server_ids:
        return explicit or DEFAULT_LOCAL_MODEL
    if len(server_ids) == 1:
        return server_ids[0]
    if DEFAULT_LOCAL_MODEL in server_ids:
        return DEFAULT_LOCAL_MODEL
    return server_ids[0]


def _endpoint_defaults_for(server: LocalServer) -> Dict[str, Any]:
    from app.config.models_config import ENDPOINT_DEFAULTS
    base = dict(ENDPOINT_DEFAULTS.get(LOCAL_ALIAS, {}))
    base.update({"base_url": server.base_url, "runtime": server.runtime, "label": server.label})
    return base


_registered_for: Optional[tuple] = None


def register_local_endpoints(*, refresh: bool = False) -> List[str]:
    """Make every scanned server a Ziya endpoint and return their ids.

    For each server: ``ENDPOINT_DEFAULTS[id]`` (local defaults + base_url,
    runtime, label), ``MODEL_CONFIGS[id]`` (its ``/v1/models``, each entry
    enriched by discovery), ``DEFAULT_MODELS[id]`` and
    ``DEFAULT_SERVICE_MODELS[id]``. Endpoints this module registered earlier
    whose server no longer answers are removed -- except the one currently
    in use, which must not vanish under a running conversation. The
    ``local`` alias table is left in place; consumers listing endpoints hide
    it when concrete ids exist (see local_endpoint_ids_for_listing).
    """
    global _registered_for
    from app.config import models_config as mc

    servers = scan_local_servers(refresh=refresh)
    signature = tuple((s.endpoint_id, s.base_url, s.model_ids) for s in servers)
    if not refresh and _registered_for == signature:
        return [s.endpoint_id for s in servers]

    live = {s.endpoint_id for s in servers}
    active = os.environ.get("ZIYA_ENDPOINT")
    for eid in [e for e in mc.MODEL_CONFIGS if e.startswith(LOCAL_PREFIX)]:
        if eid not in live and eid != active:
            for name in _synthesized.pop(eid, set()):
                mc.MODEL_CONFIGS.get(eid, {}).pop(name, None)
            if not mc.MODEL_CONFIGS.get(eid):
                mc.MODEL_CONFIGS.pop(eid, None)
                mc.ENDPOINT_DEFAULTS.pop(eid, None)
                mc.DEFAULT_MODELS.pop(eid, None)
                mc.DEFAULT_SERVICE_MODELS.pop(eid, None)

    for s in servers:
        mc.ENDPOINT_DEFAULTS[s.endpoint_id] = _endpoint_defaults_for(s)
        table = mc.MODEL_CONFIGS.setdefault(s.endpoint_id, {})
        mine = _synthesized.setdefault(s.endpoint_id, set())
        for stale in list(mine - set(s.model_ids)):
            table.pop(stale, None)
            mine.discard(stale)
        for mid in s.model_ids:
            if mid not in table:
                table[mid] = synthesize_local_model_entry(mid)
                mine.add(mid)
        default = choose_default_model(list(s.model_ids))
        mc.DEFAULT_MODELS[s.endpoint_id] = default
        mc.DEFAULT_SERVICE_MODELS[s.endpoint_id] = default
        # Enrich from the /v1/models entry first (free), then the runtime's
        # native API where it has one.
        for item in s.entries:
            if isinstance(item, dict) and item.get("id") in table:
                apply_discovery(table[item["id"]], model_from_v1_entry(s.runtime, item))
        if s.runtime in ("ollama", "lmstudio"):
            for mid in s.model_ids:
                discover_and_apply(mid, endpoint=s.endpoint_id)

    # Keep the alias table pointing at the resolved server's default so a
    # caller that still asks about "local" gets a real answer.
    if servers:
        resolved = resolve_local_alias(LOCAL_ALIAS)
        mc.DEFAULT_MODELS[LOCAL_ALIAS] = mc.DEFAULT_MODELS.get(resolved, mc.DEFAULT_MODELS.get(LOCAL_ALIAS))
        mc.DEFAULT_SERVICE_MODELS[LOCAL_ALIAS] = mc.DEFAULT_MODELS[LOCAL_ALIAS]

    _registered_for = signature
    return [s.endpoint_id for s in servers]


def local_endpoint_ids_for_listing() -> List[str]:
    """Endpoint ids a picker should show: the concrete ones when any server
    answered, else the bare alias (so ``local`` is visible as unavailable)."""
    ids = register_local_endpoints()
    return ids if ids else [LOCAL_ALIAS]


def local_endpoint_label(endpoint: str) -> Optional[str]:
    server = local_servers_by_id().get(endpoint)
    if server:
        return server.label
    if endpoint == LOCAL_ALIAS:
        return "Local"
    return None


def sync_local_catalog(*, refresh: bool = False) -> str:
    """Register every server and return the default model of the endpoint
    ``local`` resolves to. Retained as the name earlier seams call."""
    from app.config import models_config as mc
    register_local_endpoints(refresh=refresh)
    resolved = resolve_local_alias(LOCAL_ALIAS)
    return mc.DEFAULT_MODELS.get(resolved) or mc.DEFAULT_MODELS.get(LOCAL_ALIAS) or local_model_name()


def reset_discovery_cache_for_tests() -> None:
    global _scan_cache, _registered_for
    _discovery_cache.clear()
    _scan_cache = None
    _registered_for = None


def openai_sdk_missing_message(endpoint: str) -> Optional[str]:
    """Actionable error if the ``openai`` package -- required by every
    OpenAI-compatible endpoint (openai, zai, meta, local*) -- is not
    importable; None when it is."""
    try:
        import openai  # noqa: F401
        return None
    except ImportError:
        return (
            f"Endpoint '{endpoint}' needs the 'openai' Python package, which is "
            f"not installed in this environment.\n"
            f"   Install it with:  pip install openai\n"
            f"   (or reinstall Ziya; recent builds declare it as a dependency)"
        )
