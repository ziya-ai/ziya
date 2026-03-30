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

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_ENDPOINT` | str | `bedrock` | `--endpoint` | Model provider: `bedrock`, `google`, `openai`, or `anthropic` |
| `ZIYA_MODEL` | str | — | `--model` | Model alias (e.g. `sonnet4.0`, `gemini-3.1-pro`) |
| `ZIYA_MODEL_ID_OVERRIDE` | str | — | `--model-id` | Override the resolved model ID directly (advanced) |

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
| `ZIYA_THINKING_EFFORT` | str | — | — | Adaptive thinking effort for Claude 4.6+: `low`, `medium`, `high`, `max` |
| `ZIYA_THINKING_BUDGET` | int | `16000` | — | Token budget for extended thinking (Bedrock streaming) |

## AWS

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_AWS_PROFILE` | str | — | `--profile` | AWS credential profile name for Bedrock |

> Standard `AWS_REGION` and `AWS_PROFILE` are also respected. `ZIYA_AWS_PROFILE` takes precedence over `AWS_PROFILE` when both are set.

## MCP (Model Context Protocol)

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_ENABLE_MCP` | bool | `true` | `--mcp` / `--no-mcp` | Enable MCP server integration |
| `ZIYA_TOOL_TIMEOUT` | int | `120` | — | Default timeout (seconds) for individual tool executions |
| `ZIYA_TOOL_SENTINEL` | str | `TOOL_SENTINEL` | — | XML tag name for tool call boundaries in streaming |
| `ZIYA_MAX_TOOLS_PER_ROUND` | int | `5` | — | Maximum tool calls the model may make in a single round |
| `ZIYA_SECURE_MCP` | bool | `false` | — | Enforce strict MCP result signing and verification |
| `ZIYA_MAX_TOOL_ITERATIONS` | int | `200` | — | Maximum agentic loop iterations per streaming response |

## Features

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_ENABLE_AST` | bool | `false` | `--ast` | Enable AST-based code understanding |
| `ZIYA_AST_RESOLUTION` | str | `medium` | `--ast-resolution` | AST context level: `disabled`, `minimal`, `medium`, `detailed`, `comprehensive` |
| `ZIYA_EPHEMERAL_MODE` | bool | `false` | `--ephemeral` | Don't persist conversations beyond the current session |
| `ZIYA_ENABLE_NOVA_GROUNDING` | bool | `false` | — | Enable the Nova Web Grounding tool for web search |

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
| `ZIYA_ALLOW_ALL_ENDPOINTS` | bool | `false` | Bypass enterprise endpoint restrictions (dev/testing only) |
| `ZIYA_RETENTION_OVERRIDE_DAYS` | number | — | Minimum retention in days — raises any plugin-enforced TTL that is shorter than this value (e.g. `30` to guarantee 30-day retention). Set to `0` or unset to disable. Fractional values like `0.5` (12 hours) are supported. |

## Logging / Debug

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZIYA_LOG_LEVEL` | str | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ZIYA_DEBUG_PROMPTS` | bool | `false` | Log full prompt assembly details |

## Operational

| Variable | Type | Default | CLI Flag | Description |
|---|---|---|---|---|
| `ZIYA_SCAN_TIMEOUT` | int | `45` | — | Maximum seconds for folder scanning |
| `ZIYA_MAX_DEPTH` | int | `15` | `--max-depth` | Maximum depth for folder tree traversal |
| `ZIYA_DISABLE_AUTO_UPDATE` | bool | `false` | — | Disable automatic pip/pipx upgrade check |

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
