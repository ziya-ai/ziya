# Delegate System — Three Bug Analysis

## Bug 1: Delegate Conversations Are Always Empty

### Symptom
Clicking on a delegate conversation (running or completed) shows an empty chat. No messages are visible despite the delegate having executed successfully (status transitions to crystal with green checkmark).

### Root Cause
**`DelegateManager._run_delegate()` never persists messages to the chat's JSON file.**

The execution flow is:

1. `_run_delegate()` calls `_create_delegate_stream()` which creates a `StreamingToolExecutor`
2. The stream yields `text` chunks, which get accumulated into a local `accumulated` string variable (line ~320 of delegate_manager.py)
3. When the stream ends, a `MemoryCrystal` is created from the accumulated text
4. **Neither the user message (the scope/task prompt) nor the assistant response (accumulated text) are ever written to `ChatStorage`**

Compare this to the normal chat flow:
- Frontend sends a message via `chatApi.ts`
- Frontend's `addMessageToConversation` persists both the user question and streamed response to IndexedDB
- `queueSave` syncs to the server

For delegates, the entire conversation happens server-side. The `_build_delegate_messages()` method constructs a user message, and the LLM streams a response, but neither is persisted via `ChatStorage.add_message()`.

When the frontend tries to display the conversation:
- `ChatContext.tsx` line ~1256 calls `syncApi.getChat(pid, conversationId)` to fetch server-side messages
- The server returns the chat JSON, which has `messages: []` (empty) because nothing was ever written
- The UI renders an empty conversation

### Fix Location
`app/agents/delegate_manager.py` — `_run_delegate()` method.

After building messages and after stream completion, persist both the user message and the accumulated assistant response:

```python
# In _run_delegate(), after messages are built:
chat_storage = self._get_chat_storage()
from app.models.chat import Message
user_msg = Message(role="human", content=messages[0]["content"])
chat_storage.add_message(spec.conversation_id, user_msg)

# After stream completes (after the for-loop over chunks):
if accumulated:
    assistant_msg = Message(role="assistant", content=accumulated)
    chat_storage.add_message(spec.conversation_id, assistant_msg)
```

---

## Bug 2: "New Conversation" Creates Standard Chat Inside TaskPlan Folder

### Symptom
When viewing a delegate conversation (which sets `currentFolderId` to the TaskPlan's folder), pressing the "+" / New Conversation button creates a regular chat inside the decomposition folder. This should not be allowed — TaskPlan folders are managed exclusively by the delegate system.

### Root Cause
`startNewChat` in `ChatContext.tsx` (line ~987) uses `currentFolderId` as the target folder for new conversations:

```typescript
const targetFolderId = specificFolderId !== undefined ? specificFolderId : currentFolderId;
```

There is **no check** for whether `currentFolderId` belongs to a TaskPlan folder. The function has zero awareness of the delegate/TaskPlan concept.

When the user is viewing a delegate conversation:
1. `switchConversation` sets `currentFolderId` to the delegate's folder (the TaskPlan folder)
2. User clicks "New Conversation"
3. `startNewChat` creates a new regular conversation with `folderId` = the TaskPlan folder
4. A standard chat appears nested under the decomposition header

### Fix Location
`frontend/src/context/ChatContext.tsx` — `startNewChat` function.

Before creating the conversation, check if the target folder is a TaskPlan folder. If so, fall back to `null` (root level):

```typescript
const startNewChat = useCallback(async (specificFolderId?: string | null) => {
    // ...existing recovery logic...

    let targetFolderId = specificFolderId !== undefined ? specificFolderId : currentFolderId;
    
    // Don't create regular conversations inside TaskPlan folders
    if (targetFolderId) {
        const folder = folders.find(f => f.id === targetFolderId);
        if (folder?.taskPlan) {
            targetFolderId = null;
        }
    }
    // ...rest of function...
```

This requires `folders` to be in the dependency array or available via ref.

---

## Bug 3: All Output Appears Twice After Stop Button

### Symptom
After pressing Stop during a streaming response, the entire conversation output (including the delegate launch dialog) appears duplicated.

### Root Cause
This is a race condition in the abort handling in `chatApi.ts`. When the user aborts:

1. The `AbortController.signal` fires, triggering the abort event listener which captures `currentContent` and calls `addMessageToConversation`
2. The stream reader's `finally` block (or catch of the AbortError) also processes the accumulated content and persists it

Both paths independently save the same accumulated content, resulting in the message appearing twice in the conversation.

### Note
This bug was identified in the previous conversation turn and is separate from the delegate-specific issues. The fix needs to ensure the abort listener sets a flag that prevents the stream completion path from also saving.

---

## Summary Table

| Bug | Layer | File | Severity |
|-----|-------|------|----------|
| Empty delegate conversations | Backend | `app/agents/delegate_manager.py` | P0 — delegates are non-functional from user's perspective |
| New chat in TaskPlan folder | Frontend | `frontend/src/context/ChatContext.tsx` | P1 — data integrity; orphan chats in managed folders |
| Double output on Stop | Frontend | `frontend/src/apis/chatApi.ts` | P2 — cosmetic but confusing |
