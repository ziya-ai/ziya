# Environment Variables

All Ziya configuration environment variables use the `ZIYA_` prefix. This document is the canonical reference — every `ZIYA_*` variable used in the codebase is declared in `app/config/env_registry.py`.

---

## Core

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_MODE` | str | `server` | — | Execution mode: `server`, `chat`, or `debug` |
| `ZIYA_EDITION` | str | `Community Edition` | — | Edition label shown in UI and `--version` output |
| `ZIYA_HOME` | str | `~/.ziya` | — | Root directory for user data (sessions, caches, projects) |
| `ZIYA_PORT` | int | `6969` | `--port` | Port for the web server |
| `ZIYA_THEME` | str | `light` | — | UI theme: `light` or `dark` |

## Paths

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_USER_CODEBASE_DIR` | str | cwd | `--root` | Absolute path to the project root directory |
| `ZIYA_TEMPLATES_DIR` | str | (auto) | — | Path to HTML templates directory (set automatically) |
| `ZIYA_INCLUDE_DIRS` | str | `""` | `--include` | Comma-separated external paths to include in the file tree |
| `ZIYA_INCLUDE_ONLY_DIRS` | str | `""` | `--include-only` | Comma-separated paths; only these directories are shown |
| `ZIYA_ADDITIONAL_EXCLUDE_DIRS` | str | `""` | `--exclude` | Comma-separated directories to exclude from scanning |

## Model / Endpoint
## Network (corporate proxies)

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_PROXY` | str | — | `--proxy` | HTTP(S) proxy URL for outbound AI-provider traffic. Fans out to `HTTPS_PROXY`/`HTTP_PROXY` at startup so botocore (Bedrock) and httpx (Anthropic/OpenAI SDKs) route through it |
| `ZIYA_CA_BUNDLE` | str | — | `--ca-bundle` | Path to a PEM CA bundle to trust for outbound TLS. Fans out to `AWS_CA_BUNDLE` (botocore), `SSL_CERT_FILE` (httpx), and `REQUESTS_CA_BUNDLE` (requests) |

Behind a TLS-intercepting egress proxy (Netskope, Zscaler, etc.) you typically need **both**:



| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_ENDPOINT` | str | `bedrock` | `--endpoint` | Model provider: `bedrock`, `google`, `openai`, `anthropic`, `zai`, `meta`, or `local` |
| `ZIYA_MODEL` | str | — | `--model` | Model alias (e.g. `sonnet4.0`, `gemini-3.1-pro`) |
| `ZIYA_MODEL_ID_OVERRIDE` | str | — | `--model-id` | Override the resolved model ID directly (advanced) |
| `ZIYA_LOCAL_MODEL_URL` | str | (scanned) | — | **Adds** a local OpenAI-compatible server the port scan cannot see (another port, another host). Unset: the loopback ports `:11434` (Ollama), `:8000` (DwarfStar), `:1234` (LM Studio), `:8080` (llama-server) are probed and **each** answering server becomes an endpoint (`local-ollama`, `local-dwarfstar`, …; runtime from `/api/tags`, `/api/v0/models`, or the `owned_by` field of `/v1/models`). `/v1` is appended if missing. With several servers, `--endpoint local` resolves to this URL's server when set |
| `ZIYA_LOCAL_MODEL` | str | (from server) | — | Default model on each local endpoint when its server lists it. Unset: a single-model server's one model, else `qwen2.5-coder:7b` if present, else the first listed |
| `ZIYA_LOCAL_TOKEN_LIMIT` | int | — | — | Optional **ceiling** on the local model's context window. By default Ziya reads the model's full context length from the server (Ollama `/api/show`, LM Studio `/api/v0/models`) and uses all of it, sending Ollama `options.num_ctx` to match so the server allocates what Ziya budgets. Set this only when the full window's KV cache does not fit in memory; it never raises the limit above what the server reports |
| `ZIYA_LOCAL_RUNTIME` | str | — | — | Force the runtime used for metadata discovery: `ollama` or `lmstudio`. Auto-detected when unset |

### Provider API keys

Provider credentials are read directly from the environment (not the `ZIYA_*` namespace):

| Variable | Used by | Notes |
|---|---|---|
| `ZAI_API_KEY` / `ZHIPUAI_API_KEY` | `--endpoint zai` | z.ai (Zhipu / GLM) API key |
| `ZAI_BASE_URL` | `--endpoint zai` | Override the z.ai base URL. Default `https://api.z.ai/api/paas/v4` (pay-as-you-go). Use `https://api.z.ai/api/coding/paas/v4` for a Coding Plan subscription. GLM thinking mode is supported — enable it with `ZIYA_THINKING_MODE=true` (optionally `ZIYA_THINKING_EFFORT`); reasoning streams into the collapsible thinking UI |

## Model Parameters

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_TEMPERATURE` | float | — | `--temperature` | Sampling temperature (0.0–1.0 for most models) |
| `ZIYA_TOP_K` | int | — | `--top-k` | Top-k sampling (0–500, Claude only) |
| `ZIYA_TOP_P` | float | — | `--top-p` | Top-p / nucleus sampling |
| `ZIYA_MAX_OUTPUT_TOKENS` | int | — | `--max-output-tokens` | Maximum tokens the model may generate per response |
| `ZIYA_MAX_TOKENS` | int | — | — | **Deprecated.** Use `ZIYA_MAX_OUTPUT_TOKENS` instead |
| `ZIYA_MAX_INPUT_TOKENS` | int | — | — | Maximum input tokens (set via frontend settings panel) |
| `ZIYA_THINKING_MODE` | bool | `false` | — | Enable thinking/chain-of-thought mode |
| `ZIYA_THINKING_LEVEL` | str | — | `--thinking-level` | Thinking level for Gemini 3: `low`, `medium`, `high` |
| `ZIYA_THINKING_EFFORT` | str | — | — | Thinking effort for Claude 4.6+ (adaptive) and OpenAI-compatible reasoning models incl. z.ai GLM (`reasoning_effort`): `low`, `medium`, `high`, `xhigh`, `max` |
| `ZIYA_THINKING_BUDGET` | int | `16000` | — | Token budget for extended thinking (Bedrock streaming) |

## AWS

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_AWS_PROFILE` | str | — | `--profile` | AWS credential profile name for Bedrock |
| `ZIYA_RESET_BEDROCK_RETENTION` | `1` | unset | — | Reset the Bedrock **account-wide** data retention mode to `inherit` at startup when the selected model declares no retention requirement. Off by default — see below. |
| `ZIYA_TASK_SERVICE_TIER` | `flex` \| `priority` \| `default` \| `off` | `flex` | — | Bedrock service tier requested for every model call made **inside a Task Card run** (API launch, scheduled fire, `ziya` CLI card runner — including `Call`ed cards and the Until/Improve evaluators). Interactive chat is never affected. `default`/`off` sends no tier (standard billing). See below. |

> Standard `AWS_REGION` and `AWS_PROFILE` are also respected. `ZIYA_AWS_PROFILE` takes precedence over `AWS_PROFILE` when both are set.

### Bedrock service tier for Task Card runs

Bedrock prices on-demand inference in tiers: `priority` (premium, lower latency), `default` (standard) and `flex` (discounted, may queue under load, shares the standard on-demand quota). Task Card runs are the batch-like workload Flex is priced for, so a run requests `flex` by default on every `bedrock-runtime` call — `invoke_model_with_response_stream` (Claude, OpenAI-format models) and `converse_stream` (Nova, DeepSeek, Qwen, MiniMax, gpt-oss). The response's resolved tier is logged at INFO (`service tier requested=flex resolved=...`) so a silently ignored request is visible rather than an assumed discount.

Tier support is **per model**, not per account. When a model rejects the tier (`ValidationException: The provided service tier is not supported for this model`), Ziya retries that request once at the standard tier and remembers the rejection for the rest of the process, so a run on an unsupported model pays one extra round-trip, not one per call. Any other `ValidationException` surfaces unchanged. A restart re-probes, which is what you want when AWS enables a tier for a model that previously rejected it.

Observed on 2026-09-04 (subject to change by AWS): Flex is accepted for gpt-oss, DeepSeek v3.2, Qwen3 and MiniMax on `bedrock-runtime`; Claude, Nova and Llama reject it. The `bedrock-mantle` gateway (Fable 5, Mythos 5, GPT-5.6) parses `service_tier` but accepts only `default`/`auto`, so mantle providers do not send a tier at all.

Set `ZIYA_TASK_SERVICE_TIER=default` (or `off`) to disable, or `priority` for latency-sensitive runs at the premium rate. An unrecognised value disables the feature and logs a warning at run start.

The environment variable sets the tier for the whole run. A card can pin one block differently with `scope.service_tier` (`flex` | `default` | `priority`) — for example `default` on a gating Task the rest of the run waits on, so it is not queued behind Flex traffic, or `flex` to opt a bulk Task back in when the run has the tier disabled. Like `model_tier`, it applies to the block's whole subtree and the most specific (innermost) value wins.

### Bedrock data retention and Covered Models

Some Bedrock models are gated on an **account-wide** AWS data retention mode. Anthropic's Claude Fable 5 and Fable 5.1 are *Covered Models* and require `aws_review` or higher; requests are blocked with a `ValidationException` until the account is set accordingly. Ziya raises the mode automatically at startup when you select such a model, and prints the reason and the required IAM action (`bedrock:PutAccountDataRetention`) if it cannot.

What `aws_review` means, per [AWS's data retention documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html):

- AWS retains prompts and completions for **up to 30 days** and may review them by hand.
- Review happens **inside the AWS boundary**. Content is **not** shared with the model provider.
- Only models whose provider requires human review are affected. A model whose `allowed_modes` include `none` is not retained regardless of the account setting, so raising the mode does not start retaining your other traffic.
- With cross-region inference, retained data is stored in the region that processed the request.

The modes form an ordered scale — `none < default < aws_review < provider_data_share` — and a model is invocable when the effective mode is at or above what it requires. Ziya never lowers the mode to satisfy a requirement: an account already on the legacy `provider_data_share` is left alone, because downgrading a shared account-wide switch would break other models and other users of the account.

Two consequences worth knowing on a **shared AWS account**:

- Selecting a Covered Model changes a setting that applies to the whole account, not just your session.
- Ziya no longer resets the mode back to `inherit` when you switch to a model that needs nothing, because doing so would break an in-flight Covered Model session elsewhere on the account. Set `ZIYA_RESET_BEDROCK_RETENTION=1` if you want the old teardown behaviour.

The mode is **per-region**: `us-east-1` and `us-west-2` carry independent settings, so raising it in one region does not enable the model in another. Ziya applies it in the region it will actually invoke in, so the first launch in a new region sets that region's switch.

Organisations that must forbid human review can enforce it with an SCP on `bedrock:PutAccountDataRetention`; affected models then report `status: "unavailable"`.

Retention can be scoped to a Bedrock *project* instead of the account, but **not for these models**. Per [AWS's Projects documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/projects.html), projects can only be created on the `bedrock-mantle` endpoint; `bedrock-runtime` requests resolve to the account's default project, whose retention setting "always inherits from the account". Fable 5.1 is served by `bedrock-runtime`, so the account switch is the only scope available to it — project-scoping is structurally unavailable rather than merely unimplemented.

## MCP (Model Context Protocol)

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_ENABLE_MCP` | bool | `true` | `--mcp` / `--no-mcp` | Enable MCP server integration |
| `ZIYA_TOOL_TIMEOUT` | int | `120` | — | Default timeout (seconds) for individual tool executions |
| `ZIYA_TOOL_SENTINEL` | str | `TOOL_SENTINEL` | — | XML tag name for tool call boundaries in streaming |
| `ZIYA_MAX_TOOLS_PER_ROUND` | int | `5` | — | Maximum tool calls the model may make in a single round |
| `ZIYA_SECURE_MCP` | bool | `false` | — | Enforce strict MCP result signing and verification |
| `ZIYA_MAX_TOOL_ITERATIONS` | int | `1000` | — | Maximum agentic loop iterations per streaming response for unattended/batch work: `ziya task`, Task Cards, `/goal` runs, delegates and web chat |
| `ZIYA_MAX_TOOL_ITERATIONS_INTERACTIVE` | int | `200` | — | Iteration budget for interactive CLI commands where a human is waiting (`ziya chat`, `ask`, `review`, `explain`); also what `/tune iterations` sets. Independent of `ZIYA_MAX_TOOL_ITERATIONS` — changing one never affects the other |
| `ZIYA_MAX_TOOLS_PER_TURN` | int | `1000` | — | Per-turn circuit breaker: max tool invocations within one streaming response before further calls are refused. Resets each turn, so long conversations are never locked out. `0` disables (ASR F-010) |
| `ZIYA_MCP_ENV_PASSTHROUGH` | str | — | — | Comma-separated allowlist of extra parent env vars to forward to MCP server subprocesses. By default credential-bearing vars (AWS_*/MIDWAY_*/TOKEN/SECRET/…) are stripped before launch (ASR F-003) |

## Features

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_ENABLE_AST` | bool | `false` | `--ast` | Enable AST-based code understanding |
| `ZIYA_AST_RESOLUTION` | str | `medium` | `--ast-resolution` | AST context level: `disabled`, `minimal`, `medium`, `detailed`, `comprehensive` |
| `ZIYA_EPHEMERAL_MODE` | bool | `false` | `--ephemeral` | Don't persist conversations beyond the current session |
| `ZIYA_ENABLE_NOVA_GROUNDING` | bool | `false` | — | Enable the Nova Web Grounding tool for web search |
| `ZIYA_WHISPER_MODEL` | str | `base` | — | Local faster-whisper model used for voice transcription |
| `ZIYA_WHISPER_DEVICE` | str | `cpu` | — | Voice-transcription device: `cpu`, `cuda`, or `auto` |
| `ZIYA_WHISPER_COMPUTE_TYPE` | str | `int8` | — | CTranslate2 compute type used for voice transcription |

## Memory (post-conversation extraction)

These gate the memory subsystem's post-conversation extraction pipeline
(`app/memory/extractor.py`). They are read via `os.environ.get` on the
extraction hot path (fire-and-forget background task), so they are not
registered in `env_registry.py`.

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZIYA_MEMORY_TRIAGE_DISABLED` | bool | `false` | When set truthy (e.g. `1`), bypass the model-based admission **triage** call and fall back to the legacy user-only regex salience gate. Use this to disable triage if it misbehaves — extraction then admits a conversation only when the regex finds a salience hit in a USER turn, exactly as before triage existed. |
| `ZIYA_MEMORY_TRIAGE_MODEL` | str | — | Per-run override for the admission-triage model (mirrors `ZIYA_MEMORY_EXTRACTION_MODEL`). When **unset**, triage reuses the `memory_extraction` service category so it inherits that category's Haiku-tier model rather than the endpoint lite default. When set, the service resolver routes the `memory_triage` category to this model id. |
| `ZIYA_MEMORY_EXTRACTION_MODEL` | str | — | Per-run override for the extraction model (`memory_extraction` service category). Used by the eval harness to A/B model tiers against recorded verdicts. |
| `ZIYA_MEMORY_FAST_TRACK_LAYERS` | str | `architecture,decision,negative_constraint` | Comma-separated set of memory layers whose high-quality, self-contained proposals promote on **graded quality alone** (no corroboration) once past the fast-track minimum age. Blank/unset uses the design default. Other layers always keep the corroboration-or-use bar. |
| `ZIYA_MEMORY_FAST_TRACK_THRESHOLD` | float | `0.75` | Composite quality (0.0–1.0) at or above which a fast-track-layer proposal promotes without corroboration. Quality is graded at proposal time (durability 0.40 · atomicity 0.35 · self-containment 0.25) and clamped by structural checks. |
| `ZIYA_MEMORY_FAST_TRACK_MIN_AGE` | int | `2` | Minimum age in activity ticks before a fast-track promotion fires. Two ticks is the smallest window in which a contradiction or corroborating re-extraction could arrive; it sits well inside the 7-tick decay window so fast-track proposals promote before decay can reach them. |

Two-track promotion (redesign §2): fast-track layers promote on quality alone
after `ZIYA_MEMORY_FAST_TRACK_MIN_AGE`; all other layers keep the existing
corroboration-or-use requirement. A proposal with no graded `quality` field
(legacy rows, references) never fast-tracks. Corroboration is a confidence
bonus (raises the promoted memory's `importance`) on both tracks. Decay applies
only to below-threshold, contradicted, or non-fast-track proposals.

Admission triage: a small-model call reads the whole stripped transcript
(USER **and** ASSISTANT turns) and decides whether extraction runs. On triage
failure it falls back to the regex gate — it never admits, via an
unconditional path, a conversation the regex would have skipped.

## Diff Application

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZIYA_ENABLE_DIFF_VALIDATION` | bool | `true` | Validate diffs before streaming completes |
| `ZIYA_AUTO_REGENERATE_INVALID_DIFFS` | bool | `true` | Automatically ask the model to regenerate failed diffs |
| `ZIYA_AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE` | bool | `true` | Auto-add missing files to context on diff failure |
| `ZIYA_DIFF_VIEW_TYPE` | str | `unified` | Default diff view in UI: `unified` or `split` |
| `ZIYA_DIFF_CONTEXT_SIZE` | int | — | Override context lines used in diff matching |
| `ZIYA_DIFF_SEARCH_RADIUS` | int | — | Override hunk search radius for fuzzy matching |
| `ZIYA_FORCE_DIFFLIB` | bool | `false` | Bypass system `patch` and always use Python difflib |
| `ZIYA_FORCE_DRY_RUN` | bool | `false` | Never write changes — dry-run all diff applications |

## Grounding (Web Search)

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZIYA_GROUNDING_MODEL` | str | `nova-2-lite` | Model key for Nova web grounding calls |
| `ZIYA_GROUNDING_REGION` | str | `us-east-1` | AWS region for the grounding service |

## Security

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZIYA_ENCRYPTION_KEY` | str | — | Passphrase for at-rest encryption of stored conversations |
| `ZIYA_DISABLE_AUDIT_LOG` | bool | `false` | Disable the MCP tool audit log |
| `ZIYA_AUDIT_CONTEXT_SNAPSHOT` | bool | `false` | Add a co-presence `context` snapshot (active memory ids + recent tool-result ids) to each MCP tool audit entry, for forensic prompt-injection incident reconstruction (ASR NF-006/008/010). Records what was in context at decision time — correlation, not causation. Off by default; also enableable via an enterprise `ConfigProvider.should_capture_audit_context()`. When set, this env var overrides the plugin policy. |
| `ZIYA_AUTO_RECONCILE_CHATS` | bool | `false` | Auto-remove cross-project chat shadow copies at startup. Off by default (warn-only): the boot-time integrity check logs a warning when shadows exist but never deletes. Set to `1`/`true` to have startup reconcile automatically. Manual review/cleanup is available any time via `GET /api/v1/chat-integrity` and `POST /api/v1/chat-integrity/reconcile`. |
| `ZIYA_ALLOW_ALL_ENDPOINTS` | bool | `false` | Bypass enterprise endpoint restrictions (dev/testing only) |
| `ZIYA_RETENTION_OVERRIDE_DAYS` | number | — | Minimum retention in days — raises any plugin-enforced TTL that is shorter than this value (e.g. `30` to guarantee 30-day retention). Set to `0` or unset to disable. Fractional values like `0.5` (12 hours) are supported. |
| `ZIYA_CSP_MODE` | str | `relaxed` | Content-Security-Policy strictness. `relaxed` allows `unsafe-inline`/`unsafe-eval` + the jsdelivr origin so Mermaid/Vega CDN diagrams render; `strict` drops `unsafe-eval` and pins jsdelivr to specific script paths (Vega expression-eval diagrams will not render). (ASR F-005) |
| `ZIYA_CHROMIUM_NO_SANDBOX` | bool | `false` | Launch the headless diagram-rendering Chromium with `--no-sandbox`. Off by default — the sandbox is the primary defense against renderer exploits on attacker-influenced SVG/HTML. Enable only where the sandbox cannot run (e.g. root in a container). (ASR F-027) |

## Logging / Debug

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZIYA_LOG_LEVEL` | str | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ZIYA_DEBUG_PROMPTS` | bool | `false` | Log full prompt assembly details |
| `ZIYA_DEBUG_CHUNKS` | bool | `false` | Log raw streaming chunk data |
| `ZIYA_CONTEXT_DEBUG` | bool | `false` | Detailed capture for the "Submitted context (debug)" panel: per-iteration payload char counts plus on-disk snapshots under `~/.ziya/debug/context_snapshots/` (so the history survives the crash you want to inspect). The cheap in-memory tier of provider-reported token counts is always on regardless. Overrides the panel toggle when set |
| `ZIYA_CONTEXT_PAYLOAD_DEBUG` | bool | `false` | Retain the **full submitted request** per iteration — the pre-serialisation system prompt and conversation, exactly as handed to the provider — under `~/.ziya/debug/context_payloads/`. Frames are megabytes each, so retention is a rolling window of the 3 most recent rounds per conversation (4 conversations, plus a total-bytes ceiling); a round that failed is kept regardless. Frames contain your entire submitted context including source code, and are written `0600` in a `0700` directory. Overrides the panel toggle when set |
| `ZIYA_DISABLE_PROMPT_CACHE` | bool | `false` | Disable Bedrock prompt caching |
| `ZIYA_DISABLE_THINKING_PASSBACK` | bool | `false` | Stop echoing signed extended-thinking blocks back inside a tool chain. Passback keeps the model's reasoning across tool round-trips instead of making it re-derive the plan each iteration; disable it to isolate a provider or gateway that rejects round-tripped thinking blocks |
| `ZIYA_DUMP_REQUEST_PARTS` | str | — | Directory to dump the assembled system prompt, tool schemas and request params on the first request |

## Operational

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_SCAN_TIMEOUT` | int | `45` | — | Maximum seconds for folder scanning |
| `ZIYA_MAX_DEPTH` | int | `15` | `--max-depth` | Maximum depth for folder tree traversal |
| `ZIYA_DISABLE_AUTO_UPDATE` | bool | `false` | — | Disable automatic pip/pipx upgrade check |
| `ZIYA_TASK_IMPROVE_RUN_MAX` | int | `10` | — | Run-wide ceiling on self-improvement card edits across every improving level of one task run (bounds the multiplicative cost of nested self-improving blocks) |
| `ZIYA_IMPROVE_JUDGE_MODEL` | str | (endpoint's `medium` tier) | — | Model override for the Task Card self-improvement judge (the `improve_judge` service category; defaults to the endpoint's `medium` tier via the same tags `model_tier` uses) |


## Internal (not user-facing)

These are set automatically by Ziya's startup sequence. You should not need to set them manually.

| Variable | Purpose |
|---|---|
| `ZIYA_AUTH_CHECKED` | Flag: auth was attempted during startup |
| `ZIYA_PARENT_AUTH_COMPLETE` | Flag: parent process completed auth successfully |
| `ZIYA_SKIP_INIT` | Flag: skip model init (set after startup auth) |
| `ZIYA_LOAD_INTERNAL_PLUGINS` | Load internal/enterprise plugins |
| `ZIYA_PROJECT_ID` | Current project ID (set by middleware) |
| `ZIYA_INCLUDE_INTERNAL_REGISTRIES` | Include internal MCP registry sources |
| `ZIYA_CANARY` | Canary token for deployment verification |
| `ZIYA_USE_DIRECT_STREAMING` | Legacy streaming toggle (largely superseded) |

---

## Known Conflicts

`ZIYA_MAX_TOKENS` and `ZIYA_MAX_OUTPUT_TOKENS` both control the same setting. `ZIYA_MAX_TOKENS` is deprecated — use `ZIYA_MAX_OUTPUT_TOKENS` for all new code. In `app/agents/agent.py`, the resolution is:

```python
max_tokens = int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0)) \
          or int(os.environ.get("ZIYA_MAX_TOKENS", 0)) \
          or DEFAULT_MAX_OUTPUT_TOKENS
```

`ZIYA_MAX_OUTPUT_TOKENS` takes precedence when both are set.

---

## Adding a New Variable

1. Add an `EnvVar` entry in `app/config/env_registry.py` under the appropriate category
2. Add a row to the corresponding section in this document
3. Run `python scripts/lint_env_vars.py` to verify registration
4. Use `ziya_env("ZIYA_YOUR_VAR")` for type-safe access (or `os.environ.get` for hot paths where import weight matters)
