# Delegate Completion → Source Conversation Notification

## Problem

When a user decomposes a task in conversation X, the delegates run in
their own folder.  Conversation X has no knowledge that the plan
completed, which crystals were produced, or what failed.  The user
must manually navigate to the ⚡ folder, click each delegate, and
piece together the results.

## Current State

- `DelegateLaunchButton` receives `conversationId` (the source) as a prop,
  but **never sends it to the server** — the `POST .../launch-delegates`
  request has no `source_conversation_id` field.
- `DelegateManager._emit("plan_completed", ...)` fires a callback, but
  **no callback is ever registered** — `launch_plan()` is called without
  `on_progress`.
- `useDelegatePolling` updates sidebar icons but has **no mechanism to
  inject a message** into the source conversation.
- The `TaskPlan` model has no field tracking which conversation spawned it.

## Design: Minimum Viable Notification

### 1. Track the source conversation

Add `source_conversation_id` to `TaskPlan` and propagate it:

- `LaunchDelegatesRequest` gets a new optional `source_conversation_id: str`
- `DelegateLaunchButton` sends `conversationId` in the POST body
- `DelegateManager.launch_plan()` stores it on the TaskPlan

### 2. Post a completion message to the source conversation

When `_is_plan_complete()` fires in `on_crystal_ready()`:

- Build a synthetic assistant message summarizing the plan outcome
- Use `ChatStorage.add_message()` to persist it to the source conversation
- The message includes: plan name, per-delegate status, crystal summaries,
  file change list, and a link/anchor to the TaskPlan folder

### 3. Frontend notification

Two complementary paths:

**a) Polling path (existing):** `useDelegatePolling` already detects
`data.status === 'completed'`.  Extend it to:
- Look up the source conversation from the folder's `taskPlan`
- Inject a notification into that conversation's message list
- Mark `hasUnreadResponse: true` if the user is in a different conversation

**b) Direct persistence path (new):** Since the server writes the message
in step 2, the next `syncWithServer` poll (or immediate broadcast) picks
it up.  The frontend simply needs to re-fetch the source conversation.

### 4. Message format

The completion message should be structured markdown that the frontend
can render as a rich summary card:

```markdown
## ✅ Task Plan Complete: {plan_name}

**{crystal_count}/{total_delegates}** delegates completed successfully.

| Delegate | Status | Files Changed |
|----------|--------|--------------|
| 🔧 Auth Core | ✅ crystal | `src/auth/token.ts` (+48 -12) |
| 📦 Tests | ✅ crystal | `tests/auth.test.ts` (new, 245 lines) |

**Key decisions across delegates:**
- Selected JWT over session tokens for stateless auth
- Used middleware pattern for token validation

<details>
<summary>Crystal summaries</summary>

**Auth Core:** Implemented token validation with...
**Tests:** Created comprehensive test suite covering...
</details>
```

## Files to Change

| File | Change |
|------|--------|
| `app/models/delegate.py` | Add `source_conversation_id` to `TaskPlan` |
| `app/api/delegates.py` | Accept `source_conversation_id` in request |
| `app/agents/delegate_manager.py` | Store source ID, write completion message |
| `frontend/src/components/DelegateLaunchButton.tsx` | Send `conversationId` |
| `frontend/src/hooks/useDelegatePolling.ts` | Trigger source conv refresh |
