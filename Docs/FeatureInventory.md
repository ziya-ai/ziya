# Ziya Feature Inventory

> **Purpose**: Authoritative, discoverable record of what Ziya actually does — for competitive comparison, marketing, and roadmap decisions.  
> **Maintainer note**: Update this file when features ship. If it isn't here, it doesn't exist for comparison purposes.

---

## 1. Interfaces & Modes

| Feature | Detail |
|---|---|
| **Web UI** | Full-featured browser client at `localhost:6969`. Resizable panels. |
| **CLI chat mode** | `ziya chat` — rich interactive terminal with `prompt_toolkit`, autocomplete, syntax highlighting, markdown rendering, and multi-line input with paste detection |
| **Project instructions** | Reads `AGENTS.md`, `README.md`, and GUI project config for per-project steering; cross-tool compatible with Claude Code, Kiro, Cline, and Q Developer conventions |
| **Auto context scaling** | Automatically selects 200k–1M token context window based on model capability |
| **CLI one-shot mode** | `ziya ask "question"` — prints answer and exits; composable with pipes (`git diff \| ziya ask "review this"`) |
| **CLI code review** | `ziya review [--staged\|--diff]` — review staged changes, unstaged diff, or piped content |
| **CLI explain** | `ziya explain <files>` — focused code explanation |
| **Ephemeral / incognito mode** | `ziya chat --ephemeral` — session history is never written to disk |
| **Session persistence** | CLI sessions auto-saved to `~/.ziya/sessions/`, including full conversation history and context file list |
| **Session resume** | `ziya chat --resume` or `/resume` in-session — interactive picker to restore any of the last 10 sessions |
| **Session suspend** | `/suspend` — gracefully save and exit; re-enter exactly where you left off |
| **Pipe / stdin support** | Any `ziya` command accepts piped stdin (log files, diffs, error output, raw text) |

---

## 2. Agentic & Orchestration

| Feature | Detail |
|---|---|
| **Swarm / multi-agent (Delegate system)** | Orchestrator decomposes a task into parallel delegates; each delegate runs independently with its own context and 9 coordination tools |
| **Recursive sub-swarms** | Delegates can spawn their own full sub-swarms via `swarm_launch_subplan`; supports arbitrary nesting depth |
| **Swarm coordination tools** | 9 built-in swarm tools per delegate: `swarm_task_list`, `swarm_claim_task`, `swarm_complete_task`, `swarm_add_task`, `swarm_note`, `swarm_query_crystal`, `swarm_read_log`, `swarm_request_delegate`, `swarm_launch_subplan` |
| **Crystal compaction** | When a delegate completes, its output is compacted into a "crystal" (memory summary) that downstream delegates can query via `swarm_query_crystal` |
| **Progressive checkpointing** | Delegate state checkpointed every ~4000 chars; on crash, a self-rescue continuation delegate picks up where the stream died |
| **Stall watchdog** | Delegates silent >10 min with no active children are automatically flagged as stalled |
| **TaskPlan sidebar tree** | Swarm plans nest under their source conversation in the sidebar with live progress badges and status icons |
| **Parallel streaming** | All running delegates stream live simultaneously; sibling WebSocket connections kept open while a swarm is active |
| **Tool execution loop** | Model autonomously calls tools in a multi-round loop (read files, search, run shell commands, web search, etc.) before returning a final answer |
| **Diff validation feedback loop** | After generating diffs, Ziya validates them via a dry-run apply; if they fail, it appends targeted repair feedback to the conversation and restarts the stream once for a corrected response |

---

## 3. Code Intelligence

| Feature | Detail |
|---|---|
| **Codebase context injection** | Full project file tree loaded into model context; token counts shown per file |
| **AST-based code analysis** | `--ast` flag enables semantic indexing; 5 resolution levels: `disabled`, `minimal`, `medium`, `detailed`, `comprehensive` |
| **Semantic code search** | `ast_search` — find symbols by name or regex pattern (`regex=true`); filter by type and file path; cross-file coverage |
| **AST reference tracing** | `ast_references` — definitions, callers (cross-file via name fallback), dependencies (import-attribute resolution), file summaries, cursor-position context (`action=context` with `file:line:col`) |
| **React/TS hook detection** | Arrow functions, `useCallback`, `useMemo`, and other hook-wrapped variables are indexed as callable functions, not plain variables |
| **Structured diff generation** | Model produces standard git diff format; rendered inline with Apply/Undo controls |
| **Multi-strategy diff application** | Patch pipeline tries `patch`, `git apply`, difflib, and LLM resolver in sequence (4 stages); handles imperfect/inexact diffs gracefully |
| **Per-hunk diff status** | Each hunk shows individual success/fail; partial application is fine |
| **Diff undo** | One-click revert of any applied diff |
| **Diff regression suite** | 137 edge-case patch tests covering all languages and source LLMs; run automatically in CI |
| **Diff applicator in CLI** | CLI applies diffs interactively: `[a]pply / [s]kip / [v]iew / [q]uit` per diff block |
| **Content-aware syntax highlighting** | Source code highlighted for a broad range of languages |
| **File browser** | Add files outside the project root via the browser |
| **Token count per file** | File tree shows per-file token cost; helps users manage context budget |
| **Context pruning** | Selectively remove files or messages from context to stay within model limits |

---

## 4. Context & Conversation Management

| Feature | Detail |
|---|---|
| **Persistent conversation history** | Conversations never silently reset; history survives context overflow |
| **Multiple simultaneous projects** | Open separate browser tabs, each with its own project, history, and context |
| **Project organization** | Conversations grouped by project in the sidebar |
| **Conversation forking** | Fork from any message to explore a tangential path without losing the original thread |
| **Per-message editing** | Edit, resubmit, fork, truncate, or delete any message in the conversation history |
| **Conversation export / import** | Export for local reuse (JSON), export for sharing (Markdown), export directly to paste services; import to restore |
| **Project & session naming** | Name projects, groups, and individual sessions for organization in the sidebar |
| **Conversation checkpointing** | Auto-checkpointing of conversation state; web UI supports session resume across browser restarts |
| **Context window usage display** | Toolbar shows consumed tokens vs. model limit at all times |
| **Selective context removal** | Remove any file or message from context mid-conversation |
| **CLI session history** | Persistent CLI history file (`~/.ziya/history`) with `prompt_toolkit` autocomplete |
| **Shell command history** | Shell allowlist persisted to `~/.ziya/`; session-local overrides without touching persisted state |

---

## 5. Models

| Feature | Detail |
|---|---|
| **AWS Bedrock — Claude** | Sonnet 4.6 (default), Sonnet 4.5/4.0/3.7/3.5, Opus 4.6/4.5/4.1/4.0/3, Haiku 4.5/3 |
| **AWS Bedrock — Nova** | Nova Premier (1M ctx), Nova Pro, Nova Lite, Nova Micro |
| **AWS Bedrock — Other** | DeepSeek R1/V3/V3.2, Qwen3 Coder 480B, Kimi K2.5, MiniMax M2.1, GLM 4.7/4.7 Flash, OpenAI GPT 120B/20B (via Bedrock) |
| **Google Gemini** | Gemini 3.1 Pro, Gemini 3 Pro *(deprecated)*/Flash, Gemini 2.5 Pro/Flash Lite, Gemini 2.0 Flash/Lite |
| **OpenAI** | GPT-4.1/Mini/Nano, GPT-4o/Mini, o3, o3-mini, o4-mini |
| **Mid-conversation model switching** | Change models at any point without losing history |
| **Adaptive / extended thinking** | Sonnet 4.6, Opus 4.6: configurable reasoning effort (`low`–`max`); Sonnet 3.7, 4.0–4.5, Nova Pro/Premier: extended thinking toggle |
| **Gemini thinking levels** | `low`, `medium`, `high` per-request on all Gemini 3.x family models (3 Pro, 3.1 Pro, 3 Flash) |
| **Model parameter tuning** | Temperature, top-p, top-k, max output tokens — configurable in UI without restart |
| **Custom model filtering** | `~/.ziya/models.json` to restrict or extend the model picker |
| **Custom inference profiles** | Add custom Bedrock ARNs (provisioned throughput, etc.) via `~/.ziya/models.json` |
| **Prompt caching** | Extensive cache support with analytics; system prompt and code context are cached across turns, reducing latency and cost for follow-up messages |
| **Open source** | MIT license; full source code available |

---

## 6. Multimodal & File Formats

| Feature | Detail |
|---|---|
| **Image input** | Drag/drop, paste from clipboard, or image button; supported on Claude 3.x/4.x, Nova Pro/Lite/Premier, Gemini |
| **PDF input** | Native reading; loaded as context for the model |
| **Word (DOCX) input** | Native reading |
| **Excel (XLSX) input** | Native reading |
| **PowerPoint (PPTX) input** | Native reading |
| **Dynamic context helpers** | For obscure file types (e.g., network pcap), Ziya loads format-appropriate parsers |
| **Voice input** | *(roadmap — Nova Sonic available on Bedrock)* |

---

## 7. Visualizations

Ziya's visualization suite is the most comprehensive of any AI coding assistant. All renderers have a preprocessing normalization layer that handles imperfect output from any LLM.

| Renderer | Use cases |
|---|---|
| **Mermaid** | Flowcharts, sequence diagrams, ER diagrams, Gantt, state machines |
| **Graphviz** | Dependency graphs, call graphs, complex networks |
| **VegaLite** | Data charts, plots, statistical visualizations |
| **DrawIO** | Architecture diagrams, system design, exportable `.drawio` files |
| **MathML / KaTeX** | Inline and display math (`$...$` / `$$...$$`) |
| **HTML mockups** | Interactive UI previews in an isolated iframe; inherits theme; CSS isolated |
| **Packet diagrams** | Bit-level protocol frame / header / wire-format layouts with rulers and bracket annotations |
| **Architecture shape catalog** | Searchable library of AWS and generic shapes for DrawIO/Mermaid/Graphviz |

---

## 8. MCP (Model Context Protocol)

| Feature | Detail |
|---|---|
| **MCP server support** | stdio transport (subprocess spawning) and remote HTTPS (StreamableHTTP, SSE) via the official MCP SDK |
| **Multi-source config merge** | `mcp_config.json` loaded from CWD, project root, and `~/.ziya/`; later entries win |
| **Remote MCP servers** | Connect to hosted MCP endpoints via `"url"` config key; supports StreamableHTTP (default) and SSE transports |
| **OAuth / bearer token auth** | `"auth": {"type": "bearer", "token_env": "..."}` for remote MCP servers; inline tokens or env-var references |
| **Built-in Amazon internal MCPs** | AmazonInternalMCPServer, BuilderMCP — available via enterprise plugin |
| **Tool poisoning detection** | External tool descriptions scanned for 13 prompt-injection patterns at connect time |
| **Tool shadowing prevention** | External tools that collide with built-in tool names are blocked; built-ins always take precedence |
| **Rug-pull detection** | Tool definitions fingerprinted (SHA-256) at connect time; changes on reconnect trigger security warnings |
| **MCP registry browser** | Browse and install MCPs from within the Ziya UI |
| **Tool enhancement / description injection** | `tool_enhancements` block in `mcp_config.json` appends custom guidance to any tool's description before it reaches the model — corrects model misbehavior without touching the MCP server |
| **User-level tool overrides** | `~/.ziya/tool_enhancements.json` for per-user corrections |
| **Shell command allowlist** | Configurable per-session and persistently; `/shell add/rm/reset/yolo/git/timeout` commands in CLI; `save` suffix to persist |
| **YOLO mode** | `/shell yolo` disables the allowlist for the current session (confirmation required) |
| **Nova Web Grounding** | Built-in web search tool backed by Amazon Nova; Claude calls it autonomously when it needs current web information |
| **Per-MCP prettyprint** | Custom output formatting per MCP server via `FormatterProvider` plugin |

---

## 9. Enterprise & Internal Deployment

| Feature | Detail |
|---|---|
| **Plugin system** | Python plugins loaded via `ZIYA_LOAD_INTERNAL_PLUGINS=1`; structured provider interfaces |
| **AuthProvider** | Pluggable credential validation and refresh instructions (e.g., Amazon Midway) |
| **ConfigProvider** | Environment-specific defaults, endpoint restrictions, model picker policy |
| **Endpoint restriction** | `get_allowed_endpoints()` hides disallowed providers from UI and API; enforced at startup |
| **DataRetentionProvider** | Per-category TTL policies (conversation, cache, session); most-restrictive-wins when multiple providers registered |
| **ServiceModelProvider** | Enable and configure built-in service models (e.g., Nova Grounding) |
| **FormatterProvider** | Inject JavaScript for custom tool result rendering in the frontend |
| **ToolEnhancementProvider** | Organization-wide tool description augmentations |
| **ShellConfigProvider** | Extend the shell command allowlist via enterprise plugin |
| **Shared/centralized account** | Internal deployments can provide a shared Bedrock account; users don't need personal AWS credentials |
| **Developer override** | `ZIYA_ALLOW_ALL_ENDPOINTS=1` bypasses endpoint restrictions for plugin developers |

---

## 10. Skills

| Feature | Detail |
|---|---|
| **Skills panel** | Activate reusable instruction bundles from the Skills panel |
| **Custom skills** | Create and edit skills from within the UI |
| **Skills as system prompt supplements** | Each skill can include a system prompt segment active for that conversation |
| **Skill export / sharing** | *(roadmap — profile packaging concept)* |

---

## 11. Developer Experience (CLI-specific)

| Feature | Detail |
|---|---|
| **`prompt_toolkit` REPL** | Rich terminal: file path completion, command completion, model name completion |
| **Multiline input** | Paste-aware: rapid input detected as paste inserts newlines instead of submitting |
| **Bracketed paste handling** | Correct handling of multi-line pastes without accidental submission |
| **Ctrl+C double-tap to exit** | Single Ctrl+C cancels in-flight request or clears input; double-tap exits |
| **Streaming cancellation** | Ctrl+C during a streaming response cancels it mid-stream; partial response preserved |
| **Tool call display** | Each tool invocation shown with header, arguments, and result in the terminal |
| **Interactive model picker** | `/model` shows a `RadioList` with context window sizes; `→` opens settings dialog |
| **Model settings dialog** | Configure temperature, max tokens, top-k without leaving the CLI |
| **Git integration** | `ziya review --staged` / `--diff` for git-aware code review |
| **Pipe composition** | `cat error.log \| ziya ask "what's wrong?"`, `git diff \| ziya review` |
| **Session shell commands** | Per-session allowlist overrides; `/shell git add`, `/shell git all`, etc. |

---

## 12. What Ziya Does NOT Have (as of v0.4.x)

Tracked against the competitive landscape: Aki, Kiro, Cursor, GitHub Copilot, Windsurf, Claude Code, Aider, Cline.  
Format per row: the gap, who has it, and notes for context.

### 12a. Agentic & Workflow

| Gap | Who Has It | Notes |
|---|---|---|
| **Persistent cross-session memory** | Claude Code (MEMORY.md auto-save), Codex (two-phase memory pipeline), Aki (`~/.aki/memories/`), Mem0 integrations | No `~/.ziya/memories/` equivalent; history survives within a session but the model has no recall of previous sessions |
| **Plan / Act mode** | Aki (Plan=read-only, Act=execute), Cursor (preview mode) | No read-only preview toggle before file writes; users cannot review what the agent _intends_ to do before it does it |
| **Lifecycle hooks** | Aki (`PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`) | No hook system for approvals, notifications, logging, or external writes around tool calls |
| **Output schema enforcement** | Aki, LangChain structured output | No validation that LLM responses conform to a declared JSON schema; matters for pipeline/automation use cases |
| **Background task notifications** | Aki (desktop notifications), Cursor | No signal when a long-running swarm or CLI task finishes |
| **Custom subagent definitions** | Claude Code (`.claude/agents/` YAML), Kiro (`.kiro/agents/`) | Delegates are dynamically generated from task decomposition; no user-defined agent templates |
| **Commit / PR creation** | Claude Code (branch + PR via gh), Codex (GhostCommit + PR), Kiro (autonomous PRs) | No built-in git commit generation or PR creation; users apply diffs manually and commit themselves |
| **Context compaction** | Claude Code (auto at ~95% + manual /compact), Codex (model_auto_compact_token_limit), Cline (AI summarization), Kiro (customizable thresholds) | No automatic context compaction; when the context window fills, the conversation must be forked or restarted |
| **Git checkpointing / rewind** | Claude Code (per-prompt checkpoint, /rewind, Esc+Esc), Codex (Ghost snapshots + `codex undo`), Cline (shadow Git per tool use), Kiro (GA checkpointing) | Ziya has per-diff undo but no filesystem-level snapshot/rewind across an entire prompt's changes |
| **Permission modes** | Claude Code (6 modes: default, acceptEdits, plan, delegate, dontAsk, bypassPermissions), Codex (suggest, auto-edit, full-auto), Cline (per-category auto-approve) | No tiered permission system; all tool calls are either allowed or require manual shell allowlisting |

### 12b. Customization & Profiles

| Gap | Who Has It | Notes |
|---|---|---|
| **Profile packaging (export/import bundle)** | Aki (`aki profile pack/install`), Cursor rules export | No way to export a bundle of skills + system prompt + model config + tool set and share/install it as a unit |
| **Per-profile tool selection** | Aki (`enabled_tools` per manifest) | Tools are global; cannot define a profile that exposes only a restricted or specialized tool subset |
| **Profile marketplace / remote registry** | Aki (Profile Service), Cursor community rules | No community or team-shared store for Ziya profiles or skill sets |
| **Custom branding per profile** | Aki (logo, favicon, theme, tips per profile) | Single global UI theme |
| **Response annotation** | Aki (v1.11), Cursor inline comments | Cannot select and annotate specific text within a model response |

### 12c. UI & Interaction

| Gap | Who Has It | Notes |
|---|---|---|
| **Voice input** | Aki (Nova Sonic, multilingual), GitHub Copilot Voice | Nova Sonic is available on Bedrock; not yet implemented |
| **Native desktop app** | Aki (macOS + Windows), Cursor, Windsurf, VS Code | Browser-only; no Electron/native app |
| **IDE plugin / inline suggestions** | GitHub Copilot, Cursor, Cline, Tabnine, Codeium | No VS Code / JetBrains extension; Ziya is a standalone UI, not embedded in the editor |
| **Inline autocomplete (tab completion)** | GitHub Copilot, Cursor, Codeium, Supermaven | No keystroke-level code completion; Ziya operates at conversation/diff granularity |
| **Artifacts / side-panel document editing** | Aki ("Write with Aki"), Claude.ai Artifacts, Cursor Composer | No separate editable document pane alongside the chat |
| **Incognito / no-persist mode (web UI)** | Aki (incognito in UI) | CLI has `--ephemeral`; web UI supports ephemeral mode via `ZIYA_EPHEMERAL_MODE` env var but has no in-UI toggle |
| **LSP integration** | Claude Code (`.lsp.json` for go-to-def, type errors), Kiro (Code OSS native), Q Developer | No language server protocol integration; code intelligence is AST-based only |
| **Filesystem / network sandbox** | Claude Code (Seatbelt/bubblewrap), Codex (Landlock+seccomp), Kiro (Agent Sandbox) | Shell commands run unsandboxed in the user's environment; allowlist is the only guardrail |
| **Structured JSON CLI output** | Claude Code (`--output-format json/stream-json, --json-schema`), Codex (`--json`) | No machine-readable output mode for CI/scripting pipelines; CLI always outputs human-readable markdown |

### 12d. Data & Integrations

| Gap | Who Has It | Notes |
|---|---|---|
| **Bedrock Knowledge Base native integration** | Aki | No direct KB query; achievable via a custom MCP server but not out-of-box |
| **Database direct connection** | Aki (AWS Postgres integration) | No built-in DB tool |
| **Microsoft Office automation (write)** | Aki (Gandalf MCP for Word/Excel, built-in PowerPoint MCP, Outlook MCP) | Ziya reads DOCX/XLSX/PPTX; it cannot write to or automate Office apps |
| **Runtime MCP hot-reload** | Aki (connect/disconnect without restart) | MCP config changes require a Ziya server restart |
| **Figma / design import** | Augment Code Design Mode, Cursor | No design-to-code path |
| ~~**OAuth for MCP**~~ | Claude Code (full OAuth 2.0), Codex, Cline | ✅ **CLOSED** (v0.6.0) — Bearer token auth via `"auth"` config block; supports inline tokens and env-var references |
| **GitHub Actions / CI integration** | Claude Code (`claude-code-action@v1`), Codex (@codex on PRs), Kiro (autonomous agent) | No published GitHub Action or CI pipeline integration |

### 12e. Security & Compliance

| Gap | Who Has It | Notes |
|---|---|---|
| **Security / vulnerability scanning** | Amazon Q Developer, Qodo, GitHub Advanced Security | No built-in CVE, secret detection, or SAST scanning |
| **Compliance audit log** | Enterprise tools generally | No structured audit trail of what files were read/written per session |

---

*Last updated: 2026. To add a feature, describe it in the appropriate section with enough detail to be useful for comparison against Aki, Kiro, Cursor, Copilot, and similar tools.*
