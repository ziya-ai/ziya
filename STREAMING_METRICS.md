# Streaming Metrics Collection

## Instrumentation Points

### Frontend (chatApi.ts - readStream function)

Add before the while loop:
```typescript
const metrics = {
    chunks_received: 0,
    bytes_received: 0,
    events_parsed: 0,
    parse_errors: 0,
    chunk_sizes: [] as number[],
    timestamps: [] as number[],
    start_time: Date.now()
};
```

Inside the loop after decode:
```typescript
metrics.chunks_received++;
metrics.bytes_received += chunk.length;
metrics.chunk_sizes.push(chunk.length);
metrics.timestamps.push(Date.now() - metrics.start_time);

if (metrics.chunks_received % 100 === 0) {
    console.log('📊 Streaming metrics:', {
        chunks: metrics.chunks_received,
        bytes: metrics.bytes_received,
        avg_chunk: (metrics.bytes_received / metrics.chunks_received).toFixed(2),
        elapsed: metrics.timestamps[metrics.timestamps.length - 1]
    });
}
```

After stream completes:
```typescript
console.log('📊 Final streaming metrics:', {
    total_chunks: metrics.chunks_received,
    total_bytes: metrics.bytes_received,
    avg_chunk_size: (metrics.bytes_received / metrics.chunks_received).toFixed(2),
    min_chunk: Math.min(...metrics.chunk_sizes),
    max_chunk: Math.max(...metrics.chunk_sizes),
    chunks_under_10: metrics.chunk_sizes.filter(s => s < 10).length,
    duration_ms: Date.now() - metrics.start_time,
    content_length: currentContent.length
});
```

### Backend (streaming_tool_executor.py)

Add at start of streaming:
```python
self.stream_metrics = {
    'events_sent': 0,
    'bytes_sent': 0,
    'chunk_sizes': [],
    'start_time': time.time()
}
```

Before each yield:
```python
chunk_size = len(json.dumps(event_data))
self.stream_metrics['events_sent'] += 1
self.stream_metrics['bytes_sent'] += chunk_size
self.stream_metrics['chunk_sizes'].append(chunk_size)

if self.stream_metrics['events_sent'] % 100 == 0:
    logger.info(f"📊 Stream metrics: {self.stream_metrics['events_sent']} events, "
                f"{self.stream_metrics['bytes_sent']} bytes, "
                f"avg={self.stream_metrics['bytes_sent']/self.stream_metrics['events_sent']:.2f}")
```

At end:
```python
logger.info(f"📊 Final stream metrics: {self.stream_metrics}")
```

## Test Procedure

1. Add instrumentation to both files
2. Start server with: `python -m app.main`
3. Make request: "create 10+ detailed visualizations"
4. Collect logs from:
   - Browser console (frontend metrics)
   - server.log (backend metrics)
5. Compare:
   - events_sent vs chunks_received
   - bytes_sent vs bytes_received
   - Identify divergence point

## Expected Findings

If backend sends 1000 events but frontend receives 600:
- **Network layer issue** - check browser network tab

If frontend receives 1000 chunks but only 600 parsed:
- **Parser issue** - check parse_errors count

If 1000 parsed but content_length doesn't match:
- **State accumulation issue** - check React state updates
