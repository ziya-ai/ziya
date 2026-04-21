# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- ═══════════════════════════════════════════════════════════════════
  HOW TO ADD ENTRIES:
  All new changes go in [Unreleased] below. NEVER add to a numbered version.
  When a release is cut, [Unreleased] is renamed to the new version number
  and a fresh empty [Unreleased] section is created above it.
  Versions are listed newest-first (reverse chronological).
═══════════════════════════════════════════════════════════════════ -->

## [Unreleased]

### Added

### Fixed

### Changed

## [0.6.4.10] - 2026-04-21

### Added
- **Hallucination detection: session-scoped content fingerprinting (Layer A)**:
  New `app/hallucination/` subsystem catches the model reproducing prior real
  tool results as prose in its assistant text instead of issuing a `tool_use`
  block — a failure mode that became increasingly common in long conversations
  with many tool calls, where the narrow pre-existing regex patterns in
  `text_delta_processor.py` caught none of it.
  - **`region_extraction.py`**: extracts the scannable portion of assistant text
    by excluding Markdown code fences (triple-backtick and tilde), indented code
    blocks (4+ spaces or tab), blockquotes, and inline backtick spans including
    multi-backtick CommonMark escapes. Over-excludes on purpose: false negatives
    are recoverable, false positives on analytical prose about the detection
    system itself (or on pasted conversation transcripts) damage operator trust.
  - **`shingle_index.py`**: per-conversation store of tool-result fingerprints.
    Each registered result contributes (a) word-level 5-gram shingles hashed
    with blake2b-64 for paraphrased-reproduction detection and (b) per-line
    whitespace-normalized hashes for verbatim short-line detection. Bounded to
    200 shingles per result and 100 results per session (LRU eviction). Results
    shorter than 100 characters and lines shorter than 20 characters are
    skipped as noise. Thread-safe for single-process use.
  - **Detection thresholds**: high-confidence match requires ≥5 shingle
    overlaps or ≥2 line matches; low-confidence requires ≥3 shingle overlaps or
    ≥1 line match. Only high-confidence fires the retry loop; low-confidence is
    logged for observability and later threshold tuning.
  - **Layer 1 false-positive fix**: existing narrow hallucination patterns in
    `text_delta_processor.py` (which matched the literal text of
    `run_shell_command` error messages) now run against `scannable_text()`
    output rather than raw `assistant_text`. Previously fired on any
    analytical prose that discussed the detection strings themselves; the
    false-positive was reproduced live during design review.
  - **Fingerprint registration** in `tool_execution.py`: verified tool results
    (i.e., those that passed HMAC signature verification) are registered into
    the shingle index at the same emission point that yields
    `tool_result_for_model`. Error/blocked results are skipped so the model can
    legitimately echo server error phrases. Handles both list-of-blocks and
    plain-string result formats, exception-safe.
  - **Streaming detection** in `text_delta_processor.py`: runs every ~256 chars
    of accumulated scannable text against the session's fingerprint set using a
    1200-char tail window. On high-confidence match, populates
    `TextDeltaState.parrot_match` with `tool_use_id`, `tool_name`,
    `shingle_overlap`, and `line_matches`, and aborts the current iteration.
  - **Targeted corrective message** in `streaming_tool_executor.py`: when
    `parrot_match` is set, the retry loop injects a parrot-specific corrective
    citing the exact tool and invocation being parroted, with match strength
    numbers, and offering two concrete recovery paths (re-call the tool, or
    quote the prior result inside a fenced code block). Falls back to the
    existing generic "STOP / do not fabricate" message when detection came from
    the narrow regex patterns with no parrot_match info. Retry cap of 3
    preserved from existing behavior.
  - **Non-goal**: the subsystem does not attempt to give the model a token it
    can verify. Any token the model can read it can reproduce; verification
    must live server-side where the model can't forge it. Design doc for
    rationale and future layers (pressure score, provenance-absence check) at
    `.ziya/hallucination-detection-design.md`.
- **Compound shell command support (for/while/if/case/select)**: Shell server now
  detects compound shell constructs and routes them through `sh -c` instead of
  attempting to exec them as standalone binaries. Body commands within compound
  constructs are validated against the allowlist by stripping shell keywords
  (for, while, do, done, if, then, etc.) and checking the actual command words.
  Command substitutions (`$(...)` and backticks) are also recursively validated.
- **YOLO mode propagation to file_write**: `/shell yolo` now sets
  `ZIYA_YOLO_MODE` env var, and `file_write`'s write-policy check honors it —
  unrestricted in-process writes when YOLO is active, mirroring shell server
  behavior.

### Fixed
- **Chat history loss from shell writes (three-layer fix)**: SERVER_SYNC was
  loading local state as shells via `getConversationShells()` (messages stripped
  to first+last or blanked) and pushing them to the server through `bulkSync`
  whenever `_version`/`lastAccessedAt` beat the server's, silently truncating
  or blanking the authoritative per-project chat JSON files. Fingerprint on
  damaged records: exactly 2 messages with fresh message IDs sharing the same
  `Date.now()` prefix (synthesized in a single `conversationToServerChat` call).
  - **Frontend filter (`ChatContext.tsx` `SERVER_SYNC`)**: the push-list filter
    now drops any conversation with `_isShell` or where
    `messages.length < _fullMessageCount`. Shells never enter the sync pipeline.
  - **Frontend chokepoint (`conversationSyncApi.bulkSync`)**: defense-in-depth
    filter drops shell/partial records at the network boundary regardless of
    caller. Logs `bulkSync: dropped N shell/partial chats`.
  - **Server guard (`app/api/chats.py`)**: tightened the `bulk-sync` regression
    guard from "only block shrinkage when existing > 2" to "block any shrinkage
    when existing >= 1" and added a content-length guard that rejects
    same-count overwrites where incoming content is under 25% of existing.
  - **Lazy-load rendering (`ChatContext.tsx`)**: the IDB and server acceptance
    gates no longer require `length > 2` to replace in-memory shells. They now
    compare total content length, so real 2-message conversations (and records
    already truncated by the earlier bug) actually render their text instead
    of staying blank. Before the fix, clicking a 2-message conversation left
    the chat area empty because the shell had 2 empty-content messages and the
    acceptance test `length > existing.length` (2 > 2 = false) rejected both
    sources.
- **Deferred message rendering for large conversations**: clicking into a
  150+ message conversation blocked the main thread for 15+ seconds during
  initial React reconciliation of all markdown content. Introduced
  `LazyMarkdownRenderer` in `Conversation.tsx`: each non-streaming message
  wraps `MarkdownRenderer` in a placeholder that defers the real mount via a
  shared `requestIdleCallback` queue, with `IntersectionObserver` bumping
  priority when a placeholder enters the viewport (500px preload margin).
  Small messages under 400 chars render inline. Streaming is unaffected because
  live streams go through `StreamedContent.tsx`, not this path — every message
  handed to `LazyMarkdownRenderer` is already settled.
- **Deferred D3 diagram rendering**: the `LazyD3Renderer` wrapper in
  `MarkdownRenderer.tsx` now queues diagram mounts through a shared idle-time
  queue. Assistant messages with many heavyweight visualizations
  (Vega-Lite/DrawIO/Graphviz/Mermaid/Joint) no longer block the main thread on
  click; text appears first, diagrams materialize one at a time.
  `isStreaming={true}` bypasses the queue so live streaming output renders
  immediately.
- **Mermaid CDN fallback every load**: `mermaidPlugin.ts` was invoking
  `import(moduleSpecifier)` through a parameterized helper, which prevented
  webpack from statically resolving and emitting a `mermaid` chunk. Every
  mermaid diagram triggered `❌ Chunk import failed → ⚠️ Loading from CDN
  fallback`, adding ~500–1500ms network latency. Fixed to use a literal
  `import(/* webpackChunkName: "mermaid" */ 'mermaid')`. A companion
  `frontend/src/types/mermaid-shim.d.ts` ambient module declaration works
  around TypeScript 4.9's inability to parse mermaid's `exports` field
  (`moduleResolution: "bundler"` requires TS 5.0+).
- **Initial conversation-switch window clamp**: `Conversation.tsx`
  `messageWindow` state initialized to `Infinity` and only clamped to
  `INITIAL_WINDOW` inside a `useEffect`, so the first render after a
  conversation switch rendered all N messages synchronously before the clamp
  applied. Changed initial value to `INITIAL_WINDOW` and added an inline ref
  comparison (`effectiveWindow = windowConvRef.current !== currentConversationId
  ? INITIAL_WINDOW : messageWindow`) so the clamp takes effect during render,
  not after.
- **Nested scrollbar in folder-tree-panel**: `.folder-tree-panel
  .ant-tabs-content` had `overflow: auto`, producing a confusing outer
  scrollbar alongside the chat list's own inner scrollbar. Set to
  `overflow: hidden` — inner tab children (MUIChatHistory, ContextsTab,
  MUIFileExplorer) each manage their own scrolling.
- **diff-utils idempotency: double-apply now passes 139/151 (was 124/151)**:
  Fixes target the "apply the same diff twice, expect a no-op on the second
  apply" harness invariant without changing single-apply behavior.
  - **Pattern B (re-add false-application)** in `patch_apply.py`: new
    `_added_block_already_present()` helper short-circuits the destructive
    standard-fallback path when surgical and content-based matching both decline
    and the added block is already present (contiguous or scattered) in the
    file. Handles Ziya's backslash-backtick escape convention. Fixes
    `test_backtick_escaping_issue`, `test_custom_bedrock_log_level_change`,
    `test_d3renderer_container_styles`, plus collaterals.
  - **Pattern A (EOF `\ No newline at end of file` re-add duplication)** in
    `pipeline_manager.is_hunk_already_applied`: `old_block_exists` scan now
    excludes the already-applied `new_lines` region only when `old_block` is a
    strict prefix/subset of `new_lines` (`len(old_block) < len(new_lines)`).
    Without the length guard, indentation-only changes where normalized
    `old_block == new_lines` were false-flagged as already-applied because the
    one legitimate match was being excluded. Fixes
    `test_MRE_missing_newline_at_eof`, `test_MRE_hunk_context_mismatch`,
    `test_indentation_only_change`.
  - **Re-add pattern verification** in `validators.is_hunk_already_applied`:
    when removed-lines are a subset of added-lines (classic re-add shape), the
    removal-still-present signal is only suppressed if `new_lines` is actually
    present within +/-5 lines of `pos`. Without this guard, any "removed subset
    of added" diff was treated as re-add and the applier accepted a distant
    similar-looking block as evidence of "already applied" - e.g. finding an
    existing `isRawMode ? ... : <MarkdownRenderer ...>` wrapper on a different
    MarkdownRenderer, or finding `padding: 0 !important;` in a sibling CSS rule.
    Fixes `test_MRE_css_padding_real_file`,
    `test_conversation_israwmode_false_applied`.
  - **Variable-shadow regression** in `pipeline_manager.py`: inner
    `for i, file_line in enumerate(original_lines)` shadowed the outer
    `for i, hunk in enumerate(hunks, 1)`, corrupting `hunk_id_mapping` lookups
    after the distinctive-line search block. Renamed inner loop variable to
    `file_idx`. Fixes `test_variable_shadow_false_negative_already_applied`.
  - **Fuzzy-apply rewriting context lines** in `patch_apply.py`: fuzzy fallback
    now copies context lines from the file at the matched position instead of
    overwriting them with the diff's copy of those lines. Prevents e.g. an
    intentional typo `overflow: visisble` in a CSS context line from being
    silently "corrected" to `overflow: visible` when the hunk only meant to
    change a `margin-bottom` value. Fixes `test_MRE_fuzzy_context_modification`.
- **Non-deterministic diff application under randomized `PYTHONHASHSEED`**:
  Two `max(set(indents), key=indents.count)` calls in `patch_apply.py`'s
  indentation-adaptation code iterated a set in hash-order, so on ties between
  equally-common indent levels the "most common" pick varied between runs.
  Manifested as intermittent failures in `test_additive_replace_deep_offset` and
  `test_vega_lite_closing_brace_fix` (~10% failure rate without a fixed seed,
  100% fail under `PYTHONHASHSEED=0`, 100% pass under `PYTHONHASHSEED=1`). Both
  sites now tie-break on earliest source occurrence:
  `max(set(indents), key=lambda v: (indents.count(v), -indents.index(v)))`.
- **Startup hang with large conversation DBs (~minutes of unresponsive UI)**:
  `db.init()` was firing `purgeExpiredConversations()` as a "background" task that
  called `getConversations()` — a full `getAll()` deserializing every message body
  into memory. On DBs with hundreds of conversations this held the `ziya-db-read`
  Web Lock long enough to starve `getConversationShells()`, which runs immediately
  after init resolves. Result: sidebar stayed empty and the active conversation
  rendered as a shell for up to 20+ minutes until the purge eventually completed.
  Retention purge is now scheduled post-init via `requestIdleCallback` (with
  `setTimeout` fallback) in `ChatContext.initializeWithRecovery`, off the startup
  critical path.
- **Retention purge memory/lock footprint**: `purgeExpiredConversations` rewritten
  to use a cursor-based `cursor.delete()` scan serialized under the
  `ziya-db-write` Web Lock. No longer deserializes retained records or re-serializes
  them back to storage. Flat memory regardless of DB size.
- **SAVE_GUARD data-loss bypass for short conversations**: The shell-write guard
  in `_saveConversationsWithLock` only blocked writes that would reduce message
  count (`messages.length < _fullMessageCount`). Conversations with ≤2 messages,
  or conversations where `_fullMessageCount` got cleared upstream, slipped past
  the guard and had their real message content blanked when a transient shell was
  queued for save. Guard now blocks every shell write unconditionally and routes
  folderId/version metadata through the metadata-only merge path. Stack traces
  from `new Error('shell-write-caller stack')` are attached to the warning to
  identify upstream callers feeding shells into `queueSave`.
- **FAST_PATH_GUARD defense**: Defensive `console.error` + stack trace in the
  fast-path save branch in case a shell ever reaches it. Fails loudly instead of
  silently blanking content.
- **SERVER_SYNC permanent local/server divergence when `_version` ties**: Both
  push and receive filters compared `_version` strictly, and fell back to
  `lastAccessedAt` when `_version` was missing. When local and server ended up
  with identical `_version` but different message counts (possible when an
  earlier shell-push incident set server state, or when a code path appended
  messages without bumping `_version`), neither side could correct the other and
  drift was permanent. Both directions now include message-count divergence as
  an independent trigger: push when `localMsgCount > serverMsgCount`, full-fetch
  when `serverMsgCount > localMsgCount`. Shell guards still prevent pushing
  truncated shells, so widening the push condition is safe.
- **iTerm2 tab activity spinner stuck during idle**: Added OSC 133;D and 133;A
  escape sequences around the prompt loop so iTerm2 recognizes command boundaries
  and stops showing the tab activity spinner while Ziya is waiting for input.
- **TypeScript validation false positives on config-level diagnostics**: Whitelisted
  TS1xxx codes (1323, 1378, 1375, 1432, 1208) that fire from isolated-validation
  flags rather than real syntax errors. Added `--module esnext` to tsc args for
  proper ESM support. TypeScript issue checker now only reports issues newly
  introduced by the diff, not pre-existing ones (compares against original content
  with line-number normalization).
- **DrawIO text cell alignment and label overflow**: Text cells now honor the cell's
  declared `spacingLeft`, `spacingRight`, and `align` style properties for
  positioning. Margin-left is clamped to keep labels inside their parent cell
  bounds. Fallback container-label clamping detects enclosing dashed/unfilled
  shapes when the backing shape isn't a direct sibling. Edge labels no longer have
  opaque white backgrounds that obscured the connection lines behind them.
- **DrawIO popup window pan and zoom**: Popup window now has click-and-drag panning
  via `overflow: auto` on a resizable viewport, and adds a `viewBox` attribute to
  the cloned SVG for proper responsive scaling. Folding is disabled entirely to
  prevent collapsed/expanded.gif 404s. Edit-mode toggle re-applies text cell and
  container-label corrections after maxGraph's refresh wipes them.
- **DrawIO edge routing with multiple edges on same vertex side**: When multiple
  edges enter or exit the same side of a vertex, they are now distributed along
  that side instead of all connecting at the 0.5 midpoint, preventing label overlap.
- **Sidebar tree-cache invalidation for incomplete folder loads**: `useMemo` in
  `MUIChatHistory` now guards against building a tree before folders have synced
  (returns prior tree when folders=0 but conversations>0). Also detects and
  invalidates a cached tree that was built with fewer folder nodes than currently
  available, preventing a stale structural-hash match from freezing the sidebar.
- **Prism syntax highlighting for plaintext**: Added `text`, `plain`, and
  `plaintext` aliases to the language map, and null-safe prism instance access
  in the fallback path.

### Changed

## [0.6.4.9] - 2026-04-18

### Added
- **Document upload and extraction**: New `POST /api/extract-document` endpoint
  accepts PDF, DOCX, XLSX, and PPTX file uploads via multipart form data and
  returns extracted text. Scanned PDFs with no text layer are rendered as page
  images via pypdfium2 for vision-capable models. Frontend supports drag-drop and
  file picker for documents in both `SendChatContainer` and `EditSection`.
- **DocumentChip and ImageChip components**: New `FileChip.tsx` provides compact
  pill-shaped attachment indicators with preview modals for documents and images.
  Chips are displayed in the compose area and inline in conversation messages.
- **Directory scan plugin system**: New `DirectoryScanProvider` plugin interface
  with `ScanCustomization` dataclass lets plugins override recursion depth and
  include/exclude masks per directory. Default provider reads rules from
  `.ziya/scan.yaml` with match conditions (`has_file`, `name`, `name_glob`) and
  actions (`include_only`, `exclude`, `default_depth`, `depth_overrides`).
- **Memory introspection endpoint**: `GET /api/debug/memstats` reports process RSS
  (via `resource` and optional `psutil`), sizes of suspect in-memory caches
  (connection maps, folder cache, AST cache, prompt cache, etc.), gc object counts
  by type, and optional tracemalloc top-allocator snapshots. Companion endpoints
  to start/stop tracemalloc and force gc.collect().
- **DrawIO Open button**: Pop out rendered diagram into a resizable browser window
  with zoom controls (mouse wheel + buttons) and SVG download.
- **PDF page image extraction**: `extract_pdf_page_images()` renders PDF pages as
  JPEG images (150 DPI, capped at 1568px long edge) for scanned documents that
  contain no extractable text.
- **Pretty-print formatters for built-in file tools**: `file_read`, `file_write`,
  `file_list`, and AST tools now have dedicated formatters in `mcpFormatter.ts` that
  show clean summaries (e.g. `📄 Component.tsx — 500 total lines`) instead of raw JSON.
  `file_write` shows a concise one-liner with action icon, path, and byte count.
- **psutil dependency**: Added to dev dependencies for per-child-process memory
  breakdown in the memstats endpoint.

### Fixed
- **Renderer OOM crash during idle on long conversation histories**: Chrome's
  renderer process was being killed (tab shows "Page crashed" with no JS error)
  after ~60-90 minutes of idle time on accounts with many conversations. Root
  cause was three compounding leaks in the 30s server sync loop, identified via
  heap snapshot retainer-chain analysis:
  1. `db.ts` `getConversationShells` kept message `content` on shell objects as
     a "preview" via `String.slice(0, 200)`. V8's `slice()` returns a SlicedString
     that retains the entire parent string, so "truncated" shells held full
     message content alive (including base64 images and tool-call payloads).
     With 833 conversations × 2 messages per shell, this pinned ~800MB into the
     baseline heap. Fix: drop `content` from shell messages entirely — the
     sidebar only reads `title`/`id`/timestamps, never content.
  2. `ChatContext.tsx` folder sync called `setFolders(prev => ...)` with an
     internal equality-bailout. Functional updaters enqueue an Update node whose
     `action` closure captures the computed payload (`mergedFolders`); when the
     reducer bails by returning `prev`, React doesn't flush the queue, and the
     pending Update nodes accumulate in a linked list retaining every cycle's
     folder array (~100 folder objects × 40KB taskPlan metadata per cycle).
     Fix: reuse the already-computed `foldersChanged` flag and call `setFolders`
     only when true, with a plain value (no closure).
  3. `ChatContext.tsx` conversation sync had the same functional-updater pattern
     with `setConversations(prev => ...)`, leaking ~0.85MB per 30s cycle. Fix:
     hoist the merge computation out of the functional updater (using
     `conversationsRef.current` for reference-preservation), call
     `setConversations` with a plain value only when the result actually differs.
  Combined impact: baseline heap dropped from ~1070MB to ~115MB, and per-cycle
  leak rate went from ~40MB/cycle to 0MB/cycle (flat post-GC floor verified
  across 6+ sync cycles).
- **Tool results serialised as Python repr instead of JSON**: `_process_result()` in
  `tool_execution.py` used `str()` on dicts with string content (e.g. file_read
  results), producing single-quoted Python repr that `JSON.parse` rejects on the
  frontend. Now uses `json.dumps()` so structured fields (content, metadata, path)
  are parseable. Same fix applied to `anthropic_direct.py` and `enhanced_tools.py`.
- **Double code-fencing in file_read display**: `formatFileRead` was wrapping content
  in markdown fences, but ToolBlock already renders inside its own code fence —
  causing literal fence markers to appear as text. Removed inner fences; syntax
  highlighting is handled by the backend's `_infer_syntax_hint()`.
- **Pure-addition diff applied twice**: After a pure-addition hunk is applied, the
  original context lines are no longer consecutive (added lines sit between them).
  `_check_pure_addition_already_applied` now also checks whether the full
  `new_lines` (context + additions interleaved) match a consecutive block in the
  file, correctly detecting the post-apply state and preventing duplication.
- **DrawIO explicit layout labels misplaced**: Diagrams with author-specified vertex
  coordinates had labels displaced because the placement optimizer and orthogonal
  router were rearranging positions. Now detects explicit layout before running
  auto-layout logic and skips both passes. Text-only cells use a new
  `forceTextCellPositioning` method that aligns labels via view-state screen
  coordinates instead of relying on CSS offsets that break under SVG scaling.
- **DrawIO arrow markers oversized after fit()**: Arrow marker pixel size is
  `(endSize + strokeWidth) * viewScale`; fit() amplified them disproportionately.
  Reduced default endSize from 6 to 3, switched to `classicThin`, and added
  post-render `scaleDownArrowMarkers` pass capping markers at 8px.
- **DrawIO XML normalizer corrupting quoted attributes**: The regex-based attribute
  quoter was modifying values inside already-quoted attributes (e.g.
  `value="Group=fsw"` → `value="Group="fsw""`). Now processes each XML tag
  individually with a mask-and-restore approach.
- **Feedback queue unbounded growth**: `feedback_queue` in the WebSocket handler
  was an unbounded `asyncio.Queue`. If the stream consumer exited before the WS
  disconnected, queued items accumulated without limit. Now bounded to 100 with
  drop-oldest-on-full semantics.
- **Streaming thinking tags leaking into chat**: During streaming, unclosed
  `<thinking-data>` or `<thinking>` tags were rendered as raw text. Now detected
  and stripped from the rendered output, with partial content accumulated in the
  ThinkingBlock's ref for live progress display.
- **SSE orphan fragment warning**: `chatApi.ts` now logs a warning when SSE
  messages lack the expected `data:` prefix, aiding debugging of stream corruption.
- **Folder cycle in sidebar**: `anchorFolder` in `MUIChatHistory` could recurse
  into the folder's own subtree, creating an infinite cycle. Now skips self.
- **Missing conversationCount in depth-limited clone**: `cloneNode` at depth > 30
  now includes `conversationCount: 0` to prevent undefined property errors.
- **HTML injection in CLI session picker**: Session opener text is now HTML-escaped
  before rendering in `prompt_toolkit`, preventing titles with HTML entities from
  being misinterpreted.
- **TypeScript validation false positives**: Added `--target ES2022` and
  `--moduleResolution node` to the tsc validation command, eliminating false
  positives on modern syntax like top-level await and satisfies.
- **enhanced_tools content-only check too aggressive**: The content extraction
  shortcut in `DirectMCPTool` was stripping results that had both `content` and
  `path`/`metadata` keys. Now only applies to simple content-only results.

### Changed
- **Directory scanning hardened for large workspaces**: Symlink hop budget
  (`ZIYA_SYMLINK_HOPS`, default 1) prevents blowup on cross-package symlink webs
  while still allowing root-level shared-asset links. `env/`, `build-tools/`, and
  `brazil-output/` excluded by default. Gitignore scan skips descent into
  known-excluded directory names. All timeouts configurable via environment
  variables: `ZIYA_GITIGNORE_TIMEOUT` (60s), `ZIYA_SCAN_TIMEOUT` (120s),
  `ZIYA_FILE_LIST_TIMEOUT` (120s), `ZIYA_ESTIMATE_TIMEOUT` (15s).
- **Folder service uses scandir**: `os.listdir()` + `os.path.isdir()` replaced with
  `os.scandir()` to halve syscall count during external path scans.
- **AST indexer configurable**: Timeout raised to 180s (was 30s), file cap to 50k
  (was 10k). Both overridable via `ZIYA_AST_TIMEOUT` and `ZIYA_AST_FILE_CAP`.
  Brazil workspace artifact directories excluded. `followlinks=False` in os.walk.
- **File list guardrails**: `get_complete_file_list` now has a 200k file cap,
  configurable deadline, and explicit `followlinks=False`.
- **Token cache uses per-entry TTL**: Replaced global timestamp with per-entry TTL
  eviction (5 min) and size cap (16 entries), preventing stale cache from growing
  unbounded across project switches.
- **Paste optimization**: Large text pastes use direct DOM Range API insertion
  instead of `document.execCommand('insertText')`, which fired O(n) input events
  each triggering full DOM serialization + React re-render.
- **Model effort dropdown dynamic**: Reads from `supported_efforts` capability
  array instead of hardcoded options, supporting the new `xhigh` level.

## [0.6.4.7] - 2026-04-16

### Added
- **Per-model thinking effort levels**: Models now declare `supported_efforts` in
  config (e.g. `["low", "medium", "high", "max"]` for opus4.6,
  `["low", "medium", "high", "xhigh", "max"]` for opus4.7). The CLI `/model` dialog,
  capabilities endpoint, and runtime validation all read from this config instead of
  hardcoding a global list. Invalid effort levels fall back to the model's default
  with a warning.
- **`xhigh` thinking effort**: New effort level supported by opus4.7. Available in the
  `/effort` command, model settings dialog, and API.
- **Claude Opus 4.7 model support**: Added `opus4.7` to Bedrock config (inference
  profiles `us.anthropic.claude-opus-4-7` / `global.anthropic.claude-opus-4-7`) and
  `claude-opus-4-7` to the Anthropic direct-API config.
- **New Bedrock models**: Added Nova 2 Lite, GLM 5, Llama 4 Scout, Llama 4 Maverick,
  Mistral Large 3, Devstral 2, MiniMax M2.5, and Qwen3 VL 235B — all confirmed
  active and invocable in the Ziya AWS account.
- **NovaBedrockProvider**: Dedicated provider using the Converse API for Nova models
  (Micro, Lite, Pro). Handles `inferenceConfig.maxTokens`, content block arrays,
  `toolSpec` format, and `converse_stream` instead of `invoke_model`.
- **OpenAIBedrockProvider**: Dedicated provider using `invoke_model_with_response_stream`
  for OpenAI-format models on Bedrock (DeepSeek, Kimi, MiniMax, GLM, Qwen, Llama4,
  Mistral, Devstral). Preserves newlines and whitespace by using the native wire format.
- **Three-way provider routing**: Factory now routes Claude → BedrockProvider,
  OpenAI-format (`wrapper_class: "OpenAIBedrock"`) → OpenAIBedrockProvider,
  Nova/other → NovaBedrockProvider.
- **Native thinking/reasoning stream**: Handle `thinking_delta` events from DeepSeek R1
  and other reasoning models. Thinking content is wrapped in `<thinking-data>` tags
  for the collapsible thinking UI. Unclosed thinking tags are auto-closed on
  `message_stop`.
- **Reasoning tag support for OpenAI-compatible models**: Models like GLM emit thinking
  content in `<reasoning>` tags inline in their text stream. Added tag conversion to
  `<thinking-data>` in the text delta processor so reasoning content renders in the
  collapsible thinking UI.
- **Autoregressive repetition suppression**: Real-time detection of degenerate output
  loops — if the same sentence appears 3+ times, further output is suppressed to
  prevent runaway token consumption.
- **Memory system opt-in flag**: The structured memory system (persistent memory across
  sessions, memory extraction, mind-map, proposals) is now disabled by default and
  enabled with `--memory` on both the server (`ziya --memory`) and CLI
  (`ziya chat --memory`). The `ZIYA_ENABLE_MEMORY=true` environment variable also
  enables it.
- **Memory organize API**: `POST /api/v1/memory/organize` triggers LLM-powered
  reorganization (cluster, place, relate, cross-link) as a background task.
  Poll `GET /api/v1/memory/organize/status` for progress.
- **Memory embedding service**: `POST /api/v1/memory/embeddings/backfill` and
  `GET /api/v1/memory/embeddings/status` endpoints for embedding vector management
  using Bedrock Titan provider with in-memory cache.
- **Memory scope update API**: `PUT /api/v1/memory/{id}` now accepts a `scope`
  field to update `project_paths` and `domain_node`, enabling project-based
  memory organization and backfill of unscoped memories.
- **Memory Browser project filter**: Explorer tab includes a project dropdown
  filter. Memories display their project scope, and the knowledge graph colors
  nodes by project.
- **Memory extraction quality gate tests**: Comprehensive test suites for
  refactoring rejection, code description rejection, career narrative rejection,
  and intra-batch deduplication.

### Fixed
- **"invalid beta flag" error on opus4.7**: The `effort-2025-11-24` and
  `context-1m-2025-08-07` beta headers are no longer needed for opus4.7 (both features
  are GA). Added `effort_beta_required` model config flag; `_apply_thinking()` skips
  the effort beta header when `False`. Removed stale `extended_context_header` from
  opus4.7 config.
- **Spurious "Error capturing logs" on clean exit**: Every `/quit` logged ERROR for
  each MCP server because `disconnect()` terminated the subprocess while the background
  `_capture_logs()` task was still reading from stderr. Now stores the task reference
  and cancels it before process termination.
- **Calibrator rejecting plausible ratios every round**: Fixed to use proportional
  character attribution across total input (system + conversation), keeping the
  ratio stable regardless of conversation length.
- **KaTeX display math inside code fences rendered as encoded div**: Split by code
  fences first; only replace `$$` in non-fence segments.
- **KaTeX inline math inside code fences extracted as placeholders**: Applied
  fence-aware split for inline math extraction.
- **` ```latex `/` ```math ` fences not rendered as KaTeX**: Added preprocessing to
  convert them into KaTeX-encoded divs for proper math rendering.
- **` ```markdown ` fences rendered as literal text**: Added preprocessing to unwrap
  these fences so their content renders normally.
- **Nova models missing image upload button**: Fixed `MODEL_FAMILIES` reference and
  added parent-chain resolution so `supports_vision` propagates to child families.
- **Vision support not updating on model switch**: Both `SendChatContainer` and
  `EditSection` now listen for the `modelChanged` event and re-fetch capabilities.
- **GLM and other OpenAI-compatible Bedrock models returning null**: Fixed to check
  `wrapper_class` from model config instead of pattern-matching model names.
- **Nova models broken in CLI and server**: Added `NovaBedrockProvider` using the
  Converse API and updated the provider factory to route Nova models automatically.
- **OpenAI-format Bedrock models broken after dead code removal**: Added
  `OpenAIBedrockProvider` with three-way factory routing.
- **DeepSeek/Kimi/MiniMax/GLM/Qwen newlines lost in streaming**: `OpenAIBedrockProvider`
  uses the native OpenAI wire format to preserve formatting.
- **Diff content inside thinking tags destroyed by sanitizer**: Added
  `outsideCodeBlocks()` helper that preserves fenced code block content.
- **Raw view toggle on diffs non-functional**: Added `displayMode !== 'raw'` check.
- **Code blocks inside thinking blocks rendered without syntax highlighting** (frontend):
  Added `useEffect` that calls `Prism.highlightElement()` on code blocks after render.
- **Code blocks inside thinking blocks rendered as raw text** (CLI): Added
  `render_prefixed_markdown()` helper with rich rendering and prefix support.
- **CLI assistant responses missing syntax-highlighted code blocks**: Changed
  `display_content.strip()` to simple truthiness check so `"\n"` passes through.
- **Duplicate identical diffs presented to user**: Added exact-content comparison
  before the sequential pair check in diff deduplication.
- **CLI diff extraction missed 5+ backtick fences**: Changed to `` `{3,} ``.
- **Feedback buried before assistant message during tool execution**: Feedback is now
  deferred and injected AFTER the assistant message and tool results are appended.
- **Orphaned tool_use block on feedback skip**: Now yields a stub result for skipped
  tools to satisfy the API contract.
- **Assistant text dedup missed structured content**: Added
  `_assistant_text_in_conversation()` helper that checks both formats.
- **ERR_NETWORK_IO_SUSPENDED false negative after OS sleep/wake**: Added
  `wasHiddenDuringStream` session flag set by `visibilitychange` listener.
- **Duplicate feedback placeholder shown in conversation**: Added `feedbackDelivered`
  event listener that removes pending feedback placeholders.
- **Shell config path resolution broken after refactor**: Fixed to use
  `Path(__file__).resolve()` instead of `import app.mcp_servers`.
- **CLI shell restart using stale paths from mcp_config.json**: Fixed to always start
  from the manager's builtin config and only layer persisted env customizations.
- **File watcher missing .gitignore in nested project directories**: Added
  `_check_inline_gitignore()` that walks up from each file, reading and caching
  `.gitignore` files in the hierarchy.
- **MCP tool descriptions breaking LangChain template formatting**: Added curly brace
  escaping (`{` → `{{`) in `mcp_prompt_extensions.py`.
- **Unbounded cache/dict growth in long-running sessions**: Added eviction policies to
  `ThreadStateManager` (dead-thread pruning >100), `PromptCache` (LRU 200 max),
  `CooldownManager` (stale 5min cleanup), `DelegateManager` (terminal plan 2hr eviction),
  `_prompt_cache` (50 max), `agent_chain_cache` (10 max), `filtered_kwargs_cache`
  (200 max).

### Removed
- **Deprecated Bedrock models**: Removed `sonnet3.5`, `sonnet3.5-v2`, `opus3`
  (end-of-life) and `nova-premier` (broken).
- **Nova Premier family definition**: Removed `nova-premier` family config.
- **Dead LangChain fallback path in `stream_chunks`**: Removed ~1,290 lines of
  unreachable code from `app/server.py`. `server.py` reduced from 2,883 to 1,593 lines.
- **Dead `app/agents/direct_streaming.py` module**: 230-line module with no references.
- **Dead `app/mcp/security.py` module**: 116-line module with no remaining imports.
- **Unused imports in `server.py`**: Removed 15+ unused imports.
- **Dead classes in `server.py`**: `SetModelRequest`, `PatchRequest`,
  `active_websockets` set.
- **Empty `set_terminal_title` function**.
- **`import re as _re` in streaming hot loop**: Replaced with module-level `re`.
- **Duplicate paragraphs in `Docs/ArchitectureOverview.md`**.

### Changed
- **Memory extraction quality gates strengthened**: Added three new structural
  rejection patterns — refactoring notes, code descriptions, and career narratives.
- **Memory extraction prompt**: Added Gates 4-6 rejecting code descriptions, redundant
  paraphrases, and career/self-promotion content.
- **Intra-batch memory deduplication**: Extraction pipeline now deduplicates within the
  same batch before checking against existing store.
- **Logging noise reduction**: Replaced `print()` with `logger` calls throughout Nova
  wrapper, connection pool, and consolidated modules. Downgraded verbose `INFO` to
  `DEBUG` in agent, nova_tool_execution, and connection_pool.
- **Sonnet 4 thinking effort default**: Changed from `medium` to `high`.

## [0.6.4.6] - 2026-04-14

### Added
- **User query logging**: User prompts are now logged at INFO level in the
  server for operational visibility and debugging. Empty prompts are skipped.

### Fixed
- **AST indexing status stale on project switch**: When switching between
  projects, the AST status endpoint (`/api/ast/status`) could report errors
  from the previous project's indexing attempt. The middleware now resets the
  global `_ast_indexing_status` dict when starting background indexing and
  updates it on completion. The status route also checks project-specific
  state via the `X-Project-Root` header rather than relying solely on the
  global dict.
- **AST token count inflated in tool-only mode**: The frontend token counter
  included AST tokens in the total even when `--ast` flag was not set (i.e.,
  AST was available only via MCP tools, not baked into the system prompt).
  Now only counts AST tokens when `ast_in_prompt` is true.
- **AST status not refreshed after background indexing completes**: The
  `TokenCountDisplay` component now listens for `astIndexingComplete` and
  `projectSwitched` events to re-fetch AST status, so the token bar updates
  automatically when indexing finishes or the project changes.
- **Feedback delivery race condition during streaming**: User feedback sent
  while the model was streaming text would sit in `_pending_feedback` until
  `message_stop`, potentially minutes later. The streaming loop now checks
  for pending feedback every 50 events or 2 seconds, enabling prompt
  stop/redirect handling during long responses.
- **Feedback queue dual-reader race**: `tool_execution.py` was reading
  directly from the asyncio feedback queue, racing with the
  `_feedback_monitor` background task and causing ~50% of feedback messages
  to be silently dropped. Now uses the shared `_drain_pending_feedback()`
  function exclusively.
- **Feedback status lifecycle**: The frontend now distinguishes `queued`
  (monitor captured from WebSocket) from `delivered` (feedback actually
  injected into the conversation via SSE event). Previously both states
  showed as `delivered`, giving false confirmation.
- **Post-stream feedback grace period too short**: Increased the
  `asyncio.sleep` after stream completion from 50ms to 300ms to reliably
  capture feedback sent during the final moments of streaming.
- **SQLite unavailability crashes conversation graph**: `GraphManager` now
  handles missing `sqlite3` module gracefully, falling back to in-memory
  mode with no-op persistence. Graphs are rebuilt on demand from
  conversation data.
- **Memory browser button UI**: Replaced emoji 🧠 button with proper Ant
  Design `NodeIndexOutlined` icon and fixed duplicate "New Chat" tooltip
  text on the memory browser button.

### Changed
- **Log noise reduction**: Downgraded ~20 verbose `logger.info` calls to
  `logger.debug` across MCP client/manager, Bedrock provider, token routes,
  diff validation hook, streaming tool executor (file extraction, usage
  metrics, stream metrics), and diff pipeline manager. Server logs are now
  significantly quieter during normal operation.
- **Diff pipeline completion logging**: Replaced multi-line INFO summary
  with compact per-hunk DEBUG lines and a single one-line result summary.
  Failed hunk error details are still logged at DEBUG level.
- **Feedback `feedback_delivered` event**: Both `streaming_tool_executor`
  and `tool_execution` now emit a `feedback_delivered` SSE event when
  directive feedback is injected into the conversation, enabling accurate
  frontend status tracking.

## [0.6.4.5] - 2026-04-14

### Added
- **File state conversation ID regression tests**: Comprehensive test suite
  (`tests/test_file_state_conversation_id.py`) verifying that real conversation
  UUIDs propagate through the precision prompt system and that fabricated
  `precision_` IDs do not cause cross-contamination between conversations.

### Fixed
- **File state change tracking**: Fixed critical bug where the file state
  manager used a shared fabricated conversation ID
  (`precision_/streaming_tools`) instead of the real conversation UUID from
  the frontend. This caused applied diffs to not appear as changes in the
  next model context submission. The real `conversation_id` now flows through
  `build_messages_for_streaming` → `PrecisionPromptSystem.build_messages` →
  `extract_codebase` → `FileStateManager`.
- **Project root resolution**: Fixed `extract_codebase` falling back to an
  empty string for `base_dir` when `ZIYA_USER_CODEBASE_DIR` was unset,
  causing file refresh to fail silently. Now uses `get_project_root()` with
  a proper fallback chain.
- **Memory leaks from unbounded caches and tracking dicts**: Added periodic
  pruning and size caps to prevent long-running sessions from accumulating
  stale entries in `ConnectionPool.last_call_time` (cap 200),
  `SecureMCPTool._last_execution_time` (cap 500),
  `SwarmScratchManager` instances (cap 20),
  AST enhancer instances (cap 3 with LRU eviction),
  `FileChangeHandler` debounce tracking (30s prune cycle),
  and `normalize_line_for_comparison` LRU cache (reduced from 8192 to 1024).
  Replaced `@lru_cache` on `cached_token_count` with a bounded dict to avoid
  pinning full content strings in memory.
- **Delegate manager memory leak**: Completed plans now call `cleanup_plan()`
  to free in-memory state (plans, statuses, crystals, tasks) in addition to
  scratch files, preventing unbounded growth in long-running sessions.
- **AST indexer resilience**: Large or hostile directories (home directory,
  filesystem root, `/tmp`, `/var`) are now detected and skipped early.
  Directory walking uses a single `os.walk` pass with a 10,000-file cap
  and 30-second deadline to prevent runaway scanning.
- **Stream end flush ordering**: Content optimizer now flushes before the
  block-opening buffer in `handle_message_stop`, preserving chronological
  ordering of streamed content at end of turn. Previously, content held by
  the optimizer could appear after block-buffer content.
- **Page route formatter handling**: `_collect_formatter_scripts()` now
  handles dict-format formatter entries (with `src`/`url`/`path` keys) in
  addition to plain strings, preventing `TypeError` when plugins return
  mixed formatter lists.
- **Jinja2 TemplateResponse deprecation**: Updated all `TemplateResponse`
  calls in page routes to pass `request` as the first positional argument,
  fixing Starlette deprecation warnings.
- **Version detection robustness**: `get_current_version()` now uses a
  three-stage fallback: `importlib.metadata` → `ZIYA_VERSION` env var →
  `pkg_resources`, returning `"unknown"` as last resort instead of crashing.
- **Shell server import path**: Fixed `sys.path` in `shell_server.py` to
  traverse the correct number of parent directories, resolving import
  failures in certain installation layouts.
- **Gitignore scanner log spam**: Deadline warning now logs only once per
  scan instead of on every directory visit after the 10-second cutoff.
- **Python AST parser warnings**: Suppressed `DeprecationWarning` from
  `ast.parse()` to keep logs clean.

### Changed
- **Redundant imports removed across 25+ files**: Eliminated duplicate
  in-function imports of `os`, `re`, `json`, `gc`, `asyncio`, `uuid`,
  `boto3`, `subprocess`, `time`, `sys`, `traceback`, and various
  `app.` modules that were already imported at module level.
- **Dead code removed**: Removed unused `hunk_status_updates` global,
  unused `_consecutive_timeouts`/`_last_command_times` dicts from shell
  server, unused `clean_sentinels` import, unused `TOOL_SENTINEL_CLOSE`
  import, and unused `ResourceExhausted` import.
- **Formatting normalized**: Removed excessive blank lines across the
  codebase for consistent style.

## [0.6.4.4] - 2026-04-13

### Added
- **Server route decomposition**: Extracted route handlers from monolithic
  server.py (~5000 lines) into dedicated modules — diff_routes.py,
  folder_routes.py, model_routes.py, token_routes.py, debug_routes.py,
  misc_routes.py, page_routes.py — and a folder_service.py business logic
  layer. Reduces server.py to core application setup and middleware.
- **Streaming executor decomposition**: Extracted message_stop_handler.py,
  text_delta_processor.py, and tool_execution.py from the monolithic
  streaming_tool_executor.py, reducing it by ~600 lines while preserving
  all existing interfaces and behavior.
- **Design philosophy document** (`Docs/DesignPhilosophy.md`): Articulates
  the seven engineering principles behind Ziya's architectural decisions —
  user-controlled context curation, adversarial-input-by-default security
  posture, thin providers / thick orchestrator, partial success over clean
  failure, visual output as first-class, incremental refactoring, and
  transparent self-assessment with honest gap documentation.
- **Refactoring handoff documents**: `Docs/REFACTORING_HANDOFF.md` and
  `Docs/REFACTORING_PLAN.md` provide context for the server decomposition
  work and next steps.
- **Orchestrator integration test suite** (`tests/test_orchestrator_integration.py`):
  11 tests exercising the full `StreamingToolExecutor.stream_with_tools()` loop
  end-to-end with a `MockProvider` and mock tools. Covers text-only response,
  single/multi-tool sequences, conversation state evolution, system content
  passthrough, error surfacing, usage metric accumulation, and event ordering.
- **`file_write` occurrence parameter**: Patch mode now supports an `occurrence`
  parameter for targeted multi-match operations — `None` (default) errors on
  ambiguous matches, `0` replaces all, `N` (1-based) replaces only the Nth match.
- **CLI "Apply All" diff action**: New `[A]` (uppercase) option in the CLI diff
  applicator applies all remaining diffs without further prompting. Only shown
  when more than one diff remains.
- **Frontend EditableTagList component**: Reusable tag-based editor for glob
  patterns and path prefixes with add, remove (✕), inline edit (double-click),
  and comma-separated multi-add. Used in ProjectManagerModal for write policy
  and context management configuration.
- **Zombie record detection test suite**: Frontend tests for detecting and
  recovering large conversations stuck as 2-message shells in IndexedDB.
- **Streaming decomposition test suites**: Tests for text_delta_processor,
  message_stop_handler, tool_execution, stream wiring, and usage tracking.
- **Exception narrowing test suite**: Comprehensive tests verifying specific
  exception types are caught instead of broad `except Exception`.
- **Feedback conversation integrity tests**: Tests verifying assistant response
  text is preserved when user feedback arrives during streaming.
- **Folder service tests**: Unit tests for the extracted folder service layer.
- **Shell destructive safe paths tests**: Tests verifying destructive commands
  are allowed on safe paths but blocked on project files.

### Fixed
- **Narrowed exception handlers across 36 files**: Replaced broad
  `except Exception` clauses with specific exception types in agents, CLI,
  MCP client/manager, providers, storage, middleware, config, utils, and
  extensions. Prevents accidentally swallowing `KeyboardInterrupt`,
  `SystemExit`, or `MemoryError` and improves debuggability.
- **Missing `import os` in delegate manager**: `_post_progress_to_source`
  crashed with `NameError` when delegates produced artifact files in
  `.ziya/tasks/`, preventing crystal summaries from being posted.
- **`remove_skill_from_all_chats` NameError**: Used undefined `chat_id`
  instead of `chat.id`, and wrote every chat file unconditionally.
- **Model config exports missing from `app.config`**: `MODEL_FAMILIES`,
  `get_supported_parameters`, and `validate_model_parameters` were not
  re-exported from `app/config/__init__.py`, breaking model config tests.
- **Post-refactor test suite fixes** (~80 test failures across 5 categories):
  delegate_manager fixtures, model_routes mock targets, MCP integration
  attribute access, apply_state assertions, and fileio patch-mode tests
  updated for ambiguity-error default and occurrence parameter.
- **Destructive shell commands blocked on declared-safe write areas**: Commands
  like `rm`, `mkdir`, `mv`, `cp` were unconditionally rejected before the
  write policy checker could evaluate target paths. Now pass the allowlist
  gate so per-path policy decisions work correctly.
- **Shell server `2>&1` redirections passed as literal arguments**: Redirection
  operators tokenized by `shlex.split` were passed as literal args to
  subprocesses. Now extracted and translated to `subprocess.run` kwargs.
- **Tool result blocks not horizontally scrollable**: Expanded content that
  exceeded viewport width was clipped. Fixed to `overflow: 'auto'`.
- **Large conversations stuck as shells (zombie record recovery)**:
  Conversations where a shell was written with `_isShell: false` appeared
  permanently stuck. Three fixes: zombie record detection, IDB-first lazy
  load, and server fetch validation requiring > 2 messages.

### Changed
- **Removed dead `app/tools/` package**: Unused duplicate of
  `app/mcp/tools/fileio.py` with zero imports anywhere in the codebase.
- **Test suite cleanup**: Fixed 8+ broken tests across 6 files after major
  refactor — memory extractor, conversation exporter, CLI cancellation,
  raw markdown toggle, JS/TS validation, and streaming middleware tests
  updated for new module structure.
- **README updated** with current feature descriptions and project status.
- **Frontend improvements**: MUIChatHistory fork conversation simplified,
  MarkdownRenderer edge case handling, ChatContext state hardening,
  htmlSanitize improvements.
- **Dependencies updated** in pyproject.toml and poetry.lock.

## [0.6.4.3] - 2026-04-10

### Fixed
- **AST indexing wrong directory on startup**: In browser mode, the AST scanner
  was indexing the server's launch directory instead of the user's active project.
  Startup indexing is now deferred until the first request with `X-Project-Root`
  arrives. CLI mode (where `ZIYA_USER_CODEBASE_DIR` is set) is unaffected.

- **Visual diagram feedback tool**: `render_diagram` builtin tool renders diagram
  specs (Mermaid, Graphviz, Vega-Lite, DrawIO, packet, etc.) server-side via the
  headless Playwright pipeline and returns the resulting PNG as a vision content
  block. The model can see the rendered output and iteratively refine its diagrams.
- **Server-rendered conversation export API**: `POST /api/export/rendered` exports
  conversations with all diagram code blocks rendered to inline SVG/PNG images
  server-side, enabling CLI exports, API consumers, and plugin targets that lack
  a browser.
- **Plugin export targets**: `POST /api/export/to-target` dispatches rendered
  exports to plugin-registered services (Slack, Quip, wiki, etc.) via the new
  `ExportProvider` plugin interface (`app/plugins/interfaces.py`).
- **Force-directed graph plugin**: New D3 visualization plugin for force-directed
  network layouts using the ```d3``` code fence with `type: "force-directed"`.
  Supports weighted edges, node grouping, collision avoidance, and configurable
  styling.
- **D3 spec parser**: Utility (`d3SpecParser.ts`) parses JS-expression-style
  D3 specifications with unquoted keys into objects for plugin dispatch.
- **Bubble and scatter chart support**: `basicChart` plugin now handles
  `type: "bubble"` and `type: "scatter"` specs with continuous x/y scales
  and size-mapped radii.
- **Gantt dateFormat X support**: Mermaid Gantt charts using `dateFormat X`
  (numeric timestamps) are automatically converted to `YYYY-MM-DD` date format
  with scaled day offsets for correct rendering.
- **ThemeContext.setTheme()**: Programmatic theme control method added alongside
  the existing toggle.
- Comprehensive test suites for render_diagram tool, conversation exporter, rendered
  export endpoint, streaming tool executor image handling, force-directed plugin,
  D3 spec parser, Vega-Lite preprocessing, Mermaid requirement diagrams, basic
  chart plugin, packet diagrams, save guard metadata merge, thinking parser, chat
  history tree cycles, and code fence splicing.

### Fixed
- **Duplicate response after stream error**: When a ValueError occurred mid-stream
  (after chunks were already sent), the LangChain fallback path replayed the entire
  conversation, doubling the response in the frontend. Now terminates cleanly if
  any content has already been streamed.
- **Stale flush timer causing ghost responses**: A pending `setTimeout` flush
  could fire after stream cleanup deleted the content from `streamedContentMap`,
  re-inserting it and causing the response to appear twice. Added
  `_streamFinalized` guard and explicit timer cancellation before cleanup.
- **Image tool results stripped by signing metadata**: `strip_signature_metadata()`
  removed all `_`-prefixed keys, including `_has_image_content`. Now uses an
  explicit set of signing-specific keys instead of a blanket prefix filter.
- **Builtin tool Playwright deadlock**: Builtin tools were run via
  `asyncio.to_thread(_run())` which created a new event loop, deadlocking tools
  that use Playwright or other async resources bound to the main loop. Now calls
  `execute()` directly on the event loop.
- **Image content blocks truncated by sanitizer**: Structured image result lists
  (base64 content blocks) were being stringified and truncated by the tool result
  sanitizer. Now skipped for non-string results.
- **Vega-Lite area charts with fold transforms**: Area marks on categorical
  (nominal) x-axes with fold transforms failed to render because: (a) area
  interpolation requires ordered axes (nominal to ordinal conversion), and
  (b) explicit y-domain combined with fold on enough categories broke the
  rendering pipeline (domain removed, stack set to null).
- **Vega-Lite layered charts with mismatched y-axis ranges**: When layers use
  different y-fields whose data ranges differ by more than 3x, the shared axis
  clipped one layer entirely. Now auto-adds `resolve.scale.y: 'independent'`
  with left/right axis orientation.
- **Vega-Lite bar charts on log scale**: Bars imply a zero baseline but
  log(0) is negative infinity, producing invisible or broken bars. These are now
  converted to tick + text layers showing position on the log axis with
  human-readable labels (12K, 4.5M, 13.8M).
- **Mermaid requirement diagram properties**: `verifymethod` was incorrectly
  capitalized to `verifyMethod` (Mermaid's lexer requires lowercase); `id`
  property was incorrectly quoted (Mermaid expects bare tokens).
- **Network diagram stub rendering**: The network diagram plugin had placeholder
  comments instead of actual link/node rendering. Replaced with full
  implementation including node circles, labels, and edge lines.
- **Code fence premature closure in diff blocks**: Diff output containing
  indented backtick lines matched the closing fence pattern under CommonMark
  rules, splitting a single code block into fragments. The preprocessor now
  detects these collisions and upgrades the outer fence length.
- **Code fence concatenated to text without newline**: LLM output sometimes
  omits the newline before a code fence. Added a regex fix to insert the
  required blank line.
- **HTML entity `&#96;` not decoded**: Backtick HTML entities were rendered as
  literal text instead of being decoded to backtick characters.
- **Thinking block fence breakout**: Sequential thinking blocks used a fixed
  4-backtick fence that could be broken by content containing 4+ backticks.
  Fence length is now dynamically sized to exceed the longest backtick sequence
  in the content. Removal regex updated to handle variable-length fences.
- **Save guard blocking metadata updates**: When the save guard blocked a shell
  conversation write to protect message data, metadata changes (folderId,
  version, lastAccessedAt, groupId, isGlobal) were also lost. Now performs a
  separate metadata-only IDB merge transaction for blocked writes.
- **Project switch blanking active conversation**: On initial page load (not a
  switch), the project initialization code cleared all conversations, racing
  with lazy-hydration and destroying full message data. Now only clears on
  actual project switches.
- **Active conversation not re-hydrated after sync**: After server sync replaced
  the conversations array, the active conversation could remain as a 2-message
  shell. Added post-sync re-hydration from IndexedDB.
- **Folder sort ignoring nested activity**: Parent folders only reflected
  lastActivityTime from direct children. Added bottom-up rollup so nested
  subfolder activity propagates to root for correct sort order.
- **Conversation move/toggle not updating lastAccessedAt**: Moving a conversation
  between projects or toggling global scope now updates lastAccessedAt so it
  sorts correctly in the target location.
- **FolderTree spinner label on initial load**: Showed "Switching project..."
  even on first page load. Now shows "Loading..." when no project was loaded.
- **Message list key collision**: Used loop `index` instead of `actualIndex`
  for React keys, causing incorrect reconciliation when messages were filtered.
- **AST indexing wrong directory on server start**: The startup AST scan indexed
  the server's launch directory instead of the user's active project. Deferred
  indexing in browser mode until the first request provides the actual project root.
- **DiagramRenderPage D3Renderer type**: Passed `type="auto"` instead of
  `type="d3"`, causing plugin lookup failures for explicit D3 specs.

### Changed
- `SendChatContainer` input maximum height increased from 150px to 50vh,
  allowing larger code pastes without excessive scrolling.
- D3Renderer now parses raw string specs through `d3SpecParser` before plugin
  lookup, so string inputs that were previously rejected now route correctly.
- MarkdownRenderer pre-parses ```d3``` code fence content into objects
  before passing to D3Renderer, matching the parsing done for other viz types.
- Tool result image content blocks are compacted to text summaries in
  conversation history to prevent context window bloat from base64 data.
- Frontend assets rebuilt.

## [0.6.4.2] - 2026-04-11

*Release notes: see [0.6.4.3] below — content was tagged under the wrong version.*

## [0.6.4.1] - 2026-04-09

### Added
- **Headless diagram rendering API**: `POST /api/render-diagram` renders Mermaid,
  Graphviz, Vega-Lite, DrawIO, and packet diagrams to PNG or SVG server-side using
  a headless Chromium instance driven by Playwright. Produces pixel-perfect output
  through the same D3Renderer pipeline, plugins, and post-render enhancers as the
  chat UI.
- New frontend route `/render` (DiagramRenderPage) serves as the Playwright render
  harness, accepting specs via URL hash, `postMessage`, or `window.__renderDiagram`.
- Playwright added as an optional dependency (`pip install ziya[render]`).
- Sidebar panels (Files, Contexts, History) show a loading spinner during project
  switches instead of stale data from the previous project.
- Release task now includes Slack notification step and changelog cross-referencing.
- Test suites: headless diagram renderer (unit + integration), mermaid viewBox
  trimming and container-width scaling, double-tilde strikethrough tokenizer,
  orphan bare fence stripping.

### Fixed
- **Mermaid diagrams rendering too small**: ViewBox trimming reclaims wasted space
  when Mermaid's layout engine allocates a viewBox >10% wider than actual content.
  Width clamping now uses the real container width instead of a hardcoded 900px max,
  so diagrams fill available space without overflowing.
- **Single-tilde false-positive strikethrough**: Conversational tildes like `~32px`
  or `~10px` were rendered as strikethrough. The marked.js GFM `del` tokenizer is
  now overridden to require double tildes (`~~text~~`) only.
- **Orphan bare fences swallowing code blocks**: When the LLM emits a stray bare
  ``` before a real code fence (e.g. ```bash), the orphan is now detected and
  stripped so the actual code block renders correctly.
- **Stale sidebar during project switch**: File tree, contexts tab, and chat
  history panels displayed data from the previous project during switches. All
  three panels now blank immediately and show a spinner until the new project loads.
- **Loading overlay on global conversations**: Global-scoped conversations that
  survive project switches no longer show a loading overlay during the transition.
- **IDB lazy-load accepting corrupted shells**: IndexedDB lazy-loading now rejects
  shell records and corrupted 2-message stubs, falling through to server fetch
  to retrieve complete conversation data.
- **Chat history indentation**: Non-folder conversation items nested under folders
  now have additional left padding (10px) for visual distinction from folder rows.

### Changed
- Mermaid flowchart default padding increased from 15 to 20 and nodeSpacing from
  50 to 60 for improved readability.
- Project switch detection combines `isLoadingProject` and `isProjectSwitching`
  signals for earlier UI response.
- Frontend JSX indentation in index.tsx normalized to consistent 4-space nesting.

## [0.6.2.8] - 2026-04-09

### Added
- Structured memory system with opportunistic decay (archive after 90 days of low
  importance), auto-promotion of proposals on search hit, and MemoryBrowser UI
  (Ctrl+Shift+M shortcut).
- Memory comparator for deduplication and conflict detection across memory store.
- Memory extractor for post-conversation knowledge distillation.
- Memory activation directive injected into system prompt for session continuity.
- Session context injection (project root, CWD, timestamps) into system prompt.
- Service model resolver for lightweight background tasks with per-category
  overrides (memory extraction, classification, summarization).
- CLI auto-continue: detect truncated responses and resume without user intervention.
- `build:profile` npm script with source maps and React profiling aliases.
- MCP config error tracking and user-facing error display in MCPStatusModal.
- Diff test case for context mismatch insertion.
- Frontend and backend tests for config error reporting, model resolver, memory
  comparator, memory extractor, and MCP tool permission refresh.

### Changed
- `ZIYA_RETENTION_OVERRIDE_DAYS=0` now disables all plugin-provided TTLs.
- Slash command parsing allows `//` as non-command input.
- Rich syntax highlighting in terminal markdown renderer.
- Architecture overview documentation updated.
- Frontend build artifacts regenerated.

### Fixed
- Diff application safety: verify file line matches expected removal before applying
  surgical change; bail out to original lines on context offset to prevent corruption.
- Null file_path guard in diff_validation_hook.
- Bulk-sync bypasses retention check to prevent delete-recreate loop.
- IndexedDB write optimization: fast path bypasses saveQueue for single-chat saves;
  write only changed conversations instead of full clone.
- `currentMessages` converted to ref to avoid re-renders on every streaming chunk.
- FolderContext ref-mirror breaks infinite cleanup loop caused by checkedKeys dependency.
- DelegateLaunchButton avoids read-all-then-write-all pattern for new chats.
- Progressive message rendering disabled (show all messages immediately).

### Performance
- Frontend rendering and IndexedDB write optimization across ChatContext, db.ts,
  Conversation, MUIChatHistory, MarkdownRenderer, and StreamedContent components.

## [0.6.2.7] - 2025-05-05 (in progress)

### Fixed
- **Performance: Ant Design useAlign storm** — DiffControls Tooltips replaced with native `title=` attributes; ChatTreeItem Dropdown lazy-mounted on hover; MessageActions Tooltips gated behind HoverMessageActions — eliminates hundreds of rc-trigger positioning calculations on every render
- **Performance: Large human message O(N²) parsing** — MarkdownRenderer accepts `role` prop; human messages >100KB render as plain text
- **Performance: 33 commits/sec render loop** — `currentMessages` was derived via `useEffect` + `setState` which created an infinite cascade (`conversations` changed → effect ran → `setCurrentMessages` → React committed → effect re-ran). Replaced with `useMemo` + ref-based comparison — zero setState calls, zero cascading renders
- **Performance: ServerStatusContext spurious commits** — health check called `setIsServerReachable(true)` unconditionally on every successful poll; changed to functional updater that bails out when already `true`
- **Performance: syncWithServer writing all conversations** — 30-second sync was writing all 674 conversations to IDB even when only 3 changed; now filters to only changed conversations via version-map comparison
- **Performance: Shell write spam** — `syncWithServer` IDB write path now filters out shell conversations before calling `db.saveConversations`, eliminating 299 per-cycle SAVE_GUARD warnings
- **Performance: Singleton event listeners** — MathRenderer LaTeX copy handlers and MarkdownRenderer throttle-button observers consolidated from per-instance `document.addEventListener` calls to module-level singletons with lightweight registries
- **Performance: syncWithServer OOM** — replaced `db.getConversations()` with `db.getConversationShells()` in sync path
- **Performance: Concurrent sync storm** — `syncInProgressRef` useRef guard persists across effect re-runs; fixes 1047 concurrent sync cycles
- **Search: sidebar scroll** to active conversation after selecting search result
- **Search: message scroll** targets correct index using `data-message-index` with window offset correction  
- **Search: content highlighting** via DOM text-node walk on target message
- **Search: snippet highlighting** in search results panel


## [0.6.2.6] - 2025-05-04

### Fixed
- **Retention policy**: `ZIYA_RETENTION_OVERRIDE_DAYS=0` now correctly disables retention
  enforcement instead of being silently ignored, preventing Amazon enterprise 90-day policy
  from deleting conversations on every sync cycle.
- **Retention delete loop**: `bulk_sync_chats` no longer calls `storage.get()` (which
  triggers expiry checks mid-sync), breaking a delete→recreate loop for recently-expired chats.
- **Save path TDZ crash**: `performWrite` was a `const` referenced before initialization;
  fixed with `function performWrite()` declaration + `const self = this` capture.
- **Save OOM / IDB lockup**: `queueSave` fast path now completely bypasses
  `saveQueue.current.then()` for per-message saves with `changedIds`, eliminating
  Promise chain accumulation (was causing 20-second CPU lockups with 940-deep chains).
- **Other-project shell writes**: Sync cycle no longer writes all 725 conversations
  (706 other-project shells + 19 current) to IDB on every 30-second cycle — only
  current-project conversations are written.
- **Server sync loop**: Conversations with `serverHasDelegateMeta || serverHasFolder`
  now respect `recentlyFetchedFullIds` cache; failed full-fetches store `serverVer`
  instead of `_version: 0`, preventing 227-conversation refetch storm every cycle.
- **Concurrent sync cycles**: `syncWithServer._running` guard prevents multiple overlapping
  sync cycles from stacking during rapid project switches or network delays.
- **Stuck conversation spinner**: Overlay now always clears after 500ms on conversation
  switch, even when both old and new conversations have 0 messages (identical `currentMessages`
  reference prevented the hide-overlay effect from firing).
- **Empty conversation cleanup**: Removed `conv.messages?.length > 0` guard from local
  deletion logic so server-deleted conversations are pruned from React state and IDB.
  `knownServerConversationIds` provides equivalent protection against premature deletion.
- **CLI code block indentation**: `rich.Markdown` adds panel padding to code blocks;
  now rendered via `rich.Syntax` directly, preserving syntax highlighting without indent.
- **`data-message-index` accuracy**: Fixed window offset calculation so attribute stores
  raw message array index (`windowOffset + displayIndex`) rather than display index,
  enabling accurate `querySelector('[data-message-index]')` targeting for search navigation.
- **Tooltip/Dropdown `useAlign` storm**: `ChatTreeItem` Dropdown and `MessageActions`
  Tooltips now only mount on hover, eliminating 1000+ `useAlign` Promise chain calls
  per render cycle that were causing CPU lockups.
- **Search result highlights**: Matched search terms now highlighted in result snippets.
- **Sidebar scroll after search**: Sidebar scrolls to active conversation after search
  result selection clears the search panel.

### Changed
- `db.saveConversations` has a small-batch fast path (≤10 conversations, no shells)
  that bypasses `_saveConversationsWithLock` deduplication for direct IDB puts.
- `MUIChatHistory` conversation delete now uses `db.deleteConversation` (single-record
  delete) instead of saving the full filtered array.
- `MUIChatHistory` fork conversation now uses `db.saveConversation` (single-record write).
- Startup GC uses `queueSave` with `changedIds` instead of direct `db.saveConversations`.

## [0.6.2.5] - 2026-04-05

### Added
- Persistent memory system with mind-map tree and auto-maintenance — retains domain
  facts, architecture decisions, vocabulary, and lessons learned across sessions.
- MCP tools: `memory_search`, `memory_save`, `memory_propose`, `memory_context`,
  `memory_expand` for structured knowledge management.
- Memory prompt injection into system message for session continuity.
- REST API endpoints for memory CRUD operations.
- CLI `/reset` command: clears history, files, and all session state.
- Frontend `RootErrorBoundary` component for top-level crash recovery.
- Frontend copy-conversation-to-project (in addition to move).
- Chat history tree cycle detection with comprehensive tests.
- Frontend save debounce tests.
- `POST /chats/repair-timestamps` endpoint to fix historical timestamp inflation.
- Diff test runner and variable shadow false-negative test case.
- Competitive analysis document (vs Claude Code).

### Changed
- Frontend save debouncing: coalesce rapid-fire saves during streaming into single
  IndexedDB writes with dual-write timer for dirty conversation batching.
- MarkdownRenderer and TokenCountDisplay refactored for performance.
- Folder management improvements with better drag-drop handling.
- Prism language loader: retry and error handling on dynamic imports.
- IndexedDB connection management improvements.
- Delegate polling refinements for tab-hidden scenarios.
- CLI `/clear` now only clears message history (removed `/c` alias).
- Architecture, Capabilities, Enterprise, and Feature Inventory docs updated for
  memory system, state eviction, and WebSocket hardening.

### Fixed
- Touch-on-read timestamp inflation: `GET /chats/{id}` no longer mutates
  `lastActiveAt`, preventing sync loop from inflating timestamps.
- WebSocket disconnect races on page reload in feedback, file_tree, and
  delegate_stream endpoints.
- Network vs credential errors: AWS validation now detects connectivity issues
  and shows NETWORK ERROR instead of misleading credentials error.
- Memory leaks: bounded state eviction for tool states, context manager, file
  state manager, usage tracker, and stream metrics.
- macOS /tmp symlink: resolve safe_write_paths through realpath so writes to
  /tmp/* are correctly allowed.
- MCP shutdown: graceful cleanup, downgraded log levels, ProcessLookupError handling.
- Removed over-aggressive fake tool call detection heuristic.
- /bulk-sync added to quiet polling filter.

## [0.6.2.4] - 2026-04-10

### Added
- Terminal window/tab title set to `Ziya:<port>` on server startup via ANSI OSC escape sequence.
- D3Renderer displays an error panel on the d3/plugin render path (mirrors the vega-embed branch).
- Vega-Lite preprocessing fix 0.05: swap `datum`/`field` in primary/secondary encoding channels,
  fixing lollipop charts that crash with "Cannot destructure property 'aggregate' of 'i'".
- Unit tests for paragraph token filter, delegate streaming, and Vega-Lite preprocessing.

### Changed
- Chat history tree rebuild split into structural vs. sort hashes; activity-time-only changes
  now use a sort-only fast path that avoids full tree reconstruction.
- `sortComparator` and `reanchorTaskPlanFolders` extracted as module-level helpers shared between
  the full-rebuild and sort-only paths.
- `useDelegateStreaming` key memos use `for`-loops instead of `.find()`/`.filter()` to reduce
  closure allocations over the full conversations array.
- `MUIFileExplorer` token cache clear simplified: removes JSON.stringify key comparison and
  shallow-copy `setTreeData` that caused unnecessary full-tree re-renders.
- Vega-Lite color scheme hex fix now applies to `layer`/`concat` sub-specs and `fill`/`stroke`
  channels, not just top-level `color`.
- Vega-Lite SVG scaling skips attribute stripping for charts with explicit `width`/`height`
  to prevent height collapsing to 0px.
- Vega-Lite error suppression treats fully-formed spec objects as real errors (no longer
  suppresses errors when `$schema` + `data`/`mark` are present).
- Chat history tree guarded against returning transitional data during project switch.

### Fixed
- Tab-hidden background stability: conversation GC skips when `document.hidden`.
- `setConversations` wrapped in `React.startTransition` during project switch to avoid
  blocking paint frames with large conversation list updates.
- Stale conversation data cleared immediately when switching to a new project.
- Verbose debug log removed from `markConversationAsRead` state updater (eliminated per-render
  object allocation proportional to total conversation count).
- Vega-Lite `ResizeObserver` now created only once per render container (singleton guard)
  with `requestAnimationFrame` throttling to break DOM-mutation→observation feedback loops.
- `ResizeObserver` instances stored on container elements are disconnected in D3Renderer
  cleanup effect to prevent memory leaks.
- Paragraph token filter preserves whitespace-only separator tokens (e.g. `" "` between
  `em`/`strong`/`codespan`) while still discarding truly empty string tokens.

## [0.6.2.3] - 2026-04-01

### Added
- Shell server executes all commands with `shell=False` — Python-side pipeline
  orchestrator handles pipes, `&&`/`||`/`;` chaining, env var expansion, tilde
  expansion, glob patterns, and command substitution, eliminating shell injection
  and environment manipulation risks.
- Document file extraction (PDF, DOCX, XLSX, PPTX) in `file_read` tool — routes
  through text extractor with offset/max_lines support instead of reading raw bytes.
- External paths persisted to project storage and restored on server restart,
  surviving across `ziya` restarts without re-adding.
- Plain-text paste in chat input — strips rich HTML from web pages that bloats
  token counts and loses whitespace from `<pre>` blocks.
- `white-space: normal` on block elements in message content for proper
  paragraph-break newlines when copying from chat.
- New test suites: shell `shell=False` execution, TypeScript validation false
  positives, document extraction, document token counting, external path
  persistence, external path cache, conversation token counting, copy/paste
  whitespace, plain-text paste.

### Changed
- Duplicate code detection in diff pipeline is now advisory (does not block
  diff application) — reduces false positives from keyword matching in large
  TSX files.
- JavaScript handler filters reserved keywords (`if`, `for`, `while`, etc.)
  from function detection; semicolon heuristic warnings are non-fatal.
- TypeScript handler trusts tsc syntax analysis when only non-syntax diagnostics
  (TS2xxx+) are reported — no fallback to heuristic validation. Heuristic checks
  in fallback mode are advisory only.
- Token estimation now counts `tool_result` content and `tool_use` input JSON,
  preventing underestimation when tool calls are present.
- Skip file-type multiplier for document files in background token calculation
  (extracted text is already real token count).
- `add_external_path_to_cache` uses `get_project_root()` for consistent cache
  key resolution.
- Accurate token count endpoint uses `resolve_external_path()` for correct
  file resolution.

### Fixed
- Frontend resource leaks: consolidated progress poll timers, cancel debounced
  calls on unmount, close MessageChannel ports in finally block.
- Background tab optimizations: skip health checks, delegate polling, and
  WebSocket message processing when `document.hidden`.
- Token calculation cache cleared when folder data changes (stale totals).
- `X-Project-Root` header sent in add-explicit-paths requests for correct
  cache targeting.
- Removed `treeData` from effect dependency array to break clear→set loop.
- External path `file_added` WebSocket events trigger full refetch instead of
  broken incremental insert.
- Menu label text consistency ("Move to folder", "Move to project").
- Test mock paths in `test_cli_diff_applicator` corrected to patch at source.

### Removed
- `frontend/.babelrc` — unused Babel configuration.

## [0.6.2.2] - 2025-07-22

### Added
- SSE keepalive wrapper emitting `: keepalive` comment pings every 15s during
  idle stream periods to prevent proxy/browser connection drops.
- Screen Wake Lock acquired during streaming to prevent OS sleep from
  suspending the network stack — the primary cause of "Stream interrupted"
  errors during screensaver or lid-close events.
- Web Lock (`navigator.locks`) acquired during streaming to prevent browser
  tab freezing when backgrounded.
- Tab visibility detection on stream errors with targeted recovery messages.
- Tool result sanitization pipeline (`app/utils/tool_result_sanitizer.py`):
  plugin filters → base64 document extraction → size cap, reducing context
  bloat from metadata-heavy tool responses.
- `ToolResultFilterProvider` plugin interface for site-specific tool result
  filters (e.g. stripping Quip sectionId HTML comments).
- Fake tool call detection in code blocks via parameter key matching heuristic.
- Project fast startup: localStorage fast-path, `/projects/last-accessed`
  endpoint, `_path_index.json` for O(1) path lookups, parallel list loading.
- `ContextManagementSettings` model with `auto_add_diff_files` toggle.
- Project settings UI in ProjectManagerModal (context management, write policy).
- Conversation data integrity: 10-layer message count regression guard across
  server bulk-sync, ChatContext merge/sync/lazy-load, IDB read-before-write,
  cross-tab BroadcastChannel, and shell append recovery.
- `ZIYA_RETENTION_OVERRIDE_DAYS` env var to raise plugin-enforced TTLs to a
  local minimum (e.g. 30 days).
- `ZIYA_MAX_TOOL_ITERATIONS` env var for agentic loop iteration cap.
- MUIChatHistory error boundary, FNV hash null guards, circular folder
  reference protection (self-ref guard, visited-set, depth limits).
- AST parser expanded to 25+ languages: C#, Kotlin, Swift, Ruby, PHP, Scala,
  Lua, Perl, R, Elixir, Haskell, Dart, Zig, OCaml, Julia, Bash,
  HCL/Terraform, SQL, TOML, YAML.
- CLI auto-retry for failed diffs: re-reads files and re-prompts model with
  current content and failure details.
- File deletion diff support (`+++ /dev/null`) in CLIDiffApplicator.
- Extensive new test suites for stream keepalive, tool sanitization, project
  context management, retention override, message count guards, chat history
  tree cycles, visualization plugins, and diff applicator edge cases.

### Changed
- Retention TTL decisions now use `lastActiveAt` instead of `createdAt`,
  preventing active conversations from being purged prematurely.
- TypeScript diff handler: prefers project-local `tsc`, uses `--isolatedModules
  --noResolve`, only treats TS1xxx diagnostics as hard syntax errors, supports
  `.tsx` files with `--jsx react-jsx`.
- Python duplicate detector: only flags functions when count exceeds original
  (fixes false positives on `_` handlers, `__init__`, etc.).
- JavaScript semicolon checker: reduced false positives for TS/JSX patterns
  (type unions, declaration keywords, arrow functions, bare identifiers).
- Diff validation hook always injects fresh file content on failure regardless
  of prior context — model gets live state, not stale copy.
- Generic text handler no longer auto-registers (registered explicitly).
- Pipeline validator falls back to cwd when resolving file paths.
- Delegate model unwrapping checks for `ainvoke` before second unwrap.
- Shell write checker treats only last arg of cp/mv as write target.
- Write policy manager guards against non-dict settings before update.
- CLI saves/restores terminal title using xterm title stack (push/pop).
- Mermaid plugin strips markdown bold/italic from labels.
- Vega-Lite plugin supports gradient color scales and log axis.
- Feedback drain improved with `asyncio.sleep(0)` yields at loop boundaries
  and second-chance drain before break decisions.
- Feedback monitor cancelled before direct queue reads to prevent item loss.
- Test suite refactored: reduced verbosity, fixed isolation issues, aligned
  with new guard and validation behaviors.

### Fixed
- `apply_diff_atomically` null return now handled gracefully in CLI applicator.
- Diff error extraction checks `message` key before `error` fallback.
- Duplicate detector skips lines already repeated in original file.
- AST symbol formatting filters null base class entries.
- Import node type coverage expanded in treesitter_converter for cross-language
  compatibility.


## [0.6.2.1] - 2025-07-17

### Added
- CLI: `/tune` command for runtime session settings (e.g. max tool iterations).
- CLI: Graceful SIGINT handler on asyncio event loop for clean streaming cancellation.
- CLI: Thinking effort configuration for adaptive-thinking models.
- Delegate manager: Artifact report files embedded inline as collapsible `<details>`
  blocks in progress updates (replaces "N report(s) written" summary).
- Delegate manager: Progress update posted for every crystal including the final one.
- Frontend: `ActiveChatContext`, `ConversationListContext`, `ScrollContext` — focused
  context providers extracted from monolithic `ChatProvider` to eliminate 60Hz
  re-renders of unrelated components during streaming.
- Frontend: `useSendPayload` hook centralises `sendPayload` call-site boilerplate.
- Frontend: Stable content-based React keys for markdown tokens (`stableTokenKey`).
- Frontend: Headerless continuation diff blocks merged into preceding headed diff.
- Frontend: Bare code-fence stripping for prose-wrapping fences emitted by models.
- Frontend: Base64-encoded display math to protect LaTeX from markdown escaping.
- Frontend: Mermaid skip-edge rerouter arcs feedback loops above/below intermediate nodes.
- Frontend: Connection pool health logging and reader release in `chatApi.ts`.
- Frontend: Image resize (max 1568px) before Bedrock upload.
- Frontend: `MutationObserver` disconnect on tab hide to reduce idle overhead.
- Project API: `conversationCount` field on project list items.
- Diff pipeline: `diff_preprocessor.py` for additive-insert-instead-of-replace sanitisation.
- Diff pipeline: Full-file replacement fallback when single hunk covers >90% of file.
- Token calibrator: Physically reasonable bounds (1.0–15.0 chars/token) reject implausible
  samples; baseline re-established when MCP tool count changes.
- Tree-sitter: Migrate to `tree-sitter-language-pack` with legacy fallback.
- Extensive new test suites: diff pipeline edge cases, frontend context split, rendering,
  display math encoding, edge rerouter, legend dedup, shell conversation guards.

### Changed
- MUI upgraded from v5 to v7; `@maxgraph/core` upgraded from 0.11 to 0.22.
- DrawIO plugin: Use `StyleDefaultsConfig` (0.22+) for arrow size overrides;
  register core codecs.
- Mermaid plugin: Popup window inherits parent dark/light theme on open;
  theme toggle applies `!important` styles to override embedded Mermaid CSS.
- Vega-Lite plugin: Fix duplicate legend domain entries from LLM-generated specs.
- Streaming tool executor: Configurable max iterations via `ZIYA_MAX_TOOL_ITERATIONS`
  env var (default 200); baseline invoke moved to `run_in_executor`.
- Bedrock provider: Serialize body once; log payload size, image count, and timing;
  close boto3 stream on `CancelledError`.
- Diff validation wrapped in `asyncio.wait_for` with 30s timeout (CLI, server, chat API).
- Documentation: Architecture overview updated with context-split design, shell loading
  guards, and value-object hygiene rules; Capabilities adds diagram rendering section;
  NewUser broadens Python requirement to 3.10–3.14.

### Removed
- `langserve` dependency and all LangServe routing code (`initialize_langserve`,
  `/ziya` route management, LangChain fallback path in `chat_endpoint`).
- `frontend/eslint.config.mjs`, `frontend/webpack.config.js`, Playwright
  `math-copy.spec.ts` — stale/unused configs and tests.
- `typescript-eslint` and `globals` dev dependencies; `resolutions` block.
- Deleted `test_langserve_integration.py` and `test_langserve_error.py`.

### Fixed
- `clean_input_diff`: `new_count` was reading regex group(1) twice instead of group(2).
- `clean_input_diff`: No longer drops extra +/- lines when header counts are wrong.
- `hunk_line_correction`: Best-ratio match preferred over proximity-to-original-line
  when one position clearly dominates, preventing wildly wrong line numbers.
- `overlapping_hunks_fix`: Generic splice replaces hardcoded merge logic.
- `patch_apply`: Truncated diffs handled via partial old_block verification at EOF.
- `apply_diff_atomically`: Returns `None` on failure to fall through to full pipeline.
- `correct_git_diff`: Uses max of header vs actual counts for truncated diffs.
- MCP client: `CancelledError` re-raised instead of swallowed.
- `MUIChatHistory`: `InputProps` → `slotProps.input` for MUI v7 compatibility.
- `determineTokenType`: Explicit language tags no longer overridden by content heuristics.
- Operator-precedence bugs in `isDiffComplete`, `vegaLitePlugin`, `mermaidEnhancer`,
  and `useDelegatePolling` fixed with proper parenthesisation.

## [0.6.1.3] - 2025-07-14

### Added
- `app/config/env_registry.py`: centralised environment variable registry for
  declarative env-var management across the application.
- `app/config/environment.py`: runtime environment abstraction layer.
- `app/config/builtin_tasks.py`: first-class built-in task definitions (e.g.
  release, lint) exposed through the CLI.
- `app/task_runner.py`: structured task execution pipeline for running
  built-in and user-defined tasks.
- `app/providers/bedrock_client_cache.py`: reusable boto3 Bedrock client
  cache to reduce connection overhead on repeated API calls.
- `frontend/src/components/ServiceCard.tsx`: new component for displaying MCP
  service status cards in the web UI.
- `scripts/lint_env_vars.py`: linter that verifies all environment variable
  references are registered in the env registry.
- New documentation files: `Docs/EnvironmentVariables.md`,
  `Docs/CLITasks.md`, `Docs/AnnouncementPlan.md`,
  `Docs/README-Rewrite-Plan.md`.
- Extensive new test suites covering: CLI commands, task runner, environment
  registry, atomic writes, Bedrock client cache, MCP tool timeout, MCP
  get-resource, MCP failed-server TTL, diff validators, diff language
  handlers, diff unicode handling, grounding profile, crystal rehydration,
  tool processing states, shared environment, CLI cancellation, CLI session
  factory, CLI tool-display resilience, and error stream parameter.

### Changed
- `Docs/Enterprise.md`: de-Amazon-ify class names and descriptions; rename
  example provider classes to generic `Enterprise*` equivalents; neutralise
  Amazon-specific phrasing throughout.
- `Docs/FeatureInventory.md`: remove built-in Amazon-internal MCPs row;
  remove background-task notification gap row; update version reference from
  v0.4.x to v0.6.x.
- `README.md`: replace logo with social-preview image.
- `app/cli.py`, `app/main.py`: wired in the new environment registry and task
  runner subsystems.
- `app/config/app_config.py`, `app/config/common_args.py`,
  `app/config/models_config.py`: updated to surface env-registry-managed
  settings and new model configurations.
- `app/agents/agent.py`: improved cancellation handling and tool-call
  processing state machine.
- `app/agents/compaction_engine.py`: better context-window management and
  compaction strategies.
- `app/agents/delegate_manager.py`: crystal rehydration support and improved
  error recovery paths.
- `app/agents/models.py`, `app/agents/prompts.py`,
  `app/agents/wrappers/ziya_bedrock.py`: refreshed model definitions and
  system prompts; updated Bedrock wrapper.
- `app/utils/token_calibrator.py`: aligned with updated model configurations.
- `app/mcp/client.py`: improved connection handling and TTL-based
  failed-server tracking.
- `app/mcp/enhanced_tools.py`: more robust tool-timeout logic.
- `app/mcp/manager.py`, `app/mcp/registry_manager.py`: updated to use the
  `tools/` package and support MCP resource fetching.
- `app/mcp/tools/__init__.py`, `app/mcp/tools/pcap_analysis.py`: updated to
  reflect consolidated tools package.
- `app/providers/bedrock.py`, `app/providers/bedrock_region_router.py`:
  integrated client cache; improved cross-region routing.
- Frontend components (`App.tsx`, `Conversation.tsx`, `MCPRegistryModal.tsx`,
  `MarkdownRenderer.tsx`, `ChatContext.tsx`, `useDelegatePolling.ts`):
  integrate ServiceCard, improve delegate polling, and fix rendering fidelity.
- Documentation updates: `ArchitectureOverview.md`, `Capabilities.md`,
  `FeatureInventory.md`, `MCPSecurityControls.md`, `NewUser.md`,
  `UserConfigurationFiles.md`, `delegate-system-status.md`, `README.md`.
- Updated surviving diff-utils modules (`git_diff.py`, `file_handlers.py`,
  `diff_parser.py`, `diff_pipeline.py`, `pipeline_manager.py`,
  `validators.py`) to reflect the consolidated pipeline architecture.
- Updated test infrastructure: `run_backend_system_tests.py`,
  `run_diff_tests.py`, `run_diff_tests_parallel.py`, `test_all_diff_cases.py`,
  `test_file_state_tracking.py`, `test_new_file_diff_bugs.py`,
  `test_streaming_models.py`, `tests/README.md`.

### Removed
- `Docs/NewUser.md`: removed "Internal (Amazon) Users" section.
- `Docs/delegate-bugs-analysis.md`: deleted internal working/scratch document
  not intended for the public repository.
- `Docs/delegate-completion-notification-design.md`: deleted internal design
  document not intended for the public repository.
- **Legacy diff pipeline modules** (20+ files): `comment_handler.py`,
  `conservative_fuzzy_match.py`, `content_matcher.py`, `direct_apply.py`,
  `duplication_preventer.py`, `empty_file_handler.py`,
  `enhanced_fuzzy_match.py`, `enhanced_patch_apply.py`,
  `escape_handling_improved.py`, `git_apply.py`, `hunk_applier.py`,
  `hunk_utils.py`, `identical_blocks_handler.py`, `json_handler.py`,
  `language_integration.py`, `line_calculation.py`,
  `line_calculation_handler.py`, `line_matching.py`,
  `mre_whitespace_handler.py`, `newline_handler.py`, `patch_apply_fix.py`,
  `pipeline_apply.py`, `sequential_hunk_applier.py`, `cleanup.py`,
  `core/error_tracking.py`, `core/indentation_handler.py`,
  `core/method_chain_handler.py`, `debug/diff_analyzer.py`,
  `pipeline/enhanced_pipeline.py`, `pipeline/enhanced_pipeline_manager.py`.
- `app/mcp/tools.py`: replaced by `app/mcp/tools/` package.
- Obsolete tests covering deleted modules: `test_comment_handler.py`,
  `test_enhanced_fuzzy_match.py`, `test_enhanced_patch_apply.py`,
  `test_enhanced_pipeline.py`, `test_error_tracking.py`,
  `test_escape_handling.py`, `test_improved_line_calculation.py`,
  `test_pipeline_apply.py`,
  `tests/backend_system_tests/integration/integration_test.py`.

### Fixed
- Middleware `error_handling.py`: stream-parameter errors now surfaced
  correctly to callers.
- Middleware `streaming.py`: edge cases for partial responses and
  user-initiated cancellation.
- `app/services/grounding.py`: improved grounding profile handling.

---

*Earlier releases were not tracked in this changelog.*
