# Ziya Feature Inventory

> Reference list of what Ziya currently does. Useful as a quick check of what's available, and as the place to update when features land. Less polished than the philosophy or README — read those first if you're trying to decide whether the project is for you.

---

## 1. Interfaces & Modes

| Feature | Detail |
|---|---|
| **Web UI** | Full-featured browser client at `localhost:6969`. Resizable panels. |
| **CLI chat mode** | `ziya chat` — rich interactive terminal with `prompt_toolkit`, autocomplete, syntax highlighting, markdown rendering, and multi-line input with paste detection |
| **Project instructions** | Reads `AGENTS.md`, `README.md`, and GUI project config for per-project steering; cross-tool compatible with Claude Code, Kiro, Cline, and Q Developer conventions |
| **Auto context scaling** | Automatically selects 200k–1M token context window based on model capability |
| **CLI one-shot mode** | `ziya ask "question"` — prints answer and exits; composable with pipes (`git diff \| ziya ask "review this"`) |
| **CLI code review** | `ziya review [--staged\|--diff]` — review staged changes, unstaged diff, or piped content |
| **CLI explain** | `ziya explain <files>` — focused code explanation |
| **Ephemeral / incognito mode** | `ziya chat --ephemeral` — session history is never written to disk |
| **Session persistence** | CLI sessions auto-saved to `~/.ziya/sessions/`, including full conversation history and context file list |
| **Session resume** | `ziya chat --resume` or `/resume` in-session — interactive picker to restore any of the last 10 sessions |
| **Session suspend** | `/suspend` — gracefully save and exit; re-enter exactly where you left off |
| **Pipe / stdin support** | Any `ziya` command accepts piped stdin (log files, diffs, error output, raw text) |
| **Raw Markdown Toggle** | `Ctrl+Shift+U` toggles the conversation between rendered and raw markdown view, showing the pre-rendered source with all fence markers, inline formatting, and whitespace preserved verbatim — useful for diagnosing rendering issues or copying raw content for reprocessing. The raw/pretty choice is stored per conversation and survives restarts (synced across tabs), not just a transient view toggle.<br>Also called: raw/rendered toggle, markdown source view, pretty print toggle, view mode, presentation mode <!-- cap: fcm-display-mode-toggle --> |
| **Keyboard shortcuts** | `Ctrl+Shift+U` raw/rendered toggle · `Ctrl+Shift+D` health debug · `Ctrl+Shift+G` conversation graph · `Ctrl+R` reload |

---

## 2. Agentic & Orchestration

| Feature | Detail |
|---|---|
| **Swarm / multi-agent (Delegate system)** | Orchestrator decomposes a task into parallel delegates; each delegate runs independently with its own context and 9 coordination tools. Requires an AWS Bedrock endpoint — delegate routing is unavailable on Anthropic-direct, OpenAI, Google, or Ollama | <!-- cap: swarm-taskplan-launch -->
| **Recursive sub-swarms** | Delegates can spawn their own full sub-swarms via `swarm_launch_subplan`; supports arbitrary nesting depth |
| **Swarm coordination tools** | 9 built-in swarm tools per delegate: `swarm_task_list`, `swarm_claim_task`, `swarm_complete_task`, `swarm_add_task`, `swarm_note`, `swarm_query_crystal`, `swarm_read_log`, `swarm_request_delegate`, `swarm_launch_subplan` |
| **Crystal compaction** | When a delegate completes, its output is compacted into a "crystal" (memory summary) that downstream delegates can query via `swarm_query_crystal` |
| **Progressive checkpointing** | Delegate state checkpointed every ~4000 chars; on crash, a self-rescue continuation delegate picks up where the stream died |
| **Stall watchdog** | Delegates silent >10 min with no active children are automatically flagged as stalled |
| **TaskPlan sidebar tree** | Swarm plans nest under their source conversation in the sidebar with live progress badges and status icons |
| **Parallel streaming** | All running delegates stream live simultaneously; sibling WebSocket connections kept open while a swarm is active |
| **Tool execution loop** | Model autonomously calls tools in a multi-round loop (read files, search, run shell commands, web search, etc.) before returning a final answer |
| **Diff validation feedback loop** | After generating diffs, Ziya validates them via a dry-run apply; if they fail, it appends targeted repair feedback to the conversation and restarts the stream once for a corrected response |

---

## 2a. Task Cards

Durable, cross-session work units anchored to a chat. Distinct from chat streaming (one-shot) and swarm (parallel decomposition).

| Feature | Detail |
|---|---|
| **Eight composable block types (recursive block-tree grammar)** | A Task Card is a saveable, re-runnable *tree* of blocks over one discriminated grammar with eight kinds — **task** (one model turn / leaf), **repeat** (count / until-substring / for_each loop), **parallel** (concurrent fan-out), **until** (model-judged loop), **schedule** (recurring trigger), **state** (run-scoped variables + standing prose), **group** (run-once sequential container), and **call** (invoke another named card inline). Blocks nest, and stacking blocks in a body is an implicit top-to-bottom sequence. There is no conditional/branch (if/switch) block and no goto. <br>Also called: workflow deck, automation blueprint, block DAG, agentic workflow graph, task recipe / playbook. <!-- cap: taskcard-block-tree-grammar --> |
| **Repeat loops in three modes, with a concurrency cap** | The Repeat block loops its body a fixed **count** (bounded by `repeat_max`), **until** a substring appears in the iteration artifact's summary, or **for_each** over a JSON array (binding `{{item}}` per pass). `repeat_parallel` runs iterations concurrently, bounded by `repeat_max_concurrency` (default 8) so a wide fan-out cannot overrun the provider. `repeat_propagate` (none/last/all) controls which prior-iteration artifacts each pass sees. <br>Also called: for/while loop block, map-over-list / fan-out, batch iteration, parallel loop, matrix/strategy loop. <!-- cap: taskcard-repeat-loop-block --> |
| **Model-judged loop termination (`until` block)** | Distinct from Repeat's substring stop: an `until` block re-runs its body until a cheap-tier model judges a natural-language condition satisfied (a strict yes/no classifier). Any transport or parse failure resolves to *keep looping*, so it terminates conservatively rather than early; bounded by `until_max`. The `expression` (deterministic) mode is reserved in the UI but not yet implemented. <br>Also called: condition loop, goal-satisfied loop, convergence loop, acceptance-gate loop, reflexion/critic loop. <!-- cap: taskcard-until-model-evaluator --> |
| **Run-scoped variables and standing context (`state` block)** | A read-only "givens" block that declares run-scoped named variables (read via `{{var.NAME}}`) and/or freeform standing prose that flows into every in-scope task's context as a preamble. Nothing writes back — read-only preserves the sandbox invariant that only artifacts cross task boundaries. Placement is the reset policy: a State in a once-running body applies once per run; inside a loop body it re-applies each iteration. No mutable/accumulating run state, and values are untyped literals. <br>Also called: run variables/constants, given/assumption block, scenario setup, blackboard variables, fixture/setup block. <!-- cap: taskcard-state-block --> |
| **Sequential container with a failure policy (`group` block)** | The `group` block runs its body top-to-bottom exactly once (the explicit form of the implicit-sequence rule). Every container's body is governed by `on_failure`: `continue` (default) lets later siblings run after a child fails; `stop` halts at the first failed child. A child that *raises* is converted to a failed artifact so the policy still governs rather than the exception unwinding the run. Parallel is exempt. The policy is binary — no retry-N, per-block catch, or rollback. <br>Also called: sequence / block group, run-once container, step group, fail-fast vs continue-on-error, continue-on-error (GitHub Actions). <!-- cap: taskcard-group-sequence-and-failure-policy --> |
| **`{{…}}` templating for passing data between blocks** | A pure (no-I/O) Mustache-style engine substitutes an author's placeholders into a Task's instructions at dispatch: `{{index}}`, `{{item}}`/`{{item.KEY}}`, `{{previous.summary\|decisions\|outputs.NAME}}`, `{{var.NAME}}` (State vars), `{{sibling("block-id")…}}` (any completed block's artifact by id), and the loop-aware `{{…outputs_all.NAME}}` that gathers *every* iteration's named part as a JSON array. Unknown placeholders are left verbatim so typos surface; non-string values render as compact JSON so a downstream `for_each` can parse them. Text substitution only — no expressions, arithmetic, or conditionals. <br>Also called: prompt templating, variable interpolation, mustache/handlebars substitution, output piping, data passing between steps. <!-- cap: taskcard-template-substitution --> |
| **Each task runs in an isolated conversation (instructions down, artifacts up)** | The engine's core invariant: a task's conversation never leaves its task. Each Task block runs in a fresh conversation seeded with no parent transcript; only the distilled artifact (summary / decisions / typed outputs / self-assessment) flows back up, never the raw transcript. This is what keeps wide fan-outs and deep call trees tractable without context blow-up, and what enforces the artifacts-only boundary Call and State depend on. Cross-task communication is deliberately narrow (artifacts plus a shared run scratch dir); a task cannot ask a sibling a question mid-run. <br>Also called: sub-agent context isolation, sandboxed task conversation, context firewall, artifact-only handoff, fresh conversation per block. <!-- cap: taskcard-conversation-isolation-sandbox --> |
| **A for-each loop can assert it covered its whole roster** | `repeat_require_complete` makes a for-each Repeat FAIL at exit unless every roster member has a passing iteration, naming the missing members on the loop's artifact so the enclosing `on_failure` governs. It exists because the reverse — a loop reporting success over a set it did not finish — is silent by construction: a downstream stage reading an output directory sees only what ran. Iterations now carry the identity of the member they ran (`repeat_item_key` names it for object items; scalars key themselves), which is what lets a shortfall NAME the gaps rather than merely count them. Refused up front where it cannot mean anything: alongside a finite `repeat_max` (a cost ceiling and a completeness requirement contradict — bound cost with `repeat_max_concurrency`), on a duplicate or unnameable roster, or on a source that did not resolve. Its limit is deliberate and worth knowing: coverage is *status*-shaped, so an iteration that reports success while writing nothing still counts as covered. Currently authorable in a card's JSON or via the `task_card_write` tool; the block editor does not yet expose it |
| **Block references are checked before launch, and a model authoring a card gets the findings immediately** | `{{sibling("id")}}` takes a block **id**, and ids are minted on save where an author never sees them — so the natural mistake is to reference a block by its *name*, which renders empty and, in a `for_each` source, fails the loop with zero iterations after the upstream work has run. Launch now refuses a `sibling()` reference that names no block in the card, and when it matches a block's name the finding quotes the id to use (or tells the author to set an explicit `"id"`, which the save keeps). Self, ancestor and descendant references are refused too. At run time an unresolvable precise `for_each` source says which hop missed (unknown block id / no part of that name / no such key / not an array) instead of one generic remedy. A model composing a card can call `task_card_validate` on the unsaved tree and get the same findings — the `task_cards` skill instructs it to — and `task_card_write` returns them after every save, so a bad reference is corrected in the same turn rather than discovered from a failed run |
| **Ask blocks (human-in-the-loop checkpoint)** | An `ask` block holds the run at a block boundary with status `awaiting_input` until a human answers, then binds the answer into the run the way a State block binds its literals (`{{var.NAME}}` plus a standing prose note). Approve continues; reject returns a failed artifact so the enclosing container's `on_failure` governs, rather than introducing a second control-flow mechanism. Deliberately no timeout: a checkpoint that auto-approves presents as oversight while providing none. The answer is stored on the run record keyed by block id, which is what makes an Ask idempotent across both a resume walk and a server restart — a waiting run reconciles to `held` (not `failed`) with its question intact, so "answer, then resume" replays to the Ask and finds it already settled. Refused inside a parallel container or a loop body, because a run records one open question and answers key per block; both are validation errors an author sees before launching. |
| **Call blocks (reuse a named unit of work)** | A `call` block invokes another task card in the project, or a named `tasks.yaml` file task, by name — resolved at run time and executed inline in the caller's run, with the callee's artifact becoming the call block's own. Permissions deliberately do NOT cross the boundary in either direction: the callee runs under its own signed approval, which is what stops a caller from lending grants it never earned *and* what lets the callee's own approval verify at all (a leaf's escalation is authorized by hashing its full merged scope, so a leaked caller grant would change the hash and floor an approved callee). Bounded by a call-depth cap and cycle rejection, both of which surface as a failed artifact so the surrounding `on_failure` policy governs the outcome |
| **Called work is visible and audited, not a black box** | A callee lives in a different card, so its tree is in neither the caller nor the run's card snapshot. The resolved tree is recorded on the run when the call executes, which is what lets the run map draw the callee's rows beneath the call row — marked `called`, since those blocks belong to another card and run under its permissions. The same record carries the callee's effective per-block scopes into the run's permissions snapshot, so the "did this run change my workspace?" banner sees a called task's write grants. Without it that intersection came up empty and the banner reported *no* hazard for a callee that held write access |
| **Inline task tile** | Anchored to a chat message; renders live status, current block, and a built-in inspector (Iterations / Tool Calls / Events tabs) |
| **Readable prose across tool calls** | A task's narration arrives as separate text runs with a gap wherever the agent paused to use a tool — the executor emits no text across that boundary. The gap is closed with a paragraph break in both the live stream and the stored artifact summary, so output reads as paragraphs instead of running sentences together, and a heading written after a tool call still parses as a heading. Skipped when the agent already ended its run with a line break, so its own formatting is never doubled |
| **Run map with a legible active stage** | Indented map of the block tree with per-block state. The running stage is marked four independent ways — accent bar, row tint, weighted label, and a `running` chip — so it stays findable on a light theme, under the pointer, in monochrome, and with `prefers-reduced-motion` set. Pulses grow rather than fade, so the in-flight element is never the dimmest on the row. Loop rows show an iteration dot strip instead of the chip; on a run resumed mid-loop, the iterations it preserved appear as dimmed dots ahead of the ones it executed — keeping their pass/fail colour, and excluded from the run's own progress counts. A loop running its iterations in parallel draws one pulsing dot **per iteration in flight**, so an 8-wide fan-out is visibly distinct from a serial loop; each concurrent iteration's text and tool calls are attributed to its own inspector bucket, since a parallel loop's iterations all share one block id and are told apart by ordinal |
| **History replay on reconnect** | Server-side bounded ring buffer (1000 events per run) with on-the-fly delta collapse; switching away from a conversation and back replays everything that happened during the absence rather than starting blank. A 5-minute grace period keeps history after a run terminates for late reconnects | <!-- cap: sched-task-run-stream-relay --> |
| **Distinct gear affordance in conversation list** | Conversations with non-terminal task runs show a spinning gear ("Task running…") instead of the dots-spinner used for chat streaming, so the user can tell which kind of work the conversation is waiting on. The per-conversation run counts behind the gears come from a cached run-status projection that avoids decrypting every run record on each poll | <!-- cap: run-status-index --> |
| **The deck says which cards are live, and links into their runs** | The card list badges `Running · N` and `Attention · N` per card, and selecting a card lists its runs — each a link that navigates to the conversation the run is anchored in. Previously the deck's only statement about run history was a `run_count` integer, so a study still running (or one that had failed hours earlier) was reachable only if you remembered which conversation you launched it into. `paused` and `queued` count as live, not just `running`: a paused run is stopped *waiting on you*, which is the state most in need of being visible. `cancelled` is excluded from Attention — you stopped it deliberately, and badging it beside real failures teaches you to ignore the badge. Live and Attention are separate badges rather than one derived state, because a card can be both at once (a retry running while the failed attempt it came from is still on record). The run rows are links, not a second inspector: the inline tile in the conversation stays the run's home, carrying the run map, artifacts, attempt rail and pause/step/resume controls |
| **Per-block scoped permissions (additive, inherited root→leaf)** | Each block carries its own writable paths (read/write/context flags), allowed tools, allowed skills, shell-command grants (literal first-token or `re:` regex), a per-task shell timeout, and a model selection (portable `model_tier` xsmall…frontier, or an explicit model). Scope is **hierarchical and additive**: `merge_scopes()` unions deck + card + every ancestor container + the leaf, root→leaf, and a more specific layer can only *add*, never revoke (path flags OR together; tools/skills/shell union; timeout takes the max). Only `cwd` and model selection are last-wins (innermost non-null). The executor surfaces the effective set in the agent's system prompt before it tries. Path read/write flags are advisory here — enforcement lives in the write-policy / shell subsystems. <br>Also called: per-task permissions, least-privilege sandbox grants, per-block model selection / model-tier routing, sandbox permission inheritance, capability delegation. <!-- cap: taskcard-hierarchical-scope-model --> |
| **The deck edits a card as a foldable outline beside one block's editor** | Selecting a card in the deck shows its block tree as a compact outline — the same rows the run map draws — with fold carets on containers and one selected block's editor beside it, instead of every block's editor nested and expanded at once. Click a row to edit that block; `⊕ add` appends at the top level. The caret and status columns are present on every row in both edit and run mode, so folding, selecting, or switching mode never moves the row you were looking at. The proposal panel in chat keeps the expanded tree, which suits a card you read once |
| **Escalation is declared before you commit, not after** | A block requesting shell commands or writes outside the default safe set (`.ziya/`, `/tmp/`) is a privilege escalation that needs an out-of-band `ziya-approve` signature. That need is stated on the AI-authored proposal in chat, in its live preview, in the deck list, and on a staged goal tile — and every launch path confirms before starting an unsigned card. The run is never refused (`authorize_scope` clamps the escalating blocks to the floor instead), which is exactly why it must be said up front: otherwise the clamp resurfaces minutes later as an opaque mid-run permission failure. Escalation is computed server-side from the same floor subtraction the runtime gate uses, so no surface can disagree with another about the same card. Signed escalation only verifies after a one-time `ziya root --provision`; on an unprovisioned install (the default) a signature cannot be verified and the run simply clamps to the floor rather than running with the escalated grant | <!-- cap: approval-root-signer-cli -->
| **Copy a card into a conversation without running it** | **Copy to conversation** / **Copy to new conversation** in the deck stage the card as an unlaunched tile — the same shape `/goal` produces — with its own **Run** / **Discard** controls. The tile carries the card's `ziya-approve` command, so this is the route for an escalated card: stage, sign, then run authorized, instead of launching clamped to the floor and discovering it mid-run. Copying is deliberately *not* gated by the unsigned-run confirmation, because nothing executes; the conversation-list gear is likewise not raised, since with no run there is nothing to clear it |
| **A proposed card survives being ignored** | An AI-authored proposal is only persisted when launched, so scrolling past one lost it. **Save to deck** persists without running, and **Sign…** persists it as an *unlisted draft* — signable and runnable, but absent from the deck until you explicitly save it. (Signing needs persisted block ids, since approvals key on them; it never needed a deck entry.) Saved proposals group separately in the deck (`PROPOSED IN CHAT`) so a burst of them cannot bury hand-authored cards, and a never-run card says so rather than rendering as blank |
| **Permissions snapshot at launch** | Effective permissions captured once at run start and stored on the run record; later edits to the card don't rewrite history, so post-mortem can reconstruct exactly what scope a failed run had |
| **Hierarchical permissions UI** | Permissions dialog shows folder rows in primary-color when any descendant has a configured grant (mirrors `MUIFileExplorer`'s "change at a lower level" convention); Files/Tools/Skills/Shell tabs with inheritance overlays for project-policy grants |
| **Structured self-assessment as completion criterion** | Task agent must emit `<self_assessment objective_met="true|false|partial|unknown" rationale="..."/>` at the end of its response; `objective_met="false"` flips the run's `ok` flag regardless of whether the stream ended cleanly, catching tasks that abandoned their stated goal mid-run. A missing tag is distinguished from a present-but-unrecognized `unknown` | <!-- cap: taskcard-self-assessment-capture -->
| **Declared output artifacts (`emit_artifact`)** | Task agents declare durable outputs mid-run — text, files, JSON data, or diagrams rendered-and-frozen as PNGs at emit time (a failed render preserves the error evidence instead); parts carry neutral `group`/`label`/`seq` grouping plus automatic block/iteration attribution, and flow into `Artifact.outputs` for the run tile |
| **Artifact viewer with shape-selected layout** | Frozen diagram renders display inline in the run tile (served through a hardened, decryption-aware blob route that refuses script-capable types inline). Layout is a pure function of group *shape* — 1 part → card, 2 labeled → side-by-side, `seq` present → ordered sequence, ≤6 → grid, else list — so no label string is magic and artifact shapes nobody anticipated still render sensibly. Any group can be forced to the plain list, so a layout misfire cannot hide data |
| **Diff fallback hint on write rejection** | When `file_write` rejects a path outside the writable scope, the rejection text reminds the agent to emit a git diff in its response instead — closes the failure pattern where agents would give up on writing changes after a single denial |

---

## 2b. Work & Knowledge Primitives

Ziya tracks state across three distinct primitives on **different axes** — they
are not tiers of one pipeline, and work never auto-flows into durable memory.
See `design/work-primitives-taxonomy.md` for the full rationale.

| Primitive | Scope | Nature | Visibility | Status |
|---|---|---|---|---|
| **Beads** | conversation | *noticed* — attention-debt (forks not taken, asides, "come back to that") | agent-internal | shipped |
| **Work items** | conversation | *committed* — agreed work with status | user-visible queue | **not yet built** (committed direction) |
| **Memory** | cross-session | *settled* — durable knowledge (facts, decisions, vocabulary, lessons) | user-owned, reviewable | shipped |

- **Beads** are conversational debt the agent maintains silently to avoid
  dropping threads; they live and die with the conversation. The **Backlog
  Browser** (sidebar tab) sweeps parked beads across every conversation in the
  project — grouped-by-conversation or flat age-sorted views, staleness
  indicators (amber ≥ 7 days, warning ≥ 14 days), and per-bead triage actions:
  peek (context without opening), jump to the seam message, resume, branch,
  and undoable abandon/restore.
- **Work items** (planned) are a standalone, conversation-scoped queue of committed
  work — distinct from Task Cards (§2a), which are an *execution engine*, not a
  lightweight agreed-work record. Mirrors the standalone task queue pattern
  seen in peer tools (e.g. Kiro).
- **Memory** is settled, user-owned knowledge fed by post-conversation
  extraction and explicit save — never by an automatic "work completed →
  memory" deposit. Extraction produces *proposals* the user approves; each is
  one self-contained fact/decision/principle (1–3 sentences), admitted by a
  model triage gate, graded for quality, and passed through a structural
  quality gate that drops transient debug-session detail, then held in an
  activity-based probationary lifecycle where high-quality architecture,
  decision, and negative-constraint facts promote on quality alone (durable
  facts taught once no longer decay unread) while other layers keep the
  corroboration-or-use bar; a deterministic global pass keeps the mind-map
  from fragmenting into empty single-memory roots.

Allowed promotions: `bead → work item` (commit a noticed thread). Forbidden:
`work item → memory` (a decision about in-flight work is work state, not
settled knowledge).

### Memory & beads — capability index

| Feature | Detail |
|---|---|
| **Persistent cross-session memory (`memory-flat-store`)** | The durable long-term memory store every other memory feature sits on: a flat JSON file at `~/.ziya/memory/memories.json` holding facts, decisions, vocabulary and lessons with tags, importance, scope and retrieval telemetry, hardened with encryption-at-rest, atomic writes and a cross-process lock. Maturity 4. Limit: one flat file per Ziya-home (not per-project), tuned for hundreds of memories, not millions | <!-- cap: memory-flat-store --> |
| **Learn from a conversation automatically (`memory-post-conversation-extraction`)** | After a substantive conversation Ziya distils durable facts/decisions/vocabulary/lessons into *proposals*: a salience pre-pass skips low-signal chats, code/diffs/tool output are stripped, long conversations are split into ~8-turn topic windows, and candidates land in a probationary queue. Maturity 4. Limit: needs a working extraction model; skips short (<3 human turns) or zero-salience conversations | <!-- cap: memory-post-conversation-extraction --> |
| **Earn-it memory promotion / auto-archival (`memory-proposal-lifecycle`)** | Proposed memories are probationary: a lifecycle sweep promotes them on corroboration or actual use and archives them when they decay unused or duplicate an existing memory. Age is a shared user-activity counter, not wall-clock. Maturity 4 | <!-- cap: memory-proposal-lifecycle --> |
| **Memory Browser UI + management API (`memory-browser-ui-api`)** | A dedicated MemoryBrowser panel and full REST surface (`/api/v1/memory/*`) to search, list, edit, delete and restore memories, review/approve/dismiss proposals, trigger organization, and get/expand the mind-map. Maturity 4 | <!-- cap: memory-browser-ui-api --> |
| **Memory mind-map (`memory-mindmap`)** | A hierarchical knowledge tree over memories: nodes carry a compact handle, parent/children, cross-links and memory references, with recursive expansion, reparenting and tag-overlap auto-filing, exposed to both the model and the browser. Maturity 3. Limit: nodes exist only after the organizer bootstraps them (or manual API creation); tag placement needs pre-existing tagged nodes | <!-- cap: memory-mindmap --> |
| **LLM memory organizer (`memory-organizer`)** | Clusters the memory corpus into thematic domains, extracts supports/contradicts/elaborates relations, bootstraps the mind-map and discovers cross-links; auto-triggers when unplaced memories exceed 15 and also runs on demand as a background task. Maturity 3. Limit: requires an LLM and processes large corpora in batches | <!-- cap: memory-organizer --> |
| **Branch a new conversation from a bead (`bead-branching-fork`)** | A non-destructive fork: a bead records a message-index *seam*, and forking creates a new conversation truncated to that seam, carrying the beads that precede it and leaving the source conversation fully intact. Maturity 3 (backend mechanism complete; the split-from-here UI affordance may be partial). Limit: beads with no recorded seam (pre-feature or unresolvable) cannot be branched from | <!-- cap: bead-branching-fork --> |

---

## 2c. Scheduling & Run Lifecycle

How task-card work is fired on a timer, tracked through its states, observed live, and recovered after a crash. All of this rides the same single-writer task-run machinery as §2a — it is not a separate job service.

| Feature | Detail |
|---|---|
| **Scheduled / recurring task cards (`sched-task-card-scheduler`)** | An in-process loop (15s tick, started with the server) fires a task card on a timer: fixed interval (N minutes/hours/days), one-shot `at` an ISO datetime, `daily_at HH:MM`, or 5-field `cron`. Each fire promotes the card's `schedule` block body to a run and executes it in the background. Per-card firing state persists to `<project>/schedule_state.json`. Maturity 3: interval/at/daily_at are solid and tested; `cron` needs the optional `croniter` package and silently no-ops without it; `schedule_timezone` is declared but ignored (naive local time); tick granularity is 15s, not sub-minute; only the topmost `schedule` block per card is honored | <!-- cap: sched-task-card-scheduler --> |
| **Coalesce missed fires on recovery (`sched-catch-up-coalesce`)** | If the server was down across several scheduled slots, an overdue card fires exactly once on restart (missed slots collapse to a single catch-up fire, matching cron misfire semantics) rather than replaying each missed occurrence; the per-card `schedule_catch_up` flag can suppress the catch-up entirely. Maturity 3 | <!-- cap: sched-catch-up-coalesce --> |
| **Cap total fires per schedule (`sched-max-runs-cap`)** | `schedule_max_runs` stops a recurring card after a fixed number of fires (`None` = unlimited); enforced in the live loop against the persisted `fires_so_far`. Maturity 3 | <!-- cap: sched-max-runs-cap --> |
| **Read a card's next-fire time and firing history (`sched-schedule-state-endpoint`)** | A read-only endpoint returns the scheduler's per-card record — next fire time, last fire, fire count, run ids — from `schedule_state.json`, so the UI can show when a card will next run; returns empty for a card the scheduler has not fired yet. Maturity 3 | <!-- cap: sched-schedule-state-endpoint --> |
| **Internal periodic maintenance jobs (`sched-system-jobs`)** | A small interval-gated job registry rides the scheduler loop for internal housekeeping — today just memory organization (every 6h) and memory-proposal lifecycle (every 30min), each isolated so one failure cannot block another. Maturity 3, internal only: there is no public API to register a job and it is not user-authorable | <!-- cap: sched-system-jobs --> |
| **Infrastructure-fault ("held") run classification (`sched-held-fault-classification`)** | When a run stops on an infrastructure fault (expired credentials, lost endpoint, throttling) rather than a work failure, it is recorded distinctly with the reason, the block it held at, and — for fan-out collapses — an aggregate fault summary (fault count, fan-out width, primary kind, call path, fleet-wide flag), so a run tile can tell a dead credential apart from one throttled sibling. Maturity 3; detection/classification itself lives elsewhere (`infra_gate`), this records the persistence and resume point | <!-- cap: sched-held-fault-classification --> |
| **Per-iteration artifact retention cap (`sched-iteration-artifact-retention`)** | Every Repeat iteration keeps a lightweight always-retained summary; the full artifact is stored on disk only for failures and the first 50 passing iterations per loop, so a 1000-iteration loop does not bloat. Maturity 3. Limit: passes beyond the cap keep summary only, which is why mid-loop resume refuses when the immediate predecessor was over the cap | <!-- cap: sched-iteration-artifact-retention --> |
| **"Slow but alive" heartbeat and progress trail (`sched-run-activity-heartbeat`)** | A run stamps a throttled last-activity time (~1 write / 5s) and a progress note derived from its latest tool call, so a poller can tell a slow run from a hung one; notes also accumulate in a bounded (200-entry, duplicate-suppressed) trail that survives the whole run as a readable narrative. Maturity 3. Limit: the trail caps at 200 (oldest evicted) and the timestamp can lag up to ~5s | <!-- cap: sched-run-activity-heartbeat --> |
| **Run state model incl. held / partial (`sched-run-lifecycle-status-model`)** | Task runs carry a rich status set — queued, running, paused, held, done, partial, failed, cancelled — where `held` marks a resumable infrastructure fault distinct from a genuine `failed`, and `partial` marks a run that made real progress before stopping so a run that changed the workspace is not mislabeled a total loss. Maturity 4. Limit: `partial` requires at least one completed block, so a zero-progress stop stays failed/cancelled | <!-- cap: sched-run-lifecycle-status-model --> |
| **Resume descends *through* a Call block (`sched-resume-through-call`)** | When a run holds inside a called card, resume walks outward through the recorded call frames to the real block instead of re-entering the callee from its start — so a fan-out held on iteration 19/20 inside a called card resumes there rather than re-running every banked iteration. Maturity 4. Limit: call depth capped at 8 frames, and it depends on the call snapshot having been recorded when the call executed | <!-- cap: sched-resume-through-call --> |
| **Secured artifact blob serving (`sched-artifact-blob-serving`)** | Frozen renders and copied files declared by a run are stored under the run's own artifacts directory (honoring at-rest encryption) and served through a hardened, decryption-aware route: three redundant path-traversal guards, a fixed extension→media-type table, and inline serving only for known-safe types (HTML/JS/SVG are forced to attachment with `nosniff`) because the filename is model-influenced. Maturity 4. Limit: serves only from the run's own artifacts directory; unknown extensions download as attachments | <!-- cap: sched-artifact-blob-serving --> |
| **Reconcile stranded runs on startup (`sched-zombie-reconciliation`)** | At server startup every run still marked running/queued/paused but orphaned by a prior server lifetime is idempotently marked `failed` with an explanatory error, so a zombie run whose cancel button would otherwise be a no-op cannot linger. Maturity 3. Limit: it marks stranded runs failed rather than auto-resuming them — recovering the work is a separate manual resume | <!-- cap: sched-zombie-reconciliation --> |

---

## 3. Code Intelligence

| Feature | Detail |
|---|---|
| **Codebase context injection** | Full project file tree loaded into model context; token counts shown per file |
| **AST-based code analysis** | `--ast` flag enables semantic indexing; 5 resolution levels: `disabled`, `minimal`, `medium`, `detailed`, `comprehensive` |
| **Semantic code search** | `ast_search` — find symbols by name or regex pattern (`regex=true`); filter by type and file path; cross-file coverage |
| **AST reference tracing** | `ast_references` — definitions, callers (cross-file via name fallback), dependencies (import-attribute resolution), file summaries, cursor-position context (`action=context` with `file:line:col`) |
| **Multi-language parsing — 25+ languages beyond Python/TS/HTML (`ast-treesitter-multilang`)** | <!-- cap: ast-treesitter-multilang --> Tree-sitter grammars index C/C++, Rust, Go, Java, C#, Kotlin, Swift, Ruby, PHP, Scala, Lua, Perl, R, Elixir, Haskell, Dart, Zig, OCaml, Julia, Bash, HCL, SQL, TOML and YAML; surfaces definitions and imports only (no call-graph/reference edges for these languages, attributes are text-slice based). Requires the optional tree-sitter language pack and degrades to disabled if it is not installed. *Also called: tree-sitter, multi-language code parsing, polyglot AST, grammar-based parsing, 25+ language support* |
| **React/TS hook detection** | Arrow functions, `useCallback`, `useMemo`, and other hook-wrapped variables are indexed as callable functions, not plain variables |
| **Structured diff generation** | Model produces standard git diff format; rendered inline with Apply/Undo controls |
| **Multi-strategy diff application** | Patch pipeline tries `patch`, `git apply`, and difflib in sequence (3 stages); handles imperfect/inexact diffs gracefully | <!-- cap: diff-llm-resolver-stage --><!-- cap: diff-multi-stage-cascade -->
| **Per-hunk diff status** | Each hunk shows individual success/fail; partial application is fine |
| **Diff undo** | One-click revert of any applied diff |
| **Diff regression suite** | 159 edge-case patch-test-case directories plus ~22 `test_diff_*.py` modules, headless and parallel runners, and an in-app test view (`ApplyDiffTest` / `DiffTestView`) that loads and runs the corpus from the UI | <!-- cap: diff-test-harness-and-regression-suite -->
| **Diff applicator in CLI** | CLI applies diffs interactively: `[a]pply / [s]kip / [v]iew / [q]uit` per diff block |
| **Idempotent diff re-apply** | Applying the same diff twice is detected as a no-op instead of corrupting the file — indentation-only, pure-addition, invisible-unicode and fuzzy matches all recognised as already-present (idempotent / already-applied hunk detection) | <!-- cap: diff-already-applied-detection -->
| **Diff preprocessing / auto-repair** | Model diffs are normalised before apply: wrong hunk line counts are recomputed from the body, insert-instead-of-replace hunks are rewritten as removal+addition, and bare `@@` headers are synthesised (diff preprocessing / sanitization) | <!-- cap: diff-preprocessing-normalization -->
| **Backtick / fence escape recovery** | Reverses the backtick escaping models add to stop code fences terminating, using the target file itself to avoid clobbering genuine template-literal escapes (backtick / fence unescape recovery) | <!-- cap: diff-backtick-fence-recovery -->
| **Language-aware diff validation** | Per-language handlers (Python, TypeScript, JavaScript, Java, C++, Rust, plus a generic fallback) run post-apply structure checks such as brace balance; catches structural breakage, not semantic correctness (per-language handlers and post-apply validation) | <!-- cap: diff-per-language-validation -->
| **Tunable diff-apply thresholds** | Fuzz/confidence thresholds, search radius, max offset and adaptive context sizing are overridable through `ZIYA_DIFF_*` environment variables; tuning is global, not per-file or per-language (configurable apply thresholds) | <!-- cap: diff-config-tunables -->
| **Patch-pipeline security hardening** | The apply/unapply/validate routes carry named CWE fixes: path-traversal containment against crafted `+++` headers (CWE-22/94), a hunk line-count clamp (CWE-119), and a per-file reentrant apply lock against concurrent clobbering (CWE-667) (patch-pipeline security hardening) | <!-- cap: diff-security-hardening -->
| **Superseded-diff dedup at apply** | A later diff that supersedes an earlier one for the same file is detected — via overlapping hunk ranges and sequential-pair matching — so stale diffs aren't re-applied or double-counted; heuristic, not a semantic diff comparison (frontend superseded / duplicate diff detection) | <!-- cap: diff-frontend-superseded-dedup -->

| **Content-aware syntax highlighting** | Source code highlighted for a broad range of languages |
| **File browser / external path inclusion** | Add files and directories outside the project root via the browser; they appear under an `[external]` tree branch, are token-counted with entry/time/symlink caps, and persist per-project (`externalPaths`) so they survive restarts | <!-- cap: ctx-external-path-inclusion -->
| **Token count per file** | File tree shows per-file token cost to help manage context budget. In the common case the displayed number is a *calibrated size-based estimate* (a learned chars-per-token ratio per file type), not an exact tiktoken count; very large files are stubbed and accurate counts are computed only on demand | <!-- cap: ctx-per-file-token-estimation -->
| **Live file-tree updates** | The file tree updates in real time over a WebSocket (`/ws/file-tree`) as files are added, modified, or deleted — no manual refresh. Delivery is best-effort and the connection set is global, so scan-complete events are path-scoped per directory | <!-- cap: ctx-live-file-tree-websocket -->
| **Filesystem watcher** | A watchdog observer monitors the project root and drives the live tree: it filters editor temp files, re-checks nested `.gitignore`, debounces, defers deletes, and updates per-conversation file state. Single watcher over one project root, initialized at startup | <!-- cap: ctx-filesystem-watcher -->
| **Gitignore-honoring ignore engine** | File filtering respects `.gitignore` (root and nested, to depth 5) plus a large built-in default exclusion set (node_modules, VCS dirs, virtualenvs, Brazil build artifacts, macOS Library/Downloads), with symlink-loop protection, per-directory caps, and a tunable wall-clock deadline (`ZIYA_GITIGNORE_TIMEOUT`). Not full git precedence/negation semantics | <!-- cap: ctx-gitignore-ignore-engine -->
| **Context pruning** | Selectively remove files or messages from context to stay within model limits |

---

## 4. Context & Conversation Management

| Feature | Detail |
|---|---|
| **Persistent conversation history** | Conversations never silently reset; history survives context overflow |
| **User-controlled context curation** | Ziya gives users direct control over what the model sees, rather than relying on automatic summarization that may discard information the user considers important. Four complementary mechanisms work together (see below). |
| **Per-message muting** | Mute/unmute any message (human or assistant) to exclude it from model context without deleting it. Muted messages remain visible in the UI with a visual indicator and can be unmuted at any time. The model never sees muted messages. |
| **Conversation forking** | Fork from any message to explore a tangential path without losing the original thread. Optionally truncate from the fork point to shed context weight while preserving the full original conversation. |
| **Per-message editing** | Edit, resubmit, fork, truncate, or delete any message in the conversation history |
| **Selective file removal** | Remove individual files from context mid-conversation when they're no longer relevant, reclaiming token budget for new material |
| **Multiple simultaneous projects** | Open separate browser tabs, each with its own project, history, and context |
| **Project organization** | Conversations grouped by project in the sidebar; switching projects auto-restores the last-viewed conversation for that project |
| **Conversation search with selectable ordering** | Full-text search over titles and message bodies, scoped to the current project or all projects. Ordering is user-selected: **Best match**, **Newest activity**, or **Oldest activity**. Best match is a weighted score, not an occurrence count — a title hit outweighs many body hits, hits in a conversation's opening messages count double, repeated hits inside one message saturate (so one verbose message cannot dominate), and long conversations are length-normalised so a sprawling thread must be proportionally on-topic to outrank a short one. Age is the conversation's *last activity* (when it was last written to), not last access, so skimming an old thread does not make it look fresh. Search runs server-side one chat file at a time; the browser falls back to a local IndexedDB scan with identical scoring when the server is unreachable.<br>Also called: chat search, conversation full-text search, history search, grep conversations, cross-project search <!-- cap: conversation-full-text-search --> |
| **Model-queryable conversation history** | The model can query past transcripts directly — not just the distilled memory layer — with three builtin tools. `chat_search` finds conversations mentioning a term (same ranking and decryption as the sidebar search; `side=user\|assistant\|both`, current project or `all_projects`, current conversation excluded by default). `chat_read` pulls a slice of one conversation by **turn** (1-based, negative from the end; a turn is a user message plus the replies that follow it), by 0-based message index (the indices `chat_search` reports), or `around` a hit — filtered to the user side (the prompting), the assistant side, or both, with per-message and total caps and a `next_message_index` for paging. Its header carries the files in context and fork lineage (`branchedFrom`, `lineageRootId`). `chat_list` lists recent conversations without bodies. Read-only; results are labelled `trust="medium"` because they are past model output and past tool results. Category `chat_history` (`ZIYA_ENABLE_CHAT_HISTORY=false` to disable) |
| **Conversation export / import** | Export for local reuse (JSON), export for sharing (Markdown), server-side high-fidelity **PDF** (headless-rendered; falls back to browser print if Playwright is absent), and copy for a GitHub Gist (opens the Gist compose page in a browser tab — there is no direct API upload); import to restore | <!-- cap: export-paste-sharing-gist -->
| **Project & session naming** | Name projects, groups, and individual sessions for organization in the sidebar |
| **Conversation checkpointing** | Auto-checkpointing of conversation state; web UI supports session resume across browser restarts |
| **Ephemeral (unsaved) conversations** | Start a scratch conversation that lives only in the browser session — it is never written to local storage or synced to the server (a persistence guard strips ephemeral ids), so it leaves no trace. Promote it to a normally-saved conversation at any time to keep it. Ephemeral threads are lost on reload unless promoted, and are not visible to other tabs or machines while ephemeral.<br>Also called: scratch conversation, unsaved chat, incognito chat, draft conversation, throwaway thread <!-- cap: fcm-ephemeral-conversations --> |
| **Move / copy conversations & folders across projects** | From the sidebar, move or copy a conversation to another project, or move a whole folder to another project; move relocates ownership, copy duplicates into the target project, and both re-sync.<br>Also called: relocate conversation, transfer chat to project, copy chat to project, reparent conversation <!-- cap: fcm-move-copy-project --> |
| **Conversation info panel** | A read-only info popup for any conversation showing its id, project, title, folder, message breakdown (human/assistant/system/muted/tool), character and approximate byte size, timestamps, storage state (persisted / ephemeral / shell / global / inactive), display mode, open beads, and branch/lineage pointers. Read-only — no metadata editing from here.<br>Also called: conversation details, chat metadata, conversation stats, info dialog, conversation inspector <!-- cap: fcm-conversation-info-modal --> |
| **Chat shadow-copy integrity scan & reconcile** | Maintenance tooling that detects a conversation id appearing in more than one project directory (shadow-copy corruption from an old bulk-sync bug, now prevented by a guard), picks a canonical copy (owner-matches-directory, then most-recently-active / most-messages), and can reconcile by restoring salvageable grouping/global metadata onto it and deleting the shadows. Exposed at `GET/POST /api/v1/chat-integrity[/reconcile]` (dry-run by default) plus a warn-only startup self-check (destructive reconcile only when `ZIYA_AUTO_RECONCILE_CHATS` is set); the canonical choice is a heuristic and it never rewrites plaintext `_groups.json`.<br>Also called: duplicate conversation cleanup, chat deduplication, conversation integrity check, orphan chat repair <!-- cap: chat-shadow-copy-integrity --> |
| **Context window usage display** | Toolbar shows consumed tokens vs. model limit at all times |
| **CLI session history** | Persistent CLI history file (`~/.ziya/history`) with `prompt_toolkit` autocomplete |
| **Shell command history** | Shell allowlist persisted to `~/.ziya/`; session-local overrides without touching persisted state |
| **Cross-project global chats & folders** | Mark a chat or folder `isGlobal` and it is surfaced (read-only) in every project's sidebar while still owned by exactly one project; a folder passes the flag to its contents. Surfacing only — the owning project is unchanged | <!-- cap: cross-project-global-items -->
| **Auto-included project docs** | On project load/switch the checked file selection is auto-seeded with `AGENTS.md` (collected recursively) and the root `README.md`, so the model always sees project guidance; importing an external directory auto-selects its `AGENTS.md`/`README.md` too. The filename set is fixed, not user-configurable | <!-- cap: ctx-auto-included-docs -->
| **Named context sets (Contexts)** | Save a named, reusable set of files as a first-class *Context* (with a color and a cached token count) per project; reference it from any chat and manage it from the Contexts tab. Deleting a Context removes it from every chat that used it. Token counts can go stale between edits and Contexts are per-project | <!-- cap: ctx-named-context-sets -->
| **Model self-curated context files** | The model can manage its own file context across turns via three builtin tools (`context_add_file` / `context_remove_file` / `context_list_files`): additions are path-validated and token-budget-checked before being pinned and tagged as model-added, and the model can only remove its own pins, never the user's | <!-- cap: ctx-model-self-curated-files -->
| **Per-conversation file-state tracking** | Ziya tracks each in-context file's original/current content per conversation and annotates changed lines (`[NNN+]` added, `[NNN*]` modified); by refreshing from disk it detects when you did *not* apply a suggested diff and warns the model with a "file content authority" notice to keep it from hallucinating unapplied edits. State is encrypted at rest; retention is 20 conversations / 1h | <!-- cap: ctx-file-state-line-tracking -->

> **Design note — Context curation vs. auto-compaction:** Most AI coding tools (Claude Code, Cline, Codex, Kiro) use automatic context compaction — the machine decides what to summarize or discard when context fills up. Ziya deliberately does not auto-compact. Instead, it provides tools for the user to curate context: mute messages that are no longer relevant, fork and truncate to shed weight, remove files that have served their purpose. This keeps the user in control of what the model retains. In 18+ months of daily use with very large contexts, manual curation with mute/fork/truncate has proven more reliable than automatic summarization, which risks discarding details the user knows are important but the model doesn't recognize as such.

---

## 5. Models

| Feature | Detail |
|---|---|
| **AWS Bedrock — Claude** | Sonnet 4.6 (default), Sonnet 4.5/4.0/3.7/3.5, Opus 4.6/4.5/4.1/4.0/3, Haiku 4.5/3 |
| **AWS Bedrock — Nova** | Nova Premier (1M ctx), Nova Pro, Nova Lite, Nova Micro |
| **AWS Bedrock — Other** | DeepSeek R1/V3/V3.2, Qwen3 Coder 480B, Kimi K2.5, MiniMax M2.1, GLM 4.7/4.7 Flash, OpenAI GPT 120B/20B (via Bedrock) |
| **Google Gemini** | Gemini 3.1 Pro, Gemini 3 Pro *(deprecated)*/Flash, Gemini 2.5 Pro/Flash Lite, Gemini 2.0 Flash/Lite |
| **OpenAI** | GPT-4.1/Mini/Nano, GPT-4o/Mini, o3, o3-mini, o4-mini |
| **Mid-conversation model switching** | Change models at any point without losing history |
| **Per-conversation / per-project model pinning** | Pin a model to one conversation or project without changing the server default; requests resolve conversation pin → project pin → server default (per-tab, non-persisted — reload falls back to server default) |
| **Folder-scoped & persistent model pins** | Model pins also apply at folder scope (greyed when not in a folder), and each pin can be tab-only (ephemeral) or persisted to the conversation/folder/project record (syncs, survives restarts). Resolution: conversation → folder → project → server; tab pin overrides saved pref at the same level. Sidebar chip shows scope (`conv`/`folder`/`proj`) and layer (`tab`/`saved`). Pins are stored as a model alias string; validation and resolution of that alias are handled by the model-config subsystem | <!-- cap: per-conversation-model-pin -->
| **Adaptive / extended thinking** | Sonnet 4.6, Opus 4.6: configurable reasoning effort (`low`–`max`); Sonnet 3.7, 4.0–4.5, Nova Pro/Premier: extended thinking toggle |
| **Gemini thinking levels** | `low`, `medium`, `high` per-request on all Gemini 3.x family models (3 Pro, 3.1 Pro, 3 Flash) |
| **Model parameter tuning** | Temperature, top-p, top-k, max output tokens — configurable in UI without restart |
| **Custom model filtering** | `~/.ziya/models.json` to restrict or extend the model picker |
| **Custom inference profiles** | Add custom Bedrock ARNs (provisioned throughput, etc.) via `~/.ziya/models.json` |
| **Prompt caching** | Extensive cache support with analytics; system prompt and code context are cached across turns, reducing latency and cost for follow-up messages |
| **Mid-stream fault recovery** | A transient provider fault (500 `api_error`, read timeout, dropped connection) that hits *after* the answer has started streaming is resumed rather than fatal: the partial text is rewound to its last complete line and the response continues from that boundary as an assistant prefill. Distinct from a plain retry, which would append a second full response to what you already have. Withheld once a tool call has started mid-stream, since a text prefill cannot finish a half-emitted tool call |
| **Open source** | MIT license; full source code available |

---

## 6. Multimodal & File Formats

| Feature | Detail |
|---|---|
| **Image input** | Drag/drop, paste from clipboard, or image button; supported on Claude 3.x/4.x, Nova Pro/Lite/Premier, Gemini |
| **Plain-text paste** | Pasting from web pages forces plain text — strips rich HTML (styled spans, tables, inline CSS) that bloats token counts and loses whitespace |
| **PDF input** | Native reading via a `pdfplumber`→`pypdf` dual-backend text extractor; scanned/image-only PDFs fall back to rendered page images, and very large PDFs divert to the on-demand RAG path rather than loading in full (`pdf-text-extraction`) | <!-- cap: pdf-text-extraction -->
| **Word (DOCX) input** | Native reading (DOCX only; a legacy binary `.doc` is accepted but not parsed) | <!-- cap: docx-extraction -->
| **Excel (XLSX) input** | Native reading |
| **PowerPoint (PPTX) input** | Native reading (PPTX only; a legacy binary `.ppt` is not parsed) | <!-- cap: pptx-extraction -->
| **Dynamic context helpers** | For obscure file types (e.g., network pcap), Ziya loads format-appropriate parsers. Packet-capture parsing requires the optional `scapy`/`dpkt` dependencies, and the pcap MCP tools are experimental and disabled by default | <!-- cap: pcap-mcp-and-rest-surface -->
| **Document upload endpoint** | Multipart upload at `/api/extract-document` with a pluggable extension→handler registry (office/PDF vs. packet-capture), a 50 MB request cap, off-loop extraction, and large-PDF persistence into `.ziya/pdf_uploads` (`document-upload-route`) | <!-- cap: document-upload-route -->
| **Upload safety guards** | Every upload is size-capped (default 200 MB), and ZIP-container office files are checked for uncompressed-size and compression-ratio zip bombs before inflation (`document-safety-guards`) | <!-- cap: document-safety-guards -->
| **Fetched-PDF re-extraction** | A PDF returned by a fetch tool is re-fetched and text-extracted like a local upload, behind an SSRF guard (blocks literal internal/loopback/IMDS IPs, no redirects, 30 s timeout) (`fetched-pdf-http-reextraction`) | <!-- cap: fetched-pdf-http-reextraction -->
| **PCAP TCP health analysis** | Packet captures are analysed per TCP flow for retransmits, resets, zero-window, duplicate-ACK and failed-connect conditions, yielding per-IP metrics and a good/fair/poor health status (`pcap-tcp-health`) | <!-- cap: pcap-tcp-health -->
| **Voice input** | Microphone capture in the web composer with fully local faster-whisper transcription; optional dependency |

---

## 7. Visualizations

Ziya supports a wide range of inline visualization formats. All renderers have a preprocessing normalization layer that handles the imperfect output models tend to produce. Under the hood, these renderers share one lazy-loading plugin registry that downloads each grammar's library only on first use and selects a renderer by priority-ordered capability matching (`viz-plugin-registry-and-selection`); <!-- cap: viz-plugin-registry-and-selection --> a per-plugin malformed-spec recovery layer that lifts and type-stamps `{type, definition}` model output so the right renderer is chosen instead of timing out (`viz-malformed-spec-recovery`); <!-- cap: viz-malformed-spec-recovery --> the Mermaid path's ~77-rule auto-repair pipeline with postcondition self-checks (`viz-mermaid-spec-repair`); <!-- cap: viz-mermaid-spec-repair --> and a shared ELK auto-layout / orthogonal edge-routing engine for node placement (`viz-shared-layout-engine`). <!-- cap: viz-shared-layout-engine -->

| Renderer | Use cases |
|---|---|
| **Mermaid** | Flowcharts, sequence diagrams, ER diagrams, Gantt, state machines |
| **Graphviz** | Dependency graphs, call graphs, complex networks |
| **VegaLite** | Data charts, plots, statistical visualizations. Preprocessing fixes: encoding datum/field swaps, fold transform mismatches, gradient repair, log scale corrections, arc label enhancement, grouped bar fix, and 20+ other LLM-generated spec normalizations. |
| **DrawIO** | Architecture diagrams, system design, exportable `.drawio` files |
| **MathML / KaTeX** | Inline and display math (`$...$` / `$$...$$`), including `\ce{}` chemical equations via the mhchem extension |
| **LaTeX / TikZ** | `tikz`, `circuitikz` (circuit schematics), `chemfig` (chemical structures), `tikz-cd` (commutative diagrams), `pgfplots` (typeset function/data plots), `forest` (labelled trees: syntax trees, taxonomies, decision/game trees, phylogenies), `bussproofs` (proof trees: natural deduction, sequent calculus, typing rules). Compiled by a local TeX install; SVG when `dvisvgm` is present, PNG otherwise. A missing package produces an actionable `tlmgr install` notice rather than an error, and never discards the source |
| **HTML mockups** | Interactive UI previews in an isolated iframe; inherits theme; CSS isolated (`viz-html-mockup-renderer`) | <!-- cap: viz-html-mockup-renderer -->
| **Packet diagrams** | Bit-level protocol frame / header / wire-format layouts with rulers and bracket annotations |
| **Railroad diagrams** | Railroad (syntax) diagrams from a JSON spec — grammars, regex structure, config/URL formats; sequence/choice/optional, loops with separators, named production stacks |
| **WaveDrom** | Digital timing diagrams from WaveJSON — clocks, buses, groups, gaps, node/edge timing annotations; dark skin in dark mode; `reg` bit-field and `assign` logic specs too |
| **Flame graphs** | Interactive performance-profile flame graphs (d3-flame-graph) — click-to-zoom frames; accepts nested JSON or collapsed-stack text straight from py-spy / perf / flamegraph.pl |
| **Music notation** | Published-quality sheet music via VexFlow — inline `` `music:` `` phrases or a ```` ```music ```` JSON spec. Notes/chords/rests, 5 clefs, key/time signatures (incl. mid-score modulation & meter changes), beaming, tuplets, grace & cue notes, tremolo, arpeggios, slurs/ties/glissandos, dynamics/hairpins, articulations, ornaments, chord symbols, lyrics (verses/hyphens/melisma), fingerings, harp-pedal & sustain-pedal lines, breath marks/caesuras, cautionary accidentals, measures/barlines/repeats/voltas, navigation & tempo marks, title block & part names, multi-voice staves (with rests offset per voice so simultaneous rests never overprint), grand staff with cross-staff beams/slurs, and automatic multi-system wrapping with measure numbering. Malformed input (out-of-range octave, mistyped accidental, bad duration/meter/tempo, out-of-range tuplet count) degrades to a warning and a sane default instead of hanging the render (`viz-music-render`) | <!-- cap: viz-music-render -->
| **Plotly** | Scientific/statistical plots and 3-D charts (Plotly.js). A preprocessor repairs cosmetically-broken specs; under headless screenshot capture WebGL traces are demoted to SVG so the render never hangs (`viz-plotly-render`) | <!-- cap: viz-plotly-render -->
| **JointJS** | UML / ER / generic node-link diagrams. A geometry sanitizer clamps runaway coordinates that would otherwise blow up the canvas and lose the render, plus orthogonal and curved link routing (`viz-jointjs-render`) | <!-- cap: viz-jointjs-render -->
| **Chord diagram** | Circular relationship-matrix / flow diagrams (d3-chord), with spec recovery that rebuilds a usable chord spec from partial model output (`viz-chord-render`) | <!-- cap: viz-chord-render -->
| **Force-directed graph** | Spring-layout node-link graphs (d3-force) with input sanitizing for degenerate specs (`viz-force-directed-render`) | <!-- cap: viz-force-directed-render -->
| **Network diagram** | Grouped node-link topology diagrams with named groups; acts as the general graph fallback (`viz-network-diagram-render`) | <!-- cap: viz-network-diagram-render -->
| **Raw Vega** | Full Vega grammar, distinct from Vega-Lite, with a sanitizer that neutralizes force-transform / degenerate-geometry specs that would otherwise crash the Vega runtime (`viz-vega-render`) | <!-- cap: viz-vega-render -->
| **Architecture shape catalog** | Searchable library of AWS and generic shapes for DrawIO/Mermaid/Graphviz |

---

## 8. MCP (Model Context Protocol)

| Feature | Detail |
|---|---|
| **MCP server support** | stdio transport (subprocess spawning) and remote HTTPS (StreamableHTTP, SSE) via the official MCP SDK |
| **Config file lookup** | The first existing `mcp_config.json` among `./` (CWD), the project root, and `~/.ziya/` is used — there is no cross-file merge | <!-- cap: mcp-server-config-loading -->
| **Remote MCP servers** | Connect to hosted MCP endpoints via `"url"` config key; supports StreamableHTTP (default) and SSE transports |
| **Bearer / header token auth** | `"auth": {"type": "bearer", "token_env": "..."}` for remote MCP servers; inline tokens or env-var references. This is static bearer-token injection, not an OAuth 2.0 authorization flow | <!-- cap: mcp-remote-bearer-auth -->
| **Tool poisoning detection** | External tool descriptions scanned for a set of prompt-injection patterns at connect time | <!-- cap: mcp-tool-poisoning-scan -->
| **Tool shadowing prevention** | External tools that collide with built-in tool names are blocked; built-ins always take precedence |
| **Rug-pull quarantine & re-authorization** | Each server's tool definitions are SHA-256 fingerprinted against a human-approved baseline that survives restarts; if the definitions change unexpectedly ("rug pull") the server is *hard-quarantined* — its tools are withheld from every prompt and refused by the tool caller — until a human explicitly re-authorizes a new baseline. A forced accept is bound to the exact fingerprint and self-revokes if the definitions mutate again. Builtin/trusted servers are exempt (`mcp-rugpull-quarantine`) | <!-- cap: mcp-rugpull-quarantine -->
| **Homoglyph / mixed-script detection** | Tool descriptions are also scanned in a Unicode confusable-folded copy so Cyrillic/Greek lookalike injections (e.g. "ignоre" with a Cyrillic o) are caught; whitespace tokens mixing ASCII and non-ASCII letters are flagged as mixed-script obfuscation (`homoglyph-confusable-detection`) | <!-- cap: homoglyph-confusable-detection -->
| **HMAC tool-result signing** | Every tool result (built-in and external MCP) is HMAC-SHA256 signed with a per-process session secret the model never sees; unsigned or stale (>5 min) results are rejected before display (`mcp-tool-result-signing`) | <!-- cap: mcp-tool-result-signing -->
| **Response schema & size validation** | Every MCP response is checked for structure, block-count (50) / per-block byte (5 MB) / image (20 MB) caps, allowlisted MIME types and recognized block types; unsalvageable responses are rejected (`mcp-response-schema-validation`) | <!-- cap: mcp-response-schema-validation -->
| **Tool input constraint validation** | Tool arguments are validated against JSON-Schema constraints (enum / length / range / pattern) with hard errors, and string values are scanned for dangerous patterns — path traversal, command chaining, template injection (`mcp-input-constraint-validation`) | <!-- cap: mcp-input-constraint-validation -->
| **Hidden-character sanitization** | Tool-result text is iteratively stripped of zero-width, Unicode tag-block, bidi-override, surrogate and control characters (ANSI/ESC deliberately preserved) to defeat hidden-character smuggling and visual spoofing (`hidden-char-sanitization`) | <!-- cap: hidden-char-sanitization -->
| **Encoded-payload scanning** | Base64/hex/ROT13 spans that decode to instruction-like text are flagged on tool results and on memory/proposal writes (guards against latent prompt injection); detection-only — logs, never blocks (`encoded-payload-scanning`) | <!-- cap: encoded-payload-scanning -->
| **Trust-labeled tool results** | Every tool result reaches the model inside a trust-tagged envelope (high / low / medium, by tool class) with forged delimiters defanged, so results are framed as untrusted *data* rather than instructions (`tool-result-trust-envelope`) | <!-- cap: tool-result-trust-envelope -->
| **Hallucinated-tool-result guard (parroting)** | The assistant's prose stream is fingerprinted against every real tool result (session-scoped word 5-gram shingles + line hashes); high-confidence verbatim reproduction aborts and retries the turn so the model can't narrate fake tool output instead of calling the tool (`shingle-parroting-detection`) | <!-- cap: shingle-parroting-detection -->
| **Fabricated tool-result detection** | A fenced block that mimics a Ziya tool-result dict (≥2 canonical result keys) with no matching real tool call in the same turn is flagged, and at high confidence aborts the stream and triggers recovery (`fake-tool-result-echo-detection`) | <!-- cap: fake-tool-result-echo-detection -->
| **Built-in in-process tools** | A registry of 13 tool categories that run inside Ziya with no external MCP server (architecture diagrams, file I/O, PDF/RAG, AST, Nova web grounding, skills, memory, context management, diagram render, beads, task cards, task artifacts, PCAP analysis); each category toggles on/off via `ZIYA_ENABLE_<CAT>`, service-plugin allow-list, or its default. Memory and PCAP analysis are off by default (`mcp-builtin-direct-tools`) | <!-- cap: mcp-builtin-direct-tools -->
| **MCP config validation** | Static, advisory validation of each `mcp_config.json` entry with line-anchored findings: misspelled/unknown keys (fuzzy suggestions), missing launch mechanism, wrong value types, non-string env values, unresolvable relative script paths, and enabled/disabled conflicts — surfaced in a findings panel without rejecting anything the loader would accept (`mcp-config-validation`) | <!-- cap: mcp-config-validation -->
| **MCP registry marketplace** | Aggregates MCP-server catalogs from multiple registry providers in parallel, deduplicates by repo/package fingerprint, merges duplicates, and ranks by support level then popularity, with a memoised unified cross-provider search — far more than "browse and install" (`mcp-registry-marketplace`) | <!-- cap: mcp-registry-marketplace -->
| **MCP resources & prompts** | Beyond tools, Ziya loads and aggregates MCP *resources* (fetch by URI) and *prompts* (server-provided prompt templates, with arguments) from local and remote servers, exposed via `/api/mcp/resources` and `/api/mcp/prompts`. Frontend UX for resources/prompts is thinner than for tools (`mcp-resources-and-prompts`) | <!-- cap: mcp-resources-and-prompts -->
| **Runtime server lifecycle** | Individual MCP servers can be enabled, disabled, or restarted at runtime, and the whole manager can reinitialize via `POST /api/mcp/initialize` to pick up config changes — no full app restart needed. Enable/disable overrides are persisted and all lifecycle transitions are serialized by a process-wide lock (`mcp-server-lifecycle`) | <!-- cap: mcp-server-lifecycle -->
| **MCP startup diagnostics** | Each server records the furthest startup stage it reached (config → preflight → spawn → handshake → ready), captures a readable server-log tail, and detects dependency-mismatch hints from stderr, so a failure can be attributed to the user's config, their machine, or the server itself (`mcp-startup-diagnostics`) | <!-- cap: mcp-startup-diagnostics -->
| **MCP registry browser** | Browse and install MCPs from within the Ziya UI |
| **Tool enhancement / description injection** | `tool_enhancements` block in `mcp_config.json` appends custom guidance to any tool's description before it reaches the model — corrects model misbehavior without touching the MCP server |
| **User-level tool overrides** | `~/.ziya/tool_enhancements.json` for per-user corrections |
| **Shell command allowlist** | Configurable per-session and persistently; `/shell add/rm/reset/yolo/git/timeout` commands in CLI; `save` suffix to persist |
| **YOLO mode** | `/shell yolo` disables the allowlist for the current session (confirmation required) |
| **Nova Web Grounding** | Built-in web search tool backed by Amazon Nova; Claude calls it autonomously when it needs current web information |
| **Visual Diagram Feedback** | `render_diagram` builtin tool renders diagrams server-side and returns images as vision content blocks; enables model to see and iteratively fix rendering output |
| **Per-MCP prettyprint** | Custom output formatting per MCP server via `FormatterProvider` plugin |

---

## 9. Enterprise & Internal Deployment

| Feature | Detail |
|---|---|
| **Plugin system** | Python plugins loaded via `ZIYA_LOAD_INTERNAL_PLUGINS=1`; structured provider interfaces |
| **AuthProvider** | Pluggable credential validation and refresh instructions (e.g., Amazon Midway) |
| **ConfigProvider** | Environment-specific defaults, endpoint restrictions, model picker policy |
| **Endpoint restriction** | `get_allowed_endpoints()` hides disallowed providers from UI and API; enforced at startup |
| **DataRetentionProvider** | Per-category TTL policies (conversation, cache, session); most-restrictive-wins when multiple providers registered |
| **ServiceModelProvider** | Enable and configure built-in service models (e.g., Nova Grounding) |
| **FormatterProvider** | Inject JavaScript for custom tool result rendering in the frontend |
| **ToolEnhancementProvider** | Organization-wide tool description augmentations |
| **ShellConfigProvider** | Extend the shell command allowlist via enterprise plugin |
| **Shared/centralized account** | Internal deployments can provide a shared Bedrock account; users don't need personal AWS credentials |
| **Developer override** | `ZIYA_ALLOW_ALL_ENDPOINTS=1` bypasses endpoint restrictions for plugin developers |
| **Open-core plugin/provider registry (`plugin-provider-framework`)** | The provider types above are resolved through a process-global registry of ~15 typed interfaces; beyond those listed it also includes **EncryptionProvider**, **DirectoryScanProvider**, **ToolResultFilterProvider** and **ExtractionPatternProvider**. Providers register at startup, sort by integer priority, and merge most-restrictive-wins per type. Maturity 3. Limit: the multi-provider merge paths run only under the closed enterprise package — community installs register only the default providers — and plugin code itself is unsandboxed, running with full in-process trust | <!-- cap: plugin-provider-framework --> |
| **Envelope encryption of data at rest (`application-level-encryption-at-rest`)** | Stored conversation/session data can be transparently encrypted with AES-256-GCM envelope encryption (per-install Data Encryption Keys wrapped by a Key Encryption Key from a plugin provider or passphrase); reads auto-detect the `ZIYA-ALE-V1` magic and a data-safety guard refuses to overwrite an encrypted-but-undecryptable file. Maturity 4. Limit: OFF by default (needs a provider or `ZIYA_ENCRYPTION_KEY`), whole-blob only (no field-level/searchable encryption), and the strongest KEK sources (Midway/KMS) live in the unshipped enterprise plugin | <!-- cap: application-level-encryption-at-rest --> |
| **Passphrase encryption for community installs (`passphrase-encryption-community`)** | Community users can turn on at-rest encryption with no plugin by setting `ZIYA_ENCRYPTION_KEY`; the key is derived with PBKDF2-HMAC-SHA256 (600,000 iterations) over a random per-install salt stored `0600`. Maturity 3. Limit: env-var only (no UI to set/rotate, no rate-limiting/lockout); changing the passphrase without the old one renders existing data unreadable except via keyring backups | <!-- cap: passphrase-encryption-community --> |
| **Directory-scan governance (`.ziya/scan.yaml`) (`directory-scan-governance`)** | A user-editable `.ziya/scan.yaml` (matched by file/name/glob) tunes how the workspace is scanned — include-only/exclude masks, default depth and per-child depth overrides — via a shipped DirectoryScanProvider consulted during folder scanning. Maturity 3. Limit: no schema validation beyond key presence (malformed rules are logged and ignored) and requires PyYAML for rules to take effect | <!-- cap: directory-scan-governance --> |
| **Prompt-extension framework (`prompt-extension-framework`)** | Per-model system prompts are assembled by a PromptExtensionManager that applies prompt-transforming functions scoped to global → endpoint → family → specific model, discovered from `app/extensions/prompt_extensions/` and enable/disable-able via config. Maturity 3. Limit: only one extension per (scope, target) key — later registration overwrites rather than composing — and directory-loaded extensions run arbitrary Python at import with no sandbox | <!-- cap: prompt-extension-framework --> |

---

## 10. Skills

| Feature | Detail |
|---|---|
| **Skills panel** | Activate reusable instruction bundles from the Skills panel |
| **Custom skills** | Create and edit skills from within the UI |
| **Skills as system prompt supplements** | Each skill can include a system prompt segment active for that conversation |
| **12 curated built-in skills (`skills-builtin-library`)** | Ziya ships 12 authored skills across two visibility tiers — model-discoverable (code review, debug mode, web research, task decomposition, task cards, packet/circuit diagrams, music notation) and user-selectable (concise, educational, continuous documentation, test everything); several are 400+ line domain playbooks loaded on demand. Maturity 4. Limit: built-in skills are read-only — adding one is a code change | <!-- cap: skills-builtin-library --> |
| **Model-discoverable skill catalog + on-demand loading (`skills-catalog-injection-ondemand`)** | A compact one-line-per-skill catalog (~200 tokens) is injected into the system prompt; the model loads a skill's full body only when it needs it, by calling `get_skill_details` (Claude-Skills-style progressive disclosure). Maturity 4. Limit: only model-discoverable skills auto-appear; user-selectable skills need UI activation | <!-- cap: skills-catalog-injection-ondemand --> |
| **Skills declare tools, contexts, files and model overrides (`skills-enhanced-dimensions`)** | Beyond a prompt, a skill can declare model overrides (temperature, max tokens, thinking mode, model), tool ids, context ids, files and allowed tools. Model overrides and prompt segments are applied at request time today. Maturity 3: tool/context/file scoping is declared and persisted, but its runtime enforcement is not yet fully verified end-to-end | <!-- cap: skills-enhanced-dimensions --> |
| **Filesystem skill discovery (SKILL.md / agentskills.io) (`skills-file-discovery-agentskills`)** | Drop an agentskills.io-format `SKILL.md` into a project or user-global skills root and Ziya discovers it — scanning six project roots plus `~/.ziya/skills` and other harnesses' `.claude`/`.kiro` skill dirs, validating frontmatter, resolving cross-root name clashes by precedence. Maturity 4. Limit: one-level directory model; the frontmatter parser handles flat and one-level YAML maps, not full YAML | <!-- cap: skills-file-discovery-agentskills --> |
| **Skill export / sharing** | *(roadmap — profile packaging concept)* |

---

## 11. Developer Experience (CLI-specific)

| Feature | Detail |
|---|---|
| **Declarative slash-command palette** (`cli-slash-command-palette`) | ~20 in-session slash commands with aliases, subcommands and third-level options, all derived from one `COMMAND_SPEC` source of truth so completion, the inline `?` help and dispatch stay in sync; a trailing `?` reveals the next option level and `//` sends a literal message starting with a slash. Not runtime-extensible — new commands are added in source. | <!-- cap: cli-slash-command-palette --> |
| **`prompt_toolkit` REPL** | Rich terminal: file path completion, command completion, model name completion |
| **Multiline input** | Paste-aware: rapid input detected as paste inserts newlines instead of submitting |
| **Bracketed paste handling** | Correct handling of multi-line pastes without accidental submission |
| **Ctrl+C double-tap to exit** | Single Ctrl+C cancels in-flight request or clears input; double-tap exits |
| **Streaming cancellation** | Ctrl+C during a streaming response cancels it mid-stream; partial response preserved |
| **Tool call display** | Each tool invocation shown with header, arguments, and result in the terminal |
| **Granular processing indicators** | Context-aware spinner labels instead of generic "Running tools…": 📋 Planning task decomposition, 📖 Reading file context, ✏️ Writing files, ⚡ Running command, 🔍 Searching codebase, 🌐 Searching the web, 📐 Generating diagram, 🚀 Launching delegates, 💎 Compacting results |
| **Interactive model picker** | `/model` shows a `RadioList` with context window sizes; `→` opens settings dialog |
| **Model settings dialog** | Configure temperature, max tokens, top-k without leaving the CLI |
| **Git integration** | `ziya review --staged` / `--diff` for git-aware code review |
| **Pipe composition** | `cat error.log \| ziya ask "what's wrong?"`, `git diff \| ziya review` |
| **Shadow sessions (observe + read)** | `ziya shadow [cmd...]` wraps a shell or command (e.g. `ziya shadow ssh prod-42`) in a PTY, journals it locally (`~/.ziya/shadow/sessions/`, 0600, unlinked on exit), masks password-style input, and serves it over a Unix socket. Any chat session reads it with `shadow_list` / `shadow_read` / `shadow_comment` / `shadow_set_meta`. Remote hosts need nothing installed. `C-x C-z` opens the menu (ask a question from the terminal, relabel, instrument the shell for exact command boundaries). Read-only: no exec, no control. Design: `Docs/design/shadow-sessions.md` |
| **Session shell commands** | Per-session allowlist overrides; `/shell git add`, `/shell git all`, etc. |
| **Clear history** | `/clear` — wipe conversation history, keep files and session state |
| **Full session reset** | `/reset` — clear history, context files, conversation ID, and all session overrides (shell, yolo, timeout); fresh start without restarting the process |

---

## 11a. Execution Safety, Sandboxing & Approvals

Ziya's shell tool runs through layered guards, and any privilege beyond a conservative default floor (read plus write to `.ziya/` and `/tmp/`, and a read-only shell allowlist) requires an out-of-band human signature the agent cannot produce. See `Capabilities.md` → "Execution Safety & Approvals" for the full detail.

| Feature | Detail |
|---|---|
| **One-time approval-key provisioning** (`approval-keypair-provisioning`) | `sudo ziya-approve --provision` generates the root-owned Ed25519 signing keypair and installs a `visudo`-validated, locked-down sudoers entry; one-time per machine, and a prerequisite for all escalation approvals | <!-- cap: approval-keypair-provisioning -->
| **Verify-or-clamp escalation gate** (`escalation-config-signature-gate`) | At shell startup every privilege-bearing env value is checked against a signed canonical delta; a missing, invalid, or edited signature clamps the environment back to the floor. Authorization binds to content — editing a grant voids its signature | <!-- cap: escalation-config-signature-gate -->
| **Just-for-this-session grants** (`ephemeral-session-grant`) | `sudo ziya-approve --session` mints a nonce-bound Ed25519 grant that never lands on disk and is void on the next server start; a `cli-ephemeral` variant is minted in-process from the interactive `/shell` TTY | <!-- cap: ephemeral-session-grant -->
| **Expiring / time-boxed approvals** (`approval-ttl-expiry`) | An `expires_at` folded into the signed payload and enforced fail-closed; an enterprise plugin can set a maximum-TTL ceiling. The OSS default is unbounded (approvals do not expire) | <!-- cap: approval-ttl-expiry -->
| **Escalation audit report** (`escalation-audit-ledger`) | `ziya-approve --list` walks every escalating task across projects and prints signed/unsigned status; exit code `0`/`1` doubles as a CI compliance gate. A point-in-time CLI report, not a live dashboard | <!-- cap: escalation-audit-ledger -->
| **Injection-resistant command allowlist** (`shell-command-allowlist-engine`) | Commands are parsed and validated per segment and run with `shell=False`; defends against ANSI-C quoting, heredoc-hidden commands, `$()`/backtick nesting, env-prefix loader hijack and compound-body evasion. Not an OS-level sandbox | <!-- cap: shell-command-allowlist-engine -->
| **Write-policy enforcement** (`shell-write-policy-enforcement`) | Destructive commands, shell redirections and in-place edit flags are checked so writes touch only approved paths, resolved from a defaults → `~/.ziya/write_policy.json` → per-project config cascade | <!-- cap: shell-write-policy-enforcement -->
| **Credential-exfiltration guard** (`shell-credential-exfiltration-guard`) | Keeps `curl`/`aws` available for read use but blocks IMDS credential vending, `curl @file` uploads of credential paths, and high-risk IAM/STS/bulk-transfer AWS subcommands | <!-- cap: shell-credential-exfiltration-guard -->
| **Infrastructure-as-code deploy guard** (`shell-iac-deploy-guard`) | Allows `sam`/`cdk` build/synth/diff but denies `deploy`/`destroy`, so the agent cannot stand up or tear down cloud infrastructure with the developer's credentials | <!-- cap: shell-iac-deploy-guard -->
| **Interpreter-escape guard** (`shell-interpreter-escape-guard`) | Scans `python -c` / `node -e` style inline code for process-spawn and write indicators (CWE-94) that would otherwise bypass the allowlist; regex-based, over the enumerated interpreters | <!-- cap: shell-interpreter-escape-guard -->
| **Runtime execution hardening** (`shell-execution-runtime-hardening`) | Per-command process-group `SIGKILL` on timeout, byte-bounded output capture, a concurrency semaphore, a clamped timeout ceiling, and a cleaned child env. Process-level discipline, not OS isolation | <!-- cap: shell-execution-runtime-hardening -->
| **Parent-authoritative project write paths** (`parent-authoritative-project-write-paths`) | The project's own write policy is forwarded to the shell subprocess (unsigned by design — the same paths `file_write` already has) and re-asserted at every spawn so a hand-edited config value cannot forge it | <!-- cap: parent-authoritative-project-write-paths -->
| **Run permissions snapshot** (`task-run-permissions-snapshot`) | Each task run records the effective permissions it was granted at launch (eager capture, so later card edits cannot rewrite history); it records what was granted, not what executed | <!-- cap: task-run-permissions-snapshot -->
| **Task-scope permissions editor** (`task-scope-permissions-editor`) | A tri-state files/tools/skills/shell dialog for authoring a Task Card block's scope; authoring only — the grant is inert until signed and cannot bypass always-blocked commands | <!-- cap: task-scope-permissions-editor -->
| **Self-explaining scope-clamp notices** (`scope-clamp-diagnostics`) | When an escalation is clamped to the floor, the block error explains why (stale server, wrong-server grant, config changed after signing, or nothing supplied); diagnostics only, they never change what is authorized | <!-- cap: scope-clamp-diagnostics -->

---

## 11b. Web & Transport Security

Server-side HTTP hardening that is always on, independent of the shell/approval stack above.

| Feature | Detail |
|---|---|
| **Security response headers + CSP (`content-security-policy-and-headers`)** | `SecurityHeadersMiddleware` sets `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` and a Content-Security-Policy on non-SSE responses, with `relaxed` (default) and `strict` modes selectable via `ZIYA_CSP_MODE`. Maturity 3. Limit: default relaxed mode still allows `unsafe-inline`+`unsafe-eval`, strict mode must retain `unsafe-inline` (CRA inlines its runtime chunk) and breaks Vega expression diagrams; no HSTS/COOP/COEP, and nonce-based inline-script elimination is deferred | <!-- cap: content-security-policy-and-headers --> |
| **Origin/Referer CSRF guard (`origin-guard-csrf`)** | `OriginGuardMiddleware` rejects state-changing requests (POST/PUT/PATCH/DELETE) whose Origin (or Referer fallback) is not loopback, with an anchored regex so `localhost.evil.com` cannot match; safe methods and OPTIONS pass through. Maturity 3. Limit: header-less requests (curl/CLI) are allowed by default unless `ZIYA_STRICT_ORIGIN` is set, and the guard relies on loopback binding for its core assumption | <!-- cap: origin-guard-csrf --> |

---

## 12. What Ziya Does NOT Have (as of v0.6.x)

Tracked against the competitive landscape: Aki, Kiro, Cursor, GitHub Copilot, Windsurf, Claude Code, Aider, Cline.  
Format per row: the gap, who has it, and notes for context.

### 12a. Agentic & Workflow

| Gap | Who Has It | Notes |
|---|---|---|
| **Persistent cross-session memory** | Claude Code (MEMORY.md auto-save), Codex (two-phase memory pipeline), Aki (`~/.aki/memories/`), Mem0 integrations | No `~/.ziya/memories/` equivalent; history survives within a session but the model has no recall of previous sessions |
| **Plan / Act mode** | Aki (Plan=read-only, Act=execute), Cursor (preview mode) | No read-only preview toggle before file writes; users cannot review what the agent _intends_ to do before it does it |
| **Lifecycle hooks** | Aki (`PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`) | No hook system for approvals, notifications, logging, or external writes around tool calls |
| **Output schema enforcement** | Aki, LangChain structured output | No validation that LLM responses conform to a declared JSON schema; matters for pipeline/automation use cases |
| **Custom subagent definitions** | Claude Code (`.claude/agents/` YAML), Kiro (`.kiro/agents/`) | Delegates are dynamically generated from task decomposition; no user-defined agent templates |
| **Commit / PR creation** | Claude Code (branch + PR via gh), Codex (GhostCommit + PR), Kiro (autonomous PRs) | No built-in git commit generation or PR creation; users apply diffs manually and commit themselves |
| **Automatic context compaction** | Claude Code (auto at ~95% + manual /compact), Codex (model_auto_compact_token_limit), Cline (AI summarization), Kiro (customizable thresholds) | Deliberate design choice: Ziya provides user-controlled context curation (per-message muting, fork+truncate, selective file removal) instead of automatic compaction. Auto-compaction risks discarding details the user knows are important. See §4 "Context curation vs. auto-compaction" for rationale. |
| **Git checkpointing / rewind** | Claude Code (per-prompt checkpoint, /rewind, Esc+Esc), Codex (Ghost snapshots + `codex undo`), Cline (shadow Git per tool use), Kiro (GA checkpointing) | Ziya has per-diff undo but no filesystem-level snapshot/rewind across an entire prompt's changes |
| **Permission modes** | Claude Code (6 modes: default, acceptEdits, plan, delegate, dontAsk, bypassPermissions), Codex (suggest, auto-edit, full-auto), Cline (per-category auto-approve) | No tiered permission system; all tool calls are either allowed or require manual shell allowlisting |

### 12b. Customization & Profiles

| Gap | Who Has It | Notes |
|---|---|---|
| **Profile packaging (export/import bundle)** | Aki (`aki profile pack/install`), Cursor rules export | No way to export a bundle of skills + system prompt + model config + tool set and share/install it as a unit |
| **Per-profile tool selection** | Aki (`enabled_tools` per manifest) | Tools are global; cannot define a profile that exposes only a restricted or specialized tool subset |
| **Profile marketplace / remote registry** | Aki (Profile Service), Cursor community rules | No community or team-shared store for Ziya profiles or skill sets |
| **Custom branding per profile** | Aki (logo, favicon, theme, tips per profile) | Single global UI theme |
| **Response annotation** | Aki (v1.11), Cursor inline comments | Cannot select and annotate specific text within a model response |

### 12c. UI & Interaction

| Gap | Who Has It | Notes |
|---|---|---|
| **Native desktop app** | Aki (macOS + Windows), Cursor, Windsurf, VS Code | Browser-only; no Electron/native app |
| **IDE plugin / inline suggestions** | GitHub Copilot, Cursor, Cline, Tabnine, Codeium | No VS Code / JetBrains extension; Ziya is a standalone UI, not embedded in the editor |
| **Inline autocomplete (tab completion)** | GitHub Copilot, Cursor, Codeium, Supermaven | No keystroke-level code completion; Ziya operates at conversation/diff granularity |
| **Artifacts / side-panel document editing** | Aki ("Write with Aki"), Claude.ai Artifacts, Cursor Composer | No separate editable document pane alongside the chat |
| **Incognito / no-persist mode (web UI)** | Aki (incognito in UI) | CLI has `--ephemeral`; web UI supports session-wide ephemeral mode via `ZIYA_EPHEMERAL_MODE` env var, plus per-conversation ephemeral chats via the ghosted "+" button (see Capabilities.md). No in-UI toggle for whole-session incognito mode |
| **LSP integration** | Claude Code (`.lsp.json` for go-to-def, type errors), Kiro (Code OSS native), Q Developer | No language server protocol integration; code intelligence is AST-based only |
| **Filesystem / network sandbox** | Claude Code (Seatbelt/bubblewrap), Codex (Landlock+seccomp), Kiro (Agent Sandbox) | Shell commands run unsandboxed in the user's environment; allowlist is the only guardrail |
| **Structured JSON CLI output** | Claude Code (`--output-format json/stream-json, --json-schema`), Codex (`--json`) | No machine-readable output mode for CI/scripting pipelines; CLI always outputs human-readable markdown |

### 12d. Data & Integrations

| Gap | Who Has It | Notes |
|---|---|---|
| **Bedrock Knowledge Base native integration** | Aki | No direct KB query; achievable via a custom MCP server but not out-of-box |
| **Database direct connection** | Aki (AWS Postgres integration) | No built-in DB tool |
| **Microsoft Office automation (write)** | Aki (Gandalf MCP for Word/Excel, built-in PowerPoint MCP, Outlook MCP) | Ziya reads DOCX/XLSX/PPTX; it cannot write to or automate Office apps |
| **Runtime MCP hot-reload** | Aki (connect/disconnect without restart) | Supported: individual servers can be enabled/disabled/restarted live and the manager can reinitialize via `POST /api/mcp/initialize` without an app restart; picking up a *newly-added* config server still requires that re-read rather than a file watcher (`mcp-server-lifecycle`) | <!-- cap: mcp-server-lifecycle -->
| **Figma / design import** | Augment Code Design Mode, Cursor | No design-to-code path |
| **OAuth 2.0 for MCP** | Claude Code (full OAuth 2.0), Codex, Cline | Partial — static bearer/header token auth ships (v0.6.0, via the `"auth"` config block; inline tokens and env-var references), but the OAuth 2.0 authorization-code flow is not implemented | <!-- cap: mcp-remote-bearer-auth -->
| **GitHub Actions / CI integration** | Claude Code (`claude-code-action@v1`), Codex (@codex on PRs), Kiro (autonomous agent) | No published GitHub Action or CI pipeline integration |

### 12e. Security & Compliance

| Gap | Who Has It | Notes |
|---|---|---|
| **Security / vulnerability scanning** | Amazon Q Developer, Qodo, GitHub Advanced Security | No built-in CVE, secret detection, or SAST scanning |
| **Compliance audit log** | Enterprise tools generally | No structured audit trail of what files were read/written per session |

---

*Last updated: 2026. To add a feature, describe it in the appropriate section with enough detail to be useful for comparison against Aki, Kiro, Cursor, Copilot, and similar tools.*
