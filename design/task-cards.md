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
- repeat_max: upper bound on iterations

`repeat_max` is a **cost ceiling**, and in for-each mode it clips the
resolved roster rather than raising: a 112-item queue under
`repeat_max: 60` dispatches the first 60. Exceeding the ceiling silently
would be worse, so the clip stands — but it is no longer invisible. The
truncation is logged, recorded on the execution context, and surfaced as
a decision on the loop's own artifact naming both counts
("60 dispatched, 52 never run"). It is a decision and not a failure: the
loop completed every iteration it planned, so an enclosing `on_failure`
policy is unaffected.

This matters because a clipped fan-out is otherwise indistinguishable
from a complete one after the fact, and a downstream stage that reads an
output *directory* rather than the roster cannot tell the difference —
which is how a reintegration phase came to report a finished pass having
investigated 60 of 112 capabilities, with nothing anywhere naming the
other 52.

### Parallel (implicit/explicit)
Stacking blocks is an implicit sequence. For concurrent execution of
DIFFERENT blocks (not just parallel copies of the same block, which
Repeat handles), an explicit Parallel wrapper groups them.

### State (teal)
Read-only leaf for setting up a task's **assumptions and givens**. Like
Task, it has no body. Nothing writes back to a State block — context and
values flow *down* into tasks exactly as instructions do, so the one
invariant (only artifacts cross task boundaries) is preserved.

A State block has two tiers, prose first:

**Prose context (the baseline).** A freeform "Assumptions / Context"
field. Whatever you write flows into every in-scope task's context
automatically — no templating required. Most cards only need this: state
the givens in plain English ("assume we're deploying to prod, the
migration already ran, and the feature flag is off; don't re-verify
those") and the task simply *knows* them, surfaced as a standing-context
preamble the same way prior-iteration results are.

**Named variables (the adjunct).** For the minority of cases wanting a
reusable value referenced by name, declare variables (name → literal)
read via `{{var.NAME}}` templating, or by bare name (`{{NAME}}`) — the
two forms are equivalent. Values are authored as literals (strings,
numbers, booleans, arrays, objects); field access works for structured
values (`{{var.config.timeout}}`, `{{config.timeout}}`). An unknown
variable name is left verbatim in the rendered text, so typos surface to
the author rather than silently rendering empty. Reserved placeholder
heads (`index`, `item`, `previous`, `previous_sibling`, `all`, `var`,
`sibling(...)`) are resolved first and cannot be shadowed by a variable
that shares their name. Launch-time `parameter_overrides` win over
authored values at read time.

Both tiers are rendered against the same bindings: a `{{NAME}}` or
`{{var.NAME}}` reference works in prose context as well as in task
instructions. Prose does not *require* templating, but it must not
silently swallow it — a card that wrote `DEPLOY_COMMAND: {{DEPLOY_COMMAND}}`
as a prose given previously handed the agent the literal braces, which
read as an unfillable placeholder rather than as an authoring error.

**Placement is the reset policy.** A State block at the top of a
once-running body (the card root wrapper, or before an inner loop) sets
its context/variables once per run. The *same* block inside a
Repeat/Until body re-executes at the start of every iteration,
re-applying its authored prose and literals — i.e. resetting to baseline
each cycle. There is no separate reset knob; where you place the block is
the semantics.

**Visibility scales with formality.** Prose context is ambient — it
shapes the task but is not surfaced on the running card. Named variables,
when present, *are* surfaced live on the running card (showing the
resolved values after launch-override merge), so formal state you can
reference by name is also state you can watch.

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

### The tool floor

One exception to "exactly what you listed": a small set of tools is
always available regardless of scope — `emit_artifact`,
`render_diagram`, and the bead bookkeeping tools. These are harness
plumbing, not the task's permissions: the executor unconditionally
instructs every task to declare its artifacts, so a scope that omits
`emit_artifact` doesn't restrict the task, it breaks it (the model is
told to emit and given nothing to emit with). None of them can write
a file, run a command, or reach the network, so the floor widens no
meaningful surface. Tool-name matching is prefix-tolerant: a scope
listing `run_shell_command` matches the registered
`mcp_run_shell_command`. See `app/utils/task_tool_floor.py`.

## Artifacts

An artifact is what flows out of a task. Structure:
- summary (one-paragraph human-readable synopsis)
- decisions (bulleted list of key choices made)
- outputs (typed content: text / file / data parts)
- metadata (tokens consumed, tool calls made, duration)
- signature (optional hash of error identity, populated only on
  failure to enable clustering of similar failures; null on success)

Artifacts are referenceable by templating: when a downstream task's
instructions contain `{{previous.outputs.NAME}}` — naming a part the
prior task emitted via `emit_artifact` — it gets rendered at dispatch
time.  Parts are addressed by NAME, not by index; see §Propagation
for why the indexed form was not implemented.

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

### Failure policy — on_failure

A container block (Group / Repeat / Until / Schedule) may declare
`on_failure` for the implicit sequence formed by its body:

- `continue` (default, legacy) — every sibling runs regardless of a
  prior sibling's failure; the failed artifact flows onward as
  `{{previous_sibling}}`.
- `stop` — the sequence halts at the first child whose artifact is
  failed; that artifact (annotated with a skip note in its decisions)
  becomes the sequence's result, so the failure propagates upward
  instead of silently feeding failed input into later stages.

Parallel is unaffected: its children are concurrent, so there is no
"later sibling" to gate.

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
`{{previous.decisions}}`, and `{{previous.outputs.NAME}}` for a part
emitted under that name.  Keys may be appended to reach inside a data
part's object: `{{sibling("plan").outputs.roster.slugs}}`.  Missing
fields substitute to the empty string; they do not crash dispatch.
Unknown placeholders (typos) are preserved verbatim so the authoring
mistake is visible — that distinction is the contract: *known
placeholder, unavailable data* renders empty, *unknown placeholder*
stays literal.

##### Gathering a loop's outputs — `outputs_all.NAME`

`outputs.NAME` is **last-wins**: it returns the most recent part under
that name.  That is right for a single task (a task that emits a part,
spots a problem and re-emits under the same name means the correction)
and wrong across a loop, where each iteration's part belongs to a
different worker and none supersedes another.

A Repeat already accumulates every iteration's `outputs` onto its own
artifact, so the data was never lost — but a 60-wide audit loop whose
workers each emit `audit` held all 60 parts while every template
reference resolved to iteration 59 alone.  Nothing errored; the value
was simply the last one.

`{{previous_sibling.outputs_all.audit}}` returns **every** matching part
as a JSON array, in iteration order (dispatch order in the parallel
path too, since `asyncio.gather` preserves it).  A trailing dotted path
projects one field across iterations —
`{{...outputs_all.audit.subsystem}}` → `["alpha","beta","gamma"]` —
which is the shape that lets one fan-out's results drive the next.

An absent name renders `[]`, not `""`, so a downstream `for_each` source
stays parseable: an empty array honestly says "nothing to iterate",
whereas `""` would be unresolvable and fail the block.  Iterations whose
part lacks the projected key are dropped rather than contributing
`null`, which would be indistinguishable from a worker reporting null.

#### Refused launches are recorded

A refusal creates no `TaskRun`, and that is deliberate: `record_run()`
bumps `run_count`, so a record for work that never executed would
corrupt the deck's "never run" vs "has history" distinction, and a
`held` run advertises resume controls for progress that does not exist.

The cost is that refusals would otherwise vanish — and they are the one
defect class nobody pays for, therefore the one nobody remembers.  They
are appended instead to
`~/.ziya/projects/{pid}/task_card_refusals.jsonl`, following
`app/storage/proposals.py`: append-only JSONL inside the standard ALE
envelope, read-modify-write per append, capped at
`MAX_RETAINED_REFUSALS` (5,000, oldest dropped) so the rewrite cost
stays bounded.  Category is `task_definition`, matching card
definitions — a record holds finding messages and block paths, never
task instructions.

Each record carries a `signature`: a hash of the sorted **error
messages** only, excluding card id, card name, block ids and paths.
Two cards broken the same way therefore share a signature, which is
what makes `cluster_by_signature()` able to report "this defect has
been hit 14 times across 6 cards" rather than listing 200 refusals.
Warnings are excluded from the signature: they do not cause the
refusal, and including them would split one defect class across every
incidental warning combination that accompanied it.  `is_resume`
distinguishes a refused resume — where the *snapshot* was already
broken when it ran — from an author having just broken a card.

Writes are best-effort and never raise: the caller is on its way to
returning a 422 that names a real defect, and a failed sink must not
replace that with a 500.

**No indexed part access.**  An earlier draft of this document
specified `{{previous.outputs[0].text}}`.  It was never implemented and
has been withdrawn rather than built: emission order is not a contract
a card author can rely on (a task that conditionally emits a diagnostic
part shifts every later index), so a positional reference silently
resolves to the wrong part instead of failing.  Named access is the
only form.  Where several parts belong together, `emit_artifact`'s
`group`/`label`/`seq` fields carry the relationship.

A Repeat's `for_each` source may itself contain placeholders
(`{{sibling("plan-id")}}`, `{{previous_sibling}}`, `{{var.X}}`),
rendered at the Repeat's dispatch time against the artifacts completed
so far in the run.  Two parsing modes, chosen by the shape of the
source:

- **Precise** — the source is exactly one `outputs.NAME` or
  `outputs_all.NAME` reference, e.g.
  `{{sibling("plan").outputs.roster.slugs}}` or
  `{{sibling("fan").outputs_all.audit.subsystem}}`.  Parsed strictly:
  only a whole-string JSON array is accepted.  Preferred, because the
  author named an exact structured value and scanning its rendering for
  an incidental `[` would substitute something else.  (In practice both
  modes parse an identical whole-string array identically; the mode
  chiefly determines which remedy an unresolvable source reports.)
- **Lenient** — any other templated source.  The first JSON array found
  in the rendered text is extracted, which is what makes a planner
  task's prose summary usable.

This enables the canonical decomposition shape: Task("plan") →
Repeat(for_each over the plan's output).

A source that is a static literal and unparseable falls back to
count-based iteration.  A TEMPLATED source that resolves to no array
does NOT: it fails the Repeat block.  Falling back there ran the body
`repeat_max` times with `item=null`, turning a broken hand-off into a
wide fan-out over nothing — expensive, and it produced a run record
that looked populated.  An empty resolved array (`[]`) is legitimate and
yields zero iterations without failing.

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
| `block_status` | `{run_id, block_id, block_type, status, at, error?}` — per-block lifecycle transition (running / done / failed / cancelled / skipped); drives the run map |
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

### Partial outcomes

A run that completed four of seven stages — writing files and running
commands along the way — is not the same event as one that died on
stage one having touched nothing.  Reporting both as `failed` is
actively harmful: it reads as "nothing happened" for a run that may
have **materially changed the workspace**, discouraging the user from
looking for the changes it left behind.

`RunStatus` therefore has a fifth terminal value, `partial`, meaning
the run both **made progress** and **left work unfinished**.  A
zero-progress stop stays `failed` / `cancelled`, so a genuine total
loss keeps its own distinct signal.

It is **derived, not authored**.  The executor's error paths are
untouched and still write `failed` / `cancelled`; reclassification
happens once, at the terminal write, from the per-block record already
being kept (`app/utils/run_outcome.py::classify_terminal_status`).
A new terminal status therefore costs no new branches in
`block_executor`, and a run whose `block_states` are empty degrades to
exactly the previous behaviour.

Progress means either shape of evidence, since the two are recorded
differently:

* a structural block that reached `done` (`TaskRunBlockState.status`)
* a loop iteration that `passed` (`IterationSummary.status`)

The second matters: a Repeat's inner Task shares **one**
`block_states` entry across every iteration (last-write-wins), so a
loop whose 3rd of 10 iterations failed leaves that entry `failed` with
the two successes visible only in `iteration_summaries`.  Counting
blocks alone would call that run a total loss.

A cancelled run that got partway is also `partial` — a user-stopped
run carries the same workspace hazard as a crash — which makes
`cancelled` rare by design: it now means only "stopped before anything
completed".

### Continuing a stopped run

`POST /task-runs/{id}/resume-from/{block_id}?mode=retry|continue`

Two user-facing acts, one mechanism.  The difference is purely *which
block becomes the resume point*:

* **retry from X** — re-execute X.  Resume point = X.
* **continue from X** — accept X's recorded outcome and start at the
  block after it.  Resume point = X's successor.

That symmetry is why `continue` needs no executor change.  The resume
gate replays every block ahead of the resume point, so pointing it at
X's successor makes X itself replay — which *is* "accept the recorded
outcome", including when that outcome was a failure.  Continuing past
a failed block is how a user says "I fixed it by hand, move on";
retrying would undo the fix.

`continue` on the last block in the deck is rejected (422) rather than
launching a run that replays everything and executes nothing, which
would look like a resume that silently did nothing.

Target normalization is shared by both modes
(`app/utils/resume_targets.py`): only structural blocks have durable
per-block state, so a block inside a loop body resolves to its
**outermost** enclosing loop.

### Attempt lineage

A resume creates a **new run** and leaves the source intact, so the
source stays an immutable record alongside `card_snapshot` and
`permissions_snapshot`.  Prior state is genuinely preserved — every
completed block replays, and `state` blocks re-execute to rebuild
`{{var.NAME}}` — but nothing *recorded* the relationship, so the GUI
could only show a second tile materializing beside the first with no
stated connection.  The user could not tell whether prior state had
been kept or thrown away.

Five fields close that gap:

| Field | Meaning |
|---|---|
| `root_run_id` | Lineage key — every attempt shares the **first** run's id, so a chain is one filter rather than a parent-pointer walk.  Self on an initial run. |
| `parent_run_id` | Immediate predecessor; the run whose artifacts this one replays. |
| `attempt` | 1-based position, displayed as "attempt N of M". |
| `resume_kind` | `initial` / `retry_from` / `continue_from` / `rerun`. |
| `resumed_from_block_id` | The block the **user** pointed at.  For a continue this is deliberately *not* the resume point (that is its successor), so the UI names the right stage. |

`GET /task-runs/{id}/lineage` returns every attempt, oldest first.
The GUI collapses a lineage to **one tile** showing the newest attempt,
with the others on an attempt rail — so the history is visibly retained
rather than scattered across sibling tiles.  Runs written before
lineage tracking have no `root_run_id`; the id-fallback makes each its
own single-attempt lineage.

The collapse is decided client-side from fields the **bindings list**
already carries: `GET /task-bindings` loads every run to stamp
`run_status`, so `root_run_id` and `attempt` ride along on that same
read (`app/api/task_bindings.py`) and
`components/TaskCard/lineageCollapse.ts` folds them synchronously.

A per-binding run fetch would have been wrong twice over.  It is a
request burst on every binding change, and it must pick a project id —
for a **global chat** the correct one is the chat's *owning* project,
not the project being viewed, which is why the list endpoint has a
cross-project fallback at all.  A client fetch keyed on the viewing
project would have 404'd for exactly the chats most likely to have
accumulated attempts, silently disabling the collapse there.

Anchor reuse is deliberately non-fatal and is resolved **before** the
binding is created rather than inline as an argument to it: an escape
from the anchor lookup would otherwise skip creation entirely, leaving
a run that executes but can never be rendered — losing the run over a
purely cosmetic failure.

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

### Self-improvement

A container block (Group / Repeat / Until / Parallel) may carry
`self_improve: true`.  After that level completes, a judge model call
evaluates the outcome against the block's `improve_criterion` (or, when
none is authored, the objective inferred from the level's own task
text) and returns one of three verdicts:

- **accept** — the outcome is adequate, or no text change would
  tangibly and meaningfully affect the next run.  Also the
  fail-conservative default for any judge transport/parse failure, so
  a flaky evaluator can never spin a card through edits.
- **revise** — a specific weakness in the task text caused a real
  deficiency, and a concrete text change would meaningfully improve
  the next run.  Carries the patch.
- **stop** — deficient for reasons text cannot fix (permissions,
  environment, external state).  Recorded so later runs' judges see it.

The bar for `revise` is deliberately high: stylistic preference is not
grounds to rewrite.  Cards should converge, not wander.

On `revise`, the patch is applied and **that level restarts** with the
revised text, up to its revision budget.

**Text, never privilege.**  A patch may touch only `instructions` and
`state_context`, keyed by block ids that already exist within the
improving level's own subtree.  This is enforced three ways, each
independent:

1. The field whitelist (`app/utils/self_improve.py::
   IMPROVABLE_TEXT_FIELDS`) — validation rejects any other field.
2. A structure fingerprint (the tree hashed with whitelisted text
   stripped) asserted before persistence — a patch that changed
   anything but text is refused even if the applier regresses.
3. The scope-approval hash (`scope_canonical.task_scope_hash`) covers
   only privilege-bearing fields, so a text-only patch keeps signed
   approvals valid — and the existing-id keying is what makes that
   hold in practice: a whole-tree replacement would mint fresh ids for
   dropped blocks, orphaning approvals and silently dropping those
   blocks to the permission floor.  Patches cannot mint ids at all.

**Durability is two artifacts.**  The patched card (persisted to the
live definition, best-effort against drift — ids edited away since
launch simply don't apply) is what makes run N+1 better.  The
append-only per-project **lesson ledger**
(`~/.ziya/projects/{pid}/task_card_lessons.jsonl`, same capped-JSONL
shape as the refusal log) is what stops the judge re-deriving — and
re-reverting — the same lesson every run: recent lessons for the same
(card, block) are fed to the judge, and a patch whose content hash was
already applied for that (card, block) is refused outright (the
oscillation guard).  Records are best-effort; a failed ledger write
never fails the run.

**Budgets bound nesting.**  Self-improving levels multiply: 3 revisions
inside 3 revisions is 9 executions of the inner subtree.  Per-block
`improve_max` (default 2; explicit 0 = observe-only — judge and record
lessons, never edit) bounds each level; `ZIYA_TASK_IMPROVE_RUN_MAX`
(default 10) bounds the product across the whole run.

**Drift is a policy, not an accident.**  `improve_drift:
"conservative"` (default) instructs the judge to correct toward the
stated objective only — never to expand scope or ambition beyond the
ask.  Opt-in `"expansive"` permits strengthening beyond it.  Many
layers of judges with significant iteration can grow a thing more
powerful than what was asked for; that is occasionally the point and
usually not, so it requires the explicit opt-in.

**Audit note.**  `card_snapshot` records the tree a run *launched*
with; an in-run revision means later passes of that level executed
revised text.  The revision trail is recorded as `improve_revision`
events (block id, revision index, verdict, rationale, applied /
persisted flags) and as ledger records carrying the full patch, so a
run remains reconstructable.  A Repeat that restarts also re-records
iteration summaries; the `improve_revision` events are what segment
the passes.

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

## What this replaces

The existing TaskPlan folder + delegate conversation model is not
user-facing in the Task Card design. Internally, delegate machinery
(DelegateManager, crystal artifacts, delegate streaming) remains the
execution substrate — we just don't surface it in the sidebar.
TaskPlan folders, iteration sub-folders, and sibling delegate
conversations are all eliminated from the UX.
