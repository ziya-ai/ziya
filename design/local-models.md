# Local Models: the `local` endpoint

Status: **one endpoint per running server** (2026-09-09), superseding the
single `local` endpoint of 2026-09-07. Decision 2026-09-07: local models get
their own endpoint(s) rather than riding `openai` via `OPENAI_BASE_URL`.
Decision 2026-09-09 (after the first live run found Ollama AND DwarfStar on
the same Mac and had to guess): every server that answers on a well-known
loopback port — or at `ZIYA_LOCAL_MODEL_URL` — is registered at startup as
its own endpoint, `local-<runtime>` (`local-dwarfstar`, `local-ollama`;
port-suffixed only on collision, port-only for an unidentified runtime),
with that server's `/v1/models` as its catalog and a label for the picker.
Choosing a model chooses the server. `local` survives as an alias that
resolves to the sole server, or to `ZIYA_LOCAL_MODEL_URL`'s, or to the first
with the alternatives logged.

Runtime identification, in order: Ollama `/api/tags`, LM Studio
`/api/v0/models`, then `owned_by` in the `/v1/models` entry (`ds4.c` →
DwarfStar and `library` → Ollama are *observed*; `vllm`, `llamacpp`,
`lmstudio`, `sglang`, `localai` are from docs and unverified), else
`openai-compatible`. DwarfStar's entry also carries the served `--ctx` as
`context_length` (verified against a 300k launch), `max_completion_tokens`,
and `supported_parameters` naming `tools` / `reasoning_effort`, so its
capabilities need no native probe. The "credential" for a local endpoint is
reachability: `detect_available_providers` marks each scanned server
available; `ZIYA_LOCAL_MODEL_URL` only *adds* a server. The enterprise
loopback exemption is per endpoint (a LAN server stays restricted).

Implementation: `app/utils/local_models.py` (`scan_local_servers`,
`register_local_endpoints`, `resolve_local_alias`, `is_local_endpoint`,
`local_endpoint_base_url`), with `is_local_endpoint()` replacing every
`== "local"` at the seams. Tests: `tests/test_local_catalog_sync.py` (fake
ds4 + Ollama servers), `tests/test_local_provider_routing.py`,
`tests/test_local_endpoint_policy_exemption.py`.

## Why a new endpoint

Today "local" = `openai` + `OPENAI_BASE_URL`. That works on the wire but
lies everywhere else: the picker shows the static OpenAI catalog, every
local model needs a hand-written `~/.ziya/models.json` entry, capabilities
are whatever the entry claims, and `pricing.py:283` / `pdf_exporter.py:570`
already name an `ollama` provider that has no endpoint behind it.
`Docs/ArchitectureOverview.md:705` documents `ZIYA_LOCAL_MODEL_URL` for
memory extraction, but `model_resolver.py:199` has no local branch — the
row is aspirational.

## What is runtime-specific vs shared

Ollama, LM Studio, llama.cpp `llama-server`, vLLM and `mlx_lm.server` all
speak OpenAI `/v1/chat/completions` (streaming, tool calls). So the
**transport is shared** and `OpenAIDirectProvider` / `DirectOpenAIModel`
are reused unchanged, exactly as `zai` and `meta` do.

What differs per runtime is **discovery and metadata**:

| Runtime | Default URL | Model list | Capabilities / ctx len | Load state |
|---|---|---|---|---|
| Ollama | `:11434` | `GET /api/tags` (or `/v1/models`) | `POST /api/show` → `capabilities` (`tools`, `vision`, `thinking`), `model_info.*.context_length` | loads on demand |
| LM Studio | `:1234` | `GET /api/v0/models` | same response: `max_context_length`, `loaded_context_length`, `type` | `state: loaded|not-loaded` |
| llama-server | `:8080` | `GET /v1/models` | `GET /props` (ctx len, chat template) | single model, always loaded |
| generic | any | `GET /v1/models` | none — fall back to endpoint defaults | unknown |

Reference runtime for development and CI: **Ollama** (headless, Linux,
richest metadata, `brew install ollama`). LM Studio second (Apple Silicon
MLX performance story; must handle "not loaded"). Generic `/v1/models`
adapter covers everything else.

Dev models: `qwen2.5-coder:7b` (tool-capable, the reference),
`llama3.2:1b` (CI / degraded path), plus one deliberately non-tool model to
exercise what the agent loop does when tools are unavailable.

## Seams a new endpoint crosses

Precedent: `meta` (added as a pure OpenAI-compatible endpoint). Every hop
below has a `"meta"` occurrence to copy from; tests in `tests/` pin most of
these sets, and each will fail until `local` is added — that is the seam
test, not a nuisance.

```
app/config/models_config.py    ENDPOINT_DEFAULTS["local"], MODEL_CONFIGS["local"]
app/utils/provider_detection.py PROVIDER_CREDENTIALS entry  (see "credential" below)
app/providers/factory.py        _SUPPORTED_ENDPOINTS + create_provider branch
app/agents/models.py            dispatch at ~660 + _initialize_local_model
app/services/model_resolver.py  ep in (...,"local") → _call_openai_compatible,
                                DEFAULT_SERVICE_MODELS["local"]
app/config/common_args.py       endpoint_help_choices
app/config/pricing.py           rename provider "ollama" → "local" (zero-cost rule)
app/services/pdf_exporter.py    "local": "Local" label
app/routes/model_routes.py      /api/available-models, /api/endpoints,
                                /api/model-capabilities must read the
                                DISCOVERED list, not the static dict
Docs/ArchitectureOverview.md    fix the ZIYA_LOCAL_MODEL_URL row
```

### The two places `local` is NOT like `meta`

1. **Model list is dynamic.** `MODEL_CONFIGS` is a static dict populated at
   import. For `local`, entries are *synthesized at startup* (and on a
   refresh) from the runtime adapter: one entry per discovered model with
   `model_id`, `token_limit` (from ctx len), `native_function_calling`,
   `supports_vision`, `supports_thinking`, `family` mapped from the
   runtime's family string. Unknown → conservative defaults from
   `ENDPOINT_DEFAULTS["local"]` (8k ctx, no tools) so nothing over-promises.
   `~/.ziya/models.json` `"local"` entries still merge on top, so a user can
   override a wrong guess.

2. **"Credential" is reachability, not an env var.** `ProviderCredential`
   models a key. For `local` the availability check is "does
   `ZIYA_LOCAL_MODEL_URL` (default `http://localhost:11434`) answer
   `/v1/models`". `detect_available_providers` and first-run auto-select
   must use that probe, and `local` should be autoselectable: a MacBook
   with Ollama running and no cloud keys should land on it with zero
   configuration. This needs either a probe hook on `ProviderCredential`
   or a parallel declaration like bedrock's file-based check — decide when
   touching `provider_detection.py`, not before.

## Runtime adapter interface

```python
class LocalRuntimeAdapter(Protocol):
    name: str                              # "ollama" | "lmstudio" | "llama_cpp" | "generic"
    def detect(self, base_url) -> bool     # cheap probe; identifies runtime
    def list_models(self, base_url) -> list[DiscoveredModel]
    # DiscoveredModel: id, family, context_length, supports_tools,
    #                  supports_vision, supports_thinking, loaded: bool|None
```

Detection order: Ollama (`/api/tags`), LM Studio (`/api/v0/models`),
llama-server (`/props`), generic (`/v1/models`). Runtime can be forced with
`ZIYA_LOCAL_RUNTIME`. Base URL from `ZIYA_LOCAL_MODEL_URL`; the adapter
derives the OpenAI-compatible `/v1` path itself so the user sets one URL.

## Phases

0. **Endpoint exists.** ✅ Done. `local` at every seam; one entry
   synthesized from `ZIYA_LOCAL_MODEL` at import (no network at import).
   **Context is discovered, not configured**: `discover_and_apply()` runs at
   model init (ModelManager, provider factory, service-model resolver),
   asks Ollama `/api/show` / LM Studio `/api/v0/models/{name}` for the
   model's context length and capabilities, writes them onto the
   `MODEL_CONFIGS["local"]` entry, and — Ollama only — sets
   `request_extra_body = {"options": {"num_ctx": <ctx>}}`, which
   `OpenAIDirectProvider._build_request` / `DirectOpenAIModel` merge into
   every request. Ziya's budget and the server's KV allocation are the same
   number, the model's full window. `ZIYA_LOCAL_TOKEN_LIMIT` survives only
   as an optional ceiling (unset by default) for machines where the full
   window's KV cache does not fit. Rationale: Ollama's default `num_ctx` is
   4k on any machine under 24 GiB VRAM — every MacBook — and Ziya's builtin
   tool schemas alone are ~4k tokens, so the previous 8192 placeholder was
   over budget before the first message. Live check still owed against a
   running Ollama.
1. **One endpoint per server; catalog from `/v1/models`.** ✅ Done
   2026-09-09 (see Status). Still owed: CI test against a real
   `llama3.2:1b` on Linux, and the live DS4 run end to end.
1b. **Tool surface must fit local context.** Live measurement on the DS4 Mac:
   `tools: 122,370` tokens — 40 `builder-mcp` tools with 4–5k-char
   descriptions — exceeds ds4 `--ctx 100000` before the first message, and
   the 39 builtins alone are ~4k. Needs: per-endpoint MCP server disable
   (builder-mcp off for `local-*` by default) and a trimmed builtin set for
   local endpoints (file I/O, AST, shell; the rest opt-in) in
   `builtin_tools.py`. Promoted from polish to phase 1.
1c. **Reasoning replay on the OpenAI-compatible provider.** DS4's client
   guide requires `reasoning_content` on assistant history messages;
   `OpenAIDirectProvider` parses reasoning deltas but never stores or
   replays them, so every tool-loop turn forces a prefix rebuild and
   defeats ds4's disk KV cache. Provider-level; also affects GLM via z.ai.
1d. **Single-slot servers.** Service-model calls (memory extraction, intent
   judge) route to the same local endpoint; on `ds4-server` without
   `--batched-session` they queue behind the chat and evict its KV prefix.
   Route elsewhere for local, or document `--batched-session 2`.
2. **LM Studio adapter.** Loaded/not-loaded state surfaced in the picker;
   clear error when a not-loaded model is selected.
3. **Generic + llama-server adapters.** Cover the compile-it-yourself cohort.
4. **UX polish for the MacBook story.** Startup banner naming the detected
   runtime and model count; degraded-mode messaging when the chosen model
   lacks tool calling (agent loop, fabrication detection, beads all assume
   it); zero-cost accounting via the renamed pricing rule; README quick
   start for "no cloud account at all".

## Open questions

- Non-tool models: refuse tools, or run a text-only mode? The harness
  currently assumes tool calling exists.
- Token counting: local models have their own tokenizers; the current
  estimator may be badly off for context budgeting on small ctx windows.
- ~~Ollama's default `num_ctx`~~ Resolved in phase 0: `/v1` accepts
  `options.num_ctx` per request; Ziya sends the discovered context length.
- Memory vs. window: a 7B model's KV cache at 128k is several GB. Ollama
  partially offloads rather than failing, so the symptom is swapping, not a
  crash. We deliberately use the full window and leave
  `ZIYA_LOCAL_TOKEN_LIMIT` as the escape hatch; a smarter default would read
  the machine's memory and the model's KV-per-token from `/api/show`.
- `~/.ziya/models.json` `"local"` overrides are applied at import and then
  overwritten by discovery for the fields discovery reports. A user who
  wants to pin `token_limit` below the server's value should use
  `ZIYA_LOCAL_TOKEN_LIMIT`; pinning capabilities via `models.json` needs a
  "user-set wins" rule that does not exist yet.
