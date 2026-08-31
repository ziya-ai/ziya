# Task Card Self-Improvement — Design Note

## What it is

A block-level flag (`self_improve`) available at any layer of a card's
hierarchy.  When set, the executor runs the level as normal, then asks
a critic model to judge the outcome.  On a `revise` verdict the critic
supplies a **text-only patch** to that subtree, the patch is applied
and persisted to the live card, and the level restarts.  Cards
accumulate corrections across runs until the critic judges them as
correct as required.

## The gating threshold

The critic returns one of three verdicts:

| Verdict | Meaning | Effect |
|---|---|---|
| `accept` | outcome meets the criterion | loop ends, no edit |
| `no_improvement` | criterion unmet, but no text edit would meaningfully change the outcome (environmental fault, transient error, text already right) | loop ends, lesson recorded |
| `revise` | a concrete weakness in the card text caused/contributed, and a specific edit would **tangibly, meaningfully affect the outcome** | patch applied, level restarts |

Only `revise` with a concrete patch triggers a rewrite.  The prompt
sets the bar explicitly: no style rewrites, no marginal clarity, and —
the anti-drift clause — *never expand the card's scope or ambition
beyond its stated objective; improvement means better at the SAME job,
never a bigger job*.

Drift-as-amplifier (many layers of judges compounding a card into
something more powerful than the ask) remains deliberately reachable,
but only opt-in: raise the budgets and author an `improve_criterion`
that asks for expansion.  It can never happen emergently under the
default prompt.

## Authoring surface (fields on `Block`)

- `self_improve: bool` — the flag, any block type.
- `improve_criterion: Optional[str]` — authored acceptance criterion.
  `None` falls back to judging the outcome against the block's own
  instructions.  Authored criteria converge far more reliably.
- `improve_max: Optional[int]` — per-block rewrite budget.  `None` →
  3 (DEFAULT_IMPROVE_MAX); `0` disables rewriting entirely.

Run-wide: `ZIYA_TASK_IMPROVE_CEILING` (default 10) caps total rewrites
across all improving blocks in one run, because nested improving
levels multiply executions (3 inner × 3 outer = 9 inner subtree runs).

## The privilege fence — text yes, privilege never

Two independent layers:

1. **The existing approval system already excludes text.**
   `scope_canonical.task_escalation_block` hashes only non-floor
   `shell_commands` and non-floor writable `paths`.  An
   instructions-only edit keeps a signed `(block_id, scope_hash)`
   approval valid; any privilege change invalidates it and the block
   falls to the floor, fail-closed.  The agent never holds the signing
   key.

2. **The improvement write path cannot express a privilege change.**
   `apply_text_patch` assigns only fields in `PATCHABLE_FIELDS`
   (`instructions`, `state_context`) on block ids that already exist
   within the improving subtree.  It never inserts, removes, or
   reorders blocks and never touches `scope`.  Ids are therefore never
   reassigned — which is also why this deliberately does **not** ride
   on `task_card_write` / `TaskCardStorage.update`'s whole-root
   replacement: that path can silently mint fresh ids for blocks whose
   id was dropped, orphaning signed approvals.  Persistence re-applies
   the same whitelisted patch to the live card's own tree, so the
   fill-only `_assign_block_ids` in storage is a no-op by construction.

A critic hallucinating a block id from elsewhere in the card is caught
by the `allowed_ids` bound (the improving block's subtree only).

## Durability — two artifacts

- **The patched live card** — what makes run N+1 better.  Applied
  best-effort; a persistence failure never fails the run (the in-run
  restart still uses the in-memory patch).
- **The lesson ledger** — `task_card_lessons.jsonl` under the project
  dir (same shape as `task_card_refusals.jsonl`: append-only JSONL,
  read-modify-write, capped at 5,000).  One entry per critic verdict:
  `{at, card_id, block_id, run_id, verdict, lesson, patch_hash,
  applied, error?}`.

The ledger is load-bearing, not just audit: prior lessons are fed back
into every critique so the critic doesn't re-derive the same lesson
every run, and `seen_patch` (patch content hash, per card+block) stops
A→B→A oscillation — without it the card doesn't converge, it wanders.

Run records stay honest for free: `card_snapshot` freezes what each
run *launched with*; mid-run revisions are visible via `block_improved`
events and ledger entries rather than by rewriting the snapshot.

## Control flow

```
execute_block(level with self_improve)
  └─ normal dispatch (task/repeat/until/parallel/group/...)  → artifact
     loop:
       cancel / infra-gate check
       critic(block text, criterion, artifact digest, prior lessons)
       ├─ accept | no_improvement | no patch → record lesson, done
       ├─ budget or run ceiling exhausted    → record, done
       ├─ patch hash seen before (ledger)    → record oscillation, done
       ├─ patch fails whitelist/id check     → record error, done
       └─ apply in-memory + persist to live card + record + emit
          re-enter execute_block(copy with self_improve stripped)
```

The restart re-enters `execute_block` on a `model_copy` with
`self_improve=False`: the run map shows the level running again, scope
push/pop stays symmetric, and the gate cannot recurse into itself.
Nested improving blocks re-run their own loops on each restart —
intended, and bounded by the ceiling.

Resume interaction: a replayed block returns from the resume gate
before dispatch, so improvement never fires on replayed work.

## Failure semantics

A level that exhausts its budget without acceptance returns its last
artifact unchanged; the run classifies via the existing
`classify_terminal_status` machinery (typically `partial` if other
progress exists).  Self-improvement never *invents* failure or
success — it only decides whether to spend another attempt.

## Critic transport

`call_service_model(category="task_improvement")` — falls back to the
endpoint's default cheap model; override with
`ZIYA_TASK_IMPROVEMENT_MODEL` / `_ENDPOINT` / `_REGION` per the
standard service-model env scheme.  Transport failure or unparseable
reply resolves to `no_improvement` (stop, don't rewrite) — the loop is
conservative in exactly the way `until_evaluator` is.

## Known limits (v1)

- **Iteration-summary duplication.** A restarted Repeat appends a
  second generation of iteration summaries at the same indices to the
  same block state.  Truthful but cosmetically doubled in the dot
  strip.  Fix belongs in `_record_iteration` / run-map grouping, not
  here.
- **No editor UI yet.** The three fields are settable via card JSON
  and the API; block editors need a toggle + criterion field + budget
  input, and the inspector should render `block_improved` events and a
  card-text diff with one-click revert.
- **`state_context` patches on a State block inside the subtree**
  re-apply naturally on restart (placement-is-reset-policy is
  preserved).
- **Snapshot vs live card divergence.** A resumed run executes its
  snapshot tree; improvements persist to the live card.  If the card
  was edited since launch and an id no longer exists, persistence is
  refused (error recorded in the ledger) while the in-run improvement
  proceeds.
