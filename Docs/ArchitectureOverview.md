# Ziya Architecture Overview

Ziya is a local-first AI coding assistant that streams responses from large language models (LLMs) directly to your browser. All conversation data stays on your machine; the only outbound traffic is to the LLM API you configure (AWS Bedrock or Google Gemini).

---

## High-Level Components

```
Browser (React SPA)
    │  SSE / WebSocket / REST
    ▼
FastAPI Server  (app/server.py)
    │
    ├─ StreamingToolExecutor  ─────────────────► AWS Bedrock (Claude, Nova, etc.)
    │   (app/streaming_tool_executor.py)          via invoke_model_with_response_stream
    │
    ├─ MCP Manager  ───────────────────────────► External MCP Servers (stdio subprocess)
    │   (app/mcp/manager.py)
    │                  └───────────────────────► Remote MCP Servers (StreamableHTTP / SSE)
    │
    ├─ Builtin Tools  (app/mcp/tools/)
    │   ├─ file_read / file_write / file_list
    │   ├─ nova_web_search  ────────────────────► AWS Bedrock (Nova converse API)
    │   └─ architecture shapes / diagram tools
    │
    ├─ Plugin System  (app/plugins/)
    │   ├─ AuthProvider
    │   ├─ ConfigProvider  (+ endpoint restrictions)
    │   ├─ ServiceModelProvider
    │   ├─ DataRetentionProvider
    │   ├─ ShellConfigProvider
    │   └─ FormatterProvider
    │
    └─ Local Storage  (~/.ziya/)
        ├─ projects/         (contexts, skills, settings per project)
        └─ models.json       (optional user model allowlist)
```

Conversations are stored in the browser's IndexedDB, not on the server filesystem.

---

## Request Flow (Chat)

1. User types a message in the browser.
2. `POST /api/chat` receives the request with `question`, `messages`, `files`, `conversation_id`, and `project_root`.
3. `stream_chunks()` builds the full message context (system prompt + codebase files + chat history) via `build_messages_for_streaming()` and the precision prompt system.
4. `StreamingToolExecutor.stream_with_tools()` sends the request to Bedrock via `invoke_model_with_response_stream`. The system prompt uses `cache_control: ephemeral` for prompt caching to reduce cost on multi-turn conversations.
5. The model streams back text chunks and tool-use blocks.
6. Tool calls are executed immediately: builtin tools run in-process, MCP server tools are dispatched via `MCPManager.call_tool()`.
7. Tool results are appended to the conversation as `tool_result` blocks; the model continues generating.
8. Text chunks are forwarded to the browser as SSE events (`data: {"content": "..."}`) throughout.
9. When the model sends a `message_stop` event, `data: {"done": true}` is sent and the browser persists the conversation to IndexedDB.

---

## Model Abstraction

`ModelManager` (`app/agents/models.py`) is the single source of truth for model selection, configuration, and the boto3 client lifecycle. All model streaming uses `StreamingToolExecutor` directly — the legacy LangServe routing layer has been removed.

Model definitions live in `app/config/models_config.py` as nested dicts:

```
MODEL_CONFIGS[endpoint][model_alias] → { model_id, family, token_limit, ... }
```

The config hierarchy is: `GLOBAL_MODEL_DEFAULTS` → `ENDPOINT_DEFAULTS` → `MODEL_FAMILIES` → `MODEL_CONFIGS` (most specific wins).

Users can extend or filter these via `~/.ziya/models.json` (see `Enterprise.md`).

The active model is set by `ZIYA_MODEL` and `ZIYA_ENDPOINT` environment variables (or `--model` and `--endpoint` CLI flags), and can be changed at runtime via `POST /api/set-model`.

---

## Plugin System

See `Enterprise.md` for the full plugin reference.

The plugin system is a lightweight registry of provider objects loaded at startup. No monkey-patching, no import hooks — just lists of objects the server queries at the right moments. Enterprise deployments add providers; community deployments use the defaults.

Providers are loaded when `ZIYA_LOAD_INTERNAL_PLUGINS=1` is set. The system looks for `plugins` or `internal.plugins` on the Python path and calls `register()`.

---

## Environment Setup

Both entry points (web server via `app/main.py` and CLI via `app/cli.py`) share a single `setup_environment()` function in `app/config/environment.py`. This function translates parsed CLI arguments into environment variables for:

* Root directory and file inclusion/exclusion
* AWS profile, region (with model-specific defaults from `MODEL_DEFAULT_REGIONS`)
* Endpoint + model validation and auto-detection
* Model parameter flags (temperature, top_p, top_k, etc.)
* Templates directory

Each entry point adds its own extras after calling the shared function:

* **Server** (`main.py`): AST, MCP enablement, ephemeral mode, max_depth
* **CLI** (`cli.py`): Debug logging, logger reconfiguration for chat mode

---

## Service Models

Service models are small specialized models that augment the primary model by backing specific tools. The first instance is Nova Web Grounding:

- The primary model (Claude) sees `nova_web_search` in its tool list
- When Claude calls it, `NovaWebSearchTool` invokes `GroundingService.query()`
- `GroundingService` calls Nova 2 Lite via the Bedrock **Converse** API (not `invoke_model`) with `systemTool: nova_grounding`
- Nova returns text interleaved with `citationsContent` blocks
- The tool formats these into text + numbered source references and returns them to Claude

This is a general pattern — future service models could provide code execution, knowledge base retrieval, or other specialized capabilities, each backed by a different small model.

---

## MCP Tool Execution

External tools are managed by `MCPManager` (`app/mcp/manager.py`), which maintains connections to MCP servers via two transport modes:

- **stdio** — Local servers are spawned as subprocesses. MCPClient manages JSON-RPC over stdin/stdout directly.
- **Remote HTTPS** — Servers with a `"url"` config use the official MCP SDK (`mcp.client.session.ClientSession`) over StreamableHTTP or SSE transports. The SDK session is kept alive via an `AsyncExitStack` for the lifetime of the connection.

For remote servers, authentication is handled via OAuth bearer tokens (`Authorization: Bearer <token>` header) configured in `mcp_config.json`.

Builtin tools (`app/mcp/tools/`) run directly in the server process without subprocesses. They follow the same `BaseMCPTool` interface and are registered via `builtin_tools.py`.

### Tool Result Security

Every tool result is cryptographically signed (HMAC-SHA256) by `MCPClient` before being returned. The streaming executor verifies the signature before displaying the result to the user or feeding it back to the model. Unverified results are rejected with a corrective error message.

### Tool Guard (ATC Mitigations)

`app/mcp/tool_guard.py` provides three security layers for external MCP servers:

1. **Tool poisoning scanner** — Scans tool descriptions for prompt-injection patterns (13 regex patterns covering instruction override, system tag injection, bypass attempts, hidden comments). Runs at connect time for all non-builtin servers.

2. **Tool shadowing prevention** — During tool enumeration, built-in tool names are registered first. External tools that collide with built-in names are silently dropped (with a warning log). Built-in implementations always win.

3. **Rug-pull fingerprinting** — Tool definitions are hashed (SHA-256) on each connection. On reconnection, changes to the fingerprint trigger a security warning, detecting possible post-install tool definition mutations.

---

## File Tree & Context

The browser requests the project file tree from `GET /api/folders`, which triggers a background scan with gitignore-aware filtering. Token counts per file are estimated during the scan.

Files selected by the user are sent in the chat payload as relative paths. The server reads their content and injects it into the system prompt as annotated file blocks with line numbers.

External paths (outside the project root) can be added via `POST /api/add-explicit-paths`. These are tracked in `_explicit_external_paths` (a server-memory set) and in the folder cache under a `[external]` key. The security check in `apply_changes` uses `is_path_explicitly_allowed()` to permit writes only to the project root or explicitly added external paths.

---

## Diff Application Pipeline

Code changes suggested by the model are applied via `POST /api/apply-changes`. The pipeline (`app/utils/diff_utils/pipeline/pipeline_manager.py`) tries strategies in order:

1. **System patch** (`patch -p1`) — fast, handles most well-formed diffs
2. **Git apply** (`git apply`) — handles edge cases that `patch` misses
3. **Difflib** — Python-based fuzzy matching for diffs with wrong line numbers or whitespace issues
4. **LLM resolver** (future) — for structurally complex cases

Each hunk is tracked independently through the pipeline. The result reports per-hunk status (succeeded, failed, already-applied) so the UI can show partial success accurately. Failed hunks include the pipeline stage where they failed and why.

---

## Frontend React Context Architecture

The frontend uses a **slice context** pattern to prevent cascade re-renders. State is owned by a single `ChatProvider` but exposed through focused contexts so consumers only subscribe to the state they need.

```
ChatProvider  (state owner — frontend/src/context/ChatContext.tsx)
    │
    ├── ScrollContext         scroll position, auto-scroll, direction
    ├── ConversationListContext  conversations[], folders[], CRUD operations
    ├── ActiveChatContext     current messages, streaming maps, editing state
    └── StreamingContext      read-only streaming flags (isStreaming, etc.)
```

**Why slices?** A monolithic context with ~55 dependency items causes every state change to re-render all 25+ consumers. During streaming (60Hz updates to `streamedContentMap`), this forced `FolderTree`, `ProjectManagerModal`, and other unrelated components to re-render. The slice pattern keeps the state management code centralized in `ChatProvider` while delivering narrow subscriptions.

**Important:** `ChatProvider` must **not** subscribe to `FolderContext` (e.g. via `useFolderContext()`). Because `ChatProvider` is the root of the entire conversation tree, subscribing to folder state would cause a full re-render every time the user checks or unchecks a file. File selections are managed entirely within `FolderContext` and persisted via `sessionStorage`.

| Context | Key consumers | Changes when |
|---|---|---|
| `ScrollContext` | App, Conversation | User scrolls or auto-scroll fires |
| `ConversationListContext` | FolderTree, FolderButton, Debug, DelegateLaunchButton | Conversation/folder CRUD |
| `ActiveChatContext` | Conversation, SendChat, StreamedContent, EditSection | Message streaming, editing |
| `StreamingContext` | MarkdownRenderer sub-components | Streaming starts/stops |

**Migration path**: Components that previously called `useChatContext()` are incrementally migrated to the appropriate slice hook (`useScrollContext()`, `useConversationList()`, `useActiveChat()`, `useStreamingContext()`). The monolithic `useChatContext()` remains available for complex consumers that span multiple slices.

**Value object hygiene:** The `useMemo` value object in `ChatProvider` must contain only flat keys that match the `ChatContext` interface — no grouped sub-objects (they compute values that no consumer reads) and no state counters that are never incremented (they waste dependency-array slots without triggering updates). Tests in `ChatContextValueHygiene.test.ts` enforce this.

### Conversation Shell Loading & Data Integrity

On startup, `ChatProvider` loads **conversation shells** (`db.getConversationShells()`) — lightweight objects with only the first and last messages — so the sidebar renders immediately. Full message data for the active conversation is lazy-loaded afterward.

A defense-in-depth guard prevents shell data from being written back to persistence stores, which would destroy middle messages:

| Layer | Location | Mechanism |
|---|---|---|
| Shell markers | `db.getConversationShells()` | Each shell carries `_isShell: true` and `_fullMessageCount: N` |
| Save blocker | `queueSave()` in ChatProvider | Blocks wholesale saves when React state contains shells |
| Fast-path disable | `queueSave()` cache logic | Disables the `otherProjectConvsCache` optimization when shells are present, forcing a full IndexedDB read |
| Write guard | `db._saveConversationsWithLock()` | Rejects any conversation where `_isShell && messages.length < _fullMessageCount` |
| Flag clearing | Lazy-load and server sync | `_isShell` is cleared when full messages are loaded |

The markers are transient (never persisted to IndexedDB or the server). They exist only in React state during the window between shell load and full data load.
