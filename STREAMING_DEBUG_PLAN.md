# Streaming Content Loss Debug Plan

## Problem Statement
- Backend logs show complete, clean output
- Network log shows all data transmitted via SSE
- Frontend displays only ~60% of content (drops after visualization #9)
- Fragmentation increases toward end of stream (single character chunks)

## Baseline Commit
`802aefc` - "Baseline: streaming content loss issue - backend clean, frontend drops content after ~60%"

## Investigation Phases

### Phase 1: Measure & Instrument (No Code Changes)
**Goal:** Understand exactly where content is lost

1. **Add frontend logging to chatApi.ts**
   - Log every SSE event received (with size)
   - Log accumulated content length
   - Log any parsing errors
   - Track: event count, total bytes, parse failures

2. **Add backend streaming metrics**
   - Log chunk sizes being sent
   - Log total events sent
   - Track flush frequency

3. **Create test harness**
   - Reproduce issue with controlled large response
   - Capture metrics from both sides
   - Compare: events sent vs events received vs events rendered

**Test:** Run visualization request, collect metrics, identify divergence point

---

### Phase 2: Isolate the Layer
**Goal:** Determine if issue is in network, parsing, or rendering

1. **Test A: Raw network capture**
   - Use browser DevTools to save complete network stream
   - Parse SSE events manually
   - Verify: Is all data in network log?

2. **Test B: Parser isolation**
   - Extract SSE parsing logic
   - Feed it the raw network data
   - Verify: Does parser extract all events?

3. **Test C: State accumulation**
   - Log React state updates
   - Check for state size limits
   - Verify: Are all parsed events added to state?

**Decision Point:** Identify which layer drops content

---

### Phase 3: Root Cause Analysis
Based on Phase 2 results:

**If Network Layer:**
- Check SSE event size limits
- Check connection timeout/keepalive
- Check proxy/middleware interference

**If Parser Layer:**
- Check buffer overflow in EventSource
- Check JSON parsing of large payloads
- Check event boundary detection

**If State Layer:**
- Check React state size limits
- Check re-render performance
- Check memory pressure

---

### Phase 4: Targeted Fix
**Goal:** Minimal change to fix root cause

Options based on findings:
1. **Chunk size limiting** - Ensure SSE events stay under safe size
2. **Buffering strategy** - Batch small chunks before sending
3. **Parser robustness** - Handle fragmented JSON better
4. **State optimization** - Use refs or streaming accumulator

---

### Phase 5: Verification
**Goal:** Prove fix works reliably

1. **Regression test**
   - Run original failing case
   - Verify 100% content delivery

2. **Stress test**
   - Generate responses 2x, 5x, 10x larger
   - Verify no degradation

3. **Performance test**
   - Measure latency impact
   - Ensure no slowdown

---

## Test Cases

### TC1: Baseline Reproduction
```bash
# Start server with logging
# Make request: "create 10+ visualizations summarizing this project"
# Capture: server.log, network HAR, frontend console
# Verify: Content loss occurs consistently
```

### TC2: Small Response (Control)
```bash
# Request: "create 2 simple visualizations"
# Verify: Works perfectly (establishes baseline)
```

### TC3: Incremental Size
```bash
# Request: 3, 5, 7, 10, 15 visualizations
# Find: Exact threshold where loss begins
```

### TC4: Post-Fix Validation
```bash
# Repeat TC1 after fix
# Verify: 100% content delivery
```

---

## Metrics to Collect

### Backend
- `events_sent`: Total SSE events
- `bytes_sent`: Total payload size
- `avg_chunk_size`: Average event size
- `max_chunk_size`: Largest event
- `flush_count`: Number of stream flushes

### Frontend
- `events_received`: Total SSE events
- `bytes_received`: Total payload size
- `parse_errors`: JSON parse failures
- `state_updates`: React state changes
- `render_count`: Component re-renders

### Comparison
- `loss_rate`: (events_sent - events_received) / events_sent
- `divergence_point`: First missing event number
- `fragment_ratio`: events with size < 10 bytes

---

## Success Criteria
- [ ] 100% of backend events reach frontend
- [ ] No parse errors
- [ ] Content renders completely
- [ ] Performance acceptable (<100ms latency increase)
- [ ] Works for responses up to 100KB
