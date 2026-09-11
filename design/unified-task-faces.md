# Unified Task Runtime: The Faces Architecture

**Status:** proposed · **Author:** audit session 2026-08-31 · **Executable deck:** §9

## 1. Problem

Ziya has grown four multi-step-execution entry points on top of (or beside) two
separate runtimes:

| Entry point | Runtime today | State |
|---|---|---|
| Task Cards | `block_executor` / `task_executor` | Active, current, well-tested |
| Goal (`/goal`) | Task Card runtime (via `goal_synthesis.py`) | **Already unified** — the reference implementation |
| Swarm (`delegate-tasks` fence → launch button) | `DelegateManager` (separate runtime) | Stale (Jul 2026), Bedrock-only hard gate, no permission scoping, no event replay, no run store. 77/77 unit tests pass — coherent but integration-rotten |
| Delegate (model-invoked fan-out tool) | **Does not exist natively.** The `task_decomposition` skill emits a `delegate-tasks` fence and waits for a human click — the model cannot fan out on its own | Missing |
| Plan (reviewed multi-phase execution) | Does not exist. Closest concept: "staged goal" in `TaskBinding` | Prospective |

The DelegateManager runtime duplicates ~80% of what the task-card runtime does
better (provider abstraction, per-block permissions, event replay with
reconnect, durable run store, artifacts, signing, recovery). Reviving it means
re-solving all four in a second codebase.

**Decision this document proposes:** there is exactly ONE execution runtime —
the task-card block executor. Every entry point is a *face*: an entry adapter
that synthesizes a card, plus a presentation that renders the run. The swarm
runtime is ported and retired, the delegate tool is built natively on the task
runtime, and plan becomes a thin face over staged cards.

## 2. The Face contract

A **face** is defined by four things:

```
Face = (EntryAdapter, PresentationBinding, ResultRouting, ToolOverlay?)
```

1. **Entry adapter** (backend): converts face-specific input into a
   `TaskCardCreate` (+ launch parameters). `goal_synthesis.synthesize_goal_card`
   is the archetype. Adapters live in one module family
   (`app/faces/` — move goal synthesis there as the first citizen).
2. **Presentation binding**: a `presentation` field persisted on the card and
   stamped onto each `TaskCardRun` (`"card" | "goal" | "delegate" | "swarm" |
   "plan"`), plus a freeform `face_meta` dict (both models already have
   `extra="allow"`; formalize the fields). The frontend selects a renderer by
   `presentation`; unknown values fall back to the standard card tile —
   forward-compatible by construction.
3. **Result routing**: where output lands.
   - goal → TaskBinding to the launching chat (exists)
   - delegate → returned inline as the tool result in the calling stream
   - swarm → folder of materialized worker conversations + run record
   - plan → staged card bound to chat; artifacts per phase
4. **Tool overlay** (optional): face-specific tools injected into block
   execution (swarm's peer bus). Overlay tools are *additive within the
   block's existing scope* — they can never widen writable paths or the tool
   floor, so signing and permission snapshots remain authoritative.

What every face inherits for free by being a card: provider abstraction (all
endpoints, not just Bedrock), per-block `TaskScope` (writable paths, tool
floor), permissions snapshot / audit trail, signing + refusals, event replay
via `task_run_stream_relay`, durable runs + artifacts in
`storage/task_runs.py`, recovery, schedules, self-improvement, lessons.

## 3. Entry-point mappings

### 3.1 Goal — done; refactor only
- Move `app/utils/goal_synthesis.py` → `app/faces/goal.py` (keep a
  compatibility re-export at the old path).
- Replace the `GOAL_TAG = "goal"` tag convention with the formal
  `presentation="goal"` field; keep writing the tag during a deprecation
  window so existing bindings/queries don't break.
- No behavior change. This phase exists to *establish the face registry with
  a known-working face* before porting anything risky.

### 3.2 Swarm — port the entry, retire the runtime
The `delegate-tasks` fenced spec is preserved verbatim as the wire format
(the `task_decomposition` skill keeps emitting it; existing conversations keep
their launch buttons). The adapter maps:

```
{name, description, delegates[]}          TaskCardCreate
  delegate.scope + files          →   task block instructions + TaskScope
  delegate.model_tier/model_*    →   per-task model fields (exist already)
  delegate.emoji/name/color      →   block emoji/name + face_meta
  delegates[] as a set            →   ONE parallel block containing all workers
  delegate.dependencies          →   NEW: Block.depends_on (see E1)
  roles (coordinator/verifier)   →   ordinary tasks whose depends_on fan-in
                                      from the workers they merge/check
```

`DelegateLaunchButton` re-targets from `POST .../launch-delegates` to the
task-card create+launch API. `SwarmRecoveryPanel`'s retry/skip/cancel map to
the task-run equivalents (retry-block, skip-block, cancel-run — gap analysis
in Phase 4 confirms which exist).

### 3.3 Delegate — new native tool on the task runtime
A builtin tool (`delegate`) with the shape the external archetype established:

```
delegate(prompts: [{identifier, prompt, dependentIdentifiers?,
                    configuration?: {model|model_tier, readonly, parallel}}])
```

Adapter synthesizes an *ephemeral* card (not saved to the library unless the
user promotes it): a parallel block with `depends_on` edges, per-task
model tier, readonly ⇒ a scope with an empty writable set.

The hard part is **foreground execution**: task runs are background objects;
the delegate tool must launch a run and *await* its completion from inside a
tool call, then return the per-identifier results (ordered as input) as the
tool result. Requirements:
- an awaitable launch path in the run manager (launch → asyncio Event / future
  resolved on terminal status);
- recursion guard: a delegate-spawned task may not itself call `delegate`
  beyond depth N (default 1) — the swarm runtime's subplan recursion showed
  why unbounded nesting is a footgun;
- the run is still a real TaskRun: visible in run history, replayable,
  attributed to the calling conversation via TaskBinding;
- cancellation: killing the calling stream cancels the child run.
- concurrency: reuse the repeat/parallel concurrency cap machinery
  (`repeat_max_concurrency` default logic) rather than a new semaphore.
- the launch/await path is the shared spawn primitive that E6 dynamic
  spawning also rides — build it as `spawn(spec) → handle` /
  `await(handle) → result`, not as delegate-tool-private machinery.

### 3.4 Plan — thin face, gated by an explicit go/no-go
Definition: a multi-phase plan authored in conversation (by model or user),
**staged for human review, then executed with approval gates**. Mapping:
- phases → `group` blocks in sequence, `on_failure="stop"`
- review gates → `ask` blocks between phases
- staging → the existing "staged goal" TaskBinding state (a binding may exist
  before its run does — the model field comment says exactly this)
- presentation → phase checklist view; each phase's artifact inline

Net-new code is small (an adapter + a renderer); the runtime already has
everything. Whether it's worth a distinct face vs. "the card editor is the
plan view" is an open product question — the deck makes it a separately
skippable phase with an `ask` gate.

## 4. Runtime extensions required

**E1 — Sibling dependencies in parallel blocks (fan-in DAG).**
`Block.depends_on: List[str]` (sibling block ids, meaningful only for children
of a `parallel`). Parallel executor becomes a DAG scheduler: a child starts
when all its `depends_on` siblings completed successfully; a failed dependency
cascades (child marked failed-upstream, matching DelegateManager semantics).
Validation refuses: cycles, ids not among siblings, `depends_on` outside a
parallel body. This is the single biggest structural gap — task-card trees
express series/parallel nesting but not diamonds/fan-in.

**E2 — Peer coordination overlay (the "multidirectional bus").**
Opt-in flag on `parallel` (e.g. `peer_coordination: bool`). When set, children
receive a tool overlay re-hosting `swarm_tools.py` semantics on run-scoped
state: shared claimable task list (`peer_task_list/claim/complete/add`),
broadcast notes, `peer_query_result` (read a completed sibling's artifact
mid-run). Backed by the run record (persisted, survives restart), not an
in-memory singleton. Directional flow remains the *structure*; the bus is an
overlay on concurrent regions — this resolves the swarm-vs-task-card
philosophical split.

**E3 — Compact propagation ("crystals").**
`propagate: "compact"` mode alongside `none|last|all`: instead of raw output,
a bounded compaction (summary + files-changed + decisions) crosses the block
boundary. Port the shape of `MemoryCrystal`, not the regex-scraping stub
fallback. Applies to repeat propagation and to what `depends_on` children
receive from their dependencies.

**E4 — Worker-as-conversation materialization (opt-in).**
Face-level option: each parallel child's transcript materializes as a real
Chat in a folder (swarm face default, off elsewhere). Preserves swarm's
signature UX — open a worker, watch it live, converse with it afterward.

**E5 — Composition graph view.**
Generalize `SwarmFlowGraph` → `TaskRunGraph`: renders ANY task run as a DAG
(blocks as nodes: structural edges from the tree, dependency edges from
`depends_on`), fed by `useTaskRunStream` events instead of 3s polling.
Fix the hardcoded dark-theme `STATUS_COLORS` while porting. This view benefits
every card, not just swarm-shaped ones.

**E6 — Dynamic spawning (runtime tree growth under a non-escalation envelope).**
Port of `swarm_request_delegate` / `swarm_launch_subplan`. A running task may
spawn additional sibling work at runtime — with any degree of directionality
(`depends_on` edges onto existing or spawned siblings) and cross-communication
(a spawned child joins the peer bus when its region has one, E2).

The security model that makes this safe is **monotonic non-escalation**:

- A spawned block's effective scope is the *intersection* of the requested
  scope and the spawner's effective scope — writable paths, tool allowlist,
  and shell grants can only narrow, never widen. Reuse the
  `merge_scopes`/`find_scope_chain` machinery; the spawn path adds a
  narrowing merge that **refuses** any request outside the envelope rather
  than silently clamping it (explicit refusal keeps the audit trail honest).
- What the user signs is therefore a *permission envelope*, not a tree shape:
  runtime growth never changes what was approved. Signing gains exactly one
  new bit — `TaskScope.allow_spawn` — so a card that may grow at runtime says
  so on its signed surface; cards without it never receive spawn tools.
- Model tier is a cost dimension, not a security one: a spawner may select
  any tier for its children. This is the old per-delegate `model_tier`
  preserved, and it composes well — an orchestrating block on a frontier
  model fanning work out to cheap executors.
- Spawned children count against the *same* run-level concurrency cap as
  planned children (spawning is not a cap bypass), share the run's recursion
  depth guard with the delegate tool, and are recorded in the run event
  stream with both requested and effective scope — the graph view (E5)
  renders them as runtime-added nodes.

The delegate tool (§3.3) and dynamic spawning share one primitive underneath:
`spawn(spec) → handle`, `await(handle) → result`. The delegate tool is
spawn-then-await into a fresh ephemeral run from a conversation; dynamic
spawning is spawn into the *current* run's active parallel region. Build the
primitive once (Phase 6), expose its second face in Phase 6b.

## 5. Frontend generalization

A renderer registry keyed by `presentation`:

| presentation | renderer | notes |
|---|---|---|
| `card` (default/unknown) | `TaskCardInlineTile` | exists |
| `goal` | current goal binding UI | exists |
| `swarm` | `TaskRunGraph` + worker-chat folder + recovery actions | E5 + E4 |
| `delegate` | compact inline tool-result rendering in the calling stream | mostly the existing tool-block UI |
| `plan` | phase checklist with ask-gate affordances | small |

`useDelegatePolling` / `useDelegateStreaming` retire in favor of
`useTaskRunStream` (which already replays).

## 6. Deprecation & migration

After swarm parity (Phase 4–5 validated):
- Retire: `app/agents/delegate_manager.py`, `delegate_stream_relay.py`,
  `app/api/delegates.py` (6 endpoints), `useDelegatePolling`,
  `useDelegateStreaming`, `SwarmRecoveryPanel` (replaced),
  `models/delegate.py` (keep `MemoryCrystal` shape if E3 reuses it).
- Keep: `swarm_scratch.py` semantics (per-worker scratch dirs) — re-point at
  task-run workspace conventions.
- Migration: existing `ChatGroup.taskPlan` folders are historical records —
  render read-only ("legacy swarm run"), do not convert. New launches from old
  `delegate-tasks` fences in old conversations go through the new adapter.
- The 77 swarm tests: port the *semantic* ones (dependency cascade, stub
  handoff, recovery actions) as task-runtime tests; delete runtime-specific
  ones with the runtime.

## 7. Open decisions (flagged, not resolved here)

1. `depends_on` on Block vs. a parallel-level edge list — Block-level chosen
   here for editor simplicity; revisit if validation gets awkward.
2. Plan face: distinct presentation vs. "the editor is the plan view".
   Deck gates this with an `ask`.
3. Should the delegate tool's ephemeral cards appear in run history by
   default, or only when they fail? (Proposed: always — auditability wins.)
4. E4 materialized worker chats: real Chats (heavier, full feature set) vs.
   read-only transcript views (lighter). Proposed: real Chats to preserve the
   converse-with-a-worker affordance.
5. Spawn granularity (E6): may a spawned unit be a container (a nested
   parallel sub-region, the old `swarm_launch_subplan`) or only a task leaf?
   Proposed: leaf-only initially — subplan-shaped growth is expressible as a
   spawned task that `call`s a card — and revisit if that proves clumsy.
   Either way the non-escalation envelope and depth guard apply unchanged.

## 8. Validation strategy (applies across the deck)

- Every phase ends with its own test gate; the deck's containers use
  `on_failure="stop"` so a failed gate halts downstream phases.
- Seam tests over unit tests: adapter output must be validated by actually
  LAUNCHING the synthesized card on the real runtime, not by asserting JSON
  shape alone.
- The swarm-parity gate (Phase 4) is behavioral: the same `delegate-tasks`
  spec that drove the old runtime must produce equivalent observable results
  (worker fan-out, dependency ordering, failure cascade, recovery actions)
  on the new one.
- Regression floor: `python3 -m pytest tests/` for touched areas plus the
  frontend suites for touched components, per phase — not one big sweep at
  the end.

## 9. The execution deck

Block tree for a launchable card. Notes for the operator:
- `ask` gates sit at the two riskiest transitions (before runtime-extension
  work; before deprecation/deletion) and before the optional plan face.
- Phases assume `depends_on` does not exist yet, so the deck itself is a
  *sequence* (group), not a parallel DAG — the deck cannot use the feature
  it is building.
- Scopes are deliberately narrow per phase; widen only if a phase's work
  legitimately demands it.

```json
{
  "block_type": "group",
  "name": "Unified Task Faces — port swarm/delegate/plan onto the task runtime",
  "on_failure": "stop",
  "body": [
    {
      "block_type": "task",
      "name": "Phase 0 — Baseline & inventory",
      "emoji": "📋",
      "instructions": "Establish the pre-change baseline. 1) Run the swarm suites: python3 -m pytest tests/test_swarm_scratch.py tests/test_swarm_tools.py tests/test_swarm_recovery_api.py tests/test_swarm_recovery_rehydration.py tests/test_recursive_swarms.py -q — record pass counts. 2) Run the task-card backend suites (tests/ files matching task_card, block_executor, task_executor, task_runs) and record results. 3) Read design/unified-task-faces.md §3-§6 fully. 4) Produce an artifact listing: every file in the swarm stack with line counts; every API route in app/api/delegates.py with its frontend callers; the exact set of task-run lifecycle actions available today (retry/skip/cancel per block or per run) by reading app/agents/block_executor.py and app/api routes — this confirms or corrects the §3.2 assumption about recovery-action parity. Do not modify any code in this phase."
    },
    {
      "block_type": "task",
      "name": "Phase 1 — Face registry + presentation field",
      "emoji": "🎭",
      "instructions": "Create app/faces/ with a face registry: face id → synthesizer callable + presentation defaults. Move goal synthesis: app/faces/goal.py holding synthesize_goal_card, with app/utils/goal_synthesis.py becoming a re-export shim (grep all importers first and verify none break: app/api/commands.py, app/cli.py, tests). Add formal fields to TaskCard and TaskCardRun in app/models/task_card.py: presentation: str = \"card\" and face_meta: Optional[Dict[str, Any]] = None. Stamp presentation onto runs at launch. Keep writing the legacy \"goal\" tag alongside presentation=\"goal\". Update /goal handlers to go through the registry. Tests: registry resolution; goal synthesis via registry produces a card identical to the pre-refactor output (snapshot comparison); presentation field persists through storage round-trip and appears on the run; unknown presentation value renders as default card tile (frontend test in the TaskCard suite). Run the goal-related test suites and the full task-card storage suites.",
      "scope": null
    },
    {
      "block_type": "ask",
      "name": "Gate — approve runtime extension work",
      "ask_question": "Phase 0-1 complete: face registry established, goal refactored onto it, baselines recorded. Phases 2-3 modify the block executor itself (DAG dependencies, peer bus). Review the Phase 0 artifact and Phase 1 diffs. Proceed?",
      "ask_choices": ["proceed", "stop"]
    },
    {
      "block_type": "task",
      "name": "Phase 2 — E1: depends_on DAG scheduling in parallel blocks",
      "emoji": "🕸️",
      "instructions": "Add depends_on: List[str] to Block (sibling ids; meaningful only for children of a parallel block). Extend the parallel executor in app/agents/block_executor.py into a DAG scheduler: a child is eligible when all depends_on siblings completed successfully; on a dependency's failure, mark dependents failed with reason 'upstream dependency failed' (cascade, matching the old DelegateManager._resolve_and_start semantics). Respect the existing parallel concurrency cap for eligible children. Validation (app/utils/task_card_validation.py): refuse cycles, refuse depends_on naming a non-sibling, refuse depends_on outside a parallel body — with actionable messages. Emit dependency-edge info in run stream events so the graph view (Phase 5) can draw them. Tests: diamond DAG executes in correct order (A → B,C → D with D seeing both); failure cascade; cycle refused at validation AND at plan time; concurrency cap still honored when dependencies gate eligibility; a plain parallel block with no depends_on behaves byte-identically to before (regression). Run the full block-executor and validation suites."
    },
    {
      "block_type": "task",
      "name": "Phase 3 — E2: peer coordination overlay",
      "emoji": "🐝",
      "instructions": "Add peer_coordination: bool = False to Block (parallel only; validation refuses elsewhere). When set, inject a tool overlay into each child's execution: peer_task_list, peer_claim_task, peer_complete_task, peer_add_task, peer_note, peer_query_result — porting the SEMANTICS of app/agents/swarm_tools.py but backed by run-scoped state persisted in the task-run record (storage/task_runs.py), not an in-memory manager. peer_query_result returns a completed sibling's artifact (respecting E3 compaction when that lands — for now, artifact summary). Overlay tools must be additive within the block's existing scope: they cannot write files or widen permissions. Concurrency: claims must be atomic under parallel access (the old system used an RLock; the run store needs an equivalent). Tests: port the claim/complete/add/list semantics tests from tests/test_swarm_tools.py; two concurrent children racing to claim the same task — exactly one wins; state survives a simulated restart (rehydrate from run record); overlay absent when peer_coordination is false; overlay tools invisible to signing scope-bytes (permissions unchanged)."
    },
    {
      "block_type": "task",
      "name": "Phase 4 — Swarm face: adapter + launch retarget + recovery parity",
      "emoji": "🚀",
      "instructions": "Backend: app/faces/swarm.py — synthesize_swarm_card(spec) mapping the delegate-tasks JSON (name, description, delegates[] with scope/files/dependencies/model_tier/emoji/skill_id) to a card: one parallel block with peer_coordination=true, one task child per delegate, depends_on from dependencies, per-task model tier and TaskScope from files, presentation='swarm', face_meta carrying emoji/color per block id. Route: either a new endpoint or (preferred) the existing card create+launch API with the adapter invoked by the command layer. Frontend: DelegateLaunchButton re-targets to the new path — keep the fence parsing and preview modal exactly as-is; only the POST changes. Map recovery actions using the Phase 0 findings: retry/skip/cancel against task-run APIs; if a per-block retry does not exist in the runtime, ADD it (this is the one place parity may require a new runtime endpoint) rather than silently dropping the affordance. SEAM TEST (the phase gate): take a real 3-delegate spec with a diamond dependency and a verifier, launch it through the adapter on the real runtime end-to-end (cheap model tier), and assert: all workers ran, dependency order held, verifier received upstream results, run record complete, presentation='swarm' on the run. Then: the same spec with a worker forced to fail — assert cascade + retry works. Port the semantic recovery tests from tests/test_swarm_recovery_api.py."
    },
    {
      "block_type": "task",
      "name": "Phase 5 — E5: TaskRunGraph view (port SwarmFlowGraph)",
      "emoji": "📊",
      "instructions": "Create frontend/src/components/TaskRunGraph.tsx generalizing SwarmFlowGraph: nodes = blocks of a run (any card, not just swarm-shaped), structural edges from the tree, dependency edges from depends_on (distinct visual style), live status from useTaskRunStream events (NOT polling). Reuse the topological-layer layout. Fix the hardcoded dark-theme STATUS_COLORS: derive from the theme context so light mode is readable. Mount it: for presentation='swarm' runs it is the primary view; for ordinary cards it is available as an alternate view on the run tile/inspector. Tests (frontend suite conventions in frontend/src/components/TaskCard/__tests__/): layer assignment for a diamond DAG; status→style mapping in both themes; dependency edges render distinct from structural edges; graph updates on stream events. Keep SwarmFlowGraph untouched for legacy read-only rendering until Phase 8."
    },
    {
      "block_type": "task",
      "name": "Phase 6 — Delegate face: native model-invocable fan-out tool",
      "emoji": "🤖",
      "instructions": "Build the builtin 'delegate' tool (app/mcp/tools/, following existing builtin tool patterns): input prompts[] each {identifier, prompt, dependentIdentifiers?, configuration {model_tier?, readonly?, parallel?}}. Adapter app/faces/delegate.py synthesizes an ephemeral card: parallel block, depends_on from dependentIdentifiers, readonly → TaskScope with empty writable paths, per-prompt model_tier; presentation='delegate'. Runtime: add a foreground-await launch path — the tool launches the run, awaits terminal status (asyncio, no polling), and returns per-identifier results ordered as the input. Requirements from §3.3: recursion depth guard (a delegate-spawned task calling delegate refuses beyond depth 1, clear error); the run is a real TaskRun bound to the calling conversation; cancelling the calling stream cancels the child run; dependency results included in dependent prompts (crystal/compact when E3 exists, artifact summary until then). Tests: two independent prompts run in parallel and both results return in input order; A→B dependency where B's prompt demonstrably receives A's result; readonly delegate cannot write (seam test against the real scope enforcement); recursion guard fires at depth 2; cancellation propagates. Register the tool description so models discover it; update the task_decomposition skill to PREFER the delegate tool for model-initiated fan-out while keeping the delegate-tasks fence for user-reviewed swarm launches."
    },
    {
      "block_type": "task",
      "name": "Phase 6b — E6: dynamic spawning under the non-escalation envelope",
      "emoji": "🌱",
      "instructions": "Expose the second face of the Phase 6 spawn/await primitive: runtime tree growth inside an active run. 1) Add allow_spawn: bool = False to TaskScope in app/models/task_card.py and verify it participates in the signed scope bytes (flipping it must invalidate an existing signature — write that exact test). 2) When a task executes with effective allow_spawn, inject spawn tools (spawn_task, and await_task for handles): spawn_task adds a sibling task block to the CURRENT parallel region at runtime with optional depends_on onto existing or previously-spawned siblings and optional model_tier. 3) Enforce monotonic non-escalation: the spawned block's effective scope is the intersection of the requested scope and the spawner's effective scope, computed via the merge_scopes chain; REFUSE (with an actionable error naming the offending path/tool) any request outside the envelope rather than silently clamping. 4) Spawned children count against the run's existing concurrency cap, share the recursion depth guard with the delegate tool, and join the region's peer bus when peer_coordination is set. 5) Record every spawn in the run event stream with requested AND effective scope; TaskRunGraph renders runtime-added nodes with a distinct marker. Tests: spawned readonly child cannot write (seam test against real enforcement, not a mock); spawn requesting a writable path outside the spawner's envelope is refused; card whose scope lacks allow_spawn never receives spawn tools; flipping allow_spawn invalidates an existing signature; spawned child appears in run record, stream events, and graph; concurrency cap holds when spawns exceed remaining slots; depth guard fires on spawn-from-spawn beyond the limit; a spawned child with depends_on on a planned sibling starts only after that sibling completes."
    },
    {
      "block_type": "ask",
      "name": "Gate — optional plan face",
      "ask_question": "Core unification is done (goal/swarm/delegate on one runtime). Phase 7 adds the PLAN face: staged multi-phase cards with ask-gates between phases, presented as a reviewable checklist. This is the 'possibly' item — small but net-new UI. Include it?",
      "ask_choices": ["include plan face", "skip to phase 8"]
    },
    {
      "block_type": "task",
      "name": "Phase 7 — Plan face (if approved)",
      "emoji": "🗺️",
      "instructions": "If the gate answered 'skip to phase 8', immediately report skipped and do nothing. Otherwise: app/faces/plan.py — synthesize_plan_card(phases[]) mapping phases → sequential group blocks with on_failure='stop' and ask blocks between phases; presentation='plan'. Entry: a /plan command through app/api/commands.py mirroring /goal's structure (and CLI parity in app/cli.py), accepting either explicit phases or a model-drafted plan; use the staged-binding state (TaskBinding without a run) for the review period. Frontend: a phase-checklist renderer keyed to presentation='plan' — phases with status, current ask-gate surfaced prominently; reuse the ask-block answer UI. Tests: synthesis shape; staged binding lifecycle (stage → review → launch → phase 1 → gate → answer → phase 2); a rejected gate stops the run; command parsing for /plan in both web and CLI paths."
    },
    {
      "block_type": "task",
      "name": "Phase 8a — E3 compact propagation + E4 worker chats (opt-in features)",
      "emoji": "💎",
      "instructions": "E3: add propagate mode 'compact' alongside none|last|all: a bounded compaction (summary, files changed, key decisions — port the MemoryCrystal SHAPE from app/models/delegate.py, not the regex-scraping stub fallback from delegate_manager) crossing block boundaries; applies to repeat_propagate and to what depends_on dependents receive; peer_query_result returns the compact form when set. E4: face-level option (swarm face default ON) materializing each parallel child's transcript as a real Chat in a run folder — reuse the chat storage APIs the old DelegateManager used, but sourced from task-run events. Tests: compact propagation stays within its token bound and preserves files-changed fidelity on a real multi-file task; worker chat materializes with correct folder linkage and is a normal conversable chat afterward; both features OFF produce byte-identical behavior to Phase 6 output (regression)."
    },
    {
      "block_type": "ask",
      "name": "Gate — approve deprecation & deletion",
      "ask_question": "All faces are live on the unified runtime. Phase 8b DELETES the old swarm runtime (~2,500 backend lines: delegate_manager.py, delegate_stream_relay.py, api/delegates.py, polling/streaming hooks) and converts legacy taskPlan folders to read-only. This is destructive and hard to reverse. Have you exercised the new swarm path on real work at least once? Proceed?",
      "ask_choices": ["proceed with deletion", "stop — keep both runtimes for now"]
    },
    {
      "block_type": "task",
      "name": "Phase 8b — Retire the DelegateManager runtime",
      "emoji": "🪦",
      "instructions": "If the gate said stop, report and do nothing. Otherwise: delete app/agents/delegate_manager.py, app/agents/delegate_stream_relay.py, app/api/delegates.py (and its router registration + websocket endpoint in app/server.py), frontend useDelegatePolling.ts, useDelegateStreaming.ts, SwarmRecoveryPanel.tsx; remove their ChatContext wiring. Keep models/delegate.py ONLY if Phase 8a imported MemoryCrystal from it — otherwise move the shape and delete. Legacy ChatGroup.taskPlan folders: render read-only via a minimal 'legacy swarm run' banner (SwarmFlowGraph may be deleted if TaskRunGraph handles the read-only case; otherwise keep it for legacy display only — decide and record). Re-point swarm_scratch semantics at task-run workspace conventions or delete if unused. Delete runtime-specific tests (test_swarm_recovery_rehydration, test_recursive_swarms, recovery_api) — verify their SEMANTIC coverage was ported in Phases 2-4 first by mapping each deleted test to its replacement; the mapping is this phase's artifact. Full-tree verification: grep for every deleted symbol (get_delegate_manager, delegate_stream_relay, launch-delegates, delegate-status, swarm-budget, retry-delegate, promote-stub, cancel-delegates, ws/delegate-stream) — zero live references; python3 -m pytest tests/ passes; frontend build + suites pass."
    },
    {
      "block_type": "task",
      "name": "Phase 9 — Documentation & changelog",
      "emoji": "📚",
      "instructions": "Update Docs/ for the unified architecture: FeatureInventory.md (swarm/delegate/goal/plan sections reflect the face model; remove claims about the old runtime), task-card docs gain depends_on / peer_coordination / propagate=compact / presentation, document the delegate tool and /plan (if built). Add CHANGELOG.md entries under ## [Unreleased] ONLY (never a numbered section; do not bump pyproject.toml). Update design/unified-task-faces.md status header to 'implemented' with deviations recorded in a short postmortem section — especially any place where actual runtime behavior forced a departure from this plan. Final validation: full backend pytest, frontend test suites, and a manual smoke checklist artifact: /goal works, delegate-tasks fence launches on new runtime, delegate tool fans out inline, graph view renders a diamond DAG in both themes."
    }
  ]
}
```

## 10. Effort shape (for planning, not commitment)

Phases 2, 4, 6 are the substance (DAG executor, swarm parity seam, the
spawn/await primitive). Phase 6b is medium — the primitive exists by then;
the work is the envelope-intersection merge, the signed `allow_spawn` bit,
and runtime insertion into a live parallel region's scheduler (the E1 DAG
scheduler must tolerate its sibling set growing mid-flight — design for that
in Phase 2, even though nothing exercises it until 6b). Phases 1, 5, 9 are
mechanical. Phase 3 is medium (concurrency-safe shared state). Phases 7, 8a
are optional/deferrable. Phase 8b is deletion — cheap but gated. Nothing here
blocks on anything outside the repo.
