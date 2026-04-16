## Frontend Background Stability

When the browser tab is hidden (e.g., screen locked, other tab focused), the
frontend suspends or throttles all background activity to prevent crashes:

| Background process | Guard |
|---|---|
| Server health polling (`ServerStatusContext`) | Skips when `document.hidden` |
| Conversation sync (`syncWithServer`, 30s) | Skips when `document.hidden` |
| Delegate status polling (`useDelegatePolling`, 3s) | Skips when `document.hidden` |
| Folder scan progress polling | Skips when `document.hidden` |
| Conversation GC (5min) | Skips when `document.hidden` |
| Delegate WebSocket streaming | Drops messages when `document.hidden` |
| MarkdownRenderer MutationObserver | Disconnects when `document.hidden` |

Additional resource management:
- **Singleton event listener pattern** — MathRenderer LaTeX copy handlers and
  MarkdownRenderer throttle-button observers use module-level singletons
  instead of per-instance `document.addEventListener` calls.  Each component
  instance registers/unregisters via a lightweight registry; a single document
  listener delegates to the relevant instance.  Keeps listener count O(1)
  instead of O(N messages × M math expressions).
- **ResizeObserver feedback prevention** — Vega-Lite chart ResizeObservers use
  `requestAnimationFrame` throttling and a single-instance guard to prevent
  the DOM-mutation→observation→DOM-mutation feedback loop.
- **Derived state via useMemo** — `currentMessages` is computed synchronously
  during render via `useMemo` with a ref-based previous-value comparison,
  not via `useEffect` + `setState`.  The latter creates a render loop because
  every `setState` triggers a new commit which re-runs the effect.
- **Save queue coalescing** — `queueSave` serializes IndexedDB writes via a
  promise chain; only changed conversations are synced to the server.
- **State updater purity** — `setConversations` updaters avoid side-effect
  logging that would allocate objects proportional to total conversation count.

---

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
    │   └─ memory_search / memory_save / memory_propose  (~/.ziya/memory/)
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
        ├─ memory/           (persistent structured memory across sessions)
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

Model definitions live in `app/config/models_config.py` as nested dicts.
The provider factory (`app/providers/factory.py`) routes each model to its
backend-specific provider:

| Model family | Provider | API used |
|---|---|---|
| Claude (Sonnet, Opus, Haiku) | `BedrockProvider` | `invoke_model_with_response_stream` (Anthropic body format) |
| DeepSeek, Qwen, Kimi, MiniMax, GLM, OpenAI-GPT (on Bedrock) | `OpenAIBedrockProvider` | `invoke_model_with_response_stream` (OpenAI Chat Completions body format) |
| Nova (Micro, Lite, Pro, Premier) | `NovaBedrockProvider` | `converse_stream` (Converse API format) |
| Anthropic Direct | `AnthropicDirectProvider` | Anthropic Messages API |
| OpenAI / OpenRouter | `OpenAIDirectProvider` | OpenAI Chat Completions API |

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

---

## File Tree & Context

The browser requests the project file tree from `GET /api/folders`, which triggers a background scan with gitignore-aware filtering. Token counts per file are estimated during the scan.

Files selected by the user are sent in the chat payload as relative paths. The server reads their content and injects it into the system prompt as annotated file blocks with line numbers.

External paths (outside the project root) can be added via `POST /api/add-explicit-paths`. These are tracked in `_explicit_external_paths` (a server-memory set) and in the folder cache under a `[external]` key. The security check in `apply_changes` uses `is_path_explicitly_allowed()` to permit writes only to the project root or explicitly added external paths.

The `[external]` data is stored in the same `_folder_cache` entry that `/api/folders` reads from. The cache key is resolved via `get_project_root()` (request-scoped ContextVar → `ZIYA_USER_CODEBASE_DIR` → cwd), so `POST /api/add-explicit-paths` requests must include the `X-Project-Root` header to ensure the write targets the correct cache entry. On the frontend, WebSocket `file_added` events for `[external]` paths trigger a full `fetchFolders` refetch rather than incremental tree insertion, because the flat broadcast path format doesn't match the nested `{children}` structure the server builds for external directories.

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
| Save debounce | `queueSave()` debounce block | When `changedIds` is provided, coalesces saves within a 300ms window to prevent rapid-fire IDB writes during streaming |

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

### Read-Purity of Chat Endpoints

The `GET /chats/{chat_id}` endpoint is a pure read — it does **not** update
`lastActiveAt` or any other timestamp.  `lastActiveAt` is only bumped by
actual mutations: `add_message`, `update`, and `bulk-sync`.

This is a deliberate design choice.  The periodic SERVER_SYNC loop full-fetches
conversations via this endpoint when it detects version mismatches.  If the GET
had a side effect of touching `lastActiveAt`, every synced conversation would
appear "just used", corrupting the sort order in the sidebar.  The same
`lastActiveAt` field drives data-retention expiry, so a touch-on-read would also
prevent old unused conversations from being cleaned up by the retention policy.

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

### Tree Rebuild Caching (FNV Hash Fast Paths)

The `treeDataRaw` useMemo in `MUIChatHistory` uses two FNV-1a hashes to skip expensive work:

| Hash | Inputs | Purpose |
|---|---|---|
| Structural hash | folder IDs, names, parentIds, conversation IDs, titles, folderIds, delegate status | If unchanged → return cached tree (zero work) |
| Sort hash | conversation lastAccessedAt, pinned folder set | If only this changed → sort-only fast path (shallow-copy + re-sort, skip full rebuild) |

Both hashes MUST be stored in refs (`lastTreeDataInputsRef`, `lastSortHashRef`) at the end of every code path (full rebuild AND sort-only). If either ref is not updated, subsequent renders see a hash mismatch and fall through to a full O(N×M) rebuild — which with hundreds of conversations causes main-thread stalls and crashes during streaming.

The sort-only fast path creates **shallow copies** of all tree nodes (via `cloneNode`) before mutating. This is essential because `treeData` React state may still hold references to the previous tree — mutating in place violates React's immutability contract and causes inconsistent renders during concurrent updates (e.g. streaming + SERVER_SYNC poll in the same frame).

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

---

### Server Memory Management

Several server-side data structures track per-conversation state across requests. Without bounds, these grow unboundedly over long server sessions and can reach multiple GB.

**Bounded structures:**

| Structure | Location | Eviction Strategy |
|---|---|---|
| `FileStateManager.conversation_states` | `app/utils/file_state_manager.py` | LRU cap (20 conversations) + TTL (1 hour). Eviction runs on every `initialize_conversation()` and `_save_state()` call. Each conversation stores 4 copies of every selected file's content for change tracking. |
| `ExtendedContextManager._conversation_states` | `app/utils/extended_context_manager.py` | LRU cap (50 entries) + TTL (2 hours). Eviction runs on `activate_extended_context()`. |
| `GlobalUsageTracker.conversation_usages` | `app/streaming_tool_executor.py` | 100-conversation cap (evicts oldest on overflow). Per-conversation list capped at 500 `IterationUsage` entries. |
| `stream_metrics['chunk_sizes']` | `app/streaming_tool_executor.py` | Rolling window of last 100 entries per stream. |
| `PromptCache._cache` | `app/utils/prompt_cache.py` | TTL (1 hour) + LRU cap (200 entries). Lazy eviction on read; forced cleanup every 30 min by `_periodic_memory_cleanup`. |
| `_prompt_cache` (prompts_manager) | `app/agents/prompts_manager.py` | LRU cap (50 entries). Evicted on overflow; cleared on model switch. |
| `DelegateManager` plan state | `app/agents/delegate_manager.py` | Terminal plans evicted after 2 hours of inactivity by `_periodic_memory_cleanup`. Running plans are never evicted. |
| `ThreadStateManager.thread_states` | `app/utils/file_cache.py` | Cap (100 threads). Dead threads pruned lazily when cap is exceeded. |

**Per-request structures** (garbage collected when the request ends):

| Structure | Scope | Notes |
|---|---|---|
| `conversation` list in `stream_with_tools()` | Single request | Grows over tool iterations (up to 200). Includes full assistant text + tool results per iteration. |
| `StreamingToolExecutor` instance | Single request | Created per-request, not cached. All instance state (`_tool_param_sets`, `_content_optimizer`, etc.) dies with the request. |

**Tuning:** The constants `_MAX_CONVERSATIONS` (default 20) and `_CONVERSATION_TTL_SECONDS` (default 3600) in `file_state_manager.py` control the primary memory bound. Increase `_MAX_CONVERSATIONS` to support more concurrent active conversations at the cost of higher baseline memory.

---

## Structured Memory System

Ziya maintains a persistent knowledge store across sessions so the model
behaves like a colleague who was in every previous meeting.  The system is
local-first (all data in `~/.ziya/memory/`), human-owned (approve/edit/delete),
and invisible when working correctly.

> **Opt-in (experimental):** Memory is disabled by default. Enable with
> `--memory` on the command line or `ZIYA_ENABLE_MEMORY=true` in the
> environment.  When disabled, no memory tools appear, no background
> extraction runs, and no memory prompt sections are injected.

### Phase 0 — Flat Store

All memories live in a single JSON file (`memories.json`).  Each entry has:
- **content**: A distilled principle or fact (not raw transcript)
- **layer**: `domain_context`, `architecture`, `lexicon`, `decision`,
  `active_thread`, `process`, `preference`, `negative_constraint`
- **tags**: Free-form keywords for search
- **status**: `active`, `pending`, `deprecated`, `archived`
- **scope**: Optional project-path weighting

### Tools

The model interacts with memory through five builtin MCP tools:

| Tool | Purpose |
|---|---|
| `memory_search` | Keyword/tag/layer search across the flat store |
| `memory_save` | Direct save (user-initiated via `/remember`) |
| `memory_propose` | Agent proposes a memory for later user approval |
| `memory_context` | Browse mind-map handles — omit node_id for root overview |
| `memory_expand` | Load all memories under a node and its descendants |

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/memory` | GET | Status overview (counts, pending proposals) |
| `/api/v1/memory/search` | GET | Search memories by keyword/tag/layer |
| `/api/v1/memory/all` | GET | List all active memories |
| `/api/v1/memory` | POST | Save a new memory |
| `/api/v1/memory/{id}` | PUT | Edit a memory |
| `/api/v1/memory/{id}` | DELETE | Delete a memory |
| `/api/v1/memory/proposals` | GET | List pending proposals |
| `/api/v1/memory/proposals/{id}/approve` | POST | Approve a proposal |
| `/api/v1/memory/proposals/approve-all` | POST | Approve all proposals |
| `/api/v1/memory/proposals/{id}` | DELETE | Dismiss a proposal |
| `/api/v1/memory/mindmap` | GET | Full mind-map tree |
| `/api/v1/memory/mindmap/{node_id}` | GET | Node with children context |
| `/api/v1/memory/mindmap` | POST | Create/update a mind-map node |
| `/api/v1/memory/mindmap/{node_id}` | DELETE | Delete node (children reparented) |
| `/api/v1/memory/mindmap/{node_id}/expand` | POST | All memories under node tree |
| `/api/v1/memory/organize` | POST | LLM-powered clustering, relation extraction, and mind-map bootstrap |

See `design/structured-memory-system.md` for the full design rationale,
research foundation, and roadmap for Phases 1–3.

### Memory Browser UI

The frontend includes an interactive **Memory Browser** (`frontend/src/components/MemoryBrowser.tsx`) accessible via the 🧠 button in the header bar or the keyboard shortcut `Ctrl+Shift+M`.  It exposes the full memory architecture through five tabs:

| Tab | Purpose |
|---|---|
| 📊 Dashboard | Stat cards (total, proposals, domains, cross-links, review items), layer distribution ring chart, top-5 most important memories |
| 🌐 Knowledge Graph | Force-directed SVG visualization of mind-map nodes (large circles) and individual memories (colored dots).  Nodes are draggable; links show parent-child, cross-links, and memory refs.  Importance maps to node size/glow, layer maps to color. Click a memory node to jump to it in the Explorer tab. |
| 📚 Explorer | Searchable, filterable list of all memories with inline edit, delete, and real-time search.  Memories show layer badge, importance stars, freshness label, and tags. |
| 💡 Proposals | Review queue for pending memory proposals.  Each card shows content, layer, tags, and age.  Approve individually or in batch. |
| 🩺 Health | Stale memories (90+ days unaccessed), oversized mind-map nodes (12+ memories that should split), orphan memories (not linked to any node).  "Organize Knowledge" triggers LLM-powered clustering and relation extraction.  "Run Maintenance" triggers cell division and cross-link discovery. |

### Knowledge Organization

The **memory organizer** (`app/utils/memory_organizer.py`) uses LLM calls to build
mind-map structure from unorganized memories:

1. **Clustering** — Groups memories into thematic domains (e.g. "Network Architecture", "AI Tooling") via a service model call.  Batched for large corpora.
2. **Placement** — Creates mind-map nodes for each domain and assigns memories to them.  Merges with existing domains when tag/handle overlap is sufficient.
3. **Relation extraction** — Within each domain, identifies `supports`, `contradicts`, `elaborates`, and `depends_on` relationships between memories.
4. **Cross-link discovery** — Connects domains in different branches that share tags (algorithmic, uses existing `memory_maintenance.py`).
5. **Cell division** — Splits oversized nodes into focused children when a tag cluster reaches threshold.

Auto-triggers when orphan memories exceed 15 (configurable via `AUTO_ORGANIZE_ORPHAN_THRESHOLD`).

The API layer (`frontend/src/api/memoryApi.ts`) provides typed wrappers around all `/api/v1/memory` endpoints with shared constants for layer colors, labels, and icons.

### Behavioral Activation

The memory system uses a two-zone prompting strategy to ensure the model
actively uses memory tools rather than ignoring them:

1. **Activation directive** (position 0 in system prompt) — a brief
   "IMPORTANT:" message priming the model to use memories silently and
   propose new ones.  Placed at the very start of the system message
   where attention weight is highest.
2. **Full behavioral guidance** (end of system prompt) — detailed rules
   for when to search, save, and propose.  Includes imperative triggers
   like "This is not optional" and "At the end of a substantive
   conversation, review what was discussed and propose any facts worth
   retaining."

### Search Ranking (ByteRover-informed)

Memory search uses a composite score combining three factors:

$$\text{score} = \text{keyword\_match} \times (0.5 + \text{importance}) \times (0.3 + 0.7 \times \text{recency})$$

- **keyword_match**: 3 for tag hit, 2 for content hit, 1 for layer hit
- **importance**: 0.0–1.0, starts at 0.5, bumps +0.05 on each retrieval (caps at 1.0)
- **recency**: exponential decay `exp(-0.01 × days_since_access)`, half-life ~70 days

Memories that are frequently retrieved naturally become "core" knowledge
(importance → 1.0), while stale memories decay in ranking without being deleted.

### Auto-Maintenance (Phase 2)

Every `memory_save` call triggers automatic maintenance:

1. **Auto-placement** — the new memory is filed into the best-matching
   mind-map node by tag overlap.
2. **Cell division** — if the target node now exceeds 12 memories, the
   strongest tag cluster is split into a new child node.
3. **Cross-link discovery** — nodes in different branches sharing ≥2
   tags get bidirectional cross-links.
4. **Auto-linking** — embedding cosine similarity against the full store
   finds the top-5 most similar memories.  Above 0.88 similarity,
   an `elaborates` relation is created; 0.75–0.88 creates a `supports`
   relation.  Links are bidirectional (new→existing and existing→new).
5. **Tag enrichment** — for memories with >0.80 cosine similarity, tags
   from the new memory propagate to the existing memory (up to 2 new
   tags, capped at 6 total).  This enables tag-based queries to find
   related content that was tagged differently across sessions.

Additional maintenance endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/memory/review` | GET | Stale memories (>90 days), oversized nodes, orphans |
| `/api/v1/memory/maintenance` | POST | Full maintenance pass across all nodes |

### Performance

- **Read cache** — `MemoryStorage` caches the parsed memories list in
  memory, keyed by file mtime.  Subsequent searches within the same
  conversation skip disk I/O and ALE decryption.  Invalidated
  automatically on any write.
- **Retrieval escalation** — when keyword search returns empty and a
  mind-map exists, the out-of-domain response directs the model to try
  `memory_context` (tree browsing) before giving up.

### Post-Conversation Memory Extraction

After each substantive conversation (3+ human turns), a background task
automatically extracts domain facts, decisions, vocabulary, and lessons
from the conversation — without any user action or awareness.

The extraction pipeline:

1. **Strip artifacts** — tool results, code blocks, diffs, base64 blobs,
   and REWIND markers are removed.  Only the human/assistant discourse
   remains (~5-10% of original token count).
2. **Call extraction model** — the stripped conversation is sent to a
   lightweight service model via the `ServiceModelResolver`.  The model
   used depends on the active endpoint (Nova Lite for Bedrock, Flash
   Lite for Google, GPT-4.1-mini for OpenAI, Haiku for Anthropic).
   Existing memories are summarized in the prompt so the model avoids
   re-extracting known facts.
   The extraction prompt enforces five gates:
   1. **Next-session test** — would this be useful without today's context?
   2. **Self-containment** — no unresolved "the document" / "the system" references
   3. **Session artifact rejection** — code fixes, refactoring notes, CSS, test infrastructure
   4. **Tool meta-commentary** — descriptions of the AI tool itself
   5. **Career narrative** — personal branding, motivational content

   After extraction, a **structural quality gate** enforces:
   - Backtick-wrapped code identifiers (≥3 → reject)
   - Source file references (≥2 → reject)
   - CSS/layout property patterns → reject
   - Dangling references (≥2 → reject), length bounds, tag count cap (4)

3. **Deduplicate** — candidates are compared against the existing store
   using tag overlap + significant-word overlap.  Near-duplicates are
   filtered out.
4. **LLM-guided comparison** — for each surviving candidate, the system
   finds the top-5 most similar existing memories (by tag + word overlap)
   and asks a cheap service model to classify: **ADD** (genuinely new),
   **UPDATE** (supersedes/contradicts/consolidates an existing memory),
   or **NOOP** (semantic duplicate).  UPDATE replaces the target memory's
   content while preserving its ID, importance, and access history.
   Inspired by Mem0's two-phase extract→compare pipeline.
5. **Classify and save** — high-confidence extractions in most layers
   are auto-saved.  Only `decision` and `active_thread` go to the
   proposal queue (decisions are high-stakes; active threads go stale).

Project association is **structural, not textual** — the extraction
pipeline stamps `Memory.scope.project_paths` from the request context.
Memory content names domain concepts (documents, systems, APIs) but
does not redundantly embed "in the X project" in every fact.  The
extraction model is deliberately NOT told the project name — when it
was, it embedded the label in content despite instructions not to.
The conversation itself contains enough context for the model to name
specific documents, systems, and people organically.

### Auto-Promotion

Proposals are promoted to the active memory store automatically when
`memory_search` finds no active memories but a proposal matches the
query.  A search hit is strong evidence the knowledge is needed, so the
proposal is promoted on the spot and returned as a result.

The auto-save layer list includes architecture, negative_constraint,
preference, and process (in addition to lexicon and domain_context).
High-confidence extractions in these layers skip the proposal queue entirely.

### Service Model Resolver

Subsystems that need a cheap model call (memory extraction, future
summarization, classification) use `ServiceModelResolver`
(`app/services/model_resolver.py`) instead of hardcoding a provider.

Resolution order:
1. **Env var override** — `ZIYA_{CATEGORY}_MODEL` (e.g.
   `ZIYA_MEMORY_EXTRACTION_MODEL=us.amazon.nova-micro-v1:0`)
2. **Per-category endpoint override** — `ZIYA_{CATEGORY}_ENDPOINT`
   allows routing e.g. extraction through a local model while the
   primary model uses Bedrock
3. **Plugin config** — `ServiceModelProvider.get_service_model_config()`
4. **Endpoint-aware default** — picks the cheapest model for the
   active endpoint
5. **Bedrock fallback** — if the endpoint is unknown

Default lightweight models per endpoint:

| Endpoint | Default Service Model |
|---|---|
| bedrock | `us.amazon.nova-lite-v1:0` |
| google | `gemini-2.0-flash-lite` |
| openai | `gpt-4.1-mini` |
| anthropic | `claude-haiku-4-5-20251001` |
| local | `llama3.2:3b` (via Ollama at `localhost:11434`) |

**Configuration:**

| Variable | Default | Description |
|---|---|---|
| `ZIYA_MEMORY_EXTRACTION_MODEL` | (per-endpoint) | Override extraction model |
| `ZIYA_MEMORY_EXTRACTION_ENDPOINT` | (active endpoint) | Override extraction endpoint |
| `ZIYA_MEMORY_EXTRACTION_REGION` | `us-east-1` | AWS region (Bedrock only) |
| `ZIYA_LOCAL_MODEL_URL` | `http://localhost:11434/v1` | URL for local model API |