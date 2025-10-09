# Session Resumption Protocol Design

**Status**: BACKLOG - Future enhancement
**Priority**: Medium
**Effort**: Large (multi-phase implementation)

## Current Status
- Partial save on abort implemented (prevents data loss)
- Full resumption protocol designed but not implemented
- Will revisit after core streaming issues resolved

---

## Concept
When frontend loses connection during streaming, backend keeps session state alive for a grace period. Frontend can reconnect and resume from where it left off.

## Backend State Management

### Session State to Preserve
```python
active_sessions = {
    'conversation_id': {
        'content_so_far': str,           # All content generated
        'last_activity': timestamp,       # For cleanup
        'stream_position': int,           # Chunk/event number
        'is_complete': bool,              # Did stream finish?
        'expires_at': timestamp,          # Grace period (30s)
    }
}
```

### Backend Changes Needed

1. **Store session state** when streaming starts
2. **Update state** as chunks are sent
3. **Keep alive** for 30 seconds after disconnect
4. **New endpoint**: `POST /api/chat/resume`
   - Input: `conversation_id`, `last_position`
   - Output: Resume stream from that position OR full content if complete

## Frontend Reconnection Logic

### On Disconnect
```typescript
1. Save: conversation_id, last_chunk_received, currentContent
2. Wait 2 seconds (allow for quick recovery)
3. Attempt resume: POST /api/chat/resume
4. If success: Continue streaming from last position
5. If fail: Fall back to current behavior (save partial)
```

### Resume Request
```typescript
{
  conversation_id: string,
  last_position: number,        // Last chunk we received
  content_hash: string          // Hash of content we have (verify sync)
}
```

### Resume Response
```typescript
// Case 1: Stream still active, resume
{
  status: 'resuming',
  from_position: number,
  stream_url: string            // Continue streaming
}

// Case 2: Stream completed while disconnected
{
  status: 'completed',
  full_content: string,         // Send everything
  missed_chunks: number
}

// Case 3: Session expired
{
  status: 'expired',
  message: 'Session expired, please retry'
}
```

## Implementation Plan

### Phase 1: Backend Session Storage
- [ ] Add session state dict in server.py
- [ ] Store state when streaming starts
- [ ] Update state as chunks sent
- [ ] Cleanup expired sessions (background task)

### Phase 2: Resume Endpoint
- [ ] Create `/api/chat/resume` endpoint
- [ ] Check if session exists and valid
- [ ] Return appropriate response
- [ ] Resume streaming from position

### Phase 3: Frontend Reconnection
- [ ] Detect disconnect (already done)
- [ ] Wait grace period (2s)
- [ ] Attempt resume request
- [ ] Handle resume/completed/expired cases
- [ ] Fall back to save partial if resume fails

### Phase 4: Testing
- [ ] Test normal disconnect/reconnect
- [ ] Test session expiration
- [ ] Test completed-while-disconnected
- [ ] Test multiple rapid disconnects

## Benefits
- Resilient to network blips
- No content loss
- Better UX (seamless recovery)
- Handles slow connections gracefully

## Edge Cases
- Multiple tabs with same conversation
- Server restart during grace period
- Client never reconnects (cleanup needed)
- Race condition: client reconnects while still streaming
