# Refactoring Handoff — Phases 1–5d Complete, Phase 6+ Ready

## Current State (as of Phase 5d completion)

### What Exists Now

**streaming_tool_executor.py** — ~3,312 lines (down from 3,935 pre-Phase 5)
- `StreamingToolExecutor` class with `stream_with_tools()` as the main entry point
- `stream_with_tools()` body is ~1,945 lines — still large but the *event dispatch loop* itself is now thin:
  - `UsageEvent` → 7-line call to `self._handle_usage_event()`
  - `TextDelta` → 13-line call to `process_text_delta()` from `app.text_delta_processor`
  - `ToolUseStart/Input/End` → inline (small, ~30 lines each)
  - `content_block_stop` → argument parsing/validation (~200 lines inline) + 30-line call to `execute_single_tool()` from `app.tool_execution`
  - `message_stop` → 30-line call to `handle_message_stop()` from `app.message_stop_handler`
- The remaining bulk in `stream_with_tools()` is: iteration setup (~100 lines), baseline establishment (~170 lines), post-iteration logic (tool results → conversation, completion checking, feedback drain — ~300 lines), and the final usage report (~70 lines)

**Extracted modules created in Phase 5:**

| Module | Lines | What |
|--------|-------|------|
| `app/tool_execution.py` | 405 | `ToolExecContext` dataclass, `execute_single_tool()` async generator, `_process_result()` pure function |
| `app/text_delta_processor.py` | 204 | `TextDeltaState` dataclass, `process_text_delta()` synchronous function |
| `app/message_stop_handler.py` | 212 | `MessageStopState` dataclass, `handle_message_stop()` async generator |

**Extracted class methods on StreamingToolExecutor (Phase 4+5):**
- `_load_and_prepare_tools()` — MCP tool loading, schema conversion, deduplication
- `_build_conversation_from_messages()` — message format normalization
- `_should_continue_or_end_stream()` — iteration end/continue decision
- `_classify_and_handle_error()` — error classification, throttle backoff
- `_handle_usage_event()` — usage update, logging, accuracy tracking
- `_track_estimation_accuracy()` — estimated vs actual token comparison
- `_estimate_message_tokens()` / `_estimate_content_tokens()` — static helpers
- `_record_calibration()` — records actual usage for calibration
- `_build_provider_config()` — builds ProviderConfig for each iteration

**Test files:**
- `tests/test_streaming_tool_executor.py` — 13 tests (pre-existing, image compaction/detection)
- `tests/test_tool_execution.py` — 20 tests (Phase 5a: _process_result + execute_single_tool)
- `tests/test_usage_tracking.py` — 15 tests (Phase 5b: usage event handling + estimation + calibration)
- `tests/test_text_delta_processor.py` — 15 tests (Phase 5c: fence buffering, hallucination, viz, output)
- `tests/test_message_stop.py` — 11 tests (Phase 5d: buffer flushing, continuation, usage recording)
- **Total: 74 tests, all passing**

### Patterns Used

**Async generator with mutable context**: `execute_single_tool()` yields event dicts. Side effects (feedback_received, should_stop_stream) communicated via mutable `ToolExecContext` dataclass fields. The caller iterates and checks flags after exhaustion. Special `_tool_result` sentinel event type carries results back to the caller for `tool_results.append()`.

**Synchronous function returning event list**: `process_text_delta()` returns `List[Dict]` of events. Mutates `TextDeltaState` in place. Caller wraps each event with `track_yield()` before yielding. `hallucination_detected` flag signals caller to `break`.

**Async generator with mutable state**: `handle_message_stop()` (in `app/message_stop_handler.py`) yields events via `MessageStopState` dataclass. The caller syncs `assistant_text`, `last_stop_reason`, `continuation_happened` back from the state object after iteration.

**Pure side-effect method**: `_handle_usage_event()` takes mutable `iteration_usage` and `throttle_state` dicts, modifies them in place, returns nothing.

**Lazy imports**: All extracted code uses lazy imports (`from app.mcp.signing import ...` inside function bodies) to avoid circular import issues and match the existing codebase convention. Tests must patch at the *source* module path (e.g., `app.mcp.signing.verify_tool_result`), NOT at `app.tool_execution.verify_tool_result` — the latter doesn't exist as a module-level attribute.

**Backtick escaping**: Diffs involving triple-backtick strings (` ```mermaid `, ` ```diff `, etc.) are extremely fragile. The diff application tooling may mangle backtick-heavy content. For future extractions involving backtick patterns, prefer using `file_write` with patch mode or write the file directly rather than relying on git diff format.

---

## Phase 6: Exception Handling Audit — In Progress

### Goal
Replace bare `except Exception` with specific exception types throughout the codebase.

**Original baseline: 707 instances.**

### Progress So Far

Two complementary strategies have been applied:

1. **Narrowing**: Replace `except Exception` with specific exception tuples (e.g., `except (OSError, RuntimeError, asyncio.TimeoutError)`). This is the primary goal — each handler now documents exactly what it catches.

2. **Annotation**: Where a broad catch is genuinely necessary (top-level HTTP handlers, SSE stream wrappers, tool execution boundaries), the handler is annotated with `# Intentionally broad: <reason>` explaining why it must remain broad.

### Current Numbers

| Metric | Count |
|--------|-------|
| Original baseline | 707 |
| Remaining unannotated bare `except Exception` | 551 |
| Annotated as intentionally broad | 26 |
| **Resolved (narrowed or removed)** | **130** |

### Priority Files — Status

| File | Bare | Annotated | Specific | Status |
|------|------|-----------|----------|--------|
| `streaming_tool_executor.py` | **0** | 1 | 34 | ✅ **Complete** |
| `server.py` | **0** | 13 | 40 | ✅ **Complete** |
| `tool_execution.py` | **0** | 1 | 3 | ✅ **Complete** |
| `text_delta_processor.py` | **0** | 0 | 0 | ✅ **Complete** (no exceptions needed) |
| `message_stop_handler.py` | **0** | 0 | 2 | ✅ **Complete** |
| `agents/agent.py` | **0** | 6 | 37 | ✅ **Complete** |
| `cli.py` | **0** | 4 | 42 | ✅ **Complete** |
| `mcp/client.py` | **0** | 1 | 37 | ✅ **Complete** |

### Remaining by Area

| Area | Unannotated | Notes |
|------|-------------|-------|
| `utils/` | 172 | Largest batch — many small utility try/excepts |
| `mcp/` | 110 | manager.py (12) + tools/permissions/etc (client.py done) |
| `routes/` | 123 | HTTP endpoint handlers (many are top-level catch-all) |
| `agents/` | 57 | Wrappers (google, nova, openai) + delegate_manager |
| `top-level app/*.py` | 8 | server.py + cli.py done; remaining is minor |
| `middleware/` | 15 | Streaming, error handling, continuation |
| `services/` | 15 | folder_service, grounding, token_service |
| `plugins/` | 12 | Plugin init, conversation graph |
| `storage/` | 11 | JSON file I/O |
| `providers/` | 10 | Bedrock, Anthropic, OpenAI direct |
| `api/` | 10 | REST API handlers |
| `config/` | 5 | Shell config, write policy |
| `mcp_servers/` | 4 | Shell server |

### What Was Done

The Phase 6 work focused on the **highest-value targets** first — the files that are most actively maintained and most likely to mask real bugs:

- **`streaming_tool_executor.py`**: Reduced from 26 bare `except Exception` to **0 unannotated + 1 annotated**. The remaining annotated handler delegates to `_classify_and_handle_error()` which triages throttling, auth, transient, read-timeout, and generic errors.

- **`server.py`**: Reduced from ~25+ bare handlers to **0 unannotated + 13 annotated**. The annotated handlers are all top-level HTTP/SSE/WebSocket boundaries where a broad catch is correct (must always yield an error event to the client, never a silent drop).

- **`agents/agent.py`**: Reduced from 33 to **0 unannotated + 6 annotated**. The annotated handlers are retry loops (RetryingChatBedrock.astream, invoke) that need to triage throttling, validation, credential, and generic errors. The final unannotated handler (`_execute_tool_call_in_content`) was narrowed to `(ImportError, OSError, RuntimeError, ConnectionError, asyncio.TimeoutError, json.JSONDecodeError, TypeError, ValueError)`.

- **`cli.py`**: Reduced from 23 bare `except Exception` to **0 unannotated + 4 annotated**. The annotated handlers are: model init (can raise any credential/import/config error), `ask()` top-level (must preserve partial response), tool display rendering (must not crash stream), and `main()` top-level CLI error handler. The remaining 19 were narrowed to specific types: `subprocess.SubprocessError` for git ops, `json.JSONDecodeError` for session loading, `(ValueError, TypeError)` for datetime parsing, etc.

- **`mcp/client.py`**: Reduced from 21 bare `except Exception` to **0 unannotated + 1 annotated**. The annotated handler is `connect()` (subprocess/transport failures vary by OS and server type). The remaining 20 were narrowed: `(OSError, subprocess.SubprocessError)` for npm registry detection, `(OSError, RuntimeError, ConnectionError)` for MCP server communication, `asyncio.CancelledError` for shutdown cleanup, etc.

- **`tool_execution.py`**, **`text_delta_processor.py`**, **`message_stop_handler.py`**: All Phase 5 extracted modules were written with specific exceptions from the start — **0 bare handlers**.

### Approach
1. **Categorize** each `except Exception` by what it actually catches:
   - Network/API errors: `botocore.exceptions.ClientError`, `httpx.HTTPError`, `asyncio.TimeoutError`
   - JSON parsing: `json.JSONDecodeError`
   - Import failures: `ImportError`, `ModuleNotFoundError`
   - File system: `FileNotFoundError`, `PermissionError`, `OSError`
   - Authentication: `KnownCredentialException` (custom)
   - Generic programming errors that should propagate: `TypeError`, `ValueError`, `AttributeError`

2. **Triage by risk**:
   - HIGH: Exception handlers that swallow errors silently (bare `except Exception: pass` or just logging)
   - MEDIUM: Exception handlers that catch too broadly but do handle the error
   - LOW: Top-level catch-all handlers that are intentionally broad (e.g., around entire tool execution)

3. **Test strategy**: Run existing 74 tests after each file is modified. For the highest-risk changes, add tests that verify specific exception types are caught and others propagate.

### Suggested Next Targets (in priority order)
1. ~~`app/cli.py`~~ ✅ Done
2. ~~`app/mcp/client.py`~~ ✅ Done
3. `app/mcp/manager.py` — 12 unannotated (server lifecycle)
4. `app/middleware/streaming.py` — 5+ (SSE stream handling)
5. `app/providers/*.py` — 10 total (API call error handling)
6. `app/routes/*.py` — 123 total (many are HTTP top-level, may just need annotation)

---

## Phase 7: Frontend Type Safety

### Goal
Replace 666 `: any` TypeScript annotations with proper types, and break up the monolithic `MarkdownRenderer.tsx` (6,109 lines).

### MarkdownRenderer.tsx decomposition targets
This file renders all markdown content including code blocks, diffs, diagrams, tables, and interactive elements. Potential extraction targets:
- Code block rendering (Prism highlighting, copy button, language detection)
- Diff rendering (unified diff display, apply/unapply buttons)
- Diagram rendering (mermaid, graphviz, vega-lite, drawio — each has its own renderer)
- Table rendering (sortable tables, CSV export)
- Image rendering (inline images, data URIs, lightbox)
- Tool result rendering (collapsible tool outputs, syntax highlighting)

### `: any` replacement strategy
- Start with component props — define proper interfaces for each component
- Then event handlers — most are `React.MouseEvent`, `React.ChangeEvent`, etc.
- Then state variables — derive types from the data structures they hold
- Finally, utility function parameters and return types

---

## Phase 8: Logging Cleanup

### Goal
Replace emoji-prefixed debug logs with structured logging and add trace IDs for request correlation.

### Current state
The codebase uses ad-hoc emoji prefixes for log categorization:
- 🔍 = debug/diagnostic
- 📊 = metrics/calibration
- 🔧 = tool operations
- 🔐 = security
- 🔄 = feedback/retry
- ⚠️ = warnings
- 🚨 = critical errors

### Approach
1. Define a structured log format with fields: `trace_id`, `conversation_id`, `iteration`, `component`, `event_type`
2. Replace emoji prefixes with structured fields
3. Add `conversation_id` as a context variable (via `contextvars`) so all log statements within a request automatically include it
4. Consider using Python's `logging.LoggerAdapter` or a custom formatter

---

## Remaining Opportunities in streaming_tool_executor.py

After Phase 5, the largest remaining inline blocks in `stream_with_tools()` are:

1. **Baseline establishment** (~170 lines, around line 1694): The `should_establish_baseline` block that measures system prompt + tool token overhead. This is only run once per model family. Could be extracted to a `_establish_baseline()` method.

2. **Content block stop — argument parsing/validation** (~200 lines, around line 2280): The JSON parsing, `tool_input` unwrapping, schema validation, and empty-args detection before `execute_single_tool()` is called. Could be extracted to `_parse_and_validate_tool_args()`.

3. **Post-iteration completion logic** (~300 lines, around line 2700): The `if tools_executed_this_iteration` / `else` branches that handle feedback drain, continuation decisions, and stream ending. This is the most complex remaining section — it has many interacting flags (`tools_executed_this_iteration`, `blocked_tools_this_iteration`, `continuation_happened`, `last_stop_reason`, etc.) and multiple `yield`/`return`/`break`/`continue` exit paths.

4. **Post-loop feedback handling** (~100 lines, around line 2900): The `if conversation_id:` block after the iteration loop that checks for late-arriving feedback and makes one additional API call.

The Phase 5 goal was "`stream_with_tools()` main loop body under 200 lines" — we didn't quite reach that (the body is ~1,945 lines), but the *event dispatch loop* (the `async for stream_event` section) is now around 150 lines of actual logic, with the rest being iteration setup, post-iteration handling, and error recovery. Getting to 200 lines total would require extracting the post-iteration logic and baseline establishment, which are the natural next targets if further decomposition is desired.
