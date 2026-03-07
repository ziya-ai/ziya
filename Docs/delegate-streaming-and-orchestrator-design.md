# Delegate Live Streaming & Active Orchestrator Design

## Overview

Two major capabilities:
1. **WebSocket relay** — live-streams delegate chunks to the frontend so clicking a delegate conversation shows real-time streaming identical to a normal chat
2. **Active orchestrator** — the orchestrator conversation becomes a real LLM participant that receives crystals, analyzes them, directs follow-up work, and logs all communication with source→dest labels

## Architecture

### WebSocket Relay

```
DelegateManager._run_delegate()
    ↓ yields chunk
    ↓ pushes to DelegateStreamRelay (new)
        ↓ active_delegate_connections[conversation_id] → WebSocket
            ↓ frontend useDelegateStreaming hook
                ↓ streamedContentMap.set(convId, content)
                    ↓ Conversation.tsx renders live
```

**Backend (`app/agents/delegate_stream_relay.py` — new file):**
- Module-level dict: `active_connections: Dict[str, List[WebSocket]]`
- `connect(conversation_id, ws)` / `disconnect(conversation_id, ws)`
- `async push(conversation_id, chunk)` — broadcasts to all connected clients
- Called from `_run_delegate()` on every chunk

**Backend (`app/server.py` — new WS endpoint):**
- `@app.websocket("/ws/delegate-stream/{conversation_id}")`
- Registers connection in relay, keeps alive, cleans up on disconnect

**Frontend (`frontend/src/hooks/useDelegateStreaming.ts` — new file):**
- Hook that connects a WebSocket when viewing a delegate conversation
- Feeds chunks into `streamedContentMap` / `streamingConversations`
- Disconnects when navigating away

### Active Orchestrator

On each `on_crystal_ready`:
1. Format crystal as an orchestrator message: `**task-name → orchestrator:** summary`
2. Persist to orchestrator conversation
3. Run orchestrator LLM turn with all crystals so far
4. Parse orchestrator response for follow-up directives
5. Persist orchestrator analysis: `**orchestrator → task-name:** directive`
6. If directive says "request rework", update delegate status

When plan completes:
1. Orchestrator gets final LLM turn with all crystals
2. Produces synthesis summary
3. Posts to source conversation

### Message Label Format

All orchestrator conversation messages use explicit routing labels:

```
**orchestrator → all:** Launching 7 delegates for Ziya Feature Inventory
**backend-core → orchestrator:** [Crystal] Scanned 15 route files, 7 middleware...
**orchestrator → backend-core:** Accepted. Good coverage of middleware layer.
**orchestrator → frontend-core:** Accepted with note: missing D3Renderer docs.
**orchestrator → source:** ✅ Task Plan Complete: 7/7 delegates succeeded.
```
