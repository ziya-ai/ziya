# Backlog: Thread Executor Refactor for Bedrock Streaming

**Status:** Backlog — Not yet scheduled  
**Priority:** Medium (escalates to High before multi-user deployment)  
**Created:** 2025-07-21  
**Prerequisite fixes:** Remove `time.sleep` from `CustomBedrockClient` (done in same session)

---

## Problem Statement

The Ziya server runs on a single asyncio event loop (uvicorn, single worker). All boto3
calls to AWS Bedrock are **synchronous** — they block the event loop for the duration of
each HTTP round-trip and stream iteration. During these blocks, the server cannot service
any other requests, including:

- `/api/config` health checks (triggers false "Server is unreachable" banner)
- `/api/folders` and other frontend polling endpoints
- Concurrent chat requests from other browser tabs
- WebSocket heartbeats for real-time feedback

### Acute symptom (fixed separately)

`CustomBedrockClient` used `time.sleep()` for timeout retries, adding 3–9 seconds of
**pure** event loop blocking on top of legitimate I/O blocking. This was fixed by removing
the sync retry loops and propagating timeout errors to `StreamingToolExecutor`, which
retries with `asyncio.sleep` (non-blocking).

### Underlying architectural issue (this backlog item)

Even without `time.sleep`, every boto3 call blocks the event loop during active I/O:

| Call site | File:Line | Typical block | Worst-case block |
|---|---|---|---|
| Main invoke | `streaming_tool_executor.py:1094` | 1–5s (TTFB) | 60s (timeout) |
| Chunk iteration | `streaming_tool_executor.py:1171` | 10–50ms/chunk | 10s+ (Bedrock stall) |
| Extended context retry | `streaming_tool_executor.py:1117` | 1–5s | 60s |
| Post-loop feedback | `streaming_tool_executor.py:2706` | 1–5s | 60s |
| Code continuation | `streaming_tool_executor.py:3056` | 1–5s | 60s |
| Baseline calibration | `streaming_tool_executor.py:850` | 1–3s | 30s |

That's **5 separate `invoke_model` calls** and **3 separate `for event in` iteration
loops** blocking the single event loop.

---

## Proposed Solution: Producer/Consumer with `asyncio.Queue`

### Core pattern

Run boto3 streaming I/O in a thread pool worker. Bridge to the async world via an
`asyncio.Queue`. The existing async generator body (tool execution, yields, awaits)
stays on the event loop unchanged.

```python
async def _stream_from_bedrock(self, api_params, cancel_event=None):
    """
    Non-blocking adapter: reads boto3 EventStream in a thread,
    yields events to the async generator via asyncio.Queue.
    """
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue(maxsize=1000)

    def _reader():
        try:
            response = self.bedrock.invoke_model_with_response_stream(**api_params)
            for event in response['body']:
                if cancel_event and cancel_event.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, event)
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, e)  # propagate error

    future = loop.run_in_executor(None, _reader)

    while True:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item

    await future  # ensure thread cleanup
```

### Conversion pattern

Each blocking call site changes from:

```python
response = self.bedrock.invoke_model_with_response_stream(**api_params)
for event in response['body']:
    chunk = json.loads(event['chunk']['bytes'])
    # ... process chunk, yield, await tools ...
```

To:

```python
async for event in self._stream_from_bedrock(api_params, cancel_event):
    chunk = json.loads(event['chunk']['bytes'])
    # ... process chunk, yield, await tools — UNCHANGED ...
```

The 74 `yield` statements and 15 `await` calls inside the loop body remain unchanged.

---

## Scope of Changes

### Files modified

| File | Changes | Lines |
|---|---|---|
| `app/streaming_tool_executor.py` | Add adapter method, convert 5 call sites, add cancellation wiring | ~100 |
| `app/utils/custom_bedrock.py` | Remove `_current_conversation_id` module global, pass through parameters | ~30 |
| `app/agents/models.py` | ThreadPoolExecutor lifecycle (optional, default executor may suffice) | ~10 |

### Call sites to convert

1. **Main invoke** (L1094) — Primary streaming loop. Highest value.
2. **Extended context retry** (L1117) — Inside the retry loop, needs careful integration.
3. **Post-loop feedback** (L2706) — Simpler loop (text only, no tools). Lower risk.
4. **Code continuation** (L3056) — Simpler loop (text only). Lower risk.
5. **Baseline calibration** (L850) — One-time call. Could remain sync (low impact).

### Recommended conversion order

1. Fix prerequisite (module global elimination) first
2. Convert L1094 + L1171 (main loop) — highest value, most complex
3. Convert L3056 + L2706 (continuation + feedback) — simpler, validates pattern
4. Convert L1117 (extended context) — integrated into retry loop
5. Leave L850 (baseline) sync — one-time call, negligible impact

---

## Risk Assessment

### Risk 1: Module global race condition — MUST FIX FIRST

**Severity: High — this would be a day-one bug if the refactor ships without fixing it.**

`custom_bedrock.py` uses `_current_conversation_id` as a module global:

```python
# Module global set from event loop
_current_conversation_id: Optional[str] = None

class CustomBedrockClient:
    def _extract_conversation_id_from_request(self, kwargs):
        global _current_conversation_id
        if _current_conversation_id:
            return _current_conversation_id  # Read from thread!
```

Today this works because everything is single-threaded. With the thread refactor, a
second request starting on the event loop could overwrite the global while the first
request's thread is still reading it.

**Fix:** Pass `conversation_id` as a parameter through the call chain instead of using
a module global. This is a prerequisite, not part of the thread refactor itself.

### Risk 2: boto3 EventStream thread safety — UNCERTAIN

boto3 clients are documented as thread-safe for API calls. The `EventStream` iterator
reads from a `urllib3` HTTP response body. In the producer/consumer pattern, only the
reader thread touches the iterator, so this *should* be safe. However, there's no
explicit documentation guaranteeing EventStream iterator thread safety.

**Mitigation:** The adapter pattern isolates the iterator to a single thread. Only the
queue crosses the thread boundary (via `call_soon_threadsafe`). If subtle issues arise,
they'd manifest as garbled chunk data — easy to detect in testing.

**Likelihood:** Low. The pattern is well-established in Python async/sync bridging.

### Risk 3: Wrapper chain context assumptions — MEDIUM

The call chain `StreamingToolExecutor → ThrottleSafeBedrock → CustomBedrockClient → boto3`
may access logging, environment variables, or module state that assumes single-threaded
execution. Specific known issue: `CustomBedrockClient` sets `os.environ["AWS_REGION"]`
during initialization (not in hot path, but illustrative of the assumption pattern).

**Mitigation:** Audit all wrapper layers for thread-unsafe state access before converting.

### Risk 4: Cancellation reliability — MEDIUM

When the user clicks "Stop", the async generator is garbage collected. Currently this
immediately stops the `for event` loop. With threading, the reader thread continues until
it checks `cancel_event`. If the thread is blocked on a slow `next()` from Bedrock, it
holds the HTTP connection open and may consume tokens.

**Mitigation:**
- Use `threading.Event` checked between iterations
- Set a read timeout on the urllib3 socket so `next()` doesn't block indefinitely
- Wire `cancel_event.set()` to the frontend abort signal path

### Risk 5: Error propagation timing change — LOW-MEDIUM

Currently, a mid-stream exception (connection reset) interrupts processing immediately.
With the queue, buffered events ahead of the exception are processed first, then the
exception is raised. This is actually *better* behavior (preserves partial content), but
it's a behavioral change in a code path with no automated tests.

**Mitigation:** This is actually desirable. Document the behavioral change.

### Risk 6: No automated test safety net — HIGH (impact amplifier)

`streaming_tool_executor.py` is 3,155 lines with zero automated test coverage. Every
risk above is amplified by the fact that regressions can only be caught manually.

**Minimum manual test matrix:**

- [ ] Normal text streaming (short response)
- [ ] Long text streaming (>4K tokens)
- [ ] Multi-tool execution chain (5+ sequential tools)
- [ ] Tool execution interleaved with text streaming
- [ ] User abort mid-text-stream
- [ ] User abort mid-tool-execution
- [ ] Read timeout + async retry during streaming
- [ ] Throttling + retry during tool chain
- [ ] Extended context activation mid-stream
- [ ] Code block continuation after `message_stop`
- [ ] Post-loop feedback delivery
- [ ] Two browser tabs streaming concurrently
- [ ] Health check responsiveness during all above scenarios

---

## Benefit Assessment

| Scenario | Current impact | After refactor |
|---|---|---|
| Single user, one stream | Health check may timeout during TTFB (1-5s) | Event loop stays responsive |
| Single user, Bedrock stall | All endpoints frozen for 10-60s | Only the stream waits |
| Two browser tabs | Second tab completely blocked during first's stream | Both work independently |
| Multi-user deployment | Unusable — one user blocks all others | Fully concurrent |
| Parallel tool execution (future) | Impossible — tools run sequentially on blocked loop | Prerequisite met |

### For current single-user architecture: MODERATE benefit

The user is already waiting for their response during legitimate I/O blocking. The primary
observable improvement is that health checks and folder polling stay responsive (no false
"unreachable" banner). The simpler fixes (removing `time.sleep`, increasing frontend
thresholds) address the acute symptom.

### For future multi-user/multi-tab: HIGH benefit

This becomes a hard blocker. One user's Bedrock call would freeze the server for all
other users.

---

## Alternative Considered: aioboto3

Using `aioboto3`/`aiobotocore` would make boto3 calls natively async:

```python
async with client.invoke_model_with_response_stream(...) as response:
    async for event in response['body']:
        ...
```

**Rejected because:**
- Not in current dependencies; `aiobotocore` pins specific `botocore` versions (conflict risk)
- `CustomBedrockClient`, `ThrottleSafeBedrock`, and all wrappers need full rewrite
- Session/credential handling differs from boto3
- Larger blast radius for equivalent benefit

The thread executor approach reuses the existing boto3 stack unchanged.

---

## Execution Plan

### Phase 0: Prerequisites (do before scheduling this work)

1. **Eliminate `_current_conversation_id` module global** in `custom_bedrock.py`
   - Pass `conversation_id` through parameters from `StreamingToolExecutor`
   - ~30 lines changed across 2 files
   - Can be done independently, no behavioral change

2. **Add integration tests for `StreamingToolExecutor`**
   - Mock boto3 EventStream with controlled timing
   - Cover the 13 test scenarios listed above
   - This is the single biggest risk reducer for the refactor

### Phase 1: Core adapter (1–2 days)

1. Add `_stream_from_bedrock()` async generator method
2. Add `threading.Event` cancellation support
3. Convert main invoke + iteration loop (L1094 + L1171)
4. Wire cancellation to frontend abort signal
5. Manual test: all 13 scenarios

### Phase 2: Secondary call sites (0.5–1 day)

1. Convert continuation loop (L3056) — simplest, validates pattern
2. Convert feedback loop (L2706) — similar simplicity
3. Convert extended context retry (L1117) — integrated in retry logic
4. Leave baseline calibration (L850) sync — negligible impact

### Phase 3: Hardening (0.5 day)

1. Add ThreadPoolExecutor with bounded workers (optional — default executor may suffice)
2. Add metrics: queue depth, thread block duration
3. Stress test: rapid abort/retry cycles
4. Verify memory: no leaked threads or unclosed connections after abort

### Total estimated effort: 2–3 days (after prerequisites)

---

## Decision Criteria: When to Schedule

Schedule this work when ANY of these become true:

- [ ] Multi-user or multi-port deployment is planned
- [ ] Concurrent conversation streaming is desired (two chats generating simultaneously)
- [ ] Parallel tool execution is on the roadmap
- [ ] Integration tests exist for `StreamingToolExecutor`
- [ ] The health check banner reappears despite the `time.sleep` fixes (indicating
      legitimate I/O blocking is now the bottleneck)

**Do NOT schedule this if** the only symptom is the health check banner — the simpler
frontend threshold fixes are sufficient for single-user operation.
