# Delegate System — Implementation Status

**Last updated:** Round 8 — swarm stability pass (stall prevention, resilience, fidelity)

## Architecture

```
User sends complex task → Orchestrator decomposes → Launch Delegates button
→ DelegateManager.launch_plan() creates:
  - TaskPlan folder (ChatGroup with taskPlan metadata + source_conversation_id)
  - Orchestrator conversation (command center)
  - Per-delegate conversations with scoped Contexts
  - Shared SwarmTask list seeded from delegate specs
→ Delegates run in parallel (asyncio + semaphore cap)
→ Each delegate has 8 swarm coordination tools
→ Tool invocations (tool_start, tool_display) accumulated into delegate message
→ Crystals cascade: D1 completes → D2 unblocks → D3 unblocks
→ Progressive checkpoints every ~4000 chars for crash recovery
→ On stream failure: self-rescue spawns continuation delegate
→ Orchestrator analyzes each crystal (awaited, not fire-and-forget)
→ Orchestrator uses direct LLM calls (not routed through compaction)
→ Completion message posted to source conversation
→ Server restart: rehydrate() rebuilds from disk
→ Sidebar: TaskPlan folders nest under source conversation
```

## Sidebar Tree Structure

TaskPlan folders are reparented under the conversation that spawned them:

```
💬 Design discussion about auth          ← expandable conversation
  📁 ⚡ Auth → OAuth2 Refactor [3/4 ✓]  ← TaskPlan folder (child of conversation)
    💬 🎯 Orchestrator
    💬 💎 D1: OAuth Provider  ✓
    💬 🔵 D2: Token Mgmt      ⟳
    💬 💎 D3: Test Suite      ✓
    💬 ⏳ D4: Documentation
💬 Another conversation                   ← leaf (no children)
```

This is driven by `taskPlan.source_conversation_id` on the folder matching
a conversation ID. Source conversations auto-expand on initial load and
when navigating to any delegate within the plan.

## Components

| File | Lines | Role |
|------|-------|------|
| `app/agents/delegate_manager.py` | ~1460 | Core orchestration engine |
| `app/agents/swarm_tools.py` | ~425 | 8 delegate-facing coordination tools |
| `app/agents/compaction_engine.py` | ~499 | Phase A deterministic + Phase B LLM crystal extraction |
| `app/agents/delegate_stream_relay.py` | ~83 | WebSocket broadcast relay (per-delegate + sibling) |
| `app/models/delegate.py` | ~150 | Data models (TaskPlan, DelegateSpec, MemoryCrystal, SwarmTask) |
| `app/api/delegates.py` | ~265 | HTTP routes: launch, status, cancel, retry, promote |
| `frontend/src/hooks/useDelegatePolling.ts` | ~211 | Status polling + cross-tab broadcast |
| `frontend/src/hooks/useDelegateStreaming.ts` | ~174 | WebSocket live stream relay |
| `frontend/src/types/delegate.ts` | ~85 | TypeScript types mirroring Python models |
| `frontend/src/components/DelegateLaunchButton.tsx` | ~307 | Parse + launch UI |
| `frontend/src/components/MUIChatHistory.tsx` | modified | Tree reparenting + conversation-as-parent |

## Swarm Coordination Tools

All 9 tools are injected into delegate streams via `extra_tools` on `stream_with_tools`:

| Tool | Purpose | Locking |
|------|---------|---------|
| `swarm_task_list` | View all tasks with status | Read-only |
| `swarm_claim_task` | Claim a task to prevent duplicate work | RLock |
| `swarm_complete_task` | Mark done with summary | RLock |
| `swarm_add_task` | Register discovered subtask | RLock |
| `swarm_note` | Broadcast note to orchestrator | None (append-only) |
| `swarm_query_crystal` | Read sibling delegate crystals | Read-only |
| `swarm_read_log` | Read recent orchestrator messages | Read-only |
| `swarm_request_delegate` | Dynamically spawn a new delegate | RLock |
| `swarm_launch_subplan` | Launch a full recursive sub-swarm | RLock (async spawn) |

## Recursive Sub-Swarms

Delegates can spawn their own sub-swarms via `swarm_launch_subplan`. This enables
arbitrary nesting depth (Plan A → Plan B → Plan C → ...).

**Data model:** `TaskPlan.parent_plan_id` + `TaskPlan.parent_delegate_id` track lineage.

**Crystal rollup:** When a sub-plan completes, `_on_subplan_complete()`:
- Adds a `[subplan]`-tagged entry to the parent plan's shared task list
- Posts a summary to the parent plan's orchestrator conversation
- Posts results directly to the spawning delegate's conversation
- Clears `pending_subplan_ids` on parent plan
- Re-checks `_is_plan_complete` — if all delegates AND sub-plans done, finalizes parent
- Recurses upward for arbitrarily nested plans

**Pending subplan blocking:** `_is_plan_complete()` returns `False` while
`pending_subplan_ids` is non-empty, preventing premature parent finalization.

**Sidebar:** Sub-plan folders nest under the spawning delegate's conversation
via `source_conversation_id` → existing reparenting code.

## Error Recovery

### Resilience Features

| Feature | Trigger | Guard |
|---------|---------|-------|
| **Progressive checkpointing** | Every ~4000 chars of accumulated content | Content-volume, not time-based |
| **Self-rescue** | Actual stream exception/disconnect | Skipped if delegate has active sub-plans; max 1 retry |
| **Stall watchdog** | >10 min silence in `get_delegate_status` | Skipped if delegate has active sub-plans or recent checkpoint |

Self-rescue builds a continuation prompt with the last 3000 chars of prior work,
reuses the same conversation/delegate ID, and launches via `_run_delegate`.

### Error Handling Matrix

| Scenario | Handling |
|----------|----------|
| Delegate fails | Status → `failed`, downstream stays `proposed`, plan shows `needs_attention` |
| Server restarts | `rehydrate()` rebuilds from disk, running → `interrupted`, completed_partial skipped |
| Retry failed delegate | `retry_delegate` resets to `proposed`, cascades downstream reset |
| Unblock manually | `promote_to_stub_crystal` creates stub, triggers cascade |
| Plan completes with failures | Status → `completed_partial`, message shows ⚠️ with failure count |
| Concurrent mutations | `threading.RLock` on all task list writes and file persistence |
| Orphaned TaskPlan folders | Stay at normal tree position if source_conversation_id missing |
| Stream crash with checkpoint | Self-rescue spawns continuation delegate |
| Stream crash without checkpoint | Falls through to `on_delegate_failed` |
| Delegate silent, no children | Stall watchdog marks `stalled` after 10 min |
| Delegate silent, has children | Left alone — children are working |

## Frontend UX

| Feature | Status |
|---------|--------|
| Source conversations auto-expand | ✅ on initial load + on navigation |
| Progress badges (3/4 ✓) | ✅ via delegate child counting |
| Delegate status icons (💎/🔵/⏳) | ✅ via delegateMeta.status |
| `completed_partial` in polling | ✅ stops polling, updates folder status |
| `needs_attention` in status response | ✅ lists failed/interrupted delegate IDs |
| New chat blocked in TaskPlan folders | ✅ via ChatContext guard |
| Orchestrator message dedup | ✅ backend is sole persistence authority |
| Streaming spinner | ✅ driven solely by `streamingConversations` (live WebSocket data) |
| Sibling delegate streaming | ✅ background WebSockets for all running delegates in plan |
| `hasUnreadResponse` on crystal | ✅ green indicator when delegate completes |
| Eager message fetch on crystal | ✅ `syncApi.getChat()` on status transition |
| `completed_partial` in polling | ✅ triggers source refresh + stops polling |
| Synthesis in source rollup | ✅ orchestrator synthesis appended to completion message |

## Test Coverage

| Test File | Count | Scope |
|-----------|-------|-------|
| `test_delegate_manager.py` | 18 | Core launch, crystal, cancel, dependency resolution |
| `test_swarm_tools.py` | 25 | Task CRUD, locking, concurrency |
| `test_delegate_lifecycle.py` | 21 | Retry, promote, completion states, cascade |
| `test_delegate_resilience.py` | 19 | Checkpointing, self-rescue, stall watchdog, synthesis rollup |
| `test_delegate_models.py` | 18 | Pydantic serialization, backward compat |
| `test_delegate_api_models.py` | 10 | API request/response models |
| `test_compaction_engine.py` | 32 | Phase A/B extraction, LLM fallback |
| `test_compaction_hook.py` | 8 | Auto-compaction hook in streaming |
| `test_delegate_comprehensive.py` | 18 | Rehydration, cascade, dynamic delegates, concurrency stress |
| `test_recursive_swarms.py` | 14 | Parent linkage, crystal rollup, 3-level nesting |
| **Total** | **185** | All passing |

## Swarm Stability Fixes (Round 8)

Changes made in the stability pass, listed by root cause:

| Symptom | Root Cause | Fix |
|---------|------------|-----|
| Spinner + checkmark simultaneously | `isStreamingConv` fell back to `delegateStatus === 'running'` | Removed; spinner driven only by live WebSocket |
| Clicking delegate shows no content | Polling updated status but never fetched messages | Eager `syncApi.getChat()` on crystal transition |
| No indicator that delegate finished | `hasUnreadResponse` only set for failed/interrupted | Now set for `crystal` too |
| Only one delegate streamed live | `useDelegateStreaming` only opened WS for viewed conversation | Sibling WebSocket connections for all running delegates |
| Orchestrator raced past crystals | `create_task` fire-and-forget for crystal receipt | Changed to `await` |
| 6+ delegate swarms stalling | Semaphore held during post-stream orchestrator LLM calls | Semaphore narrowed to streaming loop only |
| Delegate tool output invisible | `tool_start`/`tool_display` forwarded to WS but not persisted | Now accumulated as markdown in assistant message |
| Crystal summaries ~50 tokens | `_call_summary_model` capped at 2-3 sentences | Analysis delegates get 8-12 sentences, 3000-char cap |
| Orchestrator analysis truncated | Routed through compaction engine (800-char input, 500-char output) | Direct LLM call with 4000-char cap |
| Source conversation got thin table | `_post_completion_to_source` excluded synthesis | Synthesis text appended |
| `completed_partial` never refreshed source | Frontend only checked `completed` and `cancelled` | Added `completed_partial` to condition |
| Nested swarm results dropped | Parent finalized before sub-plans completed | `pending_subplan_ids` blocks `_is_plan_complete` |
| No crash recovery | Stream death = permanent failure | Progressive checkpointing + self-rescue continuation |
| Swarm hangs indefinitely | No stall detection | Watchdog flags silence >10min with no children |
