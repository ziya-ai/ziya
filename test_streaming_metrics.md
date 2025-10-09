# Streaming Metrics Test

## Test Procedure

### 1. Start Server
```bash
cd /Users/dcohn/workplace/ziya-release-debug
python -m app.main
```

### 2. Open Browser
Navigate to: http://localhost:6969

### 3. Make Test Request
In the chat interface, send:
```
create 10+ detailed visualizations summarizing this project structure
```

### 4. Collect Metrics

#### From Browser Console (F12):
Look for these log entries:
- `📊 Streaming metrics:` (every 100 chunks)
- `📊 Final streaming metrics:` (at end)

Record:
- `total_chunks`
- `total_bytes`
- `content_length`
- `chunks_under_10`

#### From Server Log:
```bash
tail -f server.log | grep "📊"
```

Record:
- `events_sent`
- `bytes_sent`
- `avg_size`

### 5. Compare Results

Calculate:
- **Loss Rate**: `(events_sent - total_chunks) / events_sent * 100`%
- **Content Efficiency**: `content_length / total_bytes * 100`%
- **Fragmentation**: `chunks_under_10 / total_chunks * 100`%

### Expected Findings

**If working correctly:**
- events_sent ≈ total_chunks (within 1-2%)
- content_length ≈ total_bytes (within 10%)
- chunks_under_10 < 5%

**If issue present:**
- events_sent >> total_chunks (significant loss)
- content_length << total_bytes (content missing)
- chunks_under_10 > 20% (high fragmentation)

### 6. Analyze Divergence Point

If loss detected:
1. Note the chunk number where metrics stop updating
2. Check browser network tab for connection status
3. Look for JavaScript errors in console
4. Check if React component stopped rendering

## Next Steps Based on Results

### If Backend Sends All, Frontend Receives All
→ Issue is in React state/rendering layer
→ Proceed to Phase 2: Test C (State accumulation)

### If Backend Sends All, Frontend Receives Partial
→ Issue is in network or parsing layer
→ Proceed to Phase 2: Test A/B (Network/Parser)

### If Backend Stops Sending Early
→ Issue is in backend streaming logic
→ Check for exceptions, timeouts, or buffer limits
