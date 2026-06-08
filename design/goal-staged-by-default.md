# Staged-by-default goal cards

**Status:** ready to implement
**Related:** `design/task-cards.md`, `app/api/commands.py`, `app/models/task_binding.py`

## Motivation

Today `/goal <text>` synthesizes a card and immediately launches a run.
Under default-no-trust this is wrong: the user has no chance to inspect
the synthesized card or grant scoped permissions before the agent starts
working (and starts hitting approval prompts mid-iteration).

Goal: `/goal` produces a *staged* card bound inline in the chat, with a
**Run** affordance the user clicks once they're satisfied. Iteration
runs are unchanged after launch.

## Design

### Binding model: run_id becomes optional

A `TaskBinding` currently requires `run_id`. Loosen that: a binding
created from a staged goal has `run_id = None` until launch.

```python
class TaskBinding(BaseModel):
    id: str = ""
    chat_id: str
    card_id: str
    run_id: Optional[str] = None      # was: required
    anchor_message_id: Optional[str] = None
    created_at: int = 0
```

This keeps the binding as the single anchor for all states of a goal —
"proposed", "running", "done" — without inventing a parallel
"proposed-card-anchor" type.

### Server: split `_goal_create`

`/goal` flow becomes:

1. Synthesize the card (`source="goal"`, unchanged).
2. Create a binding with `run_id=None`, anchored to the user's `/goal`
   message id.
3. Return `goal_staged` (not `goal_launched`).

A new endpoint `POST /api/v1/projects/{pid}/task-bindings/{bid}/launch`
performs the deferred launch:

1. Look up binding; reject if `run_id` is already set (one-shot).
2. Call `_launch_run_for_card(...)` with the binding's card.
3. Update the binding with the resulting `run_id`.
4. Return the run.

Existing `/task-cards/{cid}/launch` stays as-is for the library path
(reusable cards launched without a chat anchor).

### Frontend: staged-mode rendering in `TaskCardInlineTile`

`TaskCardInlineTile` currently assumes `binding.run_id` is set and
hands it to `useTaskRunStream`. New branch:

```tsx
if (!binding.run_id) {
  return <StagedCardTile binding={binding} card={card} onLaunch={...} />;
}
// existing running/terminal rendering unchanged
```

`StagedCardTile` shows:
- Card title + synthesized instructions (collapsible)
- Iteration cap (read-only for now; editable as a follow-up)
- A permissions summary (read-only for now; editable as a follow-up)
- **Run** button → calls the new launch endpoint, then dispatches
  `task-binding-created` so the parent `useTaskBindings` re-fetches
  and re-renders this tile in running mode.
- **Discard** button → deletes the binding (existing endpoint), card
  is left orphaned in the library (consistent with current behavior
  of unused synthesized cards).

### Slash-command response shape

`commandApi` response gains `goal_staged`:

```ts
{ type: 'goal_staged', message: '🎯 Goal staged: <text>', data: { binding_id, card_id } }
```

`SendChatContainer` already dispatches `task-binding-created` when
`result.data.binding_id` is set (from the prior fix), so the staged
tile will appear inline without further changes.

### Auto-run opt-in

Two reasonable mechanisms; pick one:

**(a) Per-invocation:** `/goal!` (bang) means "stage and immediately
launch". Maps to current behavior.

**(b) Config:** `goal.auto_run: false` in user config. Power users
can flip globally.

Recommendation: **(b)**. `/goal` semantics stay stable; users who
trust their setup set the flag once. `/goal!` can be added later
without breaking anything.

## Migration

- Existing bindings all have `run_id` set; the optional field is
  backward-compatible.
- Existing card launch path (button in library, etc.) unchanged.
- The only behavior change is `/goal` not auto-launching.

## Out of scope (follow-ups)

- Editing iteration cap / permissions in the staged tile UI.
- Task-scoped permission grants surfaced as in-run prompts (separate
  doc, depends on this one shipping first).
- `iteration_status` field on iteration results for clean exit
  conditions (separate doc).
- Convergence backstop (similarity over iteration summaries).

## Test plan

1. `/goal add a docstring to X` → tile appears in chat, **not running**.
   No task run record created.
2. Click **Run** on the staged tile → run is created, tile transitions
   to running mode, iterations stream normally.
3. `/goal status` on a staged-but-not-launched goal → reports the
   staged state (needs `_goal_status` to handle `run_id=None`).
4. `/goal pause` / `/goal clear` on a staged goal → clear deletes the
   binding (and orphans the card); pause is a no-op with a clear
   message ("nothing to pause; goal is staged").
5. Discard from the tile → binding gone, card orphaned, no errors.
6. Reload chat → staged tile re-renders correctly from persisted
   binding.

## Code changes (concrete)

### Backend

```diff
--- a/app/models/task_binding.py
+++ b/app/models/task_binding.py
@@ -27,7 +27,9 @@ class TaskBinding(BaseModel):
     id: str = ""
     chat_id: str
     card_id: str
-    run_id: str
+    # Optional: a binding may exist for a staged goal whose run hasn't
+    # been launched yet.  Populated by the deferred-launch endpoint.
+    run_id: Optional[str] = None
     # The message this binding is anchored after.  Null when the
     # anchor was removed (message deleted); renderers should show
     # such bindings at the top of the chat with an 'orphaned' flag.
```

```diff
--- a/app/api/commands.py
+++ b/app/api/commands.py
@@ -103,7 +103,6 @@ async def _goal_create(body: CommandRequest, request: Request) -> CommandRespons
     """Synthesize a task card from goal text and launch it."""
     from ..utils.goal_synthesis import synthesize_goal_card
-    from .task_cards import _launch_run_for_card

     goal_text = body.args.strip()

@@ -129,30 +128,26 @@ async def _goal_create(body: CommandRequest, request: Request) -> CommandRespons
     saved_card = card_storage.create(card_create, source="goal")
     logger.info(f"🎯 GOAL: Synthesized card {saved_card.id[:8]} for: {goal_text[:60]}")

-    # Launch the run
-    run = await _launch_run_for_card(
-        project_id=project.id,
-        card_id=saved_card.id,
-        source_conversation_id=body.conversation_id,
-    )
-
-    # Bind to conversation if we have one
+    # Stage the card by creating a binding with no run.  The user
+    # clicks Run on the inline tile to actually launch.
     binding_id = None
     if body.conversation_id:
         binding_storage = TaskBindingStorage(project_dir)
         binding = binding_storage.create(
             chat_id=body.conversation_id,
             card_id=saved_card.id,
-            run_id=run.id,
+            run_id=None,
             anchor_message_id=body.anchor_message_id,
         )
         binding_id = binding.id
         logger.info(
-            f"🎯 GOAL: Bound to conversation {body.conversation_id[:8]} "
-            f"→ binding {binding.id[:8]}"
+            f"🎯 GOAL: Staged in conversation {body.conversation_id[:8]} "
+            f"→ binding {binding.id[:8]} (run not launched)"
         )

     return CommandResponse(
-        type="goal_launched",
-        message=f"🎯 Goal set: {goal_text}",
+        type="goal_staged",
+        message=f"🎯 Goal staged: {goal_text}\nReview the task card and click **Run** to start.",
         data={
             "card_id": saved_card.id,
-            "run_id": run.id,
             "binding_id": binding_id,
             "goal_text": goal_text,
         },
     )
```

New endpoint in `app/api/task_bindings.py`:

```python
@router.post("/{binding_id}/launch", response_model=TaskRun)
async def launch_staged_binding(
    project_id: str, chat_id: str, binding_id: str,
) -> TaskRun:
    """Launch the run for a staged binding (run_id was None)."""
    from .task_cards import _launch_run_for_card

    project_dir = get_project_dir(project_id)
    binding_storage = TaskBindingStorage(project_dir)
    binding = binding_storage.get(chat_id, binding_id)
    if not binding:
        raise HTTPException(status_code=404, detail="Binding not found")
    if binding.run_id:
        raise HTTPException(status_code=409, detail="Binding already launched")

    run = await _launch_run_for_card(
        project_id=project_id,
        card_id=binding.card_id,
        source_conversation_id=chat_id,
    )
    binding_storage.update_run_id(chat_id, binding.id, run.id)
    return run
```

**Storage helpers:** `update_run_id(chat_id, binding_id, new_run_id)`
already exists on `TaskBindingStorage` (see `app/storage/task_bindings.py`
L118). The `get(chat_id, binding_id)` lookup also exists. The new
endpoint can use both directly without further storage changes.

### Frontend

Add `launchStagedBinding` to `taskBindingApi.ts`:

```ts
export async function launchStagedBinding(
  projectId: string, chatId: string, bindingId: string,
): Promise<{ id: string; status: string }> {
  const res = await fetch(
    `${base(projectId, chatId)}/${encodeURIComponent(bindingId)}/launch`,
    { method: 'POST', headers: projectHeaders() },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`launchStagedBinding failed: ${res.status} ${text}`);
  }
  return res.json();
}
```

Add to `TaskCardInlineTile.tsx`:

```tsx
// near the top of the component, before useTaskRunStream
if (!binding.run_id) {
  return <StagedCardTile binding={binding} />;
}
```

New `StagedCardTile` component (sketch):

```tsx
const StagedCardTile: React.FC<{ binding: TaskBinding }> = ({ binding }) => {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? '';
  const [card, setCard] = useState<TaskCard | null>(null);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    taskCardApi.get(projectId, binding.card_id).then(setCard).catch(() => {});
  }, [projectId, binding.card_id]);

  const handleRun = async () => {
    if (!projectId || !binding.chat_id) return;
    setLaunching(true);
    setError(null);
    try {
      await launchStagedBinding(projectId, binding.chat_id, binding.id);
      window.dispatchEvent(new CustomEvent('task-binding-created'));
    } catch (e: any) {
      setError(String(e));
      setLaunching(false);
    }
  };

  return (
    <div className="task-card-inline-tile staged">
      <div className="header">
        🎯 <strong>{card?.name ?? 'Goal'}</strong>
        <span className="badge">staged</span>
      </div>
      {card && <pre className="instructions">{card.root.instructions}</pre>}
      <div className="actions">
        <Button type="primary" loading={launching} onClick={handleRun}>
          Run
        </Button>
        <Button onClick={() => deleteBinding(projectId, binding.chat_id, binding.id)}>
          Discard
        </Button>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
};
```

## Open questions

1. Should the staged tile let the user **edit the synthesized
   instructions** before launch? Probably yes eventually, but adds
   scope. Leave for follow-up; today's "Discard and re-`/goal`" is
   adequate.
2. What does `_goal_status` say for a staged binding? Probably
   `staged`, distinct from `running`/`done`. Add a third status value.
3. If the user `/goal`s twice in quick succession without launching
   the first, do we end up with two staged tiles? Yes. That's fine
   and probably desirable.
