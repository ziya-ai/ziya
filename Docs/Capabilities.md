# Ziya Capabilities

## Models

Ziya supports models from multiple providers. The default model is `sonnet4.6` on AWS Bedrock. Use the model picker in the toolbar to switch at any time.

### Amazon Bedrock — Claude Models

| Alias | Model | Context | Notes |
|---|---|---|---|
| `sonnet4.6` | Claude Sonnet 4.6 | 200K (1M extended) | **Default**. Adaptive thinking. |
| `sonnet4.5` | Claude Sonnet 4.5 | 200K (1M extended) | Extended context. |
| `sonnet4.0` | Claude Sonnet 4.0 | 200K (1M extended) | Extended context. |
| `sonnet3.7` | Claude Sonnet 3.7 | 200K | EU regions only. |
| `sonnet3.5-v2` | Claude 3.5 Sonnet v2 | 200K | |
| `sonnet3.5` | Claude 3.5 Sonnet | 200K | |
| `opus4.6` | Claude Opus 4.6 | 200K (1M extended) | Advanced. Adaptive thinking. |
| `opus4.5` | Claude Opus 4.5 | 200K | Advanced. |
| `opus4` | Claude Opus 4 | 200K | Advanced. |
| `opus3` | Claude Opus 3 | 200K | US regions only. |
| `haiku-4.5` | Claude Haiku 4.5 | 200K | Fast and cheap. |
| `haiku` | Claude 3 Haiku | 200K | Fast and cheap. |

### Amazon Bedrock — Nova Models

| Alias | Model | Context | Notes |
|---|---|---|---|
| `nova-premier` | Amazon Nova Premier | 1M | Multimodal. Web grounding capable. |
| `nova-pro` | Amazon Nova Pro | 300K | Multimodal. Thinking mode. |
| `nova-lite` | Amazon Nova Lite | 300K | Fast. Multimodal. |
| `nova-micro` | Amazon Nova Micro | 128K | Text only. |

### Amazon Bedrock — Other Models

| Alias | Model | Context | Notes |
|---|---|---|---|
| `deepseek-r1` | DeepSeek R1 | 128K | Reasoning model. |
| `deepseek-v3` | DeepSeek V3 | 128K | |
| `deepseek-v3.2` | DeepSeek V3.2 | 128K | |
| `qwen3-coder-480b` | Qwen3 Coder 480B | 128K | us-west-2 only. |
| `kimi-k2.5` | Kimi K2.5 | 128K | Thinking model. |
| `minimax-m2.1` | MiniMax M2.1 | 1M | |
| `glm-4.7` | GLM 4.7 | 128K | |
| `openai-gpt-120b` | OpenAI GPT OSS 120B | 128K | us-west-2 only. |
| `openai-gpt-20b` | OpenAI GPT OSS 20B | 128K | us-west-2 only. |

### Google Gemini

| Alias | Model | Context | Notes |
|---|---|---|---|
| `gemini-3.1-pro` | Gemini 3.1 Pro Preview | 1M | Thinking levels. Native function calling. Default. |
| `gemini-3.1-pro-customtools` | Gemini 3.1 Pro Preview (Custom Tools) | 1M | Optimized for agentic workflows with bash/custom tools. |
| `gemini-latest` | Gemini Pro Latest | 1M | Floating alias — auto-updates to latest Pro model. |
| `gemini-3-pro` | Gemini 3 Pro Preview | 1M | ⚠️ Deprecated March 9, 2026. Use `gemini-3.1-pro`. |
| `gemini-3-flash` | Gemini 3 Flash Preview | 1M | Thinking levels. Native function calling. |
| `gemini-2.5-pro` | Gemini 2.5 Pro | 1M | Native function calling. |
| `gemini-flash` | Gemini 2.5 Flash | 1M | Native function calling. |
| `gemini-2.0-flash` | Gemini 2.0 Flash | 1M | |
| `gemini-2.0-flash-lite` | Gemini 2.0 Flash Lite | 1M | No function calling. |
| `gemini-2.5-flash-lite` | Gemini 2.5 Flash Lite | 1M | Thinking mode. |

### OpenAI

| Alias | Model | Context | Notes |
|---|---|---|---|
| `gpt-4.1` | GPT-4.1 | 200K | Native function calling. Vision. |
| `gpt-4.1-mini` | GPT-4.1 Mini | 200K | Native function calling. Vision. |
| `gpt-4.1-nano` | GPT-4.1 Nano | 200K | Native function calling. Vision. |
| `gpt-4o` | GPT-4o | 128K | Native function calling. Vision. |
| `gpt-4o-mini` | GPT-4o Mini | 128K | Native function calling. Vision. |
| `o3` | o3 | 200K | Reasoning model. |
| `o3-mini` | o3 Mini | 200K | Reasoning model. |
| `o4-mini` | o4 Mini | 200K | Reasoning model. |

> **Note**: OpenAI models require `OPENAI_API_KEY` set in your environment and `--endpoint openai`. Enterprise deployments may restrict available endpoints via policy.

---

## Portable Model Tiers

Instead of naming a specific model, you can select a **tier** — a portable,
endpoint-agnostic cost/capability rung. Tiers let a decomposed workload run
cheap executor work on small models under a smarter supervisor, without
hardcoding a provider- or version-specific name that rots when models are
retired or you switch endpoint.

| Tier | Intent | Bedrock | Google | OpenAI | Anthropic | z.ai |
|---|---|---|---|---|---|---|
| `xsmall` | Cheapest/fastest | Nova Micro | Flash Lite | GPT-5.5 Nano | Haiku 4.5 | GLM-4.6 |
| `small` | Cheap | Nova Lite | Gemini Flash | GPT-5.5 Mini | Haiku 4.5 | GLM-4.6 |
| `medium` | **Default** (average) | Sonnet 5 | Gemini 3.1 Pro | GPT-5.5 | Sonnet 5 | GLM-5.2 |
| `large` | Most capable | Opus 4.8 | Gemini 3.1 Pro | GPT-5.5 Pro | Opus 4.8 | GLM-5.2 |
| `frontier` | Cutting edge — rare, expensive | Fable 5 | Gemini 3.1 Pro | GPT-5.5 Pro | Fable 5.1 | GLM-5.2 |

Five rungs, cheapest → most capable. **`medium` is the center: the default,
"average" model — the same one the top-level conversation uses (Sonnet 5 on
Bedrock).** It is also the resolution fallback target. **`frontier`** is the
rarely-warranted top: cutting-edge models that today run roughly 20× the cost
of `large` with heavy throttling, so reserve it for work that genuinely needs
it.

The resolution is **not** a separate table — each model entry carries a
`tier` tag, so the tier follows the model automatically as models are added
or retired. A rung with no exactly-tagged model **rounds up** to the nearest
defined rung at or above it (falling to the highest rung below only if nothing
at/above exists), so an unmapped rung never silently under-serves a task.
`--model <tier>` works at the top level too.

**Where tiers apply:**

- **Top-level conversation** — `--model medium` (or any tier) selects the
  resolved model for the session.
- **Task Card blocks** — the Model control (Task Advanced section, and on
  every container block / the card / the deck via the Permissions row) picks
  a tier (recommended) or a specific model. A tier set on a container, the
  card, or the project-wide deck flows down to every task beneath it; a leaf
  overrides for itself. Set a smart tier on the card and cheap tiers on
  mechanical leaf tasks to run cheap executors under a smart supervisor.
- **Delegates** — each delegate spec can carry a `model_tier` so a swarm runs
  cheap executors under a smarter orchestrator. A model that authors its own
  plan (a `delegate-tasks` or `task-card` block, or `swarm_request_delegate`)
  can set `model_tier` per unit of work directly.

Also known as: multi-agent orchestration, orchestrator/delegate roles, manager–worker agents; competitors call this MultiDevin hierarchical agents (devin), Kiro Crew (kiro), or coordinator-dispatched droid roles (factory). <!-- cap: hierarchical-multiagent-orchestration -->

A **specific model name** (e.g. `sonnet4.6`) or inference-profile ARN is
still available as an escape hatch, but is explicitly flagged non-portable in
the UI — prefer a tier unless you have a concrete reason to pin an exact model.

---

## Tools

The model has access to tools it can call autonomously when they would help answer your question.

### Builtin Tools (no setup required)

| Tool | What it does |
|---|---|
| `file_read` | Read files from your project |
| `file_write` | Write files to approved locations |
| `file_list` | List directory contents |
| `nova_web_search` | Search the web with citations (requires `bedrock:InvokeTool` IAM permission) |
| Architecture shapes | Browse and search diagram component catalogs for DrawIO, Mermaid, and Graphviz |

Also known as: web search, web grounding, integrated web search with citations (`nova_web_search` / `GroundingService`); competitors call this a built-in web search tool (librechat, open-webui) or managed web browsing with citations (chatgpt). <!-- cap: web-search-tool -->

### MCP Tools

Connect any MCP-compatible server to give the model additional capabilities — shell access, internal APIs, databases, and more.

<!-- cap: mcp-server-config-loading -->
MCP servers are configured in `mcp_config.json`. Ziya uses the **first existing** `mcp_config.json` among these three locations (there is no cross-file merge):

1. Current working directory (`./mcp_config.json`)
2. Ziya project root
3. User home (`~/.ziya/mcp_config.json`)

```json
{
  "my_server": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    "enabled": true
  }
}
```

#### Tool Enhancements (per-server)

MCP tool descriptions are set by the server and can't always be changed upstream. If a tool has ambiguous parameters or the model keeps calling it incorrectly, add a `tool_enhancements` block to inject supplemental hints into the tool's description:

```json
{
  "my_server": {
    "command": "npx",
    "args": ["-y", "some-mcp-server"],
    "tool_enhancements": {
      "search_tool": {
        "description_suffix": "\n<Rule>The 'query' parameter must be a string, not an array.</Rule>"
      },
      "file_tool": {
        "description_suffix": "\nAlways use absolute paths."
      }
    }
  }
}
```

The `description_suffix` is appended verbatim to the tool's description before it reaches the model. This is useful for correcting common model mistakes without waiting for the MCP server to update.

Enhancement sources are merged in priority order (later overrides earlier for the same tool):

1. **Enterprise plugin** — organization-wide defaults via `ToolEnhancementProvider` (see `Enterprise.md`)
2. **MCP server config** — the `tool_enhancements` block shown above
3. **User overrides** — `~/.ziya/tool_enhancements.json` (see below)

The shell command allowlist can be extended by enterprise plugins via the `ShellConfigProvider` interface — see `Enterprise.md` for details. Users can also add commands per-session with `/shell add <cmd>` or persist them with `/shell add <cmd> save`.

Also known as: request-pipeline middleware, plugin/provider framework, tool-call interception; competitors call this Pipelines (open-webui) or a programmable plugin event bus (amp). <!-- cap: pipeline-middleware-extensibility -->

#### Built-in (in-process) tools

Not every tool needs an external MCP server. Ziya ships a registry of **13 built-in tool categories** that run in-process and are dispatched through the same signed tool path as external tools: architecture diagrams, file I/O, PDF/RAG, AST code intelligence, Nova web grounding, skills, memory, context management, diagram rendering, beads, task cards, task artifacts, and PCAP analysis. Each category maps to a getter that returns tool instances; every enabled instance is wrapped as a standard LangChain tool and dispatched by the manager's built-in fallback branch. Categories toggle independently via a `ZIYA_ENABLE_<CATEGORY>` environment variable, a service-plugin allow-list, or their built-in default — and `memory` and `pcap_analysis` are off by default (PCAP is also hidden as "not ready yet"). Per-tool depth belongs to each owning subsystem; this entry covers the framework and catalog.
<!-- cap: mcp-builtin-direct-tools -->
Also called: built-in tools, direct tools, in-process tools, native tools, no-server tools; internally `DirectMCPTool` / `BaseMCPTool` and the tool category registry.

**MCP config validation.** When `mcp_config.json` is parsed, each server entry is checked entry-by-entry by an advisory validator that never rejects anything the loader would otherwise accept. It flags unknown or misspelled keys (with difflib fuzzy suggestions and a known-alias table), a missing launch mechanism, wrong value types for `command`/`args`/`env`, non-string env values, relative script paths that will not resolve against the trusted roots, and conflicting enabled/disabled settings. Each finding carries a source line number and a severity and is surfaced in a GUI findings panel. Limit (maturity 4, static only): it cannot detect a command that exists but fails at runtime, and it does not check whether a remote `url` is reachable.
<!-- cap: mcp-config-validation -->
Also called: config linter, config findings panel, schema validation, typo detection, config diagnostics; internally `validate_config`.

**MCP registry marketplace.** The registry browser is backed by an aggregation engine, not a single catalog. It fetches MCP-server catalogs from multiple registry providers in parallel, deduplicates entries by a repo/package fingerprint, merges duplicates, and ranks results by support level and then download/star counts. A unified cross-provider search is memoised per query and provider set so typing does not re-fan the fetch on every keystroke, and a partial refresh (one provider failing) is never cached for the full TTL. Limit: results depend on external registry availability — a failing provider yields a partial, uncached catalog.
<!-- cap: mcp-registry-marketplace -->
Also called: MCP marketplace, server registry browser, tool discovery, registry aggregator, unified catalog; internally `RegistryAggregator` / `search_unified`.

**MCP resources & prompts.** MCP defines three primitive types — tools, resources, and prompts — and Ziya supports all three, not just tools. The client loads and the manager aggregates *resources* (list all, fetch content by URI) and *prompts* (list all, fetch a server-provided prompt template with arguments) from both local and remote servers, exposed via `GET /api/mcp/resources` and `/api/mcp/prompts`. This is maturity 3: the primitives work end-to-end with routes and tests, but resources and prompts are exercised less than tools in practice and their frontend UX is thinner than the tool surface.
<!-- cap: mcp-resources-and-prompts -->
Also called: MCP resources, MCP prompts, resource fetching, prompt templates, server-provided prompts; internally `get_resource_content` / `get_prompt_content`.

**Runtime server lifecycle.** MCP servers do not require an app restart to reconfigure. `initialize()` connects all enabled servers; `set_server_enabled()` disables (an idempotent disconnect) or enables-and-restarts a single server at runtime and persists the override; `restart_server()` replaces a running server with new config; and `POST /api/mcp/initialize` reinitializes the whole manager to pick up config changes. All of these transitions are serialized by a process-wide lifecycle lock so overlapping initialize/shutdown/restart/toggle operations cannot race, and persisted enable/disable overrides survive a reinitialize. Limit: runtime toggle/restart operate on servers that already exist in the config — picking up a *newly-added* config server still requires the `POST /initialize` re-read rather than a file watcher.
<!-- cap: mcp-server-lifecycle -->
Also called: server lifecycle, runtime enable/disable, hot restart, toggle server, MCP hot reload; internally `set_server_enabled` / `restart_server` behind a lifecycle lock.

**MCP startup diagnostics.** When a server fails to start, Ziya tries to tell you *where*. Each client records the furthest startup stage it reached (config → preflight → spawn → handshake → ready), captures a readable tail of the server's log, and detects dependency-mismatch hints from stderr, so the GUI can attribute a failure to the user's config, their machine, or the server itself. Preflight checks (for example, a command that is not on `PATH`) produce a stored diagnostic without even spawning the process. This is maturity 3: hint detection is signature-based, so a novel failure mode falls back to a raw log tail rather than a specific explanation.
<!-- cap: mcp-startup-diagnostics -->
Also called: startup stages, preflight check, server log tail, failure attribution, connection troubleshooting; internally `startup_stage` with stderr capture.

---

## MCP tool-result integrity & hallucination defenses

A tool result is untrusted input — it can be spoofed, smuggled, or simply hallucinated by the model. Ziya treats every result as an attack surface and layers several independent checks over it. These run automatically on the tool path; there is nothing to configure.

**HMAC tool-result signing.** Every tool result — built-in and external MCP alike — is wrapped with an HMAC-SHA256 signature computed over the tool name, canonical-JSON arguments, canonical-JSON content, a timestamp and the conversation id, using a 256-bit per-process session secret generated at startup and never exposed to the model. Signing happens transparently in the client/manager for external tools and in tool execution for built-ins; unsigned results are rejected. Limit: the session secret is in-process only — signatures do not survive a restart (verification enforces a 5-minute staleness window regardless), and there is no asymmetric or cross-process/persisted verification.
<!-- cap: mcp-tool-result-signing -->
Also called: tool output signing, response integrity signing, tamper-evident tool results, message authentication code for tool calls.

**Response schema & size validation.** Every MCP response passes through a validator that enforces dict structure, a list of content blocks, a block-count limit (50), a per-text-block byte cap (5 MB, truncated), an image-data cap (20 MB, rejected), an allowlist of MIME types (a bad text MIME is coerced to `text/plain`, a bad image MIME is rejected) and recognized block types (text / image / resource); each block is also hidden-char sanitized and injection-scanned. Structurally unsalvageable responses are rejected outright. Limit (maturity 3): the MIME allowlists are fixed sets and there is no per-tool structured-content schema validation beyond type and size.
<!-- cap: mcp-response-schema-validation -->
Also called: tool output validation, content-block validation, MIME allowlist, size-limit enforcement, malformed-response rejection.

**Tool input constraint validation.** Before a call leaves Ziya, its arguments are validated against the tool's JSON-Schema constraints — `enum`, `minLength`/`maxLength`, numeric `minimum`/`maximum`/exclusive bounds, and `pattern` — with hard errors on violation. String values are additionally scanned for dangerous patterns: directory traversal (`../..`), command chaining (`;rm`, `curl`, `wget`, `bash`), template injection (`${...}`) and backtick command substitution. Limit (maturity 3): the dangerous-pattern scan only *warns* (it does not block), and only fields declared in the schema's properties are validated — unknown fields are skipped.
<!-- cap: mcp-input-constraint-validation -->
Also called: input validation, argument schema enforcement, parameter constraint checking, command-injection heuristics, dangerous-input scanning.

**Tool-description prompt-injection scanning (tool poisoning).** At connect time, descriptions of external (non-builtin, non-trusted) MCP tools are scanned for a set of polarity-aware prompt-injection patterns — ignore-previous-instructions, override-security, "you must always/never …" phrased harmfully, hidden HTML comments, and so on. Concrete matches are *blocking*: the tool is removed from the client's tool set so it never reaches any prompt path; an over-long description is treated as advisory only. Trusted and built-in servers are exempt. Limit: the scan is regex/keyword-based (novel or non-English phrasings can evade) and covers description text only, not input-schema descriptions.
<!-- cap: mcp-tool-poisoning-scan -->
Also called: prompt-injection scanning, tool description sanitization, hidden-instruction detection, malicious tool-description filter, indirect prompt-injection defense.

**Rug-pull fingerprinting & quarantine.** A server can pass inspection on day one and swap in malicious tool definitions later. Ziya defends against this by persisting a human-approved SHA-256 baseline of each server's tool definitions (canonical sorted name / description / inputSchema) that survives restarts. When a server's definitions change unexpectedly on reconnect — a "rug pull" — the server is *hard-quarantined*: its tools are excluded from `get_all_tools()` and refused by the tool caller (error `-32001`), rather than being silently accepted (CWE-345). An explicit human `reauthorize_server()` flow re-scans and accepts a new baseline; if the new definitions match blocking injection patterns, re-authorization is refused unless forced, and a forced accept is bound to the exact fingerprint and persisted so it survives a restart but self-revokes the moment the definitions mutate again. Builtin and trusted servers are exempt, since their fingerprints legitimately change on Ziya upgrades. Limit: the fingerprint covers name/description/inputSchema only, and lifting a quarantine relies on a human — there is no automated diff review beyond the blocking lists.
<!-- cap: mcp-rugpull-quarantine -->
Also called: rug-pull detection, tool-definition fingerprinting, supply-chain tamper detection, definition drift detection, trust-on-first-use baseline, reauthorization; internally `fingerprint_tools` / `check_fingerprint_change`.

**Homoglyph / mixed-script detection.** Tool descriptions are scanned a second time in an NFKC-normalized, confusable-folded copy (Cyrillic/Greek lookalikes mapped to Latin) so homoglyph-substituted injections — e.g. "ignоre" written with a Cyrillic *о* — are caught. Independently, any whitespace-delimited token mixing ASCII and non-ASCII letters is flagged as a mixed-script obfuscation signal even when no pattern matched. Limit (maturity 3): the confusable map is a hand-curated high-frequency Cyrillic/Greek subset, and the check applies to tool descriptions, not to tool-result content.
<!-- cap: homoglyph-confusable-detection -->
Also called: Unicode confusable folding, homoglyph attack detection, mixed-script detection, lookalike-character normalization, spoofed-character detection.

**Hidden-character sanitization.** Tool-result text is iteratively stripped of zero-width / invisible characters, the Unicode tag block (U+E0000–U+E007F), orphaned surrogates, control characters and bidi-override characters until the output stabilizes — defeating hidden-character smuggling and visual spoofing. The escape character (0x1B) is deliberately preserved so ANSI color codes survive (shown in the terminal in CLI mode, converted to HTML spans in the web UI). Stripping events are logged as security-audit events. Limit: because ESC is preserved, ANSI-based tricks are not stripped (mitigated by how the frontend renders them), and it applies to tool-result text rather than to all model-facing text universally.
<!-- cap: hidden-char-sanitization -->
Also called: Unicode smuggling defense, zero-width character stripping, invisible-character removal, bidi-override sanitization, tag-block stripping, text hygiene.

**Encoded-payload scanning.** A detection-only scanner flags Base64/hex/ROT13 spans that decode to mostly-printable, instruction-like text (ignore-previous, you-are-now, run/curl/rm-rf/git-push, …). It gates on decode-to-instruction so it does not trip on commit SHAs or binary blobs, and it runs on tool-result text *and* on memory/proposal writes — where an encoded payload could be stored and later decoded into the system prompt (latent prompt injection). Detections are logged as `encoded_payload_detected` security events. Limit (maturity 3): detection only — it never blocks or mutates; the instruction signal is English/imperative, so a payload decoding to instructions in another language slips through (explicitly accepted); and only Base64/hex/ROT13 are covered (no gzip, URL-encoding, etc.).
<!-- cap: encoded-payload-scanning -->
Also called: obfuscated payload detection, base64 injection detection, encoded instruction scanning, latent prompt injection (LPCI) detection, steganographic instruction detection.

**Trust-labeled tool-result envelope.** Model-facing tool-result text is wrapped in a trust-labeled envelope — `‹tool_result trust="high|low|medium"›…‹/tool_result›` — that classifies tools into high (local shell/file), low (web/wiki/search — the highest injection risk) and medium (default). Any forged envelope delimiter inside the content is defanged so a result cannot break out and inject message-level instructions, and a paired system-prompt directive teaches the model to treat envelope contents as untrusted data, never as instructions. Limit (maturity 3): this is a framing/spotlighting defense that relies on the model honoring the directive (no hard enforcement), and the trust classification is a hand-maintained tool-name allowlist — unknown tools default to medium.
<!-- cap: tool-result-trust-envelope -->
Also called: tool-result trust boundary, data-vs-instruction demarcation, trust-tier labeling, content-provenance envelope, spotlighting / delimiting defense.

**Hallucinated-tool-result guard (parroting detection).** Ziya keeps a session-scoped fingerprint index of every real tool result (word-level 5-gram shingles plus normalized line hashes, blake2b-hashed and LRU-bounded per conversation). While the model streams, its non-fenced prose is probed against that index roughly every 256 characters; a high-confidence match (≥5 verbatim line matches) aborts the stream — "the model is reproducing prior tool output instead of calling the tool" — and retries, while low-confidence matches only log. Fingerprints registered in the current iteration are skipped so legitimate mid-turn summarization is not flagged. Limit: single-process only (no distributed store), in-fence content is deliberately left to the other layers, and the word-level shingles are English/whitespace-oriented.
<!-- cap: shingle-parroting-detection -->
Also called: parroting detection, tool-output regurgitation detection, n-gram fingerprint matching, verbatim-reproduction detection, hallucination layer A.

**Fabricated tool-result detection.** A companion check catches the model *inventing* a tool-result dict (Python-repr or JSON) inside a fenced block as if a tool had run — e.g. a ```` ```python ```` block whose first line is `{'success': True, 'path': ..., 'bytes_written': N}`. It fires only when at least two canonical Ziya tool-result keys are present (success / path / bytes_written / stdout / returncode / error / …), shapes a high/medium/low confidence, and suppresses itself when a matching real tool (e.g. `file_write`, `run_shell_command`) actually ran that iteration; high-confidence closed-fence matches abort and trigger recovery. Limit (maturity 3): it is keyed to Ziya's own tool-result schema — a fabricated result using foreign key names would not match — and only closed fences are walked in the live path.
<!-- cap: fake-tool-result-echo-detection -->
Also called: fabricated result dict detection, fake JSON tool-result detection, invented tool-output detection, tool-result echo detection, hallucination layer C.

---

## Web security, encryption & extensibility

Ziya ships web-facing HTTP hardening, opt-in at-rest encryption, and an open-core plugin surface. The response headers and CSRF guard are always on; encryption is opt-in because it carries key-management cost. (These capabilities are indexed in `FeatureInventory.md` §9 "Enterprise & Internal Deployment" and §11b "Web & Transport Security".)

**Application-level encryption at rest.** `DataEncryptor` provides envelope encryption for stored data: per-project AES-256-GCM Data Encryption Keys (DEKs) wrapped by a Key Encryption Key derived either from a plugin provider (Midway/KMS) or from the `ZIYA_ENCRYPTION_KEY` passphrase. Ciphertext is a self-describing binary envelope (magic `ZIYA-ALE-V1`, version, DEK id, nonce, ciphertext+tag), so storage auto-detects the magic on read and decrypts, and picks plaintext-vs-cipher on write by inferring an encryption *category* from the file path (`conversation_data`, `session_data`, `task_definition`). A merged `EncryptionPolicy` lets specific categories be encrypted while others (e.g. task definitions) stay plaintext, and it is wired into ~14 storage/util modules that call `get_encryptor()`. A data-safety guard refuses to overwrite a file that is currently encrypted-but-undecryptable (wrong or missing KEK), preventing silent loss. Documented at maturity 4. Limit: it is **off by default** (enabled only when a provider or `ZIYA_ENCRYPTION_KEY` is present); encryption is whole-blob, not field-level or searchable; the strongest KEK sources (Midway/KMS) live in the unshipped enterprise plugin, so only passphrase-based encryption is exercisable in OSS; and it needs the optional `cryptography` package.
<!-- cap: application-level-encryption-at-rest -->
Also called: ALE, at-rest encryption, envelope encryption, data-at-rest encryption, AES-256-GCM storage encryption, `EncryptionProvider`.

**Passphrase-based encryption for community installs.** Without any plugin, a community user can enable at-rest encryption by setting a single env var, `ZIYA_ENCRYPTION_KEY`. The KEK is derived via PBKDF2-HMAC-SHA256 with 600,000 iterations and a random per-install 16-byte salt stored `0o600` at `~/.ziya/ale_passphrase_salt`; the per-install salt defeats cross-installation precomputation and rainbow tables. Default policy rotates DEKs every 90 days. Documented at maturity 3. Limit: the passphrase lives in the process environment (no secret-manager integration in OSS), there is no rate-limiting/lockout, there is no UI to set or rotate it, and changing the passphrase without the old one renders existing data unreadable (recovery only via keyring backups).
<!-- cap: passphrase-encryption-community -->
Also called: passphrase encryption, password-based encryption, PBKDF2 key derivation, per-install salt, `ZIYA_ENCRYPTION_KEY`.

**Content-Security-Policy and security response headers.** `SecurityHeadersMiddleware` adds `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `X-XSS-Protection`, `Referrer-Policy` and a Content-Security-Policy on non-SSE responses. `build_csp()` offers a `relaxed` mode (default: `unsafe-inline`+`unsafe-eval` plus the whole `cdn.jsdelivr.net` for Mermaid/Vega) and a `strict` mode (drops `unsafe-eval`, pins jsdelivr to specific script URLs), selectable via `ZIYA_CSP_MODE`; both modes set `object-src none`, `base-uri self`, and `frame-ancestors none`. Documented at maturity 3. Limit: the default relaxed mode still allows `unsafe-inline`+`unsafe-eval`; strict mode itself must retain `unsafe-inline` because CRA inlines its runtime chunk, and Vega expression diagrams break under strict; nonce-based inline-script elimination is deferred (it needs a build step), and there are no HSTS/COOP/COEP headers.
<!-- cap: content-security-policy-and-headers -->
Also called: CSP, content security policy, security headers, clickjacking protection, `X-Frame-Options`, `ZIYA_CSP_MODE`.

**Origin/Referer CSRF guard.** `OriginGuardMiddleware` rejects state-changing requests (POST/PUT/PATCH/DELETE) whose `Origin` — or `Referer` as a fallback — is not loopback, protecting a locally-bound server from browser drive-by attacks. Safe methods and OPTIONS preflight pass through, so SSE and reads are unaffected. The loopback regex is anchored so `http://localhost.evil.com` cannot match. Header-less requests (curl/CLI) are allowed by default but can be rejected with `ZIYA_STRICT_ORIGIN`, recommended when binding `0.0.0.0`. Documented at maturity 3. Limit: header-less state-changing requests are permitted unless `ZIYA_STRICT_ORIGIN` is set, and the guard relies on loopback binding for its core assumption.
<!-- cap: origin-guard-csrf -->
Also called: CSRF protection, cross-site request forgery guard, origin validation, same-origin enforcement, drive-by protection.

**Directory-scan governance.** `DirectoryScanProvider` lets plugins alter workspace scanning (per-child depth limits, include/exclude masks) via a `ScanCustomization`. The shipped `DefaultDirectoryScanProvider` reads user rules from `.ziya/scan.yaml` — matching by `has_file`/`name`/`name_glob` and setting `include_only`, `exclude`, `default_depth` or per-path `depth_overrides` — walks up to find the project root, and caches parsed rules per root; the file-tree scanner consults it during folder scanning. Documented at maturity 3. Limit: there is no schema validation of `scan.yaml` beyond key presence (malformed rules are logged and ignored), and PyYAML must be installed for rules to take effect (it degrades gracefully when absent).
<!-- cap: directory-scan-governance -->
Also called: scan rules, workspace scan customization, `.ziya/scan.yaml`, include/exclude globs, monorepo scan tuning.

**Prompt-extension framework.** `PromptExtensionManager` registers prompt-transforming functions scoped to `global`, `endpoint`, `family`, or a specific `model`, applied in that order to the system template. Extensions are discovered by loading `*.py` files from `app/extensions/prompt_extensions/` (claude/gemini/nova/openai/cli/mcp) plus dynamic loading from a directory; each can be enabled/disabled via config and receives a merged context (resolved model name/family/endpoint). `get_extended_prompt` uses it to build the per-model system prompt, cached and bounded to 50 entries. Documented at maturity 3. Limit: only one extension is kept per `(type, target)` key — a later registration overwrites rather than composing — and directory-loaded extensions run arbitrary Python at import with no sandbox.
<!-- cap: prompt-extension-framework -->
Also called: prompt governance, per-model prompt customization, system prompt assembly, prompt templating hooks, `PromptExtensionManager`.

*(The open-core plugin/provider registry these hook into — ~15 typed provider interfaces resolved by priority with per-type merge — is described above under "Tool Enhancements" as the plugin/provider framework, and indexed in `FeatureInventory.md` §9. <!-- cap: plugin-provider-framework -->)*

---

## Skills

Skills are reusable instruction bundles that shape how the model behaves in a conversation. Activate one (or several) from the Skills panel to give the model standing guidance — a particular review style, a communication style, a focus area.

Also known as: custom personas, custom assistants, agent modes — assembled from a Skill plus per-folder `systemInstructions` and a pinned model; Ziya has no single "persona" primitive. Competitors call this Custom Assistants (jan) or custom agent modes (amp). <!-- cap: custom-personas-assistants -->

Also known as: reusable prompt presets, output styles, saved prompt configurations; competitors call this Output Styles (claude-code) or Presets (librechat). <!-- cap: reusable-prompt-presets -->

Ziya ships **12 curated built-in skills**, not just a handful of examples. Eight are model-discoverable (code review, debug mode, web research, task decomposition, task cards, packet diagrams, music notation, circuit diagrams) and four are user-selectable (concise, educational, continuous documentation, test everything). Several are substantial domain playbooks — the music-notation and CircuiTikZ skills run to hundreds of lines — that are loaded on demand rather than always occupying the prompt. Built-in skills are read-only (adding one is a code change); your own skills live alongside them.
<!-- cap: skills-builtin-library -->
Also called: instruction bundles, expertise packs, prompt presets, personas, agent skills. Competitors call this Claude Skills (claude-code) or GPTs (openai).

**On-demand skill loading.** Rather than pasting every skill body into the system prompt, Ziya injects a compact one-line-per-skill *catalog* (~200 tokens) of the model-discoverable skills and lets the model pull a full skill body only when it needs it, by calling the `get_skill_details` tool. This progressive-disclosure pattern (matching Claude's Skills design) keeps the prompt small while making the whole library reachable; id/name/keyword matching is fuzzy, so an approximate reference still resolves. Only model-discoverable skills appear in the auto-catalog — user-selectable ones are activated from the panel or requested by name.
<!-- cap: skills-catalog-injection-ondemand -->
Also called: progressive disclosure, just-in-time instructions, lazy skill activation, skill discovery. Competitors call this Claude Skills / SkillMesh (claude-code).

**Filesystem skill discovery.** Skills are not limited to the ones authored in the UI. Ziya scans standard directories in each project (`.ziya/skills`, `.agents/skills`, `.skills`, `SKILLS`, `.claude/skills`, `.kiro/skills`) and the user-global equivalents for agentskills.io-format `SKILL.md` files, parsing their YAML frontmatter and validating that each skill's name matches its directory. Name clashes across roots resolve by precedence (`.ziya` beats other roots, project beats user-global, newest file wins ties). Because it reads the same layout Claude Code and Kiro use, skills authored for those harnesses are picked up unchanged. Limit: the layout is only one directory level deep, and the frontmatter parser handles flat and one-level maps, not full YAML.
<!-- cap: skills-file-discovery-agentskills -->
Also called: SKILL.md discovery, drop-in skills, file-based skills, cross-harness skill import. Follows the agentskills.io spec; interoperates with `.claude/skills` and `.kiro/skills`.

**Enhanced skill dimensions.** A skill is more than a prompt segment: it can also carry model overrides (temperature, max output tokens, thinking mode, or a specific model) and declare scoped tools, context presets, and files. The model overrides and prompt segments are applied at request time today — an active skill can, for example, lower temperature for a review pass. The tool- and context-scoping fields are modelled and persisted, but their end-to-end runtime enforcement has not yet been fully verified, so treat tool/context scoping as available-but-unconfirmed while model and prompt overrides are live. (Maturity 3.)
<!-- cap: skills-enhanced-dimensions -->
Also called: skill model overrides, per-skill temperature, tool scoping, context presets, skill config.

Custom skills can be created and edited from the Skills panel.

---

## Memory

Ziya keeps durable, cross-session knowledge in a **persistent memory store** that is separate from conversation history and from beads (see *Work & Knowledge Primitives* in the Feature Inventory). Memory is user-owned and reviewable — nothing is silently learned and then trusted.

**The store.** Memories live in a flat JSON file at `~/.ziya/memory/memories.json`. Each record carries its content, a layer, tags, importance, status, scope, typed relations to other memories, and retrieval telemetry. The store does full CRUD with encryption-at-rest, an mtime-keyed in-memory cache, a reentrant cross-process file lock guarding read-modify-write, and batched writes. It is the durable substrate the rest of the memory subsystem sits on. Limit: it is a single flat JSON file per Ziya home (not per-project, not a database), with no pagination or sharding — tuned for hundreds of memories, not millions.
<!-- cap: memory-flat-store -->
Also called: long-term memory, persistent memory, cross-session memory, agent memory store, knowledge base. Competitors call this Mem0-style memory.

**Automatic extraction.** After a substantive conversation, Ziya can distil durable facts, decisions, vocabulary, and lessons from it without being asked. The extractor strips tool results, code, and diffs, runs a salience pre-pass (and skips conversations with no salient content), requires at least three human turns, splits long conversations into ~8-turn topic windows, and sends the compressed discourse to a cheap model to produce candidates. It runs fire-and-forget at the end of a stream. Extracted items are candidates, not memories — they must still earn promotion.
<!-- cap: memory-post-conversation-extraction -->
Also called: auto memory extraction, fact extraction, reflection, memory distillation, salience detection, learning from conversation. Competitors call this the Mem0 extract phase.

**Probationary lifecycle.** New candidates land in a probationary proposals queue and are swept periodically. A proposal is promoted to durable memory on corroboration plus use, on high corroboration alone (seen ≥2 times), or on reference plus use; it is archived when it decays (ages without signal) or is redundant (a near-duplicate of an active memory). Age is counted in a shared user-activity counter rather than wall-clock time, so idle time does not age a memory out. Promotion re-embeds the memory and carries its provenance. Limit: the redundancy check needs embeddings to be meaningful (it returns no signal without them).
<!-- cap: memory-proposal-lifecycle -->
Also called: memory promotion, probation queue, proposal approval, auto-archival, consolidation engine, review queue.

**Organizer.** When unplaced memories accumulate (more than ~15 orphans) — or on demand via the API — an LLM organizer reorganises the corpus: it clusters memories into 3–12 thematic domains, extracts supports/contradicts/elaborates relations, bootstraps the mind-map from the clusters, discovers cross-links, and divides oversized cells. Manual organisation runs as a background task you can poll. Limit: it requires an LLM, processes large corpora in batches, and manual runs are fire-and-forget. (Maturity 3.)
<!-- cap: memory-organizer -->
Also called: memory clustering, auto-organization, knowledge-graph builder, topic modeling, relation extraction.

**Mind-map.** The organizer's output is a hierarchical knowledge tree stored at `mindmap.json`: nodes carry a compact handle, parent/child links, cross-links, memory references, and tags. The tree supports one-level context fetch, recursive expansion (to depth 5), delete-with-reparent, and tag-overlap auto-filing of new memories. It is exposed to the model for context and to the UI. Limit: nodes exist only after the organizer bootstraps them (or after manual creation), and tag-overlap placement needs pre-existing tagged nodes. (Maturity 3.)
<!-- cap: memory-mindmap -->
Also called: knowledge graph, concept map, memory tree, topic hierarchy, semantic tree, domain overview.

**Memory Browser.** A dedicated Memory Browser UI, backed by a full REST API (`/api/v1/memory/*`), lets you inspect and manage everything above: status and counts, search, list active and contested memories, full CRUD, review and approve/dismiss proposals (individually or in bulk), trigger and monitor organisation, back-fill embeddings, and browse or edit the mind-map. Archived memories are restorable from the browser.
<!-- cap: memory-browser-ui-api -->
Also called: memory dashboard, memory management UI, memory inspector, knowledge browser, proposals review UI, memory REST API.

**Branch a conversation from a bead.** Beads (the agent's silent attention-debt markers — see *Work & Knowledge Primitives* in the Feature Inventory) double as branch points. Each bead records a *seam*: the message count at the moment it was created. Forking from a bead creates a brand-new conversation truncated to that seam — it carries the beads that precede the seam, promotes the chosen bead active, remaps parent links with fresh ids, and stamps lineage metadata, all while leaving the source conversation completely intact (a non-destructive fork, not an edit-in-place). The branch backend is fully implemented; the split-from-here UI affordance may still be partial. Limit: a bead with no recorded seam (created before the feature, or on an unresolvable chat) cannot be branched from — you get a graceful error. (Maturity 3.)
<!-- cap: bead-branching-fork -->
Also called: conversation branching, fork from here, split conversation, timeline branching, non-destructive fork.

---

## Code Intelligence (AST)

<!-- cap: ast-treesitter-multilang -->
Ziya's AST index is not limited to Python, TypeScript and HTML/CSS. A generic Tree-sitter parser (`TreeSitterParser` / `TreeSitterConverter`, backed by `tree-sitter-language-pack` with a legacy `tree-sitter-languages` fallback) registers one parser per configured language and maps each grammar's concrete syntax tree onto Ziya's unified node types (function, method, class, interface, import, variable). Twenty-five languages are configured beyond the built-in three: C/C++, Rust, Go, Java, C#, Kotlin, Swift, Ruby, PHP, Scala, Lua, Perl, R, Elixir, Haskell, Dart, Zig, OCaml, Julia, Bash, HCL, SQL, TOML and YAML — so `ast_search` and file summaries reach code in all of them. **Limit (maturity 3):** for these tree-sitter languages the parser surfaces only definitions and imports — there are no call-graph or reference edges, so `ast_references` callers/importers are weak for them, and attribute extraction (params, return type, base classes) is text-slice based rather than typed. The whole layer depends on an optional package: if neither tree-sitter package is importable it degrades gracefully to disabled. Depth for these languages is shallower than the native Python parser.

Also called: tree-sitter, multi-language code parsing, polyglot AST, grammar-based parsing, 25+ language support.

---

## Code Application

When the model suggests a diff:

- **Apply** writes the change to disk immediately
- **Undo** reverses it
- The diff pipeline tries multiple strategies (`patch`, `git apply`, difflib) so it handles imperfect diffs gracefully
- Per-hunk status is shown — partial success is fine
- **File deletion** diffs (`deleted file mode` / `+++ /dev/null`) delete the target file when applied
- **New file creation** diffs (`new file mode` / `--- /dev/null`) create the file when applied

Files outside the project root can be modified if they were added via the file browser.

### Inside the patch pipeline

The multi-strategy applier is only the visible layer. Several mechanisms make it robust against the messy, imperfect diffs that real models emit.

**Diff preprocessing / auto-repair.** Before any apply strategy runs, model output passes through a preprocessing pass that repairs the most common LLM diff defects: a pure-addition hunk whose added line closely matches the preceding context line is rewritten as a proper removal+addition; hunk headers carrying wrong old/new line counts are recomputed from the actual body so `patch`/`git apply` don't choke; and bare `@@` headers are synthesised. Duplicate `+++`/`---` headers are cleaned and trailing-newline semantics preserved. The additive-to-replace rewrite only fires when the added line is ≥0.85 similar to a preceding context line.
Also called: diff sanitization, hunk header recount, malformed diff repair, `preprocess_diff`, count correction. <!-- cap: diff-preprocessing-normalization -->

**Idempotent / already-applied detection.** When a diff is applied twice, Ziya recognises that a hunk's target state is already present and reports it as a no-op rather than corrupting the file. It special-cases indentation-only changes, pure additions (with duplicate-declaration/import checks), invisible-unicode and escape-sequence equivalence, and whitespace-insensitive and fuzzy line matching, feeding the pipeline's `already_applied` status. It is heuristic, so pathological near-duplicate content can still mislead it.
Also called: idempotency check, no-op detection, double-apply guard, re-apply safety, `is_hunk_already_applied`. <!-- cap: diff-already-applied-detection -->

**Backtick / fence unescape recovery.** Models often escape backticks so a triple-fence code block doesn't terminate the surrounding markdown; Ziya reverses that transport escaping without destroying genuine template-literal escapes. The decision is grounded in the target file itself — a context/removal line must exist in the file, so a file match proves whether a backtick is real content (preserve) or an artifact (unescape); content heuristics apply only when the file gives no signal, and in that fallback case they can misfire on ambiguous markdown.
Also called: backtick unescaping, fence truncation recovery, template-literal escape handling, transport-escape reversal. <!-- cap: diff-backtick-fence-recovery -->

**Per-language handlers and post-apply validation.** A pluggable registry of seven handlers (Python, TypeScript, JavaScript, Java, C++, Rust, and a generic fallback) provides language-aware matching, duplicate-structure detection, and match-confidence enhancement. After a patch applies, a validation pass runs against the post-patch file to catch language-level breakage such as unbalanced braces or damaged structure. This covers six real languages plus the generic fallback, and it catches structural breakage — not semantic correctness.
Also called: language-aware diff, per-language handling, post-apply validation, syntax validation stage, `LanguageHandlerRegistry`. <!-- cap: diff-per-language-validation -->

**Configurable apply thresholds.** The pipeline's tunables are centralised and overridable through `ZIYA_DIFF_*` environment variables: search radius, confidence thresholds (exact/high/medium/low/min), `MAX_OFFSET`, adaptive context sizing, and a `ZIYA_FORCE_DIFFLIB` switch to bypass `patch`/`git apply`. Tuning is global rather than per-file or per-language, and these knobs are aimed at operators rather than everyday users.
Also called: diff configuration, fuzz thresholds, `ZIYA_DIFF_` env vars, confidence threshold config, search radius config, `MAX_OFFSET`. <!-- cap: diff-config-tunables -->

**Patch-pipeline security hardening.** The apply/unapply/validate routes and parser carry explicit, commented security fixes: path-containment checks on both the diff-extracted and request-provided target path (CWE-22/94 traversal/injection via crafted `+++` headers), a sibling-prefix escape fix on unapply, a file-existence-oracle fix on validate, a hunk line-count clamp against out-of-bounds slice arithmetic (CWE-119), and a per-file reentrant lock across the whole pipeline (CWE-667) to stop concurrent last-writer-wins clobbering. Path containment relies on a shared allowlist helper whose correctness is owned elsewhere.
Also called: path traversal protection, diff apply sandboxing, CWE-22 fix, hunk count clamp, patch input validation, `is_path_explicitly_allowed`. <!-- cap: diff-security-hardening -->

**Superseded / duplicate diff detection.** Beyond the export path (which drops superseded diffs from printed output), the client detects when a later diff supersedes an earlier one for the same file — using overlapping hunk ranges and sequential-pair detection — so stale diffs aren't re-applied or double-counted at apply time, and it extracts per-file diffs and locators from combined output. The detection is heuristic and overlap-based, not a semantic diff comparison.
Also called: superseded diff detection, stale diff suppression, diff dedup, sequential diff pairing, `findSupersededDiffIndices`. <!-- cap: diff-frontend-superseded-dedup -->

**Diff regression suite and in-app test harness.** The applier is backed by a large edge-case corpus — 159 diff test-case directories under `tests/diff_test_cases`, roughly 22 `test_diff_*.py` modules, and both serial and parallel runners (`run_diff_tests.py`, `run_diff_tests_parallel.py`, `test_all_diff_cases.py`) — plus an in-app harness (`ApplyDiffTest` / `DiffTestView`) that loads and runs those cases directly from the UI. The test mass is roughly equal to the source mass, and it is what keeps the pipeline's behaviour stable as new models and new edge cases arrive.
Also called: diff regression tests, patch test suite, edge-case patch corpus, in-app diff test view, `run_diff_tests`, `ApplyDiffTest`. <!-- cap: diff-test-harness-and-regression-suite -->

---

## Context & Projects

- Multiple projects can be open in separate browser tabs simultaneously
- Each project has its own conversation history, contexts, and skills
- The file tree shows token counts to help you manage context size
- Files outside the project root can be added via the browser
- Context window usage is shown in the toolbar

### Context Curation

Ziya gives you direct control over what the model sees, rather than relying on automatic summarization that may discard information you consider important:

- **Mute/unmute messages** — exclude any message from context without deleting it. Muted messages stay visible (dimmed) and can be restored anytime. Use this to shed weight from dead-end explorations while keeping the important discoveries.
- **Fork + truncate** — branch from any message to explore an alternative. Optionally truncate the fork to start with a lighter context, while the original conversation remains intact.
- **Edit or resubmit** — revise any message in the history and resubmit from that point.
- **Selective file removal** — drop individual files from context when they've served their purpose, reclaiming token budget.

This is a deliberate alternative to automatic context compaction (used by Claude Code, Cline, and others). Auto-compaction lets the machine decide what to keep — which risks losing details you know are critical. Manual curation takes a few clicks but keeps you in control.

### File tree & context sources

The file tree is more than a picker; several mechanisms keep it live, filtered, and grounded.

**Live file-tree updates.** As files are added, modified, or deleted on disk, the tree updates in real time — no manual refresh. A `/ws/file-tree` WebSocket broadcasts `file_added` / `file_modified` / `file_deleted` / `scan_complete` events (each carrying the relative path and token count) to connected clients. Delivery is best-effort (there is no acknowledgement), and the connection set is global rather than per-project, which is why `scan_complete` is scoped to the finishing directory so one project's scan cannot clear another window's scanning state.
Also called: real-time file tree, live tree sync, filesystem push updates, websocket file events. <!-- cap: ctx-live-file-tree-websocket -->

**Filesystem watcher.** A watchdog observer monitors the project root and drives the live tree. On each create/modify/delete it filters editor temp files, re-checks inline and nested `.gitignore`, debounces bursts, defers deletes (pending-delete then commit), updates the per-conversation file-state, and fires the folder-cache invalidation that triggers the WebSocket broadcast; a separate handler refreshes ignore patterns when `.gitignore` itself changes. It is a single watcher over one project root, initialized at startup, and relies on the platform's watchdog backend (inotify / FSEvents).
Also called: file watching, hot reload of tree, inotify/FSEvents monitor, watchdog observer. <!-- cap: ctx-filesystem-watcher -->

**Gitignore-honoring ignore engine.** File filtering respects `.gitignore` and then some. The exclusion set combines a large built-in default list (`node_modules`, `.git`, virtualenvs, Brazil `env`/build-tools/`brazil-output`, macOS `Library`/`Downloads`, and more), the root `.gitignore`, and recursively-discovered nested `.gitignore` files (to a maximum depth of 5). It is hardened for large or hostile trees: per-directory entry caps, a wall-clock deadline (`ZIYA_GITIGNORE_TIMEOUT`), symlink-loop protection via a realpath visited-set, and invalid patterns skipped with a warning rather than crashing the scan; results are per-directory cached under a build lock so concurrent callers share one walk. It is not a full reimplementation of git semantics — negation-precedence edge cases are not covered and nested discovery is depth-capped.
Also called: ignore rules, `.gitignore` parsing, exclude patterns, workspace ignore (à la Cursor `.cursorignore` / Aider `.aiderignore`). <!-- cap: ctx-gitignore-ignore-engine -->

**Per-file token estimation.** The token cost shown against each file is, in the common case, a *calibrated estimate* rather than an exact count: it uses the token calibrator's learned chars-per-token ratio for the file's extension, falls back to a size-based estimate for very large files, skips known binaries (→ 0), and handles document formats (PDF/DOCX/XLSX/PPTX) via calibrator heuristics — with large PDFs short-circuited to a ~3000-token stub because they are replaced by an outline+excerpt stub at include time. The fast path never reads file contents, so estimates can drift; files over ~50k tokens return 0 (excluded) and an accurate tiktoken recompute happens only rarely, on demand.
Also called: token counting per file, context cost estimate, token budget display, tiktoken estimate. <!-- cap: ctx-per-file-token-estimation -->

**External (out-of-project) path inclusion.** Files and directories outside the project root can be grafted into the tree via the browser. They appear under an `[external]` branch and are token-counted with guardrails (a 10k-entry cap, a 15s deadline, and a symlink budget), and subtrees that resolve back into the project are skipped. External paths persist to the project's `externalPaths` setting so they survive a restart and are restored on the next folder scan (on a worker thread); a clear action removes them from both the cache and the persisted settings. Persistence is per-project and external-tree token counts use the fast estimate only.
Also called: add external files, include outside workspace, cross-repo context, reference files outside project. <!-- cap: ctx-external-path-inclusion -->

### Global, auto-included and reusable context

**Auto-included project docs.** On project load or switch, the checked file selection is auto-seeded with `AGENTS.md` (collected recursively) and the root `README.md` (root-only, to avoid pulling every nested README), so the model always starts with project guidance. Importing an external directory likewise auto-selects any `AGENTS.md`/`README.md` it finds. The filename set is hardcoded to those two names; there is no user-configurable auto-include list.
Also called: auto context seeding, default included files, `AGENTS.md` ingestion, README auto-include. <!-- cap: ctx-auto-included-docs -->

**Named context sets (Contexts).** A named, reusable set of files can be saved as a first-class *Context* — with a stable color and a cached token count — per project. Full CRUD is backed by a JSON-per-context store and the `/api/v1/projects/{id}/contexts` API; deleting a Context also removes it from every chat that referenced it. Contexts are surfaced by the Contexts tab and the Active Context bar and are referenced from chats via `contextIds`. Two known limits: the cached token count can go stale between file edits (it is only refreshed when files change), and Contexts are per-project — there is no cross-project reuse except by marking items global.
Also called: saved file sets, context presets, reusable selections, file bundles, `@`-mention contexts. <!-- cap: ctx-named-context-sets -->

**Cross-project global chats & folders.** A chat or folder can be marked `isGlobal`, which surfaces it (read-only) in every project's sidebar while it remains owned by exactly one project; a folder passes the flag to its contents (a chat is effectively global if its own flag or an ancestor folder's flag is set). This is surfacing, not copying — dedicated atomic endpoints set the flag as the single source of truth, and self-healing mtime-keyed caches keep the cross-project scan cheap (a fix for cold lookups that once blocked a project switch for tens of seconds). Callers must not mutate the shared cached chat objects, and scan cost scales with the number of sibling projects.
Also called: global conversations, shared chats across projects, cross-workspace items, `isGlobal`, pinned-across-projects. <!-- cap: cross-project-global-items -->

**Model self-curated context files.** Three builtin MCP tools let the model manage its own file context across turns. `context_add_file` validates the path (project-relative or under the shared read-allowlist), enforces a per-file token limit *before* pinning (refusing oversized files, since pinned files re-cost every turn), stores the file on the chat record's `additionalFiles` tagged with a model-added ownership sentinel, and returns the file content inline this turn via a TOCTOU-safe (`O_NOFOLLOW`) read; `context_remove_file` can remove only model-added files, never the user's pins; `context_list_files` reports ownership. The guardrail is a per-file token limit, not an aggregate budget, and user-pinned files are never token-limited by design.
Also called: agent context management, model-pinned files, self-service context, read-into-context tool. <!-- cap: ctx-model-self-curated-files -->

**Per-conversation file-state and change tracking.** For each conversation, Ziya tracks every in-context file's original, current, last-seen, and last-submitted content plus a per-line change map computed with difflib (`+` added, `*` modified). By refreshing files from disk it detects when the user did *not* apply a suggested diff, and it emits `[NNN+]`/`[NNN*]`/`[NNN ]` annotated content together with a strongly-worded "file content authority" warning that keeps the model from hallucinating edits that were never applied — the anti-hallucination grounding behind Ziya's edit loop. It also keeps a rolling applied-diff history for continuity, persists state to an encrypted, owner-only, size-guarded, corruption-backed JSON file, and evicts stale conversations (max 20, 1h TTL). Line matching is SequenceMatcher-granular rather than token/char, and the 20-conversation retention is aggressive for very heavy users.
Also called: file change tracking, conversation diff state, modified-line markers, unapplied-diff detection, file authority. <!-- cap: ctx-file-state-line-tracking -->

### Conversation management

Beyond curating context, Ziya gives the conversation itself a lifecycle: scratch threads, cross-project moves, a per-conversation display preference, an inspector, integrity repair, and durable model pins.

**Ephemeral (unsaved) conversations.** `startNewEphemeralChat` opens a conversation that lives only in browser (React) state for the session — it is never written to IndexedDB or pushed to the server, and a persistence guard strips ephemeral ids from any save so they cannot leak to disk. `promoteEphemeralToRetained` converts one into a normally-persisted conversation (clears the ephemeral flag, bumps the version, routes through the normal save path). Limits: an ephemeral conversation is lost on reload unless it is explicitly promoted, and while ephemeral it is not visible to other tabs or machines.
Also called: scratch conversation, unsaved chat, temporary session, incognito chat, draft conversation. <!-- cap: fcm-ephemeral-conversations -->

**Move / copy conversations & folders across projects.** From the sidebar a conversation can be moved or copied to another project, and a folder moved to another project. Move relocates ownership and re-scopes the conversation; copy duplicates it into the target project; a folder move re-parents the folder and its contents. Correctness depends on the bulk-sync foreign-chat guard (so a copy is not re-cloned) and on both projects being resolvable.
Also called: relocate conversation, transfer chat to project, copy chat to project, cross-project move, reparent conversation. <!-- cap: fcm-move-copy-project -->

**Per-conversation raw/pretty display mode.** Each conversation stores a display mode — `raw` (markdown source) or `pretty` (rendered output) — controlling how its messages are shown. Toggling it (`Ctrl+Shift+U`) persists the choice on the conversation record, so it survives restarts and syncs across tabs rather than being a transient view toggle. There are two modes only.
Also called: raw/rendered toggle, markdown source view, pretty print toggle, view mode, render mode. <!-- cap: fcm-display-mode-toggle -->

**Conversation info panel.** A read-only popup inspects one conversation: its id, project id, title, folder id, a message breakdown (human / assistant / system / muted / tool counts), total characters, approximate byte size, timestamps and version, its storage state (persisted in IndexedDB, not persisted, shell, ephemeral, global, or inactive), display mode, open beads, and branch / lineage-root pointers. Stats are computed from the hydrated full history, falling back to the in-state record. It only reports — no metadata can be edited from here.
Also called: conversation details, chat metadata, conversation stats, info dialog, message breakdown, conversation inspector. <!-- cap: fcm-conversation-info-modal -->

**Chat integrity scan & reconcile.** A read-only scanner detects chat ids that exist in more than one project directory — the shadow-copy corruption left by an old bulk-sync bug — chooses a canonical copy (owner-matches-directory first, then most-recently-active / most-messages), and identifies grouping and global metadata that a shadow retained. Reconciliation restores that salvageable metadata onto the canonical copy and deletes the shadows, preserving encryption on the rewrite. It is exposed through the `/api/v1/chat-integrity` and `/chat-integrity/reconcile` endpoints and a startup self-check that is warn-only unless `ZIYA_AUTO_RECONCILE_CHATS` is set. This is remediation tooling for a past bug rather than a headline feature: it is dry-run by default, the canonical-copy choice is a heuristic (hence the warn-only boot behaviour to avoid silent deletion), it only touches duplicated chat files, and it never rewrites the plaintext `_groups.json`.
Also called: duplicate conversation cleanup, shadow copy detection, chat deduplication, conversation integrity check, orphan chat repair. <!-- cap: chat-shadow-copy-integrity -->

**Server-side conversation full-text search.** A search endpoint scans conversation message text and titles server-side, streaming chat files one at a time so the whole history is never resident in memory, and decrypting transparently. It supports case sensitivity, a configurable snippet length, and scoping to the current project or all projects; the sidebar wires it into a search box that renders snippet results. This is substring/text matching — there is no stemming, ranking model, boolean operators, semantic/embedding search, or persistent inverted index, so cost grows linearly with history size on each query. (The sidebar's ordering and weighted "best match" scoring are documented in the Feature Inventory.)
Also called: chat search, conversation full-text search, history search, grep conversations, message search, cross-project search. <!-- cap: conversation-full-text-search -->

**Per-conversation / multi-scope model pin.** A conversation can store a durable `modelPreference` (a model alias string, e.g. `opus4.5`) that pins which model it uses; the same durable field exists at folder and project scope. Setting a pin mutates the record and syncs it (surviving restarts and shared across tabs); clearing it (null) falls back through folder → project → server default. A separate tab-ephemeral layer sits above this durable layer for one-off overrides. Storage only holds the alias string — validation and resolution of the alias, and the tab-only-versus-saved precedence, live in the model-config subsystem and the frontend.
Also called: per-chat model selection, conversation model override, model pin, sticky model, per-thread model, saved model preference. <!-- cap: per-conversation-model-pin -->

---

## Large PDF Reference Documents

PDFs that would blow past the context window (reference manuals, specs,
textbooks) are handled through a built-in per-document RAG path:

- Below the threshold (25k tokens / 60 pages by default, tunable via
  `ZIYA_PDF_RAG_TOKEN_THRESHOLD`), PDFs are extracted in full as before.
- Above the threshold, the PDF is replaced in context with a **stub**
  containing the native bookmark tree / table of contents (or a
  heuristic figure & table list if the PDF has no bookmarks), along
  with the first and last pages verbatim.
- The model can then pull specific sections on demand using three
  built-in MCP tools:
  - `pdf_outline(path)` — re-fetch the full outline, figures, tables
  - `pdf_read_pages(path, start_page, end_page, include_images?)` —
    read a page range verbatim, optionally with rendered page images
    (helpful for scanned or diagram-heavy pages)
  - `pdf_search(path, query, top_k?, mode?)` — BM25 keyword search
    across pages and figure/table captions; set `mode="embedding"` to
    use semantic search when `sentence-transformers` is installed

The routing decision is a ladder: an existing on-disk index, then the
caller's fast token estimate against the threshold, then a page-count
gate (≥ 60), and finally a cheap bounded byte-scan of `endobj` markers
that catches *object-dense* short PDFs — annotation-heavy files that
would be pathologically slow for the full extractor, not just long ones.
Limits: the token threshold is a fixed global default, not derived from
the active model's context window, and only `.pdf` has this
large-document path (DOCX/XLSX/PPTX get only a hard size cap).

Also called: large document handling, context-window overflow guard, PDF chunking gate, document RAG, big-PDF stubbing. <!-- cap: pdf-large-rag-routing -->

Indexes are cached under `.ziya/pdf_index/<sha1>`, content-addressed by
file path + mtime + size so any edit auto-invalidates, and persist across
restarts. Each index is built in two tiers: a cheap **light** build
(outline plus head/tail pages, other pages extracted on demand) that is
lazily promoted to a **full** build the first time `pdf_search` runs.
Per-key build locks serialize concurrent builds, promotions, and reads
so a read never sees a torn promotion, and the cache directory is created
owner-only (`0o700`). Limits: each PDF is indexed in isolation (no
cross-document index), a changed file rebuilds from scratch (no
incremental update), and embeddings are a flat `.npy` scanned linearly
rather than a real ANN index.

Also called: on-disk PDF index, per-document index, RAG index, chunk cache, index invalidation. <!-- cap: pdf-rag-index -->

---

## Document Upload & Ingestion Pipeline

**PDF text extraction.** Ziya extracts a PDF's text layer page by page,
preferring `pdfplumber` for richer output and falling back to `pypdf`
when pdfplumber is unavailable or errors. A PDF with no text layer
(scanned/image-only) returns nothing here, and the upload route then
renders page images instead; large PDFs are diverted to the RAG stub
path (above) rather than extracted whole. Limit: no OCR, and no
table-structure or reading-order reconstruction beyond what pdfplumber
provides.
Also called: PDF parsing, PDF-to-text, document loader, text-layer extraction. <!-- cap: pdf-text-extraction -->

**Document upload endpoint.** Uploaded files arrive at
`/api/extract-document`, a multipart route that dispatches by extension
through a pluggable handler registry (office/PDF documents to the
document extractor, packet captures to the pcap handler); a companion
`GET` returns the supported-extension list. It enforces a 50 MB request
cap both before and after reading, runs extraction off the event loop,
and — for large PDFs — moves the temp file into `.ziya/pdf_uploads` so
the RAG index outlives the request. No-text uploads map to a typed 422,
unknown types to 400. Limits: the 50 MB cap is hardcoded (separate from
the extractor's 200 MB disk cap) and there is no virus or content-type
sniffing beyond the file extension.
Also called: file upload API, attach document, drag-and-drop ingestion, document intake, multipart upload. <!-- cap: document-upload-route -->

**Upload safety guards.** Before any parser touches an uploaded file,
Ziya enforces an on-disk size cap (default 200 MB,
`ZIYA_MAX_DOCUMENT_DISK_BYTES`) for every format and, for ZIP-container
office formats (`.docx`/`.xlsx`/`.pptx`), sums the declared uncompressed
sizes and checks the compression ratio (defaults 1 GB uncompressed /
200:1) to refuse likely zip bombs *before* inflation. A non-positive env
value disables the corresponding check. Limits: the content-expansion
(zip-bomb) check covers only ZIP-container office formats — PDFs and
legacy formats get only the flat disk cap — and the caps are global, not
per-user.
Also called: zip-bomb protection, decompression-bomb guard, resource-exhaustion defense, upload size limit. <!-- cap: document-safety-guards -->

**Fetched-PDF re-extraction over HTTP.** When a fetch tool returns an
unparsed PDF (detected by the fetch server's "cannot be simplified to
markdown" signal plus an `application/pdf` type, or a `%PDF-` magic
header) and the tool arguments carry a URL, Ziya re-fetches the URL and
runs the bytes through the same text extractor used for local uploads,
so remote PDFs become readable text. It refuses literal
internal/loopback/RFC-1918/IMDS addresses, disables redirect following,
and applies a 30 s timeout; any failure returns the original text
unchanged. Limits: the SSRF guard blocks only literal-IP hosts (a
hostname that resolves to an internal IP is not re-checked at this
layer), there is no size cap on the re-fetch beyond the timeout, and a
scanned remote PDF falls back to the raw dump (no image-rendering path
for fetched bytes).
Also called: remote document ingestion, URL PDF loader, web PDF extraction, fetch-tool PDF recovery, SSRF protection. <!-- cap: fetched-pdf-http-reextraction -->

**PCAP TCP health analysis.** For uploaded packet captures, Ziya walks
every TCP packet and tracks per-flow sequence numbers and ACKs to detect
retransmissions, out-of-order packets, resets, FINs, zero-window
conditions, three-or-more duplicate ACKs (a fast-retransmit signal), and
unanswered SYNs (failed connections). It produces per-IP metrics, an
issue-rate percentage, a good/fair/poor health status, and a list of
problematic IPs with timestamped samples — comparable to Wireshark
expert-info heuristics. Limits: retransmission detection is
sequence-repeat-based (can false-positive on keepalives), there is no
RTT, throughput, or window-scaling awareness and no per-connection state
machine, and the pcap toolset is optional/hidden (requires `scapy`/`dpkt`).
Also called: TCP diagnostics, network health analysis, retransmission detection, packet-loss analysis, connection-failure detection. <!-- cap: pcap-tcp-health -->

---

## Vision / Multimodal

Drag images into the chat input, paste from clipboard, or use the image button. Supported on: Claude Sonnet/Opus 4.x, Claude 3.x, Nova Pro/Lite/Premier, Gemini.

Also known as: image input, paste/drag-drop image, screenshot input, vision; competitors call this image/screenshot input to the agent (codex-cli) or vision multimodal input (jan). <!-- cap: multimodal-image-input -->

---

## Diagram Rendering

Ziya renders inline diagrams from fenced code blocks. Supported formats:

| Format | Block syntax | Notes |
|---|---|---|
| Mermaid | `` ```mermaid `` | Flowcharts, sequence, class, state, ER, Gantt, etc. Auto-preprocessed for syntax compatibility. |
| Graphviz | `` ```graphviz `` | DOT language. Full layout engine. |
| DrawIO | `` ```drawio `` | XML-based diagrams with export and online editor support. |
| Vega-Lite | `` ```vega-lite `` | JSON data visualization specs. |
| HTML Mockup | `` ```html-mockup `` | Interactive UI prototypes in sandboxed iframes. Add the `figure` modifier (`` ```html-mockup figure ``) to drop the frame and controls for a graphic that is part of the discussion rather than a design under review. The iframe supplies a theme-matched foreground plus `--mockup-border` / `--mockup-muted`, so incidental text can be left unstyled and stay legible in both themes — deliberate colour (brand, status, palette, or a fixed light/dark design) is expected, and should carry its own background so the pairing does not depend on the surface behind it. |
| Packet | `` ```packet `` | Bit-level protocol frame layouts. |
| Music | `` ```music `` | Published-quality sheet music (VexFlow). Notes/chords/rests, beaming, tuplets, grace & cue notes, slurs/ties, dynamics/hairpins, articulations, ornaments, lyrics, chord symbols, pedal & harp-pedal lines, measures/repeats/voltas, mid-score key & meter changes (modulation), tempo & navigation marks, title block, multi-voice & grand staff with cross-staff beams/slurs, and multi-system wrapping. A short phrase can also be written inline as `` `music: C4/q, D4/q` ``. Malformed input (bad octave/accidental/duration, out-of-range tuplet count) degrades gracefully instead of hanging the render. |
| Plotly | `` ```plotly `` | Scientific/statistical plots and 3-D charts (Plotly.js). A preprocessor fixes cosmetically-broken-but-valid specs (title/plot collisions, colorbars outside the paper bounds, overlapping annotations, scene domains); under headless capture WebGL trace types are demoted to SVG so the single Chromium instance never exhausts its WebGL contexts and hangs the screenshot, while the interactive UI keeps WebGL. <!-- cap: viz-plotly-render --> |
| JointJS | `` ```joint `` (also `` ```jointjs ``, `` ```diagram ``) | UML / ER / generic node-link diagrams. A geometry sanitizer clamps runaway element positions/sizes/waypoints that would otherwise inflate the canvas into a resize loop and total render loss; a link-routing module draws orthogonal and curved connectors. <!-- cap: viz-jointjs-render --> |
| Chord | `` ```chord `` | Circular chord / relationship-matrix / flow diagrams (d3-chord), with dedicated spec recovery that reconstructs a usable chord spec from partial or malformed model output. A niche grammar with limited styling options versus a full charting library. <!-- cap: viz-chord-render --> |
| Force-directed | `` ```force-directed `` | Spring-layout node-link graphs (d3-force), with a sanitizer for degenerate input. Force layouts are non-deterministic, and large graphs render slowly. <!-- cap: viz-force-directed-render --> |
| Network | `` ```network `` | Grouped node-link topology diagrams — nodes, links and named groups — with viewport clamping to avoid runaway sizing. Lowest renderer priority, so it acts as the general graph fallback and only wins when no more specific renderer matches. <!-- cap: viz-network-diagram-render --> |
| Vega (raw) | `` ```vega `` | Raw Vega specs, distinct from Vega-Lite (no VL `$schema`). A sanitizer neutralizes force-transform links with missing endpoints and other degenerate geometry that would throw uncaught errors in the Vega runtime. Raw Vega is verbose and fewer model specs use it, so its preprocessing breadth is narrower than the Vega-Lite path. <!-- cap: viz-vega-render --> |
| Railroad | `` ```railroad `` | Railroad (syntax) diagrams from a JSON spec: terminals, nonterminals, choice, optional, loops with separators, dashed groups — a single production or a stack of named rules. For grammars, regex structure, and config/URL/file formats. Tolerates trailing commas, comments, and stray fences in the JSON. |
| WaveDrom | `` ```wavedrom `` | Digital timing diagrams from WaveJSON: clocks, signals, buses with labeled data, groups, gaps (`|`), and annotated node/edge arrows for setup/hold and handshake timing. Accepts the canonical JSON5 style (unquoted keys, single quotes). Dark mode uses WaveDrom's own dark skin. Also renders `reg` bit-field and `assign` logic specs. |
| Flame graph | `` ```flamegraph `` | Interactive flame graphs for performance profiles (click a frame to zoom, click the root to reset). Accepts nested JSON (`{name, value, children}`) or collapsed-stack text — the `frame;frame;frame count` lines py-spy, perf, and flamegraph.pl emit — pasted directly into the fence. Frame names may contain spaces; `#` comment lines are skipped. |
| TikZ | `` ```tikz `` | General LaTeX vector drawing. Rendered server-side. |
| CircuiTikZ | `` ```circuitikz `` | Electronic circuit schematics. |
| chemfig | `` ```chemfig `` | Chemical structures, reaction schemes, stereochemistry. |
| tikz-cd | `` ```tikz-cd `` | Commutative diagrams. |
| pgfplots | `` ```pgfplots `` | Typeset function/data plots with math-notation axes and legends, continuous with KaTeX derivations. Includes the `smithchart` and `polar` libraries. |
| forest | `` ```forest `` (also `` ```syntax-tree ``) | Labelled trees in field-standard notation: constituency/syntax trees, taxonomies, decision and game trees, phylogenies, parse trees. Bracket syntax, with roofs over elided constituents, aligned tiers, and TikZ movement arrows. Prefer graphviz/mermaid for a generic hierarchy — forest is for trees whose *notation* matters. The `linguistics` and `edges` libraries are preloaded, so `roof` and `forked edges` work without declaring them. |
| bussproofs | `` ```bussproofs `` (also `` ```prooftree ``, `` ```proof-tree ``) | Proof trees read as premises-over-conclusion: natural deduction, sequent calculus, typing rules. A stack discipline — `\AxiomC` pushes a pending subproof, `\UnaryInfC`/`\BinaryInfC`/`\TrinaryInfC` consume 1/2/3 — with `\RightLabel` rule annotations and `\fCenter` turnstile alignment. |

Rendered diagrams include **Open** (popup with zoom/pan), **Save** (SVG download), and **Source** (view/edit definition) buttons.

**Also known as** (reader and competitor vocabulary for the renderers above): Plotly — scientific plot, 3-D chart, scattergl fallback <!-- cap: viz-plotly-render -->; JointJS — UML diagram, ER diagram, node-link diagram <!-- cap: viz-jointjs-render -->; Chord — chord diagram, relationship matrix, circular flow diagram <!-- cap: viz-chord-render -->; Force-directed — force layout graph, spring layout, network graph <!-- cap: viz-force-directed-render -->; Network — topology diagram, grouped node-link, infrastructure diagram <!-- cap: viz-network-diagram-render -->; Vega (raw) — vega runtime, grammar-of-graphics <!-- cap: viz-vega-render -->; HTML Mockup — HTML preview, UI mockup renderer, wireframe preview, iframe sandbox render <!-- cap: viz-html-mockup-renderer -->; Music — ABC-style notation, sheet music, score renderer, staff notation <!-- cap: viz-music-render -->.

### Renderer coverage and internals

The diagram layer is a registry of lazily-loaded render plugins (fifteen grammars: Vega, Plotly, force-directed, chord, network, basic-chart, Mermaid, Graphviz, Vega-Lite, JointJS, D2, DrawIO, packet, music, LaTeX). Each renderer's — often multi-hundred-KB — library is downloaded only when a spec that needs it first appears, and the dispatcher selects a renderer by trying each plugin's capability check in integer-priority order (priority resolves overlaps such as Vega-Lite versus raw Vega). The set is fixed at build time — there is no runtime third-party plugin registration — and a spec that matches no renderer is retried until the render timeout. <!-- cap: viz-plugin-registry-and-selection -->
Also called: renderer registry, plugin dispatcher, diagram type router, code-splitting lazy loader, canHandle capability matching. <!-- cap: viz-plugin-registry-and-selection -->

The Mermaid path carries the deepest preprocessing: an extensible pipeline of roughly 77 registered preprocessors and error handlers rewrites model-authored Mermaid text to fix the recurrent mistakes LLMs make — label backtick/pipe escaping, sequence-diagram semicolons, requirement-diagram syntax, auto-quote repair, chain-exit invariants — before Mermaid ever parses it, and each pass can declare a postcondition regex so a rule that silently failed its own contract is flagged. Because it is rule-based, a novel malformation with no matching rule still fails; there is no learned or model-based repair. <!-- cap: viz-mermaid-spec-repair -->
Also called: mermaid preprocessor, LLM syntax normalization, diagram autofix, syntax healing, error-recovery registry. <!-- cap: viz-mermaid-spec-repair -->

More generally, model output often arrives as `{type, definition}` where the definition body carries no inner type. Each plugin lifts that body and stamps the type only when the content genuinely matches, so the correct renderer is chosen instead of falling through to a 30-second timeout, without hijacking a foreign spec. This recovery is per-plugin rather than a single shared engine — Chord, Network and Music each ship their own recovery and recognition suites — so a plugin lacking a recovery function still fails on wrapped or partial input. <!-- cap: viz-malformed-spec-recovery -->
Also called: spec normalization, definition unwrapping, type inference for specs, recovery from partial output, YAML fence extraction. <!-- cap: viz-malformed-spec-recovery -->

Graph and node-link renderers share an ELK (Eclipse Layout Kernel, elkjs) auto-layout module for node placement and edge routing, plus a standalone orthogonal router that draws right-angle connectors between fixed rectangles with obstacle avoidance. This is internal infrastructure usable across DrawIO/Mermaid/Graphviz/custom D3; how many renderers invoke it end-to-end is not fully traced. <!-- cap: viz-shared-layout-engine -->
Also called: ELK layout, elkjs, auto-layout, orthogonal router, obstacle avoidance, connector routing. <!-- cap: viz-shared-layout-engine -->

### LaTeX diagrams (server-side)

The seven LaTeX-family types above are compiled by a local TeX installation rather
than in the browser, so they need one to be present. When TeX is missing, the
diagram is not lost: the block renders as a notice with the exact `tlmgr install`
command for the packages that type needs, and the LaTeX source stays visible.

Output is **SVG** when `dvisvgm` is installed — text stays selectable and is
recoloured for dark mode — and **PNG** otherwise.

**Dark mode.** TeX draws black on transparent, which measures 1.27:1 against the
dark diagram background — invisible. SVG output is recoloured on the client:
TeX's default black becomes a light ink, and any colour you authored is lightened
only as far as it takes to clear a 3:1 contrast floor, with its hue preserved so
`\draw[red]` still reads as red. Stroke widths are never altered, because TeX's
hairline weights are part of the engraving. PNG output cannot be recoloured
selectively, so it is inverted instead.

**Sizing.** Diagrams render at their natural size rather than being stretched to
the width of the chat column. dvisvgm reports an absolute size in points, which
becomes the diagram's width, capped at the container so it still shrinks on a
narrow viewport. This matters most for small structures: a lone benzene ring is
intrinsically 70px wide, so filling an 820px column would scale it nearly 12×.

To install a minimal working toolchain:

```bash
# macOS (BasicTeX), then the packages Ziya's profiles use
sudo tlmgr install standalone dvisvgm pgf circuitikz siunitx chemfig tikz-cd pgfplots \
                   forest bussproofs
```

**No macro definitions.** `\def`, `\newcommand`, `\renewcommand`, `\let`, `\edef`
and `\gdef` are refused in a diagram body across every LaTeX type, because they
can construct unbounded expansions. Anything a notation genuinely needs is
supplied by its profile's preamble instead — `bussproofs`, for example, presets
`\fCenter` to a turnstile, since its own default (`\relax`) renders a sequent
proof with no turnstile at all rather than failing.

**Chemistry.** The `chemfig` type draws structures on its own. Two extras are
worth installing alongside it:

```bash
sudo tlmgr install mhchem      # \ce{} chemical equations, \pu{} units
```

`mhchem` is optional and loaded only if present, so its absence disables `\ce{}`
without affecting structure rendering. Lewis dot structures (`\lewis`, `\Lewis`)
need no extra package — they come from a module chemfig already ships, which Ziya
loads for you.

**Charges and lone pairs.** `\charge` separates the angle from the symbol with
`=`, not `:` — `\charge{90=\|,180=\|}{O}` — because `:` is already taken for the
optional radial offset. Both mistakes are **repaired automatically**, so either
form renders, but the rule is worth knowing since the raw errors name the wrong
cause: chemfig uses `:` for *bond* angles (`-[:30]`), so the wrong separator is
the natural first guess, and it fails with `Argument of \charge_g has an extra
}` — pointing at brace balance and never mentioning the separator. The charge
argument is also not math mode, so `\ominus` and friends fail with `Missing $
inserted`, which likewise doesn't say which argument was at fault.

The repair promotes a stand-in `:` to `=` and wraps a math symbol in `$...$`,
reporting each correction alongside the image. It is deliberately conservative
in two ways. The math wrap fires **only** for payloads containing a TeX control
word, because wrapping is not always neutral — `\charge{90=-}` renders
*differently* in math mode (a text hyphen is not a math minus), while `\|`, `+`
and `2+` are byte-identical either way, so blanket wrapping would silently
alter diagrams that already worked. And a genuine offset survives: in
`45:2pt:\|` only the *last* colon is promoted. An undefined command such as
`\+` is left to fail, since that is a different error and guessing at intent
would trade a clear message for a wrong structure.

For plain lone pairs `\lewis{1:5:7:,O}` is usually less fiddly than stacking
`\|` marks by angle.

`\ce{}` also works in ordinary prose math (`$...$` / `$$...$$`) with no TeX
installation at all, since the browser's KaTeX renderer loads the mhchem
extension.

**Electron-pushing arrows.** Diagrams using `\chemmove` (or TikZ
`remember picture` overlays) are always rendered as PNG. These resolve
coordinates recorded during a previous compilation pass, which the DVI→SVG
driver places incorrectly — an SVG would render successfully but silently omit
the arrow. PNG is chosen instead, at the cost of selectable text and dark-mode
recolouring for those particular diagrams.

**Ring-closure lint.** chemfig ring specifications are checked before
compilation, because an under-specified ring is not a syntax error: chemfig
draws the bonds it was given, leaves the ring open, and the pipeline reports
success — so the output is a picture of a *different molecule* with nothing in
the TeX log to indicate it. The rule is that a standalone `*n(...)` ring needs
`n` bonds while a **fused** one needs `n-1` (it inherits its closing edge from
the ring it is nested in), and a ring nested inside a *branch* is pendant
rather than fused, so it still needs all `n`.

The count ignores branches, bond options and brace groups, since each can
legitimately contain a bond character that is not a ring bond — `(-OH)` is a
substituent, `-[:-30]` carries a negative angle, and `SO_{4}^{2-}` ends in a
minus sign. This is the trap in practice: `*5(-(=O)-(=O)-)` looks like five
bonds but has three, so a carbonyl-rich ring reads as complete when it is short.

Rings missing exactly one bond in an unambiguous case — an even ring whose
bonds strictly alternate, i.e. a Kekulé aromatic ring — are closed
automatically, which is positionally safe (substituents keep their relative
placement, so a para pair stays para). Everything else is reported and left
alone: the bond *order* of an added bond is genuinely ambiguous for odd rings,
and guessing would trade a visibly broken ring for a plausible-looking wrong
structure. Both corrections and warnings are returned alongside the image, so
a model iterating on a diagram sees the defect rather than only the success.

**Skip-edge rerouting**: For Mermaid diagrams with feedback/control-loop edges that span multiple nodes, a post-render rerouter automatically arcs those paths above or below intermediate nodes instead of drawing them straight through. Arcs are nested by skip distance — shorter-range arcs sit closer to the node row, longer-range arcs arc further out — so overlapping edges remain visually distinct even when several skip edges share the same side.

### Visual Diagram Feedback (Builtin Tool)

The `render_diagram` builtin tool renders a diagram specification server-side and returns the resulting image directly as a vision content block, enabling the model to *see* what was rendered and evaluate correctness. This supports an iterative refinement loop:

1. Model defines a diagram spec (mermaid, graphviz, vega-lite, drawio, packet, etc.)
2. `render_diagram` tool renders it via the headless Playwright pipeline
3. Model receives the PNG image and analyzes it with vision capabilities
4. Model identifies rendering issues (missing labels, broken layout, wrong colors)
5. Model fixes the spec or pipeline code, re-renders, and re-checks

This is particularly useful for validating the rendering pipeline itself — systematically testing each diagram type and fixing any rendering issues discovered.

The tool uses the same headless Chromium renderer as the export API, running through the full frontend D3Renderer pipeline with all plugins and post-render enhancers.

### Headless Diagram Export (API)

Diagrams can be rendered to PNG or SVG images server-side via the REST API, enabling integration with external services like Slack, CI pipelines, or documentation generators.

The headless renderer uses Playwright to drive a real Chromium instance through the same frontend rendering pipeline as the chat UI — including all post-render enhancers (edge rerouting, theme application, layout fixes). This guarantees pixel-perfect output.

**Setup** (the Chromium build is a post-install step pip cannot run):
```bash
ziya-install-extras --browser
```

**API**:
```bash
curl -X POST http://localhost:6969/api/render-diagram \
  -H "Content-Type: application/json" \
  -d '{
    "type": "mermaid",
    "definition": "graph LR\n  A-->B-->C",
    "theme": "dark",
    "format": "png"
  }' \
  --output diagram.png
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `type` | string | required | `mermaid`, `graphviz`, `vega-lite`, `drawio`, `packet`, etc. |
| `definition` | string | required | Diagram source text or JSON spec |
| `theme` | `dark`\|`light` | `light` | Color theme |
| `format` | `png`\|`svg` | `png` | Output format (SVG falls back to PNG for canvas renderers) |
| `width` | int | auto | Explicit width in pixels |
| `height` | int | auto | Explicit height in pixels |

### Rendered Conversation Export (API)

Conversations can be exported with server-side rendered diagram images via the REST API. This enables plugin export targets (Slack, Quip, wiki), CLI-driven exports, and API consumers to get fully rendered output without a browser.

The pipeline extracts all diagram code blocks (mermaid, graphviz, vega-lite, drawio, packet, etc.) from the conversation, renders each through the headless Playwright pipeline, and embeds the resulting images inline in the exported markdown or HTML.

**API — Server-rendered export**:
```bash
curl -X POST http://localhost:6969/api/export/rendered \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "human", "content": "Draw an architecture diagram"},
      {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B-->C\n```"}
    ],
    "format": "markdown",
    "theme": "dark"
  }'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `messages` | array | required | Conversation messages with role and content |
| `format` | `markdown`\|`html` | `markdown` | Output format |
| `theme` | `dark`\|`light` | `light` | Color theme for diagram rendering |
| `target` | string | `public` | Export target ID (for metadata) |
| `image_format` | `svg`\|`png` | `svg` | Image format for embedded diagrams |

**API — Export to plugin target**:
```bash
curl -X POST http://localhost:6969/api/export/to-target \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [...],
    "target_id": "slack",
    "theme": "dark"
  }'
```

Plugin export targets are registered via the `ExportProvider` interface. No concrete target ships in-tree — `get_export_providers()` returns empty by default — so plugin export is inert unless a third-party `ExportProvider` plugin is installed. See `app/plugins/interfaces.py` for the contract. <!-- cap: export-plugin-targets -->

### Conversation PDF Export

Exporting a conversation to PDF (the **PDF** option in the export modal) is
**server-side rendered**. The client `POST`s the raw conversation to
`POST /api/export/pdf`; the server renders the whole conversation through the
real frontend `MarkdownRenderer` pipeline at a hidden `/print` route and
captures it with headless Chromium (`page.pdf()`, A4, `printBackground`). It is
not the browser's "Print to PDF" of the on-screen chat — it is a dedicated,
light-themed render built for print.

Because it drives the same rendering pipeline as the chat UI, the PDF preserves
what the old client-side print path dropped:

- **Syntax highlighting** (Prism) and **code-block backgrounds**.
- **Diff add/remove colors** and **text highlight** colors, via
  `print-color-adjust: exact` in the shared print stylesheet.
- **Rendered diagrams** (mermaid, D3, graphviz, etc.) as images, including
  `<canvas>` renderers rasterized to `<img>` so their pixels survive.
- **KaTeX math**, tables, and `<details>` blocks.
- **Dark-mode content forced to light** so diagrams and code composite onto the
  white page instead of leaving dark bands (baked dark mermaid themes are
  normalized to light before rendering).
- **Sane pagination** — oversized figures are scaled to fit one page, headings
  are kept with their content, and long code/diffs split across pages.

The export is also built as a **finished document**, not merely a correct one:

- **Flow-aware figure sizing.** A figure that technically fits a page but cannot
  fit alongside the prose that introduces it — so it would otherwise be bumped
  whole onto its own page, stranded from its context behind an empty band — is
  shrunk the *minimum* needed to keep it with its introducing text. The shrink
  is bounded: **0.75 is the floor** for flow-driven shrinking (never smaller for
  flow reasons), while a genuinely oversized figure may still scale below 0.75
  purely to fit one page. The two cases are tagged (`data-print-fit-reason` =
  `flow` vs `oversize`).
- **Live-session chrome and superseded diffs are excluded.** Content that
  belongs to the interactive session but not to a document is dropped in print
  mode: a **superseded diff** (an earlier diff the assistant corrected later in
  the same message — the app merely fades it to 0.45 opacity, which would land
  in the PDF as low-contrast noise and inflate the page count) is omitted
  entirely, keeping only the final version; and live-session **UI-chrome notes**
  (the "Auto-added N file(s) to context … Remove via the A button in the Files
  panel." banner, context-enhancement warnings, "Checking context…" spinners)
  are stripped. Both are suppressed at the shared `/print` route, so the HTML
  export inherits the same hygiene.
- **A diff header stays with its diff body.** A "Modify: `<path>`" header is
  bound to the diff it introduces (a `break-after: avoid` relationship) and diff
  tables are allowed to *flow* across a page boundary rather than being forced
  whole onto a fresh page, so a header is never stranded at a page bottom with a
  large empty band above its body.
- **Wide tables are fit-scaled.** A markdown table far wider than the printable
  content width is uniformly scaled down (mirroring the oversized-figure fit) so
  its right-hand columns are no longer clipped off the margin; narrow and
  ordinary-width tables are left untouched.
- **A navigable outline.** The PDF carries a **bookmark tree with one entry per
  message** ("You (message N)" / "Ziya (message N)"), synthesized during
  capture, so a long conversation can be navigated in any PDF viewer.
- **Live hyperlinks.** URLs in the conversation body and the footer are real
  clickable `/Link` annotations, not just blue text.
- **Sensible document metadata.** `/Title` (the conversation title), `/Author`
  ("Ziya"), `/Creator` ("Ziya PDF Exporter"), `/Subject`, and creation date are
  set rather than left at the headless-Chromium defaults.
- **Embedded fonts.** Every font is embedded (Type0 CID subsets; diagram text
  uses self-embedded Type3 fonts), so the document renders identically on a
  machine that does not have those fonts installed.
- **Vector diagrams.** mermaid/D3 diagrams reach the PDF as vector path
  operations (not rasterized bitmaps), so they stay crisp at any zoom.

Option filtering (`roundLimit` / `includeHuman` / `includeCollapsed`) happens in
the `/print` page, so the PDF and HTML exports share one implementation — which
is why the client sends raw messages instead of pre-filtering them.

**Fallback.** If the server has no Playwright/Chromium installed,
`/api/export/pdf` returns HTTP `501` and the modal falls back to the
**client-side** renderer (`frontend/src/utils/pdfExport.ts`), which rasterizes
the live DOM and opens the browser print dialog. This is lower fidelity (subject
to the user's print settings) but ensures a PDF is always available.

**Troubleshooting:**

| Symptom | Cause / Fix |
|---|---|
| "Server PDF renderer unavailable; used the browser print dialog" | Playwright/Chromium not installed server-side (`501`). Run `ziya-install-extras --browser`, then restart Ziya, to get the high-fidelity path. |
| PDF export errors with a non-501 status | A render error or a missing conversation. `404` = conversation id not found; `400` = no message source; `500` = a render failure (check the server log for the `/print` console/pageerror diagnostics the renderer captures). |
| Export hangs / times out | The `/print` render never reached `data-render-status="complete"` (a very large conversation, or a diagram that never settled). The session has a bounded safety timeout; retry, and check the server log for stuck renders. |
| Colors or diagrams missing after a frontend change | The `/print` route lives in the built bundle. After editing the print path or `frontend/src/styles/print.css`, rebuild: `cd frontend && npx craco build`. |
| A very wide table has its right columns cut off | Fixed: tables far wider than the printable width are now uniformly fit-scaled so their right columns are no longer clipped (narrow tables are untouched). Long unbroken code lines also wrap instead of clipping. If a table still looks cramped, it was scaled to fit the page width. |

The fidelity of this path is verified by the shared export-fidelity harness
under `tests/export_fidelity/` (see `Docs/CONTRIBUTING.md` and the harness
`README`-style module docstrings): a fast wiring tier runs on every test
invocation, and an `integration`-marked tier drives a real headless render.

### Conversation HTML Export

Exporting a conversation to HTML (the **HTML** option in the export modal,
reachable via **Download** — saves a `.html` file — and **Paste** — GitHub Gist
and plugin targets) is **dual-mode**. Clipboard copy always forces Markdown and
is out of scope here.

- **High-fidelity mode (route-driven).** When Playwright/Chromium is available,
  HTML is generated by driving the same shared `/print` route the PDF path uses
  and extracting self-contained HTML from the rendered DOM. This gives
  real-renderer fidelity (Prism syntax highlighting, `react-diff-view` per-line
  diff coloring, KaTeX math, tables, diagrams as images) because it is the exact
  chat rendering pipeline.
- **Fallback mode (Python).** When no browser is installed, HTML is produced by
  the pure-Python exporter (`app/utils/conversation_exporter.py`,
  `_export_as_html` → `_markdown_to_html_basic`). HTML export **never
  hard-fails** merely because a browser is absent. The fallback has a lower
  fidelity ceiling than the real renderer but is itself faithful: it now
  delivers **syntax highlighting** (Pygments, inline-styled token spans),
  **per-line diff add/remove backgrounds**, **KaTeX math** (rendered to
  self-contained MathML via a Node subprocess), **GFM tables** as real
  `<table>` grids, and a **light-pinned** document that stays light even when
  opened on a dark-mode machine.

**What each mode preserves**

| Aspect | Route-driven (browser) | Python fallback |
|---|---|---|
| Syntax highlighting | Prism (chat theme) | Pygments inline spans |
| Diff add/remove color | `react-diff-view` per line | per-line background spans |
| Math | KaTeX HTML+fonts | KaTeX MathML (no external fonts) |
| Tables | full renderer | real `<table>` grids |
| Diagrams | embedded images | embedded images (via `/api/export/rendered`) |
| Dark-mode independence | light-pinned wrapper | light-pinned wrapper |
| Self-containment | inlined CSS + data-URI assets | inlined CSS + data-URI assets |

Both modes produce a **self-contained** document: all CSS is inlined and any
diagram images are embedded as data URIs / inline SVG — opening the `.html` with
the network disconnected loses nothing. Both modes **neutralize XSS**: prose is
HTML-escaped before any tag is generated and `javascript:` / `vbscript:` /
`data:` link schemes are rejected, so untrusted conversation content renders as
inert text in either mode (a hard gate, verified by the fidelity harness).

**How to tell which mode produced the output.** The route-driven mode requires a
live Ziya server whose built bundle includes `/print`. When Playwright/Chromium
is unavailable the exporter transparently uses the Python fallback; the returned
export metadata records the format, and the fallback's markup carries the
Pygments/MathML/`<table>` structures described above rather than the chat
renderer's Prism/`react-diff-view`/`.katex`-HTML classes.

**Troubleshooting:**

| Symptom | Cause / Fix |
|---|---|
| Downloaded `.html` opens dark on a dark-mode machine | Should not happen — the document is pinned to `color-scheme: light` and carries no `prefers-color-scheme` dark block. If you see this, you are on a stale build; regenerate the export. |
| Code blocks show no colors in the fallback | Pygments not importable server-side. Reinstall (`pip install pygments`); the exporter degrades to an uncolored block rather than failing. |
| Math shows as literal `$$...$$` in the fallback | `node` not on `PATH` or `frontend/node_modules/katex` missing. Install the frontend deps and ensure `node` is available; math degrades to escaped LaTeX otherwise (still valid HTML). |
| A markdown table shows as literal `\| --- \|` pipe text | Should not happen — GFM tables render as real `<table>` grids in both modes. If you see this you are on a stale build. |
| Diagrams missing from the exported HTML | Diagram images are embedded only when exporting through `POST /api/export/rendered` (server-side diagram render); a plain paste without captured diagrams keeps diagram source as a code block. |

The HTML fidelity of both modes is verified by the same
`tests/export_fidelity/` harness (18 checks per fixture variant across light and
forced-dark), including `self_containment`, `dark_mode_independence`,
`diff_coloring`, `syntax_highlighting`, `math_rendering`, `table_rendering`,
`structural_validity`, and `xss_neutralized`.

---

### Conversation Markdown Export

Exporting a conversation to Markdown is the **most-used export path**: the
**Copy to clipboard** action in the export modal **always** produces Markdown
regardless of the format selector, **Download** saves a `.md` file, and
**Paste** targets GitHub Gist plus any plugin-registered export targets (none ship in-tree). All three run the same pure <!-- cap: export-plugin-targets -->
Python exporter (`app/utils/conversation_exporter.py`, `_export_as_markdown`);
it never requires a browser and never goes through the `/print` route.

**What the Markdown export preserves (losslessly):**

- Full conversation content — every user and assistant turn, in order, under
  `## 👤 User` / `## 🤖 AI Assistant` headings separated by horizontal rules.
- Code blocks in **language-tagged fences** (so the consumer applies its own
  syntax highlighting), and diffs in **` ```diff ` fences** (so Gist colors
  them with its native diff highlighter).
- **Math** verbatim (`$…$` inline and `$$…$$` block), **GFM tables**, and
  collapsible `<details>` sections.
- **Diagrams**: rendered to embedded images when the frontend captured them,
  otherwise the diagram **source fence is preserved** (Gist renders mermaid
  natively; other viewers show the code) — never dropped.
- Fences are **balanced per message** and **tool-output wrappers are widened**
  past any interior backtick run, so no fence can "run away" and swallow the
  rest of the document or leak tool text as prose.

**What it deliberately excludes (export hygiene):**

- **Superseded diffs.** When the assistant re-diffs a file it already diffed
  earlier in the same message, the UI greys the earlier one out (opacity 0.45).
  Markdown has no opacity, so a retained stale diff would be indistinguishable
  from the live one. The exporter ports the frontend supersession algorithm and
  **drops superseded diffs**, keeping only the final one.
- **Live-session UI chrome.** The "Auto-added N file(s) to context … Remove via
  the A button in the Files panel." banner and the "Checking context…" spinner
  are live-session affordances with no meaning in an exported document; they are
  **stripped**. The real answer next to them is preserved.

**What it does not attempt:**

- **Color is the consumer's job.** Markdown has no native color, and the export
  does **not** inject raw HTML `<span style>` / `<mark>` to force diff or
  highlight colors. Doing so would break the plain-text clipboard path and be
  stripped by Gist's sanitizer anyway (see the export-fidelity notes). Diff
  fences and language tags let the *consumer* colorize; that is by design, not a
  gap.
- **System and empty messages are skipped.** `role == "system"` messages and
  messages whose content is empty/whitespace are omitted — the **same policy the
  HTML export applies** — so an export of a system-only or empty conversation
  yields just the header and footer (it never crashes). Whether to surface
  system-prompt content in exports is an open product decision, applied
  consistently across formats rather than diverging per-format.

Markdown fidelity and hygiene are verified by the `tests/export_fidelity/`
harness (`md_fence_integrity`, `md_tool_block_fence_integrity`,
`md_diagram_embedding`, `md_math_preservation`, `md_table_integrity`,
`md_structural_sanity`, `md_roundtrip_legible`, plus the format-neutral
`no_superseded_diffs` and `no_ui_chrome` hygiene checks).

---

## Local Voice Input

The web composer supports microphone input using local `faster-whisper`
transcription. Recorded audio is sent only to the local Ziya server and is
never submitted to a browser speech service or AI model provider.

No separate installation command is required. On first use, clicking the
microphone installs `faster-whisper` through the exact Python interpreter
running Ziya, so virtual environments and pipx installations are handled
correctly. Recording begins immediately after installation completes. The first
transcription also downloads the selected Whisper model into
`~/.ziya/models/whisper/`; subsequent recordings reuse it. The default `base`
model runs on CPU with `int8` computation.

| Variable | Default | Description |
|---|---|---|
| `ZIYA_WHISPER_MODEL` | `base` | faster-whisper model name or local model path |
| `ZIYA_WHISPER_DEVICE` | `cpu` | CTranslate2 device: `cpu`, `cuda`, or `auto` |
| `ZIYA_WHISPER_COMPUTE_TYPE` | `int8` | Compute type such as `int8`, `float16`, or `default` |

Microphone capture requires a secure browser context. `http://localhost:6969`
qualifies, but a plain-HTTP LAN address generally does not.

## Thinking Mode

Some models support extended reasoning before responding:

- **Adaptive thinking** — Sonnet 4.6, Opus 4.6: controllable effort (`low` through `max`), enabled via model settings panel
- **Extended thinking** — Sonnet 3.7, Sonnet/Opus 4.0–4.5, Nova Pro/Premier: enable via model settings panel
- **Gemini thinking levels** — Gemini 3 Pro/Flash: `low`, `medium`, `high`, set in model settings

## Multi-Region Routing (Bedrock)

Models available in multiple AWS regions benefit from automatic region failover on throttle. When a request is rate-limited in the primary region, Ziya transparently retries in an alternate region before surfacing the error.

**How it works:**
- Models with cross-region inference profiles (e.g. `us.`, `eu.`, `global.` prefixes) are eligible
- Each region is weighted; the user's configured region gets a preference bonus
- When a throttle or overloaded error occurs, the request is retried once in the highest-weighted alternate region
- Throttled regions have their weight temporarily reduced, shifting subsequent requests toward healthier regions
- Weights recover automatically after a cooldown period (default: 2 minutes)

**Eligible models** (those with multi-region model IDs):
- Sonnet 4.0, 4.5, 4.6
- Sonnet 3.5, 3.5-v2
- Opus 4.6

**Environment variables:**
| Variable | Default | Description |
|---|---|---|
| `BEDROCK_REGION_COOLDOWN_SECS` | `120` | Seconds before a throttled region recovers full weight |

---

## CLI Mode

Ziya provides a full terminal interface alongside the web UI. All commands use the same model, credentials, and MCP tools as the server.

### Commands

| Command | Description |
|---|---|
| `ziya chat [FILES...]` | Interactive terminal chat with optional file context |
| `ziya ask "question" [FILES...]` | One-shot question — prints the answer and exits |
| `ziya review [--staged\|--diff] [FILES...]` | Code review with optional custom prompt |
| `ziya explain [FILES...] [--prompt "..."]` | Explain code from files or stdin |

### In-Session Commands

Inside `ziya chat`, the following slash commands are available:

| Command | Description |
|---|---|
| `/add <file\|dir>` | Add files or directories to conversation context |
| `/rm <file\|pattern>` | Remove files from context |
| `/files` | List files currently in context |
| `/shell <subcommand>` | Manage shell command allowlist (`add`, `rm`, `reset`, `yolo`, `git`, `timeout`) |
| `/goal <text>` | Set an autonomous goal (synthesizes + launches a task card) |
| `/tune <key> <val>` | Adjust session settings (e.g. `/tune iterations 50`) |
| `/model [name]` | Switch model or open interactive model picker |
| `/clear` | Clear conversation history |
| `/reset` | Clear history, context files, and all session state |
| `/suspend` | Save session and exit |
| `/resume` | Restore a previous session |
| `/join [id\|title]` | Attach to a live GUI conversation for this project (shared, synced) |
| `/help` | Show command reference |

Also called: slash commands, in-session commands, command palette, REPL commands, meta-commands, chat commands. The whole in-session surface is a **declarative slash-command palette** (Ziya: `cli-slash-command-palette`): ~20 top-level commands with aliases, subcommands and third-level options are all derived from a single `COMMAND_SPEC` source of truth, so completion, the inline `?` help and dispatch always stay in sync. A trailing `?` prints the next level's option table and `//` escapes to send a literal message beginning with a slash. Limit: the palette is not user- or plugin-extensible at runtime — adding a command means editing the spec in source. <!-- cap: cli-slash-command-palette -->

Also known as: autonomy dial / autonomy-level controls, autopilot vs supervised mode, YOLO mode, trusted-command allowlist, write-autonomy tiers; competitors call this Autopilot vs Supervised execution modes + Trusted Commands (kiro) or an autonomy-level dial with org-enforced deny-lists (factory). <!-- cap: autonomy-level-controls -->

Also known as: declarative permission rules, per-command / per-path permission rules, repo-committed permission config, write policy; competitors call this granular per-command/per-path permission rules checked into git (opencode). In Ziya these live in the `.ziya/tasks.yaml` `allow` block and the write-policy allowlists rather than a file named "permissions". <!-- cap: declarative-permission-rules -->

### /goal — Autonomous Goals

<!-- cap: lint-test-fix-repair-loop -->
The `/goal` command lets you define a verifiable objective and have Ziya work on it autonomously. Under the hood it auto-synthesizes a Task Card with an Until block and **stages** it — the inline tile shows a `staged` badge with **Run** and **Discard**, so you can review the synthesized instructions and grant permissions before the agent starts working, rather than discovering both mid-run. This staged-review step is the web-UI flow; from the CLI, `/goal` launches the synthesized card directly against a running Ziya server (there is no staged badge on the command line). <!-- cap: cli-goal-autonomous --> The Until stop condition is evaluated by a **model-judged** check on each pass; deterministic expression gates (e.g. keying completion off a shell command's exit status) are not yet implemented, so completion is a model judgement rather than a hard, verifiable gate.

Also known as: reusable/parameterized workflows, recipes, playbooks, macros — Task Cards with State-block variables and `{{var.NAME}}` templating; competitors call this Recipes (goose). Ziya has the reusable, parameterized, templated deck (~85% of the idea) but not goose's one-click deeplink package export/import. <!-- cap: reusable-parameterized-workflows -->

Also known as: a lint-and-fix / test-and-fix repair loop — the Until block driving the built-in "Fix until tests pass" skill; competitors call this an integrated lint/test repair loop (aider). Note the stop condition is model-judged, not yet a deterministic exit gate. <!-- cap: lint-test-fix-repair-loop -->

```
/goal fix all TypeScript errors in frontend/src
/goal migrate from Pydantic v1 to v2 with all tests passing
/goal refactor the auth module to use dependency injection
```

If any task in the synthesized card requests shell commands or writes outside
the default safe set (`.ziya/`, `/tmp/`), the staged tile says it needs signing
and lists how many blocks are affected. **Run** still works — those blocks are
clamped to the default floor rather than the run being refused — but signing
first (via the `ziya-approve` command shown in Task Cards) is what makes the
extra permissions actually take effect.

The agent iterates (up to 15 times by default), re-evaluating whether the goal is met after each pass. Progress is visible via the inline task tile.

**Subcommands:**

| Command | Description |
|---|---|
| `/goal status` | Show the active goal's progress |
| `/goal pause` | Pause the running goal |
| `/goal resume` | Resume a paused goal |
| `/goal clear` | Cancel and remove the goal |

> **`/goal pause` vs. the task-run tile Pause button.** These are different
> mechanisms. `/goal pause` stops the goal's run (it goes to `cancelled`) and
> `/goal resume` **relaunches the card from scratch** as a fresh run — right
> for a goal's Until-loop, which re-evaluates repo state each pass. The
> **Pause button** on a task-run tile is a true in-place hold: the same run
> pauses at the next boundary (between Repeat iterations, sequence siblings,
> or `until` loops — the same boundaries Cancel uses), keeping loop progress
> and in-memory context, and **Resume** continues it. An in-flight Task/LLM
> step always finishes before the hold takes effect. A held run shows a
> distinct non-terminal `paused` status.

#### Force-stopping a stuck run

**Cancel** is honored at the same boundaries as Pause, so a run whose
in-flight step never returns — a hung shell command, a model stream that
stalls without erroring — keeps reading `running` after Cancel is pressed.
When that happens the tile swaps the Cancel button for **Force stop**: it
appears as soon as a requested cancel has not landed, or once a running tile
has been silent for ten minutes (the age label turns amber at two). Force
stop interrupts the in-flight block in place and records the run as `held`
(reason `user_abort`) at that block, so the recovery banner offers **Resume
from here** exactly as it does for a credential fault or a server restart:
completed blocks and banked iterations are replayed from disk and only the
interrupted block is redone. `POST /task-runs/{id}/cancel?force=true` is the
same lever from the API.

#### Step-debugging a run

Alongside **Pause** and **Resume**, a live run tile has a labelled **Step**
button: it advances
the run by exactly one block and holds again, which is how you walk a complex
card while building it rather than launching it and watching it die. Clicking
repeatedly queues more steps (the tile shows `held +N` for unspent ones).

Also known as: trajectory inspector, run navigator / execution timeline, step-debug browser, run replay (Ziya's `TaskRunMap` + progress trail); competitors call this a Trajectory Inspector / step-debug browser (swe-agent). <!-- cap: trajectory-inspection-replay -->

Stepping works on a run that is already going, not just a paused one — the step
takes control, so the run advances to its next boundary and stops there.

Granularity is a whole block, because Step reuses the same three hold points as
Pause and Cancel (sequence siblings, Repeat iterations, `until` loops) and adds
none. Stepping past a Task runs that entire Task, including all of its LLM
iterations and tool calls; there is no mid-Task stop. One step buys one unit of
real work at any nesting depth — descending into a group or starting a new loop
iteration is free.

A held run keeps its `held` chip when the tile is collapsed to its one-line
receipt, so a run waiting on you is distinguishable from a finished one — but
the receipt carries no buttons, so expand the tile to reach Step and Resume.

#### When a finished tile folds itself away

A tile that finishes while you are not touching it collapses to its receipt
after 8 seconds. Interacting with it — clicking, typing, selecting trace text —
pushes that out by a quiet period, so reading is never interrupted mid-sentence.

Expanding a tile **by hand** does more: it pins the tile open, and only
collapsing it yourself closes it again. A run held on an infrastructure fault
never auto-collapses at all, since the receipt offers no way to resume it.

#### When infrastructure breaks under a fan-out

A held run is one that stopped because the *environment* broke — expired
credentials, a lost endpoint, throttling that outlasted its retries — rather
than because the work failed. The distinction matters because the two ask for
opposite responses: a failed run needs the card or the code fixed, a held run
needs only the infrastructure back before it continues from where it stopped.

Inside a wide fan-out this is not a single event but a collapse. When one
subagent's credential dies, its siblings are usually about to hit the same wall,
so the hold reports its **breadth**, not just the first fault: how many
subagents faulted out of how many ran, which kinds, and the call path from the
outermost card down to the subagent that raised it. A `fleet-wide` marker
separates "the credential died and took all 20 auditors" from "one auditor got
throttled" — both are infrastructure faults, and only one of them means you
should stop and go fix something.

Whether the remaining subagents are cancelled depends on the kind. An
authentication fault is session-level: one means every sibling is already dead,
so the fan-out is cut short immediately rather than burning the rest against a
dependency known to be gone. Throttling and transient service errors are
per-request and have already survived several retries with backoff, so a single
one never aborts a healthy fan-out — the run holds, but the siblings that can
still finish do. A proportion of the fan-out failing that way does gate it;
`ZIYA_TASK_INFRA_GATE_RATIO` (default `0.34`) sets that fraction, so the threshold
scales with the width of the fan-out rather than being a fixed count.

The conversation list carries a **gear per run status**, so what a chat's tasks
are doing is legible without opening it. The gear is colour-coded to match the
run tile — blue spinning for running, violet static for paused or held, green
for done, red for failed, amber for partial or cancelled — and animates only
while something is genuinely progressing, since a spinning indicator is how you
decide to keep waiting rather than intervene.

Where a conversation holds more than one task, each status gets its own gear
with a count beside it: "2 done, 1 held" is a different situation from either
"3 done" or "1 held", and collapsing them to a single winner would hide
whichever one you were looking for. Needs-attention states are ordered first so
a problem cannot be pushed off the end of a narrow row by successes. Counts
appear from two upward — a "1" beside a lone gear is noise. Retry attempts count
once, not once per attempt, so a card retried twice reports one gear rather than
three.

This matters most for terminal states. The gear previously meant only "a task is
running", so every stopped state — done, failed, cancelled, partial, held —
rendered as nothing at all, and a conversation whose overnight study died on an
expired credential looked identical to one that had never run a task.

The indicators cover **every** conversation, not just the one you have open.
A run that finishes, fails, or holds in a conversation you have not visited
still updates that row, which is the whole point of a background indicator: the
work worth being told about is the work you are not currently watching. This is
polled from a small server-side projection rather than the run records
themselves — those carry block states, iteration summaries and artifacts and
are encrypted at rest, so reading them all to learn a few status strings would
cost work proportional to your entire run history on every tick. Only the
project you have open is polled, at a 40-second interval, and only while
something can still change on its own; polling pauses while the window is in
the background and refreshes on return. On a project with two hundred runs of
history an idle check costs about four hundredths of a millisecond, so having
many projects and a long task history does not accumulate cost.

Also called: run status cache, status projection, sidebar run counts, background run indicator, lineage collapse. <!-- cap: run-status-index -->

Because a hold propagates up through nested cards, the run map marks every
row with its position relative to the fault, so you do not have to open each
subagent to find out which one broke. The block that raised it reads
**HELD HERE**; the containers above it read **holding**, since they cannot
finish while a step below them is stopped; and anything that never got to run
reads **blocked**. Hovering any of them explains the fault and its breadth.

A called card can also answer the question from its own side. A Call runs
inline in the caller's run, so a six-card study produces one run record owned
by the outermost card — which meant opening one of the inner cards directly
showed nothing, even while that card was the one holding the study. Opening it
now resolves its own portion of the blocking tree: which of *its* blocks is
held, which of its stages are blocked behind it, and the same breadth and
remedy the caller shows. A hold in a sibling card is reported as context only
and never marked on this card's blocks, since pointing at a card that is fine
is worse than showing nothing.

Within a fan-out, the iteration dots separate the subagents that actually
faulted from the ones the gate cancelled — a cancelled sibling was killed
deliberately because a peer hit dead infrastructure, so it is not counted as
a failure of the work.

Resuming a wide parallel fan-out does **not** re-run every iteration. Picking
a single iteration is refused — parallel iterations do not depend on each
other, so there is no ordering for "resume at 3" to mean — but the block-level
retry that remains banks every iteration that already produced a result and
executes only the ones that never finished. For a 20-agent audit that lost one
subagent to an expired credential, that is one subagent re-run, not twenty.
Stages *before* the loop replay from record as usual. Clicking an iteration of
a parallel loop therefore explains the refusal and points at the block-level
retry, since taking it costs nothing.

#### Resuming a finished run from a block

A run that died partway through used to be unrecoverable: the only option was
relaunching the card, discarding every block that had already succeeded.

A stopped run now leads with a **recovery banner** naming the block it stopped
at, with **↻ Retry \<block\>** and **▶ Continue past it**. Both start a *new*
run that replays the earlier blocks' recorded results instead of re-running
them; they differ only in whether the named block itself runs again — continue
is what you want after fixing the cause by hand.

This is separate from **Restart** in the tile header, which relaunches the card
from the beginning and keeps none of the run's progress. The banner says so,
because Restart is the more prominent control and is usually the wrong one for
a run that got partway.

For a deliberate choice other than the stopping point, every row of the block
map also carries **↻ from here** and **▶ past here** on hover — useful for
re-running from *earlier* than the failure.

What this preserves, and why:

- Earlier blocks are marked `skipped` but keep their original summaries, so
  `{{sibling("id")}}` and `{{previous_sibling}}` still resolve.
- A replayed block's failure flag is cleared — otherwise an `on_failure="stop"`
  sequence would halt before ever reaching your target.
- `state` blocks are genuinely re-run (they only write authored literals), which
  is how `{{var.NAME}}` is rebuilt.
- The original run's launch-time variable overrides are carried forward.

The source run is kept as an immutable record, so the resumed run appears as a
second tile next to it rather than replacing it. Picking a block inside a loop
body resumes from the **whole enclosing loop**, because only structural blocks
carry per-block state — so you can click any row and let the server decide.
Runs created before run snapshotting existed cannot be resumed, and show no
button.

Retrying a loop this way does **not** restart it at iteration zero. The retry
banks the iterations the loop already completed and restarts at the first one
that did not, so a run held 22 iterations into a serial campaign re-runs from
22 — you do not have to find and click the right iteration dot to get that.

The banner names that iteration rather than only the loop: the button reads
**↻ Resume \<loop\> at #22** and the note says how many iterations will be
replayed. Without it, the control that preserves 22 iterations described
itself identically to one that would re-run them. The number is a prediction
of a server-side decision, so it is worded as where execution resumes rather
than as a promise — in the two cases it can be wrong (a chained resume whose
carried iterations are not visible to the browser, or a record that disagrees
with what is on disk) the run starts *earlier or later* than named, and the
resumed run's dot strip shows what actually happened.

The rule differs by loop shape, because the shapes mean different things:

- A **serial** loop banks a *prefix*. Its iterations are dependent —
  `{{previous}}` binds the one before — so the prefix ends at the first
  iteration that failed, was never recorded, or whose full result was dropped
  past the 50-pass retention cap. Everything before that point still replays.
- A **parallel** loop banks an *index set*, since its iterations are
  independent and a gap in the middle is simply filled.

A `▶ Continue past it` on a loop is unaffected: it resumes *after* the loop,
which then replays whole as a single block.

#### Resuming inside a loop

The recovery banner already restarts a serial loop at the first iteration
that did not complete, so you do not normally need this section. It exists for
the *deliberate* choice: resuming from an iteration other than the automatic
one — earlier than the failure, or one past a result you have fixed by hand.

Iteration dots on a loop row carry the same two actions the block rows do,
applied to one iteration:

Click an iteration dot to focus it; the detail panel below then offers:

- **↻ re-run #N** re-runs that iteration.
- **▶ continue from #N+1** accepts its recorded result and runs the next one.

The buttons live in the detail panel rather than on the dot itself because
they need a sentence explaining that earlier iterations are replayed — a
user who doesn't know that will assume the loop restarts from zero.

Earlier iterations are **replayed from record** rather than re-executed, so
the first iteration that actually runs receives the same `{{previous}}` and
`{{all}}` bindings it saw originally. Blocks before the loop replay through
the existing block-level mechanism, exactly as any other resume.

The resumed run's dot strip shows the replayed iterations as **dimmed dots
preceding the ones it executed**, so the preserved work is visible as
preserved. They keep their original colour — a preserved failure still reads
red — and stay clickable, because the carried artifacts are copied onto the
resumed run. Without this the strip restarted at one circle, which was
indistinguishable from a fresh short run and read as though the banked
iterations had been thrown away.

Replayed iterations are excluded from the run's own progress figures
("N iterations passed", the partial-run classification, and failure
clustering), so an attempt is never credited with a prior attempt's results.

Two cases are refused rather than half-supported, because both would produce
a run that looks successful while feeding empty input to the work:

- **A parallel loop.** Its iterations cannot see each other, so there is no
  ordering for "resume at 3" to mean — the earlier iterations were never
  prerequisites. Retry the whole loop instead.
- **A predecessor whose full result was dropped.** Only the first 50 passing
  iterations of a loop keep their complete artifact; past that there is
  nothing to replay into `{{previous}}`. Picking a specific iteration past
  that point is refused; the block-level retry instead banks everything up to
  the cap and re-runs from there, which is a shorter prefix rather than a
  refusal.

While stepping, the status tag briefly reads `running`, because the executor
genuinely is running the block your step bought. The `held` chip beside it is
the thing to watch: it stays lit for as long as the run is under your control.

### /join — Continue a GUI Conversation from the Terminal

`/join` attaches your CLI session to a conversation that already exists in the
Ziya GUI for the same project directory, so both surfaces operate on the same
underlying chat. Run it with no argument for an interactive picker, or pass a
conversation id (or title) to attach directly:

```
/join
/join "Auth refactor"
```

You can also attach at launch:

```bash
ziya chat --join            # interactive picker
ziya chat --join "Auth refactor"
```

While attached:

- The GUI chat's `id` becomes the session's conversation id, so **beads,
  task-card results, and the GUI sidebar all track the shared conversation**.
- Each completed turn is **written back** into the GUI chat; message ids for the
  unchanged prefix are preserved so the sidebar doesn't churn.
- Turns added elsewhere — from the GUI, or another attached CLI — are **pulled
  in and previewed at the next prompt**. Your input buffer is never touched, so
  you can keep typing while sync happens.
- A `[⇄<id>]` badge on the prompt marks the attached state.

To split off a private local branch, use the existing fork mechanic: **`/save`
forks the current history into a local session and detaches** — the GUI
conversation is left untouched and your CLI continues privately from that point.
`/clear` and `/reset` also detach first (clearing local history while attached
would otherwise truncate the shared GUI chat).

> The GUI has no "join from GUI" affordance yet; attachment is CLI-initiated.
> Concurrent edits are last-writer-wins in this version.

`/join` requires the directory to have been opened in the GUI at least once (so
a project record exists); otherwise there are no conversations to join.

### Piping

Any command that accepts content also reads from stdin, so standard Unix piping works:

```bash
git diff | ziya review                      # Review uncommitted changes
git diff --cached | ziya review             # Same as: ziya review --staged
cat error.log | ziya ask "what's wrong?"    # Diagnose a log file
cat utils.py | ziya explain                 # Explain a file via pipe
```

When both a question argument and piped input are provided, they are combined:

```bash
cat handler.py | ziya ask "find the bug"    # "find the bug" + file contents
```

### Common flags

All subcommands accept the same global flags:

```bash
ziya ask "..." --model haiku-4.5            # Use a specific model
ziya review --staged --profile prod         # Use a specific AWS profile
ziya ask "..." --endpoint google            # Use Google Gemini
ziya chat --no-stream                       # Disable streaming output
ziya chat --debug                           # Enable debug logging
```

Flags can appear before or after the subcommand:

```bash
ziya --profile dev ask "explain this"       # Equivalent to:
ziya ask "explain this" --profile dev
```

### Sessions

Interactive `chat` sessions are auto-saved to `~/.ziya/sessions/`. Resume a previous session with:

```bash
ziya chat --resume                          # Interactive session picker
ziya chat --ephemeral                       # Don't save this session
```

## Task Cards & Blocks

A Task Card is a saveable, re-runnable tree of Blocks — the engine behind `/goal`, the deck, and every scheduled run. The sections below document the block grammar itself; the run lifecycle and scheduling that surround it are covered under *Scheduling & Run Lifecycle* below.

**A Task Card is a recursive tree of eight block types.** The `Block` model is a single discriminated union over `block_type` with eight kinds: `task` (an atomic model invocation / leaf), `repeat` (a count / until / for-each loop), `parallel` (concurrent children), `until` (a model-evaluated loop), `schedule` (a recurring trigger), `state` (run-scoped variables and prose), `group` (a run-once sequential container), and `call` (invoke a named external unit inline). Recursion is via a forward-referenced body list, and stacking blocks in a body is an implicit sequence run top-to-bottom; a single dispatcher (`execute_block`) branches on `block_type`, and every type has a real executor path, per-block-type frontend editors, and full deck/card CRUD. Documented at maturity 4. Limit: there is no conditional/branch (if/switch) or goto block; `until_mode='expression'` is reserved but unimplemented; and the grammar overloads one flat `Block` model with many nullable fields, so invalid field combinations are only caught at execution rather than by the type.
Also called: workflow deck, agentic workflow graph, task recipe / playbook, block DAG, reusable parameterized workflow. <!-- cap: taskcard-block-tree-grammar -->

**The Repeat block loops in three modes, with a bounded parallel fan-out.** A repeat runs its body in `count` mode (a fixed N, capped by `repeat_max`), `until` mode (re-run until the `repeat_until` substring appears in an iteration's artifact summary), or `for_each` mode (iterate a JSON/prose-embedded array, binding `{{item}}` per pass). `repeat_parallel` runs iterations concurrently, bounded by `repeat_max_concurrency` (default 8; `<=0` means unbounded) — a cap added after a 60-wide loop overwhelmed the provider. `repeat_propagate` (none|last|all) controls which prior-iteration artifacts each pass sees, and every iteration's outputs accumulate onto the loop's artifact so a wide fan-out's per-iteration parts stay retrievable. Documented at maturity 4. Limit: a `for_each` source must resolve to a JSON array; there is no reduce/accumulate primitive beyond `propagate=all`; and parallel iterations share one block id, told apart only by ordinal.
Also called: for/while loop block, map-over-list / fan-out, batch iteration, for_each map step, matrix/strategy loop. <!-- cap: taskcard-repeat-loop-block -->

**The Until block loops until a model judges a condition satisfied.** Distinct from Repeat's substring `until`: each iteration's artifact is passed to a cheap-tier model (`evaluate_condition`) that acts as a strict binary yes/no classifier on a natural-language condition. Any transport/parse failure or ambiguous reply resolves to *False*, so the loop conservatively keeps iterating rather than terminating early, and iterations are bounded by `until_max`. Documented at maturity 3, one rung below the Repeat block: it reuses the shared cheap-tier router rather than a dedicated model category, and the second mode (`expression`, a deterministic gate) is reserved-but-unbuilt (greyed out in the UI). Limit: the judge sees only the artifact summary/decisions, not tool outputs or files, so a wrong verdict can loop to `until_max`.
Also called: goal-satisfied loop, LLM-evaluated while, convergence loop, self-terminating iteration, model-graded stopping criterion. <!-- cap: taskcard-until-model-evaluator -->

**A State block declares run-scoped variables and standing context.** A read-only, leaf-like block that sets named variables (`state_variables`, read via `{{var.NAME}}`) and/or freeform standing prose (`state_context`, e.g. "assume prod, migration already ran, flag is off") that flows into every in-scope task's context automatically as a preamble — no templating required. Nothing writes back: read-only by design preserves the invariant that only artifacts cross task boundaries. Placement *is* the reset policy — a State in a run-once body applies once, but inside a Repeat/Until body it re-applies (resets to baseline) each iteration — and State blocks are deliberately re-executed even while resume-skipping, because the two stores are the only run-scoped state not persisted on disk. Documented at maturity 3. Limit: read-only, so no mutable run state or accumulators; values are untyped literals; and scope is the enclosing container's body, not global.
Also called: run variables / constants, given/assumption block, shared context preamble, blackboard variables, fixture/setup block. <!-- cap: taskcard-state-block -->

**A Group block is a run-once sequence with a failure policy.** The group block is a neutral run-once sequential container: it runs its body top-to-bottom exactly once (the explicit form of the implicit-sequence rule) and also serves as the invisible card-root wrapper. Every container's body forms an implicit sequence governed by `on_failure`: `continue` (the default) lets later siblings run after a child produces a failed artifact, while `stop` halts at the first failed child, whose artifact becomes the sequence's artifact and remaining siblings are skipped. A child that *raises* is converted to a failed artifact so `on_failure` still governs rather than the exception unwinding the whole run. Documented at maturity 3. Limit: `on_failure` is binary (stop|continue) — there is no retry-N-then-stop, no per-block catch, and no rollback.
Also called: sequence / block group, run-once container, step group, fail-fast vs continue-on-error, continue-on-error (GitHub Actions). <!-- cap: taskcard-group-sequence-and-failure-policy -->

**A templating engine passes data between blocks.** A pure (no-I/O) Mustache-style substitution engine is applied to a Task block's instructions at dispatch time. Placeholders include `{{index}}`, `{{item}}` / `{{item.KEY}}`, `{{previous.summary|decisions|outputs.NAME}}`, `{{previous_sibling...}}`, `{{all.summaries}}`, `{{var.NAME}}` (State variables), `{{sibling("block-id")...}}` (by-id lookup of any completed block's artifact), and the loop-aware plural `{{...outputs_all.NAME}}` that gathers *every* iteration's named part as a JSON array (versus last-wins for the singular). Unknown placeholder heads are left verbatim so typos surface to the author, known-but-unavailable ones render empty, and non-string values render as compact JSON so downstream `for_each` can parse them. Documented at maturity 4. Limit: text substitution only — no expressions, arithmetic, conditionals, or filters, and no default-value syntax (unknown heads stay literal by design).
Also called: prompt templating, variable interpolation, instruction parameterization, data passing between steps, `{{sibling}}` cross-block reference. <!-- cap: taskcard-template-substitution -->

**Block scopes inherit additively down the tree.** Every block may carry a `TaskScope` granting file paths (read/write/context flags), tools, skills, shell-command grants (literal or `re:` regex), a per-task shell timeout, and a model selection (a portable `model_tier` rung `xsmall`..`frontier`, or an explicit model/endpoint). Scope is hierarchical and additive: `merge_scopes()` unions the deck scope, the card scope, every ancestor container's scope, and the leaf's own, root→leaf — a more specific layer can only *add*, never revoke (path flags OR together, tools/skills/shell union, `shell_timeout` takes the MAX). The only non-additive fields are `cwd` and model selection (innermost non-null wins, since a task runs in one dir on one model). `find_scope_chain()` lets signing, the scope-status editor and the compliance audit compute a block's effective scope without running the executor. Documented at maturity 4 — no comparable per-step permission tree exists in mainstream CLI agents. Limit: path read/write flags are advisory here (enforcement lives in the write-policy/shell subsystems), and model selection is per-block but a task still runs on exactly one model.
Also called: per-task permissions, least-privilege sandbox grants, tool allowlist per step, per-block model selection, model tier routing. <!-- cap: taskcard-hierarchical-scope-model -->

**Each task runs in an isolated conversation — a context firewall.** The core invariant of the engine: a task's conversation never leaves its task. `execute_task_block` runs a single Task block in an isolated sandbox — the block's instructions become a fresh conversation with no parent history, and only the distilled Artifact (summary, decisions, typed outputs, self-assessment) flows back up, never the raw transcript. Every task in a run shares `conversation_id=run_id` for usage tracking, but each task's message list is seeded independently with an isolation system prompt. This is what makes wide fan-outs and deep call trees tractable without context blow-up, and what enforces the artifacts-only boundary that the Call and State blocks depend on. Documented at maturity 4. Limit: cross-task communication is deliberately narrow (artifacts plus a shared run scratch directory) — there is no shared live conversation, so a task cannot ask a sibling a question mid-run.
Also called: sub-agent context isolation, sandboxed task conversation, no-parent-history seeding, artifact-only handoff, fresh conversation per block. <!-- cap: taskcard-conversation-isolation-sandbox -->

**Every task self-reports whether it met its objective.** To stop conflating "the stream ended cleanly" with "the task succeeded", each task's system prompt requires the agent to emit a final `<self_assessment objective_met="true|false|partial" rationale="..."/>` tag. `parse_self_assessment` reads it (a permissive regex tolerant of attribute order and quoting), normalizes the verdict (unrecognized → `unknown`), and the executor uses that verdict — not stream cleanness — to set the artifact's `failed` flag and error signature. A missing tag is distinguished from a present-but-unrecognized `unknown`, and the verdict also drives failure clustering and the frontend completion-check surfaces. Documented at maturity 3. Limit: it is a self-report, not an independent verifier — a model can be wrong about its own work or omit the tag (mitigated only by the missing-vs-unknown distinction).
Also called: objective-met verdict, task success check, self-grading / self-critique tag, outcome verification, run success signal. <!-- cap: taskcard-self-assessment-capture -->

## Scheduling & Run Lifecycle

Task cards do not only run when you launch them: a card can be fired on a schedule, and every run — launched or scheduled — moves through a tracked lifecycle that Ziya observes live, retains for inspection, and reconciles after a crash. All of this rides the same single-writer task-run loop that executes cards interactively; it is not a separate job service.

**Scheduled and recurring task cards** are fired by an in-process loop that starts with the server and ticks every 15 seconds. A card's topmost `schedule` block chooses one of four modes — a fixed interval (N minutes/hours/days), a one-shot `at` an ISO datetime, a `daily_at HH:MM` time, or a 5-field `cron` expression — and each fire promotes that block's body to a run and executes it in the background, exactly as an interactive launch would. Per-card firing state (next fire time, last fire, fire count, run ids) persists to `<project>/schedule_state.json`. This capability is at moderate maturity (3): interval, `at` and `daily_at` are deterministic, tested and wired to a real frontend editor, but `cron` mode depends on the optional `croniter` package and silently does nothing if it is absent; `schedule_timezone` is declared but currently ignored (times are naive local time); the tick is 15-second, not sub-minute, granularity; and only the topmost `schedule` block per card is honored.
Also called: cron jobs, scheduled tasks, recurring runs, timed triggers, background scheduled execution. <!-- cap: sched-task-card-scheduler -->

**Missed fires coalesce on recovery.** If the server was down across several scheduled slots, an overdue card fires exactly once when it comes back — the missed slots collapse into a single catch-up fire, matching cron's coalesce-on-recovery behavior rather than stampeding one run per missed window. A per-card `schedule_catch_up` flag can suppress the catch-up entirely, re-projecting the schedule forward instead. Maturity 3. Limit: by design it cannot replay each individually missed occurrence; only one catch-up fire happens.
Also called: missed-run coalescing, catch-up firing, backfill suppression, misfire handling. <!-- cap: sched-catch-up-coalesce -->

**A schedule can cap its total fires.** Setting `schedule_max_runs` stops a recurring card once it has fired that many times (`None` means unlimited); the cap is enforced in the live loop against the persisted fire count. Maturity 3. Limit: the count is only ever incremented — it is never reset without editing the stored state — and there is no verified UI display of remaining runs.
Also called: run limit, occurrence cap, max executions, bounded recurrence. <!-- cap: sched-max-runs-cap -->

**You can read a card's next-fire time and firing history** through a read-only schedule-state endpoint, which returns the scheduler's own record for a card — next fire time, last fire, fire count, run ids — from `schedule_state.json`. It returns an empty result for a scheduled card the scheduler has not fired yet or a card with no schedule block, and it never produces records itself; only the scheduler writes them. Maturity 3. Limit: it reflects only what the scheduler has persisted — there is no projected schedule preview beyond the stored next-fire time.
Also called: next-run time API, schedule status endpoint, firing history, scheduled card status. <!-- cap: sched-schedule-state-endpoint -->

**Internal periodic maintenance jobs** ride the same scheduler loop through a small job registry: each job declares a name, an interval that gates how often its cheap check runs, and the work to do when the gate passes, with per-job isolation so one job's failure never blocks another. Today only two internal jobs are registered — memory organization (every 6 hours, gated on orphaned or stale memories) and the memory-proposal lifecycle (every 30 minutes, gated on open proposals) — and their state persists to `~/.ziya/system_jobs.json`. Maturity 3, and internal by design: there is no public API to register a job and it is not user-authorable, so the registry is a foundation for future workflow automation rather than a general workflow engine today.
Also called: background jobs, maintenance cron, periodic tasks, internal cron kernel, housekeeping jobs. <!-- cap: sched-system-jobs -->

**A run held on an infrastructure fault is recorded distinctly from a work failure.** When a run stops because of expired credentials, a lost endpoint, or throttling — rather than because the work itself failed — it is marked with the reason, the block it held at, and, for fan-out collapses, an aggregate fault record (fault count, fan-out width, primary fault kind, a kinds histogram, the call path, and whether the fault was fleet-wide). This lets a run tile show a held run's location (e.g. `CL0 → CL1 → audit-mcp-security`) and tell a dead credential apart from one throttled sibling without expanding anything. Maturity 3: the fault *detection* lives elsewhere (`infra_gate`); this capability owns persisting the classification and the resume point. Limit: a `held` run object is terminal — continuation is a new run.
Also called: infra fault handling, held state, fault aggregation, credential-expiry handling, throttle recovery state. <!-- cap: sched-held-fault-classification -->

**Loop iterations are retained under a scale-aware cap.** Every iteration of a Repeat block keeps a lightweight, always-retained summary; the full artifact is written to disk only for failures and for the first 50 passing iterations of each loop, so a 1000-iteration loop does not bloat storage. Iteration files use the same encryption-aware writer as run records. Maturity 3. Limit: passing iterations beyond the cap keep the summary only (no full artifact), which is exactly why a mid-loop resume refuses when the immediate predecessor iteration was over the cap.
Also called: iteration history, loop result storage, artifact retention policy, per-iteration persistence. <!-- cap: sched-iteration-artifact-retention -->

**A run reports "slow but alive" through a heartbeat and a progress trail.** As a run works it stamps a throttled last-activity time (about one disk write every 5 seconds) plus a progress note derived from its latest tool call, so a REST poller can distinguish a slow run from a hung one. Because the single progress-note slot is overwritten on each update, notes are also appended to a bounded trail (capped at 200 entries, with consecutive duplicates suppressed) that survives the whole run as a readable narrative; a genuinely new note bypasses the throttle. Maturity 3. Limit: the trail caps at 200 (oldest evicted) and the last-activity timestamp can lag up to ~5 seconds behind real work.
Also called: liveness heartbeat, progress tracking, run heartbeat, progress narrative, keepalive. <!-- cap: sched-run-activity-heartbeat -->

**A live run is streamed to the browser with reconnect replay.** Block-executor events are fanned to WebSocket clients over a per-run channel by a module-level relay. A bounded (1000-slot) per-run ring buffer replays whatever a mid-run or reconnecting client missed, and adjacent same-block text-delta events are folded on the fly into one entry to keep the slot count low; a 5-minute grace period keeps a run's history after it terminates for late reconnects. Registration and the replay snapshot happen under one lock hold, so replay and the live stream partition the event sequence exactly — no double-render, no lost event. Documented at maturity 4. Limit: the buffer is per-process and in memory (a client reconnecting to a *different* server instance replays from persisted storage, not this buffer), the cap is 1000 slots, and history is dropped 5 minutes after the terminal event.
Also called: live run streaming, WebSocket event relay, run event bus, reconnect replay, event replay buffer. <!-- cap: sched-task-run-stream-relay -->

**The run status model distinguishes held and partial outcomes**, not just success and failure. A task run carries a rich status set — queued, running, paused, held, done, partial, failed, cancelled — in which `held` marks an infrastructure fault (expired credentials, lost endpoint, throttling) that is resumable *without* fixing the card, distinct from a genuine `failed`, and `partial` marks a run that made real progress before stopping so a run that changed the workspace is not mislabeled a total loss. Block-level status adds `skipped` and `held`. This is documented at maturity 4: the held/failed/partial trichotomy is a distinction most agent harnesses collapse into a single "error", and it is backed by storage transitions, a derived-status classifier and tests. Limit: `partial` is derived from "at least one block completed AND work was left unfinished", so a zero-progress stop stays `failed`/`cancelled`.
Also called: run states, job status, execution status enum, held vs failed distinction, partial completion. <!-- cap: sched-run-lifecycle-status-model -->

**Resume descends *through* a Call block** to the real held block. Because a Call block runs its target card inline, a run held inside a called card records a hold point that names a block in no tree the resume endpoint would otherwise see. Ziya records each resolved callee's tree when the call executes and, on resume, walks outward through the call frames so execution descends through the call to the actual block rather than re-entering the callee from its start. This handles a genuinely hard case: under an earlier design a fan-out held on iteration 19 of 20 inside a called card re-ran every banked iteration on resume, discarding hours of work. Documented at maturity 4. Limit: call depth is capped at 8 frames, and it depends on the call snapshot having been recorded when the call executed.
Also called: nested card resume, sub-card recovery, call-chain descent, callee resume, cross-card resume. <!-- cap: sched-resume-through-call -->

**Declared run artifacts are served through a hardened blob route.** Rendered or copied outputs a run declares (frozen diagram PNGs, copied files) are written under the run's own artifacts directory, honoring at-rest encryption, and served by a route that decrypts transparently. Because the filename is model-influenced, serving is deliberately hardened: three redundant path-traversal guards, a fixed extension→media-type table (not the `mimetypes` module), and inline serving only for a known-safe media set — everything else (HTML, JS, SVG) is forced to an octet-stream attachment with `nosniff`. Documented at maturity 4. Limit: it serves only from the run's own artifacts directory, and any unknown extension downloads as an attachment rather than displaying inline.
Also called: artifact download, blob endpoint, secure file serving, artifact viewer backend, frozen render storage. <!-- cap: sched-artifact-blob-serving -->

**Stranded runs are reconciled at startup.** When the server starts, every project's task-run directory is swept and any run still marked running, queued, or paused — orphaned by a previous server lifetime with no live executor behind it — is idempotently marked `failed` with an explanatory error. This prevents a zombie run whose cancel button would otherwise be a no-op, because there is no coroutine left to honor the flag. Maturity 3. Limit: it marks stranded runs failed rather than auto-resuming them, so a paused run that outlives a restart becomes `failed` and must be resumed from a block manually.
Also called: crash recovery, stale run cleanup, orphan reconciliation, zombie run sweep, startup recovery. <!-- cap: sched-zombie-reconciliation -->

## Execution Safety & Approvals

Ziya's shell tool is not a raw shell. Everything the agent runs passes through a layered set of guards, and any privilege beyond a conservative default floor — read plus write to `.ziya/` and `/tmp/`, and a read-only shell allowlist — requires an out-of-band human signature the agent provably cannot produce. Competitors describe parts of this territory as "permission modes", "trusted commands", "sandboxing", and "allow/deny dialogs"; the sections below document how Ziya implements it.

### The approval gate

The **root-key escalation signer** (`ziya-approve`) is the top tier of Ziya's tiered permission model and the one control the agent cannot satisfy. Any privilege beyond the built-in floor must be authorized by an Ed25519 signature that only this separate, sudo-gated CLI can mint. The signing key lives root-owned and `0600` at `/etc/ziya/approve_ed25519`, so the agent — running as the normal user — gets a `PermissionError` reading it; the sudoers entry re-prompts for a password / Touch-ID on every call (`timestamp_timeout=0`, no `NOPASSWD`), and the agent's piped, TTY-less shell cannot answer that prompt. The signer signs the *delta* versus the floor and previews exactly what is being granted, so a human signs a specific, visible privilege set rather than a blank cheque, and every branch fails closed (no TTY, or no key, means the escalation is refused). Limit: it requires a one-time `sudo ziya-approve --provision`; on an unprovisioned machine (the default) escalations simply never verify and the run clamps to the floor rather than being refused.
Also known as: human-in-the-loop approval gate, out-of-band approval, privilege-escalation approval, sudo-gated signer, permission approval prompt. <!-- cap: approval-root-signer-cli -->

**One-time keypair provisioning** (`sudo ziya-approve --provision`) performs the per-machine root setup behind the gate: it generates the Ed25519 keypair (private `0600` root, public `0644`, created `0600` from the first byte so it is never briefly world-readable) and installs a locked-down `/etc/sudoers.d/ziya-approve` entry, which it validates with `visudo -cf` before writing so a malformed rule cannot lock the operator out of sudo. `--force` regenerates the keypair, which voids all existing approvals until they are re-signed. Limits: it needs root once per machine, has no key-rotation lifecycle beyond `--force`, and assumes a Linux/macOS `sudoers.d` layout.
Also known as: approval key bootstrap, signing-key generation, sudoers install, trust-anchor provisioning, Ed25519 keypair setup. <!-- cap: approval-keypair-provisioning -->

**The escalation-config integrity gate** is the keystone control that enforces the signature at runtime. When the shell subprocess starts, before trusting any privilege-bearing environment value (`ALLOW_COMMANDS`, `SAFE_WRITE_PATHS`, `ALLOWED_WRITE_PATTERNS`, `ALLOWED_INTERPRETERS`, `SAFE_GIT_OPERATIONS`, `YOLO_MODE`), the server computes the delta versus a canonical floor and requires a valid root signature (`ZIYA_SCOPE_SIG`) over that exact delta. A missing, invalid, or mismatched signature clamps the environment back to the floor. An empty delta — a config at or within the floor, including *narrowing* it — needs no signature, so the configuration GUIs stay usable for everyday edits. Authorization binds to content: editing any granted privilege changes the delta hash, the old signature stops verifying, and the privilege silently drops to the floor until re-approved. Limits: it only gates values crossing the parent→subprocess environment boundary, and a durable signature takes effect on the next shell restart rather than instantly.
Also known as: signed policy enforcement, config integrity gate, fail-closed permission clamp, tamper-evident config, verify-or-clamp scope gate. <!-- cap: escalation-config-signature-gate -->

**Ephemeral session grants** are a lighter, temporary alternative to a durable on-disk signature. `sudo ziya-approve --session` (or the UI's "Apply for this session") mints an Ed25519 grant bound to the running server's per-start nonce; the escalation never lands in the durable config, and the grant is automatically void on the next cold start (a new nonce) with no expiry file to manage. A second, `cli-ephemeral` variant is minted in-process by the interactive `/shell` TTY handler and verified against a per-process key that is never written to disk, so "a human typed it at the terminal" is the trust anchor. Limits: it depends on the long-running server forwarding the grant at spawn (a stale in-memory server cannot, which surfaces as a warning), and the single-slot nonce means multi-server setups can only satisfy the last writer's grant.
Also known as: temporary permission grant, session-scoped approval, one-shot escalation, just-for-this-session allow, nonce-bound grant. <!-- cap: ephemeral-session-grant -->

**Signed approval expiry** lets an approval carry an `expires_at` timestamp folded *into* the signed payload, so a tamperer cannot add, strip, or extend the expiry without invalidating the signature; expiry is then enforced fail-closed. An enterprise plugin can declare a maximum approval TTL, in which case an approval must carry an expiry within that bound or be denied, and the signer auto-stamps a compliant expiry (the most restrictive of `--ttl-days` and the policy bound wins). Limit: the open-source default, with no such plugin, is unbounded — approvals do not expire unless an enterprise policy sets a ceiling.
Also known as: approval expiration, time-boxed permission, credential TTL, grant-lifetime policy, expiring approval. <!-- cap: approval-ttl-expiry -->

**The escalation audit report** (`ziya-approve --list`) is a read-only, no-key, no-sudo inventory that walks every escalating task across all registered projects and CLI `tasks.yaml` files and prints each with its signed/unsigned status, granted privileges, and store key. Its exit code *is* the compliance signal — `0` when every escalation is signed, `1` when any is unsigned — so it doubles as a CI/audit gate, and it reports encrypted card files it cannot inspect out-of-process rather than skipping them silently. Limits (maturity 3): it is a point-in-time CLI report, not a live dashboard, and encrypted cards need the GUI/server audit to be inspected.
Also known as: permission audit, who-can-do-what report, escalation inventory, authorization ledger view, security-posture report. <!-- cap: escalation-audit-ledger -->

### Shell command guards

**The injection-resistant command allowlist** means the shell tool does not hand strings to a shell by default. It parses and validates every command segment against a per-command regex allowlist and then executes with `shell=False` through a hand-rolled pipeline orchestrator. Validation splits across `&&`, `||`, `;`, `|` and newline separators, recurses into `$()` and backtick command substitutions, strips and re-validates heredoc bodies, rejects unterminated quotes (including the ANSI-C `$'…'` bypass), peels `VAR=value` prefixes to reject loader-hijack variables, and inspects the bodies of compound constructs (`if`/`while`/`for`) rather than naively splitting on `;`. A per-task grant is only consulted when the base patterns decline, and never for always-blocked commands. Limit: it is an allowlist by command name/pattern, not an OS-level sandbox — an allowlisted binary still runs in the user's environment (see runtime hardening below; there is no seccomp/Landlock/bubblewrap).
Also known as: command allowlist, shell command filtering, shell-injection prevention, safe command execution, allowed-commands gate. <!-- cap: shell-command-allowlist-engine -->

**Write-policy enforcement** checks that any command which *writes* touches only approved paths, after the allowlist gate. Destructive commands (`rm`, `mv`, `cp`, `mkdir`, `chmod`, `chown`, `ln`) are allowlisted by name but their target paths are checked against the safe-write paths and allowed patterns; shell redirections (`>`, `>>`, `2>`, `tee` targets) are extracted and path-checked; in-place edit flags (`sed -i`, `awk -i`, `perl -pi`) are gated; and special devices such as `/dev/null` are handled. The policy merges a cascade of defaults → `~/.ziya/write_policy.json` → per-project settings (decrypting encrypted project config as needed). Limit: it is path-based and cannot reason about a program that builds a path at runtime inside an opaque binary (interpreter one-liners are covered separately, below).
Also known as: write-path policy, file-write guardrails, safe write paths, destructive-command gating, redirection guard. <!-- cap: shell-write-policy-enforcement -->

**The credential-exfiltration guard** keeps `curl` and `aws` available for normal read use but denies the specific vectors that would steal the developer's credentials through the agent's shell access: `curl` to the link-local cloud-metadata (IMDS) endpoint `169.254.169.254`, `curl @file` uploads of known credential paths (e.g. `-d @~/.aws/credentials`), and high-risk AWS CLI subcommands (IAM/STS role escalation, bulk S3/DynamoDB movement). These are evaluated per segment before execution. Limit: it cannot close *all* exfiltration while outbound network and file reads are allowed — it blocks the enumerated IMDS/AWS vectors, not novel endpoints or indirect copy paths.
Also known as: SSRF / IMDS protection, credential-theft prevention, data-exfiltration guard, cloud-credential protection, egress guard. <!-- cap: shell-credential-exfiltration-guard -->

**The infrastructure-as-code deploy guard** keeps `sam` and `cdk` allowlisted for read/build use (synth, diff, build, validate, local) but denies the `deploy` and `destroy` verbs that stand up or tear down real cloud infrastructure, so the agent cannot provision or delete account resources with the developer's credentials. Limit: it covers `sam` and `cdk` specifically (a raw `aws cloudformation deploy` is caught by the AWS subcommand guard); arbitrary other IaC binaries are not individually enumerated.
Also known as: IaC guardrail, cloud-deploy prevention, terraform/cdk/sam deploy block, infrastructure-mutation guard. <!-- cap: shell-iac-deploy-guard -->

**The interpreter-escape guard** closes the hole that an allowlisted interpreter (`python3`/`python`/`node`/`ruby`) would otherwise open: the allowlist gates the outer invocation but not what the interpreted code does. The write-checker scans inline interpreter code for process-spawn indicators (`os.system`, `os.popen`, `os.exec*`, `subprocess(shell=True)`, `pty.spawn`, `exec`/`eval`, `__import__('os')`) independently of file-write indicators — because a one-liner that writes nothing but spawns a process would otherwise bypass the whole allowlist (CWE-94) — and also scans for write indicators, gating them against the write policy, while common safe cases (`pytest`, `py_compile`, `-c` test snippets) short-circuit. Limit: it uses regex heuristics rather than an AST of the interpreted code, so encoded or obfuscated payloads (base64-exec, char-code assembly) may evade it, and only the enumerated interpreters are covered.
Also known as: sandbox-escape prevention, code-injection guard, interpreter one-liner gating, `python -c` abuse block, arbitrary-process-spawn block. <!-- cap: shell-interpreter-escape-guard -->

**Runtime execution hardening** disciplines the execution surface itself, independent of the allowlist. Every command runs in its own process group so a timeout `SIGKILL`s the whole group (backgrounded grandchildren cannot hold the server past the timeout); captured stdout/stderr is byte-bounded (default 512 KB, head and tail kept with a marker) so a runaway command cannot bloat memory or the model context; a dispatch semaphore (default 8, `ZIYA_SHELL_MAX_CONCURRENT`) bounds in-flight commands; model-requested timeouts are clamped to a ceiling (default 30 s, max 300 s, raisable by a task-scope grant); and commands run with a cleaned child environment. Limit: this is process-level resource discipline, not OS isolation — there are no CPU/memory cgroup limits and no filesystem or network namespace sandbox; commands run in the user's real environment.
Also known as: execution timeout, runaway-process kill, output truncation, resource limits, concurrency cap. <!-- cap: shell-execution-runtime-hardening -->

### Project write paths, provenance and diagnostics

**Parent-authoritative project write paths** forward the project's own write policy (safe-write paths plus allowed patterns) to the shell subprocess via `ZIYA_PROJECT_WRITE_PATHS`. This channel is deliberately *not* signature-gated: it grants the shell exactly the paths `file_write` already gets from the same policy in the trusted parent, so extending them to shell commands is consistency rather than new trust. Forgery resistance comes from the manager always setting-or-deleting this key at spawn from the freshly decrypted project config, so a value hand-written into the MCP config is overwritten before the subprocess sees it. Limit (maturity 3): it only propagates already-granted project policy — it is not an escalation channel — and its correctness leans on the manager's spawn-time overwrite discipline.
Also known as: project write-policy propagation, unsigned trusted grant, spawn-time policy injection, parent-authoritative paths. <!-- cap: parent-authoritative-project-write-paths -->

**A permissions snapshot** is captured at every task-run launch: the effective permissions the run was granted (base write policy plus each block's merged scope — paths read/write/context, tools, skills, shell commands, working directory) are recorded onto the run. Capture is eager rather than lazy, because a card's scope can be edited after the run and a lazy read would destroy the historical record; Call blocks, whose callee scope is not in the launch-time tree, are appended when the call actually executes without overwriting existing entries. Limits (maturity 3): it records what was *granted*, not what actually executed, and a Call target's scope only appears once the call runs.
Also known as: audit trail, permission provenance, run-permissions log, forensic snapshot, effective-scope capture. <!-- cap: task-run-permissions-snapshot -->

**The task-scope permissions editor** is a React dialog for authoring a Task Card block's scope across four tabs — files, tools, skills, and shell. The files tab is a tri-state cascade over three independent path sets: *scope* (in-scope at all; in-project reads are implicit), *writable* (cascades to a directory's descendants), and *context* (preload file contents into the system prompt). Pure-function cascade math computes each cell's direct, inherited, or indeterminate state, and the UI states that grants are additive over the global write policy and cannot bypass always-blocked commands. Limits (maturity 3): it is an authoring surface only — the grant it produces is inert until signed via `ziya-approve`, and no enforcement lives here.
Also known as: permissions UI, scope editor, capability picker, file-access dialog, per-task allow UI. <!-- cap: task-scope-permissions-editor -->

**Self-explaining scope-clamp notices** turn an opaque denial into an actionable one. When the startup gate clamps an unsigned or invalid escalation to the floor, the server records *why* — a session grant with no nonce means a stale server; a grant that fails verification means a stale or wrong-server grant; a `ZIYA_SCOPE_SIG` mismatch means the config changed after signing; nothing supplied means no approval — and appends that human-readable reason to every subsequent blocked-command error. The signer additionally emits delivery-preflight warnings at mint time when a valid grant cannot be forwarded (a stale in-memory server, or multiple servers sharing the single-slot nonce). Limit (maturity 3): these are diagnostics only and never change what is or is not authorized.
Also known as: actionable permission error, clamp reason, why-was-this-blocked, approval troubleshooting, grant-delivery warning. <!-- cap: scope-clamp-diagnostics -->



