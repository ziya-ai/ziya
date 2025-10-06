# Detour: Backend Content Duplication Issue

## Discovery
While instrumenting for Phase 1 metrics, discovered a **backend-only issue** visible in server logs:
- Content appears out of order (viz #10, #11, #10 again, #11 again)
- Duplicate content (conclusion appears twice verbatim)
- Model continues generating after `STREAM_END` logged

## Evidence from Server Log
```
## 9. Data Flow... [starts]
## 10. Code Quality... [appears]
## 11. API Endpoint... [appears]
## 10. Code Quality... [DUPLICATE - same malformed JSON]
## 11. API Endpoint... [DUPLICATE]
## 12. Testing... [appears]
## 13. Security... [appears]
[conclusion text]
[conclusion text DUPLICATE]
```

## Why This Matters
This is **not** a frontend issue - it's happening in the backend before data even leaves the server. Must fix this before continuing with frontend streaming investigation.

## Hypothesis
The iteration/continuation logic in `streaming_tool_executor.py` is:
1. Not properly detecting when model is done
2. Continuing iterations when it shouldn't
3. Re-sending already-generated content

## Investigation Plan
1. Check iteration loop termination conditions
2. Check `message_stop` handling
3. Check `stream_end` logic
4. Look for duplicate content buffering/flushing

## Current Status
- Paused Phase 1 metrics collection
- Investigating backend iteration logic
- Will resume Phase 1 after fixing duplication
