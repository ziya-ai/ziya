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

## Root Cause Analysis

### Bug Found in `_update_code_block_tracker`
The code block tracker was checking if closing ` ``` ` had the **same type** as opening:
```python
if new_block_type == tracker['block_type']:  # BUG!
    tracker['in_block'] = False
```

**Problem**: Closing ` ``` ` often has no type, defaults to `'code'`, which doesn't match `'vega-lite'` or `'mermaid'`.

**Result**:
1. Opens: ` ```vega-lite ` → `in_block=True`, `block_type='vega-lite'`
2. Closes: ` ``` ` → `new_block_type='code'` (default)
3. `'code' != 'vega-lite'` → Thinks it's a NEW block!
4. Triggers continuation to "complete" the "incomplete" block
5. Model re-generates content → duplicates

### Fix Applied
Changed logic to: **any ` ``` ` closes the current block**, regardless of type.

## Testing Required

### Test Case
Run the same request that caused duplication:
```
"create 10+ detailed visualizations summarizing this project structure"
```

### Success Criteria
- [ ] No duplicate visualizations in output
- [ ] No duplicate conclusion text
- [ ] Content appears in linear order (9, 10, 11, 12, 13)
- [ ] Server log shows single `STREAM_END` with no continuation
- [ ] No `INCOMPLETE_BLOCK` or `UNCLOSED_BLOCK` warnings

### If Test Fails
- Hypothesis was wrong
- Need to investigate other causes:
  - Model itself repeating
  - Iteration logic issue
  - Buffer flushing problem

## Current Status
- ⏸️ Paused Phase 1 metrics collection
- 🔧 Fix applied but NOT verified
- 🧪 Ready for testing
- Will resume Phase 1 after verification
