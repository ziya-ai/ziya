## Project Startup Performance

On page load or browser refresh, Ziya restores the most recently used project
rather than always falling back to the server's working directory:

1. **localStorage fast-path** — The frontend stores the last-used project ID
   in `ZIYA_LAST_PROJECT_ID`. On init it fetches `/projects/{id}` directly
   (a single file read) instead of the slower `/projects/current` path scan.
2. **`/projects/last-accessed` fallback** — When localStorage is empty (first
   visit, cleared storage), the frontend calls this endpoint which returns
   the most recently touched project across all projects. Only if no projects
   exist at all does it create one for the current working directory.
3. **Path index** — `ProjectStorage` maintains a `_path_index.json` mapping
   normalized paths to project IDs. This makes `get_by_path()` O(1) instead
   of scanning every project directory. The index auto-rebuilds on cache miss.
4. **Parallel loading** — The project list loads in the background while the
   current project is already set, so the UI is interactive sooner.

---

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

### Stream Resilience

Long-running streams (especially during tool execution) are protected against idle-connection drops by a server-side **SSE keepalive** that emits comment pings (`: keepalive`) every 15 seconds when no data is flowing. On the browser side, the streaming session acquires a **Web Lock** (`navigator.locks`) to signal the browser that the tab is performing important work, preventing it from freezing the page when the screen saver activates or the user switches away. If a stream is still interrupted (e.g. OS-level sleep), the frontend detects whether the tab was hidden at the time and provides a more specific recovery message.

Additionally, streaming sessions acquire a **Screen Wake Lock** (`navigator.wakeLock`) that prevents the display from dimming and the OS from entering sleep mode while a response is being generated. This stops the OS power manager from suspending the network stack — the primary cause of "Stream interrupted" errors during screensaver or lid-close events. The Wake Lock auto-releases when the tab becomes hidden (e.g. screensaver overlay) and is automatically re-acquired when the tab returns to the foreground.

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

### Tool Result Sanitization

Tool results are sanitized before entering conversation context to prevent
context-window exhaustion from metadata bloat.  The sanitizer
(`app/utils/tool_result_sanitizer.py`) runs on every tool result and applies
transforms in order:

1. **Plugin filters** — `ToolResultFilterProvider` plugins are called first, in priority order.  This is the extension point for site-specific cleanup (e.g. stripping Quip `sectionId` HTML comments in enterprise deployments).  Each filter receives the output of the previous one.
2. **Base64 blob replacement** — Long base64 strings are detected.  PDFs are decoded and text-extracted via `document_extractor.py` (pdfplumber/pypdf).  ZIP-based Office documents (DOCX, XLSX, PPTX) are identified by inspecting the ZIP contents and extracted with the appropriate library (python-docx, openpyxl/pandas, python-pptx).  Legacy OLE2 documents (.xls, .doc) are also detected.  Other binary blobs are replaced with size placeholders.
3. **Size cap** — Results exceeding `TOOL_RESULT_MAX_CHARS` (default 100K, configurable via env var) are truncated with a note.

The sanitizer runs at the point where `result_text` is determined in the streaming tool executor, before the result enters both the user display path and the model conversation context.

See `Enterprise.md` for registering custom `ToolResultFilterProvider` plugins.

See `Enterprise.md` for registering custom `ToolResultFilterProvider` plugins.

---

## File Tree & Context

The browser requests the project file tree from `GET /api/folders`, which triggers a background scan with gitignore-aware filtering. Token counts per file are estimated during the scan.

Files selected by the user are sent in the chat payload as relative paths. The server reads their content and injects it into the system prompt as annotated file blocks with line numbers.

External paths (outside the project root) can be added via `POST /api/add-explicit-paths`. These are tracked in `_explicit_external_paths` (a server-memory set) and in the folder cache under a `[external]` key. The security check in `apply_changes` uses `is_path_explicitly_allowed()` to permit writes only to the project root or explicitly added external paths.

The `[external]` data is stored in the same `_folder_cache` entry that `/api/folders` reads from. The cache key is resolved via `get_project_root()` (request-scoped ContextVar → `ZIYA_USER_CODEBASE_DIR` → cwd), so `POST /api/add-explicit-paths` requests must include the `X-Project-Root` header to ensure the write targets the correct cache entry. On the frontend, WebSocket `file_added` events for `[external]` paths trigger a full `fetchFolders` refetch rather than incremental tree insertion, because the flat broadcast path format doesn't match the nested `{children}` structure the server builds for external directories.

The `[external]` data is stored in the same `_folder_cache` entry that `/api/folders` reads from. The cache key is resolved via `get_project_root()` (request-scoped ContextVar → `ZIYA_USER_CODEBASE_DIR` → cwd), so `POST /api/add-explicit-paths` requests must include the `X-Project-Root` header to ensure the write targets the correct cache entry. On the frontend side, WebSocket `file_added` events for `[external]` paths trigger a full `fetchFolders` refetch rather than incremental tree insertion, because the flat broadcast path format doesn't match the nested `{children}` structure the server builds for external directories.

---

## Diff Application Pipeline

Code changes suggested by the model are applied via `POST /api/apply-changes`. The pipeline (`app/utils/diff_utils/pipeline/pipeline_manager.py`) tries strategies in order:

1. **System patch** (`patch -p1`) — fast, handles most well-formed diffs
2. **Git apply** (`git apply`) — handles edge cases that `patch` misses
3. **Difflib** — Python-based fuzzy matching for diffs with wrong line numbers or whitespace issues
4. **LLM resolver** (future) — for structurally complex cases

Each hunk is tracked independently through the pipeline. The result reports per-hunk status (succeeded, failed, already-applied) so the UI can show partial success accurately. Failed hunks include the pipeline stage where they failed and why.

### Language Validation

After applying a diff, the pipeline runs a **language-specific validator** (`app/utils/diff_utils/language_handlers/`) to catch syntax errors before writing to disk. Handlers exist for Python, TypeScript, JavaScript, Java, C++, and Rust.

The TypeScript handler uses `tsc` when available (preferring a project-local `node_modules/.bin/tsc`). Because tsc cannot validate isolated files (missing imports, tsconfig context), the handler passes `--isolatedModules --noResolve` and only treats **syntax errors** (TS1xxx) as hard failures. Import/type resolution errors (TS2xxx+) fall back to basic bracket-matching validation. For `.tsx` files, the handler uses the correct file extension and `--jsx react-jsx` so JSX syntax is parsed correctly.

When tsc reports only non-syntax diagnostics (TS2xxx+), the handler trusts tsc's syntax analysis and returns valid — it does not fall back to heuristic validation.  When tsc is completely unavailable, the fallback path uses bracket-matching as the structural gate; additional heuristic checks (semicolon usage, angle bracket balance, `any` type usage) are logged as debug warnings but do not block diff application, since they produce too many false positives on real-world TSX/JSX files.

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

### Message Count Regression Guards

Beyond shell detection, a separate defense layer prevents *any* merge or sync operation from reducing the number of messages in a conversation. This addresses a class of bugs where partial data (from stale server copies, interrupted syncs, or cache inconsistencies) could silently overwrite complete conversation histories.

| Layer | Location | Mechanism |
|---|---|---|
| Server bulk-sync | `app/api/chats.py` | Rejects incoming updates that have fewer messages than the existing server copy (threshold: existing > 2 messages) |
| SERVER_SYNC merge | `ChatContext.tsx` syncWithServer | When server version wins by `_version` but has fewer messages, keeps local messages while accepting server metadata |
| In-memory preservation | `ChatContext.tsx` setConversations updater | Compares merged conversation message count against React state; keeps whichever has more |
| Lazy-load guard | `ChatContext.tsx` loadConversation | IDB and server lazy-load only accepted when loaded message count ≥ current count |
| Delegate fetch guard | `ChatContext.tsx` loadConversation | Background delegate fetches won't overwrite local messages if server has fewer |
| Shell append guard | `ChatContext.tsx` addMessageToConversation | Detects when a message is being appended to a shell conversation and triggers async recovery from IDB before appending |
| IDB dedup guard | `db.ts` _saveConversationsWithLock | Shell entries never overwrite non-shell entries in the same save batch |
| Cross-tab merge guard | `ChatContext.tsx` mergeConversations | Cross-tab BroadcastChannel merge rejects remote versions with fewer messages even if `_version` is newer |
| IDB read-before-write guard | `db.ts` _saveConversationsWithLock | Before every IDB write, reads existing record and preserves messages for any conversation where the write would reduce count |
| Retention timestamp | `retentionPurge.ts` | Retention decisions use `lastAccessedAt` (most recent activity), not `createdAt` — an old conversation still in active use is never purged |

The threshold of `> 2` messages allows shell conversations (which legitimately have only first+last) to be freely replaced, while protecting any conversation with meaningful history.

#### Known Limitation: Direct IDB Writers

Several UI paths (`ChatHistory.tsx` rename/delete, `MUIChatHistory.tsx` rename/delete/fork) call `db.saveConversations()` directly, bypassing `queueSave`. These write the entire conversations array as a single IDB record. If `queueSave` is also running (e.g., from streaming), the two writes are serialized by `navigator.locks` but read stale data — a TOCTOU (Time-of-Check-to-Time-of-Use) race. The IDB read-before-write guard mitigates the most dangerous outcome (message loss) by preserving higher message counts from the existing IDB record. Metadata changes (title, folder) from the losing writer may still be lost and require a retry. A future refactor should route all conversation mutations through `queueSave`.

### Null-Safety in Sidebar Tree Rendering

The chat sidebar (`MUIChatHistory`) computes a tree from conversations and folders inside a `useMemo`. A lightweight FNV-1a hash detects whether inputs have changed. Because `useMemo` runs during rendering, any unhandled exception here crashes the entire page — React error boundaries don't catch errors thrown during the render phase of hooks.

**Key defensive measures:**

| Layer | Location | Mechanism |
|---|---|---|
| FNV hash null guard | `MUIChatHistory.tsx` treeDataRaw | All `fnv()` inputs use `\|\| ''` fallbacks so null/undefined fields produce empty-string hashes instead of TypeErrors |
| Tree node name guard | `MUIChatHistory.tsx` treeDataRaw | `name: conv.title \|\| 'Untitled'` prevents null names from reaching the row renderer |
| Shell normalization | `db.ts` getConversationShells | Filters out entries with no `id` and normalizes `title` to `'Untitled'` before returning |
| Merge normalization | `ChatContext.tsx` mergeConversations | Normalizes `title` and `messages` on every merged conversation before setting state |
| Error boundary | `MUIChatHistory.tsx` (export wrapper) | A `ChatHistoryErrorBoundary` wraps the sidebar so rendering crashes show a retry button instead of a white screen |

**Root cause:** `setConversations` is a raw `useState` setter called from 40+ locations. Many of these paths — server sync, cross-tab BroadcastChannel, delegate polling, lazy loading — can introduce conversations with null or undefined `title` fields. The validation in `queueSave` only runs on the persistence path, not on all state-setting paths. The defensive measures above ensure the rendering layer is resilient regardless of what data enters state.

### Circular Folder Reference Protection

The sidebar tree is built from folders with `parentId` references. If a folder's `parentId` equals its own `id` (self-reference) or two folders reference each other (mutual cycle), the tree-building code creates a circular data structure. Six recursive functions then hit infinite recursion → stack overflow → page crash. This is the most common cause of "entire page crashes" because the crash occurs inside `useMemo` during render, bypassing all error boundaries.

**How circular references enter the system:** Server sync (`listServerFolders`) passes `parentId` from the server with no validation. Corrupted server data from race conditions in drag-drop folder moves can produce `parentId === id`.

**Defenses:**

| Layer | Location | Mechanism |
|---|---|---|
| Self-ref guard | `MUIChatHistory.tsx` tree building | `folder.parentId !== folder.id` check before adding a folder as child of its parent |
| Depth limits | `flattenVisibleNodes`, `rollUpConversationCount`, `sortRecursive`, `removeNodeFromTree`, `anchorFolder`, `reanchor` | All recursive functions cap at depth 20 |
| Visited-set | `flattenVisibleNodes` | Tracks seen node IDs to break cycles even within the depth limit |
| Error boundary | `MUIChatHistory.tsx` export wrapper | `ChatHistoryErrorBoundary` catches any residual crashes and shows a retry button |
