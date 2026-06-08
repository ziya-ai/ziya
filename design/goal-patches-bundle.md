# Goal mechanism patch bundle (revised)

Concrete diffs for `design/goal-staged-by-default.md` and
`design/goal-exit-conditions.md`. Apply in this order; each section
is independently reviewable.

---

## Part 1 — Staged-by-default goal cards

### 1.1 `app/models/task_binding.py` — make run_id optional

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

### 1.2 `app/storage/task_bindings.py` — accept optional run_id at create

```diff
--- a/app/storage/task_bindings.py
+++ b/app/storage/task_bindings.py
@@ -82,7 +82,7 @@ class TaskBindingStorage(BaseStorage[TaskBinding]):
         return None

     def create(
-        self, chat_id: str, card_id: str, run_id: str,
+        self, chat_id: str, card_id: str, run_id: Optional[str] = None,
         anchor_message_id: Optional[str] = None,
     ) -> TaskBinding:
         binding = TaskBinding(
```

(`update_run_id` already exists — used as-is by the new launch endpoint.)

### 1.3 `app/api/commands.py` — `/goal` stages instead of launching

```diff
--- a/app/api/commands.py
+++ b/app/api/commands.py
@@ -100,9 +100,8 @@ async def _handle_goal_command(body: CommandRequest, request: Request) -> Comman


 async def _goal_create(body: CommandRequest, request: Request) -> CommandResponse:
-    """Synthesize a task card from goal text and launch it."""
+    """Synthesize a task card from goal text and stage it (no run yet)."""
     from ..utils.goal_synthesis import synthesize_goal_card
-    from .task_cards import _launch_run_for_card

     goal_text = body.args.strip()

@@ -126,32 +125,28 @@ async def _goal_create(body: CommandRequest, request: Request) -> CommandRespons
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
+    # clicks Run on the inline tile to actually launch.  This gives
+    # them a chance to review the synthesized instructions and
+    # adjust scoped permissions before the agent starts working.
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
+        message=(
+            f"🎯 Goal staged: {goal_text}\n"
+            "Review the task card and click **Run** to start."
+        ),
         data={
             "card_id": saved_card.id,
-            "run_id": run.id,
             "binding_id": binding_id,
             "goal_text": goal_text,
         },
     )
```

### 1.4 `app/api/task_bindings.py` — new launch endpoint

```diff
--- a/app/api/task_bindings.py
+++ b/app/api/task_bindings.py
@@ -118,3 +118,32 @@ async def delete_task_binding(
     storage = _bindings_storage(project_id)
     if not storage.delete(chat_id, binding_id):
         raise HTTPException(status_code=404, detail="Task binding not found")
+
+
+@router.post("/{binding_id}/launch", response_model=TaskRun)
+async def launch_staged_binding(
+    project_id: str, chat_id: str, binding_id: str,
+) -> TaskRun:
+    """Launch the run for a staged binding (one whose ``run_id`` is
+    None because it was created by the ``/goal`` slash command and
+    is awaiting explicit user confirmation).
+
+    409 if the binding has already been launched.  404 if the binding
+    does not exist for this chat.
+    """
+    _ensure_project(project_id)
+    project_dir = get_project_dir(project_id)
+    binding_storage = TaskBindingStorage(project_dir)
+    binding = binding_storage.get(chat_id, binding_id)
+    if not binding:
+        raise HTTPException(status_code=404, detail="Binding not found")
+    if binding.run_id:
+        raise HTTPException(status_code=409, detail="Binding already launched")
+
+    run = await _launch_run_for_card(
+        project_id=project_id,
+        card_id=binding.card_id,
+        source_conversation_id=chat_id,
+    )
+    binding_storage.update_run_id(chat_id, binding.id, run.id)
+    logger.info(f"🚀 Staged binding {binding_id[:8]} launched → run {run.id[:8]}")
+    return run
```

### 1.5 `frontend/src/types/task_binding.ts` — run_id optional

```diff
--- a/frontend/src/types/task_binding.ts
+++ b/frontend/src/types/task_binding.ts
@@ -10,7 +10,11 @@ export interface TaskBinding {
   id: string;
   chat_id: string;
   card_id: string;
-  run_id: string;
+  /**
+   * Optional: null for a staged binding from /goal that hasn't been
+   * launched yet.  The inline tile renders a "Run" button in this case.
+   */
+  run_id?: string | null;
   anchor_message_id?: string | null;
   created_at: number;
 }
```

### 1.6 `frontend/src/services/taskBindingApi.ts` — add launchStagedBinding

```diff
--- a/frontend/src/services/taskBindingApi.ts
+++ b/frontend/src/services/taskBindingApi.ts
@@ -55,3 +55,17 @@ export async function deleteBinding(
     throw new Error(`deleteBinding failed: ${res.status}`);
   }
 }
+
+export async function launchStagedBinding(
+  projectId: string, chatId: string, bindingId: string,
+) {
+  const res = await fetch(
+    `${base(projectId, chatId)}/${encodeURIComponent(bindingId)}/launch`,
+    { method: 'POST', headers: projectHeaders() },
+  );
+  if (!res.ok) {
+    const text = await res.text().catch(() => '');
+    throw new Error(`launchStagedBinding failed: ${res.status} ${text}`);
+  }
+  return res.json();
+}
```

### 1.7 `frontend/src/components/TaskCard/TaskCardInlineTile.tsx`

Add a staged-mode early return at the top of the component. Sketch:

```diff
--- a/frontend/src/components/TaskCard/TaskCardInlineTile.tsx
+++ b/frontend/src/components/TaskCard/TaskCardInlineTile.tsx
@@ -216,6 +216,11 @@ export const TaskCardInlineTile: React.FC<Props> = ({ binding, hideWhenTerminal
   const { currentProject } = useProject();
   const projectId = currentProject?.id ?? '';

+  // Staged binding (created by /goal) — no run yet.  Render the
+  // "Run / Discard" affordance instead of streaming run state.
+  if (!binding.run_id) {
+    return <StagedCardTile binding={binding} />;
+  }
+
   // Live-streamed run state.  Hook handles initial REST fetch, WS
   // subscription, and terminal refetch for the final artifact.
   const { run, error: streamError, refresh, live, clearLive } = useTaskRunStream(
```

`StagedCardTile` itself, as a sibling component in the same file:

```tsx
import { Button, Tag } from 'antd';
import { launchStagedBinding, deleteBinding } from '../../services/taskBindingApi';

const StagedCardTile: React.FC<{ binding: TaskBinding }> = ({ binding }) => {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? '';
  const [card, setCard] = useState<TaskCard | null>(null);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    taskCardApi.get(projectId, binding.card_id)
      .then(c => { if (!cancelled) setCard(c); })
      .catch(() => { /* non-fatal */ });
    return () => { cancelled = true; };
  }, [projectId, binding.card_id]);

  const handleRun = async () => {
    if (!projectId) return;
    setLaunching(true);
    setError(null);
    try {
      await launchStagedBinding(projectId, binding.chat_id, binding.id);
      window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT));
    } catch (e: any) {
      setError(String(e));
      setLaunching(false);
    }
  };

  const handleDiscard = async () => {
    if (!projectId) return;
    try {
      await deleteBinding(projectId, binding.chat_id, binding.id);
      window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT));
    } catch (e: any) {
      setError(String(e));
    }
  };

  // Pull instructions from the root block (or first child).
  const instructions = useMemo(() => {
    if (!card) return '';
    const root = card.root;
    return (root.instructions || root.body?.[0]?.instructions || '').trim();
  }, [card]);

  return (
    <div className="task-card-inline-tile staged">
      <div className="header">
        <span>🎯</span>
        <strong>{card?.name ?? 'Goal'}</strong>
        <Tag color="default">staged</Tag>
      </div>
      {instructions && (
        <details>
          <summary>Instructions</summary>
          <MarkdownRenderer markdown={instructions} />
        </details>
      )}
      <div className="actions">
        <Button type="primary" loading={launching} onClick={handleRun}>
          Run
        </Button>
        <Button onClick={handleDiscard} disabled={launching}>
          Discard
        </Button>
      </div>
      {error && <div className="error" style={{ color: '#f85149' }}>{error}</div>}
    </div>
  );
};
```

Plus a small CSS addition to `task-card-inline-tile.css`:

```css
.task-card-inline-tile.staged {
  border-style: dashed;
  opacity: 0.95;
}
.task-card-inline-tile.staged .header {
  display: flex;
  gap: 8px;
  align-items: center;
}
.task-card-inline-tile.staged .actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
```

---

## Part 2 — Exit conditions (revised)

The implementation here is **dramatically simpler** than the first
draft because `Artifact.self_assessment` already exists. The agent
already emits `<self_assessment objective_met="..." rationale="..." />`
at the end of every iteration; we just need the loop to honor it.

No new tools. No new model fields. No new prompt instructions. Just
two changes to `_execute_until` + one change to goal synthesis.

### 2.1 `app/agents/block_executor.py` — honor self_assessment + convergence

```diff
--- a/app/agents/block_executor.py
+++ b/app/agents/block_executor.py
@@ -32,6 +32,7 @@ from .until_evaluator import evaluate_condition as _evaluate_until_condition_wit

+import hashlib
+
+
+def _iteration_signature(a: Artifact) -> str:
+    """Cheap signature for convergence detection: SHA-16 of normalized
+    summary text.  Two iterations producing the same normalized
+    summary are treated as a stop signal — the agent is repeating
+    itself with no new information.
+    """
+    body = " ".join((a.summary or "").lower().split())
+    return hashlib.sha256(body.encode()).hexdigest()[:16]
+
+
 async def _execute_until(block: Block, ctx: ExecutionContext) -> Artifact:
     """Repeat the body until a model-evaluated condition is true.
     ...
     """
     n_max = max(1, int(block.until_max or 5))
     condition = (block.until_condition or "").strip()
     mode = (block.until_mode or "model").lower()
     start = time.time()
     last_artifact: Optional[Artifact] = None
     outputs: List[ArtifactPart] = []
     decisions: List[str] = []
+    signatures: List[str] = []  # convergence backstop

     await _emit(ctx, {
         "type": "block_started",
         "block_id": block.id, "block_type": "until",
         "planned": n_max, "at": time.time(),
     })

     for i in range(n_max):
         if ctx.cancel_requested():
             raise BlockExecutionCancelled()
         await _emit(ctx, {
             "type": "iteration_started",
             "block_id": block.id, "index": i,
         })
         bindings = task_templating.IterationBindings(
             index=i, item=None, previous=last_artifact, all_summaries=[],
         )
         ctx.binding_stack.append(bindings)
         iter_ctx_token = set_task_iteration_context(block.id, i)
         try:
             artifact = await _execute_sequence(block.body, ctx)
         finally:
             ctx.binding_stack.pop()
             reset_task_iteration_context(iter_ctx_token)
         await _record_iteration(block, ctx, i, artifact)
         await _emit(ctx, {
             "type": "iteration_completed",
             "block_id": block.id, "index": i,
             "status": ("failed" if artifact.failed else "passed"),
             "signature": artifact.signature,
             "duration_ms": artifact.duration_ms, "tokens": artifact.tokens,
         })
         last_artifact = artifact
         outputs.extend(artifact.outputs)

+        # ---- Layer A: agent self-assessment ----------------------------
+        # The agent emits <self_assessment objective_met="..."/> at the
+        # end of every task response.  Honor that as the primary
+        # exit signal.  A claimed "true" verdict (including vacuous
+        # satisfaction like "no instances found") terminates the loop;
+        # "false"/"partial" mean keep iterating; missing/"unknown"
+        # means we have no agent signal — fall through.
+        sa = artifact.self_assessment or {}
+        verdict = (sa.get("objective_met") or "").lower()
+        if verdict == "true":
+            rationale = sa.get("rationale") or ""
+            decisions.append(
+                "self_assessment: objective met"
+                + (f" — {rationale}" if rationale else "")
+            )
+            break
+
+        # ---- Layer B: convergence backstop -----------------------------
+        # If two consecutive iterations produce identical summaries the
+        # agent is going in circles — stop with a clear decision.
+        sig = _iteration_signature(artifact)
+        signatures.append(sig)
+        if len(signatures) >= 2 and signatures[-1] == signatures[-2]:
+            decisions.append(
+                "converged: two consecutive iterations produced identical summaries"
+            )
+            break
+
+        # ---- Layer C: model-evaluated until_condition (existing) -------
         if not condition:
             # No condition → behave like Repeat-until-success.
             if not artifact.failed:
                 break
             continue
         if mode == "expression":
             decisions.append("until_mode='expression' not yet implemented; running to max")
             continue
         # mode == "model"
         try:
             satisfied = await _evaluate_until_condition_with_model(condition, artifact)
         except Exception as e:
             logger.warning(f"until condition eval failed (continuing): {e}")
             satisfied = False
         if satisfied:
             decisions.append(f"until condition satisfied at iter {i}")
             break

     elapsed_ms = int((time.time() - start) * 1000)
     await _emit(ctx, {
         "type": "block_completed", "block_id": block.id, "at": time.time(),
     })
     return Artifact(
         summary=(last_artifact.summary if last_artifact else "(until ran 0 iterations)"),
         decisions=(last_artifact.decisions if last_artifact else []) + decisions,
         outputs=outputs, duration_ms=elapsed_ms,
         created_at=time.time(),
         failed=bool(last_artifact and last_artifact.failed),
     )
```

### 2.2 `app/utils/goal_synthesis.py` — drop until_condition

```diff
--- a/app/utils/goal_synthesis.py
+++ b/app/utils/goal_synthesis.py
@@ -75,7 +75,11 @@ def synthesize_goal_card(
     until_block = Block(
         block_type="until",
         name="Goal condition",
         until_mode="model",
-        until_condition=goal_text,
+        # The model-evaluated until-condition is unreliable for
+        # action-phrased goals ("add X", "fix Y") because it answers
+        # "have these actions been performed?" — wrong for vacuously-
+        # satisfied cases.  We rely on Artifact.self_assessment instead;
+        # see design/goal-exit-conditions.md.
+        until_condition="",
         until_max=iteration_cap,
         body=[task_block],
     )
```

That's it for Part 2. Three layers of exit handling, and the agent's
existing self_assessment becomes load-bearing rather than decorative.

---

## Apply order

1. **Part 1** — staged-by-default. Independently useful. Lower risk
   because it doesn't change loop semantics.
2. **Part 2** — exit conditions. Flips the loop's primary stop signal
   from "until-evaluator says yes" to "agent says objective_met=true".

After Part 2 lands, re-run the original failing case:

```
/goal find any places in app/api/commands.py that swallow exceptions
silently and add logger.warning calls
```

Expected: agent's iter-0 response will include
`<self_assessment objective_met="true" rationale="no try/except blocks
in target file" />`, Layer A fires, loop terminates after iter 0, run
status `done`. Total runtime ~20s instead of ~2m20s.

## Tests to add

- `tests/agents/test_block_executor.py::test_until_honors_self_assessment_true`
  Run an Until block whose body returns an artifact with
  `self_assessment={"objective_met": "true"}`. Assert exactly 1
  iteration runs.
- `tests/agents/test_block_executor.py::test_until_continues_on_objective_met_false`
  Body returns `objective_met="false"` for 3 iterations. Assert
  loop runs to completion of those 3 (no early exit from Layer A).
- `tests/agents/test_block_executor.py::test_until_convergence_backstop`
  Body returns identical summary with `objective_met="unknown"`
  twice. Assert loop stops after 2nd with "converged" decision.
- `tests/utils/test_goal_synthesis.py::test_synthesized_card_has_no_until_condition`
  Assert `synthesize_goal_card("anything").root.until_condition == ""`.
