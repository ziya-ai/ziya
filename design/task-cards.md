# Task Cards — Design Note

## Why

The existing swarm / delegate system supports hierarchical sub-agent
execution but the user experience is clunky: config is emitted as a
model-authored JSON block and accepted wholesale; post-launch state
is frozen; hierarchy is hidden; progress is scattered across multiple
surfaces; loops aren't expressible; and there's no reusable unit. We
need a UX primitive that lets a user configure, launch, watch, and
re-use a piece of agent work without ceremony.

## The core principle

**A task is a cognitive sandbox.** Its purpose is to let the model do
contextually expensive work (many files, many tool calls, lots of
reasoning) without polluting the parent's conversation. The parent
says what it wants in one sentence; the child does whatever is
necessary; only the resulting artifact flows back.

This is not a side benefit — it is the entire reason tasks exist.

## The one invariant

**A task's conversation never leaves its task.**

- Parent → child passes: instructions (an abstract brief)
- Child → parent returns: artifact (summary + outputs)
- Nothing else crosses the boundary. Ever.

This applies at every level: spawned sub-tasks, loop iterations,
pipeline stages. Conversations are sandbox-local; artifacts are the
only inter-sandbox vocabulary.

## Grammar — three block shapes

Visual-programming-language style (Scratch/Blockly). Blocks compose
by nesting. The Task is the atom; the others are decorators.

### Task (blue)
Atomic action. Has:
- instructions (abstract brief, written for the model)
- scope: the set of files, tools, and skills this task is allowed

### Repeat (yellow)
Wrapper decorator. Runs its body N times. Modes:
- count — run N times
- until — run until condition met, with max
- for-each — run once per item in an input list

Orthogonal options:
- parallel: on/off
- propagate: none / last artifact / all artifacts
  (controls whether an iteration's instruction gets templated with
  a prior iteration's artifact; the iteration's conversation remains
  fresh either way)

### Parallel (implicit/explicit)
Stacking blocks is an implicit sequence. For concurrent execution of
DIFFERENT blocks (not just parallel copies of the same block, which
Repeat handles), an explicit Parallel wrapper groups them.

### Composition rules
- Any block's body can contain any other block.
- Repeat nests freely inside Repeat.
- Pipelines (implicit sequences) nest freely inside Repeats.
- Depth is unlimited; legibility is the only practical cap.

## Context scoping

**Each task sets its own scope. No inheritance, no cascading.**

If two sibling tasks both need `renderer.py`, they both list it.
No magic union or intersection across levels. What you see on a
block is what that task gets.

Scope has three facets:
- files: paths the task is allowed to read
- tools: MCP/builtin tools the task is allowed to call
- skills: skill contexts the task loads

At runtime these are the exact permissions the task has. Nothing
inherited from parent, nothing intersected from card-level.

## Artifacts

An artifact is what flows out of a task. Structure:
- summary (one-paragraph human-readable synopsis)
- decisions (bulleted list of key choices made)
- outputs (typed content: text / file / data parts)
- metadata (tokens consumed, tool calls made, duration)
- signature (optional hash of error identity, populated only on
  failure to enable clustering of similar failures; null on success)

Artifacts are referenceable by templating: when a downstream task's
instructions contain `{{previous.artifact.outputs[0]}}`, it gets
rendered at dispatch time.

## Runtime semantics

These are the execution contracts a block executor is required to
honor.  They sit beneath the grammar: a user composing blocks does
not see them directly, but the authoring surface must render them
correctly and the execution surface must enforce them.

### One iteration equals one pass through the body

A Task block is the atomic unit of model invocation: one streamed
conversation in, one Artifact out.  Larger structures produce
composite artifacts:

- **Sequence** (implicit — stacked blocks in a body) — runs each
  block top-to-bottom.  The sequence's artifact is the last block's
  artifact.  Earlier artifacts are available to later blocks via
  propagation.
- **Repeat (one iteration)** — one full traversal of the body.  If
  the body is a single Task, the iteration's artifact is that Task's
  artifact.  If the body is a sequence, the iteration's artifact is
  the sequence's last artifact.
- **Parallel** — runs all body blocks concurrently.  The Parallel's
  artifact is a composite whose `outputs` is the concatenation of
  each child's outputs in declared order.

### Propagation — what an iteration sees

An iteration's instructions can reference state from prior iterations
or prior siblings via template variables.  Substitution happens at
dispatch time, immediately before the model is invoked; the
conversation inside the iteration sees only the rendered string,
never the template.

Inside a Repeat body:

| Variable | When defined | Shape |
|---|---|---|
| `{{item}}` | mode `for_each` | the current item from the source list |
| `{{index}}` | always | 0-based iteration index (integer) |
| `{{previous}}` | iteration > 0, propagate ≠ `none` | prior iteration's Artifact |
| `{{all}}` | propagate: `all` | list of every prior iteration's Artifact |

Inside a sequence:

| Variable | Shape |
|---|---|
| `{{previous_sibling}}` | the immediately-prior sibling's Artifact |
| `{{sibling("block-id")}}` | a named sibling's Artifact |

Field access follows the Artifact schema: `{{previous.summary}}`,
`{{previous.outputs[0].text}}`, `{{previous.decisions}}`.  Missing
fields substitute to the empty string and log a warning; they do not
crash dispatch.

### Iteration result storage at scale

A Repeat with count=10,000 cannot serialize 10,000 full Artifacts
into a single TaskRun JSON file.  The storage shape:

- `TaskRun.block_states[block_id].iteration_summaries` — an array of
  lightweight records, one per iteration, each ~100 bytes:
  `{index, status, signature, duration_ms, tokens}`.  Always retained.
- Full Artifacts stored per-iteration in separate files:
  `~/.ziya/projects/{pid}/task_runs/{run_id}/iterations/{block_id}_{index}.json`.
- Every failing iteration persists its full Artifact.
- Up to the first 50 passing iterations persist their full Artifact;
  passes beyond that retain only the summary record.

The `signature` on an Artifact is a hash of `(error_type,
error_location)` derived from a failed iteration's output.  Null on
success.  This single field is what drives failure-signature
clustering in observation surfaces — the "10,000 runs, 4 error
patterns" view is a group-by over this field.

### Live observation

Runs are observable via both REST and WebSocket.

- `GET /task-runs/{id}` — full snapshot.  Always available.  Used on
  reload and after reconnect.  Source of truth.
- `WS /ws/task-runs/{id}` — incremental events pushed during
  execution.  Follows the pattern in `app/agents/delegate_stream_relay.py`.

Event types (server → client):

| Event | Payload |
|---|---|
| `run_started` | `{run_id, started_at}` |
| `block_started` | `{run_id, block_id, at}` |
| `iteration_started` | `{run_id, block_id, index}` |
| `iteration_completed` | `{run_id, block_id, index, status, signature?, duration_ms, tokens}` |
| `block_completed` | `{run_id, block_id, at}` |
| `run_completed` | `{run_id, status, at}` |
| `whisper_received` | `{run_id, block_id, text}` — ack of a whispered hint |

Events are transient; persisted storage remains the source of truth.
Reconnecting clients reconcile by reading the snapshot and then
resuming the event stream.

### Cancellation

`POST /task-runs/{id}/cancel` sets `TaskRun.cancel_requested = True`
and returns immediately.  The block executor checks the flag at two
points:

1. Between iterations of a Repeat.
2. Between siblings in a sequence.

In-flight Task invocations complete normally; they are not
interrupted.  When cancellation is observed, the executor stops
scheduling new work, seals partial results, and transitions the run
to `status: cancelled`.  Partial artifacts are preserved.

Hard cancel (interrupting a mid-stream LLM invocation) is deferred;
it requires plumbing `asyncio.CancelledError` through
`StreamingToolExecutor` and is not needed for any committed use case.

### Relationship to the delegate substrate

The block executor uses `StreamingToolExecutor` directly — the same
engine that powers the main chat flow and the delegate system.  It
does not go through `DelegateManager`; task cards and delegates are
sibling systems that share the underlying model-invocation engine.

Task cards have their own sandboxed conversations per Task (per the
core invariant).  Delegate conversations have their own sandbox as
visible chats.  A task card does not spawn a delegate, and a delegate
does not spawn a task card — they compose only through their shared
engine, not through each other.

### Queryable runs

A live or completed run is not a blob of state — it is a queryable
object.  The REST surface supports filtered views over the iteration
summaries, and the chat surface can call those views in response to
user questions.

Common queries:

- **By status** — "which iterations failed?"
- **By signature** — "which iterations hit this crash pattern?"
- **By range** — "the last 20 iterations" or "iterations 100–200"
- **Count-only** — lightweight stats for aggregate views without
  payloads

Concrete shape:
`GET /task-runs/{id}/iterations?status=failed&signature=abc123&limit=50`
— server-side filter over `iteration_summaries`, returning the matching
summaries plus (optionally) the full Artifacts for those entries.

Beyond structured filtering, the Artifacts and summaries are designed
to be feedable as context into a regular chat turn — so "summarize the
still-broken cases" is a legitimate interaction: a chat turn loads
the failed iterations via the query endpoint and the model writes
prose over them.  The task-card system does not own a bespoke
summarization path; it owns the queryable substrate that a chat turn
can draw from.

## UX shape

### The container

The conversation is the outer container. Task cards are objects the
conversation holds, like code blocks or images. Nothing about tasks
lives in the sidebar — no mission folders, no delegate entries, no
iteration groups. The existing conversation active/done indicators
are the only cross-conversation affordance.

### Entry paths

Two paths, same resulting object:
1. **Ask for it.** User describes intent in natural language; model
   renders a task card with the block structure; user edits inline
   before launch.
2. **Drop one in.** User clicks `+ Task` or `📚 From library` in the
   composer; a pre-made card is inserted at cursor.

### In-flight state

The card stays exactly where it was dispatched. Status appears on
the blocks themselves: iteration dots on Repeats, streaming glow
on active Tasks, green checkmarks on completed blocks, error states
on failed ones. No modal, no hijacked layout, no sidebar entry.

### Sub-task detail

When a task spawns a sub-task, the sub-task renders inline under
its parent block, indented one level. The user stays in one card.
If hierarchies become unwieldy in practice, a Mission Canvas view
is a future addition — but only if the inline view proves
insufficient.

### Saved templates / library

Any block (single Task, Repeat, or whole card) is saveable. Saved
items live in a library accessed from the composer (`📚 From library`
dropdown) or a keyboard shortcut. The library is not a sidebar
section.

Colloquially, a saved Task with a strong persona is "an agent." A
saved Repeat-until-pass is "a retry pattern." A saved whole card is
"a workflow." These are informal labels on the same underlying
block structure.

## "Agent" as popular culture

Popular culture framing: an agent is a persistent entity with
identity, memory, persona, and autonomy. The user "has" agents.
Agents "chat" with each other.

Our framing: tasks are subroutines — named units of dispatched
work with their own scope, returning a value, leaving no residue.
The user does not have agents; the user dispatches tasks.

Everything popular multi-agent systems do (tool use, collaboration,
iteration, hierarchical plans) falls out of block composition. We
just don't route collaboration through shared conversations; we
route it through artifacts and instructions.

## Out of scope (for now)

- Mission Canvas (graph/tree/timeline view) — defer until inline
  view proves insufficient
- Cross-task artifact inspector — artifacts are visible inline on
  each block; a dedicated browser can come later
- Agent marketplace / community templates — local library first
- Streaming artifacts — artifacts are final outputs, not streamed

## What this replaces

The existing TaskPlan folder + delegate conversation model is not
user-facing in the Task Card design. Internally, delegate machinery
(DelegateManager, crystal artifacts, delegate streaming) remains the
execution substrate — we just don't surface it in the sidebar.
TaskPlan folders, iteration sub-folders, and sibling delegate
conversations are all eliminated from the UX.
