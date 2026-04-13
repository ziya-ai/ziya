# Refactoring Plan — Ziya Codebase Cleanup

## Status: Phases 1–5d Complete ✅, Phase 6 In Progress 🟡

### Summary

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| **server.py** | 7,177 lines | 2,879 lines | **−60%** |
| **streaming_tool_executor.py** | 3,935 lines | 3,312 lines | **−16%** (Phase 4–5d extractions) |
| **app/tool_execution.py** | — | 405 lines | New module (Phase 5a) |
| **app/text_delta_processor.py** | — | 204 lines | New module (Phase 5c) |
| **app/message_stop_handler.py** | — | 212 lines | New module (Phase 5d) |
| `@app` decorators in server.py | 50 | 6 | **−88%** |
| `global` declarations in server.py | 20+ | ~5 | **−75%** |
| New test cases added | — | 93+ | — |
| Git net change | — | **−2,373 lines** | — |
| **Tests passing** | — | **85/85** (executor + extraction suites) | — |
| **Integration tests** | — | **11/11** (full orchestrator loop) | — |

### What Was Done

#### Phase 1: Dead Code Removal (−622 lines)
- Removed `_warm_folder_cache_background()` — never called after startup scan was removed
- Removed `_mark_folder_scan_complete()` — empty function body
- Removed orphaned unreachable code after `_monitor_key_rotation()` loop
- Removed `execute_tools_and_update_conversation()` — pre-StreamingToolExecutor tool dispatch
- Removed `detect_and_execute_mcp_tools()` — XML sentinel-based tool parser (replaced by provider API)
- Removed `get_response_continuation_threshold()`, `check_context_overflow()`, `find_continuation_point()`,
  `handle_continuation()`, `splice_continuation_response()`, `create_continuation_prompt()`,
  `stream_continuation()` — all replaced by `middleware/continuation.py`
- Removed unused `_continuation_lock`, `_active_continuations` globals
- Removed commented-out router imports
- Updated `directory_util.py` to remove caller of dead `_mark_folder_scan_complete`

#### Phase 2: Extract Folder Cache Service (−600 lines → 587-line module)
- Created `app/services/folder_service.py` with all folder cache state management
- Moved: `_folder_cache`, `_cache_lock`, `_explicit_external_paths`, globals and related functions
- Moved: `invalidate_folder_cache()`, `is_path_explicitly_allowed()`, `add_file_to_folder_cache()`,
  `update_file_in_folder_cache()`, `remove_file_from_folder_cache()`, `add_external_path_to_cache()`,
  `add_directory_to_folder_cache()`, `broadcast_file_tree_update()`, `get_cached_folder_structure()`,
  `_collect_leaf_file_keys()`, `_collect_documentation_file_keys()`, `_restore_external_paths_for_project()`
- Updated callers: `file_watcher.py`, `context_enhancer.py`, `main.py`, `folder_routes.py`
- Backward-compatible re-exports maintained in `server.py`

#### Phase 3: Route Extraction (−2,992 lines → 18 route modules)

The initial extraction created 7 core route modules. Additional route modules were created during subsequent feature work, bringing the total to 18 files (6,355 lines) under `app/routes/`.

| Module | Lines | Endpoints |
|--------|-------|-----------|
| `app/routes/model_routes.py` | 778 | available-models, config, current-model, set-model, model-capabilities, model-settings |
| `app/routes/folder_routes.py` | 659 | folder, folder-progress, cancel-scan, clear-cache, files, browse, add-explicit-paths, folders |
| `app/routes/diff_routes.py` | 504 | apply-changes, unapply-changes, validate-files, check-files, export, restart-stream |
| `app/routes/page_routes.py` | 486 | /, /render, /info, /debug1, /debug2, /favicon.ico |
| `app/routes/debug_routes.py` | 436 | debug/mcp-state, api/info, debug/reset-mcp, telemetry/* |
| `app/routes/misc_routes.py` | 219 | dynamic-tools, pcap/analyze, pcap/status, abort-stream, retry-throttled |
| `app/routes/token_routes.py` | 185 | token-count, accurate-token-count, cache-stats, cache-test |
| `app/routes/mcp_routes.py` | 1,724 | MCP server management, tool listing, tool invocation |
| `app/routes/mcp_registry_routes.py` | 312 | MCP server registry CRUD |
| `app/routes/conversation_routes.py` | 232 | Conversation history, session management |
| `app/routes/builtin_tools_routes.py` | 131 | Built-in tool endpoints |
| `app/routes/diagram_routes.py` | 114 | Diagram rendering |
| `app/routes/cache_routes.py` | 110 | Cache management |
| `app/routes/ast_routes.py` | 107 | AST indexing and search |
| `app/routes/export_routes.py` | 192 | Export/download |
| `app/routes/file_validation.py` | 71 | File path validation |
| `app/routes/graph_routes.py` | 64 | Graph/dependency visualization |
| `app/routes/static_routes.py` | 31 | Static file serving |

#### Phase 4: StreamingToolExecutor Method Extraction
- Extracted `_load_and_prepare_tools()` — MCP tool loading, schema conversion, deduplication (65 lines)
- Extracted `_build_conversation_from_messages()` — message format normalization (30 lines)
- Extracted `_should_continue_or_end_stream()` — iteration end/continue decision logic (60 lines)
- Extracted `_classify_and_handle_error()` — error classification, throttle backoff, token reduction (130 lines)
- Replaced 194 lines of inline error handling with 34-line method call
- Replaced 60 lines of inline conversation building with 3-line method call
- All 4 methods independently tested (19 new tests)

#### Phase 5a: Extract `execute_single_tool()` (−349 lines → 391-line module)
- Created `app/tool_execution.py` with:
  - `ToolExecContext` dataclass — bundles 18 parameters into a typed context object
  - `execute_single_tool()` async generator — complete tool execution lifecycle
  - `_process_result()` pure function — error classification and content extraction
- Extracted from `content_block_stop` handler:
  - Frontend notification (processing_state, tool_start events)
  - Pre-execution feedback check (stop vs directive)
  - Builtin tool (DirectMCPTool) vs external (MCP manager) routing
  - Cryptographic signature verification + security recording
  - Audit logging
  - Result processing (error classification, image preservation, text extraction)
  - Result sanitization (plugin + general-purpose transforms)
  - User display (tool_display with image data URI extraction)
  - Model feedback (tool_result_for_model)
  - Adaptive inter-tool delay
  - Post-execution feedback drain
  - Exception handling (timeout, shutdown, generic)
- Call site in `streaming_tool_executor.py` reduced to ~40 lines
- Side effects communicated via mutable `ToolExecContext` flags
- Internal `_tool_result` sentinel event for caller to append to tool_results
- 20 new tests (12 unit for `_process_result`, 8 async integration for full lifecycle)

#### Phase 5b: Extract `_handle_usage_event()` + helpers (−251 lines inline → 4 methods)
- Extracted `_handle_usage_event()` — orchestrates usage update, logging, accuracy tracking, calibration
- Extracted `_track_estimation_accuracy()` — compares estimated vs actual token counts
- Extracted `_estimate_message_tokens()` / `_estimate_content_tokens()` — static token estimation helpers
- Extracted `_record_calibration()` — records actual usage for future calibration improvement
- Replaced 263 lines of inline code with 7-line method call
- All methods independently tested (15 new tests)

#### Phase 5c: Extract `process_text_delta()` (−210 lines inline → 204-line module)
- Created `app/text_delta_processor.py` with:
  - `TextDeltaState` dataclass — mutable state for text delta processing within a single iteration
  - `process_text_delta()` synchronous function — returns list of event dicts
- Extracted: fence buffering, fake tool-call suppression, fence spacing normalization, hallucination detection, visualization block buffering, content optimization
- Call site reduced to ~13 lines
- 15 new tests (fence buffering, hallucination, viz buffering, output)

#### Phase 5d: Extract `handle_message_stop()` (−155 lines inline → 212-line module)
- Created `app/message_stop_handler.py` with:
  - `MessageStopState` dataclass — mutable state for message-stop processing
  - `handle_message_stop()` async generator — buffer flushing, code block continuation, usage recording
- Call site reduced to ~30 lines
- 11 new tests (buffer flushing, continuation, usage recording)

### What server.py Contains Now
- App skeleton, middleware registration, lifespan handlers
- 3 WebSocket endpoints (feedback, file-tree, delegate-stream)
- `/api/chat` endpoint + `stream_chunks()` orchestrator
- `build_messages_for_streaming()` (message construction)
- `_keepalive_wrapper()` (SSE heartbeats)
- Router imports and registration

---

### Phase 6: Exception Handling Audit — In Progress 🟡

| Metric | Count |
|--------|-------|
| Original baseline | 707 bare `except Exception` |
| Resolved (narrowed or removed) | 129 |
| Annotated as intentionally broad | 26 |
| Remaining unannotated | 551 |

**Completed files:** `streaming_tool_executor.py` ✅, `server.py` ✅, `tool_execution.py` ✅, `text_delta_processor.py` ✅, `message_stop_handler.py` ✅, `cli.py` ✅, `mcp/client.py` ✅, `agents/agent.py` ✅
**Next targets:** `mcp/manager.py` (12), `middleware/streaming.py` (5+), `providers/` (10), `routes/` (123)

See `REFACTORING_HANDOFF.md` Phase 6 section for full breakdown.

### Remaining Opportunities (Future Phases)

#### Phase 7: Frontend type safety
- Replace 666 `: any` annotations in TypeScript
- Break up MarkdownRenderer.tsx (6,109 lines)

#### Phase 8: Logging cleanup
- Replace emoji-prefixed debug logs with structured logging
- Add trace IDs for request correlation
