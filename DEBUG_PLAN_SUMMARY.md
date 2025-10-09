# Streaming Content Loss - Debug Plan Summary

## Status: Phase 1 Complete ✅

### Commits
- **Baseline**: `802aefc` - Problem state captured
- **Phase 1**: `c0240bb` - Metrics instrumentation added

## Problem
Large streaming responses (10+ visualizations) lose content after ~60%. Backend logs show complete output, but frontend only displays partial content.

## Systematic Approach

### ✅ Phase 1: Measure & Instrument (COMPLETE)
**Goal**: Collect hard data on where content is lost

**What we added**:
1. Frontend metrics in `chatApi.ts`:
   - Tracks every chunk received
   - Logs bytes, chunk sizes, fragmentation
   - Reports final statistics

2. Backend metrics in `streaming_tool_executor.py`:
   - Tracks every event sent
   - Logs bytes, event sizes
   - Reports final statistics

**Next Action**: Run test (see `test_streaming_metrics.md`)

---

### 🔄 Phase 2: Isolate the Layer (PENDING)
**Goal**: Determine if issue is network, parser, or state

**Tests to run**:
- **Test A**: Check browser network tab - is all data there?
- **Test B**: Parse SSE events manually - does parser work?
- **Test C**: Check React state updates - is state accumulating?

**Decision Point**: Metrics will tell us which test to run first

---

### 🔄 Phase 3: Root Cause Analysis (PENDING)
Based on Phase 2 findings, investigate:
- Network layer (SSE limits, timeouts)
- Parser layer (buffer overflow, JSON parsing)
- State layer (React limits, memory)

---

### 🔄 Phase 4: Targeted Fix (PENDING)
Minimal code change to fix root cause:
- Option 1: Chunk size limiting
- Option 2: Buffering strategy
- Option 3: Parser robustness
- Option 4: State optimization

---

### 🔄 Phase 5: Verification (PENDING)
Prove fix works:
- Regression test (original case)
- Stress test (2x, 5x, 10x size)
- Performance test (no slowdown)

## Key Principles

1. **No guessing** - Let metrics guide us
2. **Minimal changes** - Only fix what's broken
3. **Test each step** - Verify before proceeding
4. **Commit often** - Easy rollback if needed

## Files Modified

### Phase 1 Instrumentation
- `frontend/src/apis/chatApi.ts` - Added metrics collection
- `app/streaming_tool_executor.py` - Added metrics tracking

### Documentation
- `STREAMING_DEBUG_PLAN.md` - Full detailed plan
- `STREAMING_METRICS.md` - Instrumentation guide
- `test_streaming_metrics.md` - Test procedure
- `DEBUG_PLAN_SUMMARY.md` - This file

## How to Proceed

1. **Run the test**: Follow `test_streaming_metrics.md`
2. **Collect metrics**: Frontend console + server log
3. **Analyze**: Compare events_sent vs chunks_received
4. **Decide**: Which layer has the problem?
5. **Fix**: Targeted solution based on data
6. **Verify**: Confirm fix works

## Success Criteria
- [ ] 100% of backend events reach frontend
- [ ] No content loss regardless of size
- [ ] Performance acceptable (<100ms overhead)
- [ ] Works reliably for responses up to 100KB
