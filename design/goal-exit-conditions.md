# Goal-loop exit conditions

**Status:** proposal (revised)
**Related:** `design/goal-staged-by-default.md`, `app/agents/block_executor.py`,
`app/agents/until_evaluator.py`, `app/utils/completion_check.py`

## Background

Before any fix, a `/goal` whose objective doesn't apply produces this
behavior:

```
Iter 0: agent inspects target, finds nothing to do, emits
        <self_assessment objective_met="true" rationale="vacuously satisfied" />
Iter 1: agent does the same thing again, emits the same self_assessment
Iter 2: agent does the same thing again
... until until_max (15) is hit
```

Each iteration is marked "passed" in the task panel because
`is_failure()` returns False for `objective_met="true"`. But the
**Until block ignores `self_assessment` entirely** — it routes the
goal text into a separate model-evaluated condition (`until_evaluator`).

That evaluator answers the question "given this artifact summary, is
the action `add logger.warning calls` complete?" — which it
correctly says no to (no logger calls were added; the file was
already clean). So the loop never terminates until `until_max`.

## Root cause

There are two channels of "is this done?" signal flowing through the
executor and they don't talk to each other:

1. **Agent self-assessment** (`Artifact.self_assessment.objective_met`).
   The agent has already evaluated whether it met the objective and
   emitted a structured tag.
2. **Until-condition evaluator** (`evaluate_condition`). A separate
   small-model call that classifies the goal-text-as-condition
   against the artifact summary.

For goal-synthesized cards the goal text is action-phrased ("add X",
"fix Y"). The until-evaluator reads it as "have these actions been
performed?" — which is wrong for the vacuous-satisfaction case where
the correct answer is "the action wasn't needed."

The agent's self-assessment already captures this distinction
(`objective_met="true"`, rationale="vacuously satisfied"). We just
aren't honoring it.

## Fix

Three layers, all in `_execute_until`. Each layer handles a class of
case the previous one doesn't.

### Layer A: honor self_assessment

After each iteration, before consulting the until-condition evaluator,
check the agent's own verdict:

- `objective_met == "true"` → stop (success). This handles both the
  "actually did the work" case and the "vacuously satisfied" case.
- `objective_met == "false"` → continue iterating (agent declared
  failure; the loop should retry).
- `objective_met == "partial"` → continue iterating; same logic.
- `objective_met == "unknown"` (missing tag) → fall through to next
  layer; we have no signal from the agent.

This is the layer that fixes the original bug. It costs nothing
because the data is already on the artifact.

### Layer B: convergence backstop

If two consecutive iterations produce identical normalized summaries,
stop with `decisions += "converged"`. This catches:

- Agent that consistently emits malformed self_assessment tags.
- Agent that says `objective_met="false"` but produces the same
  finding every time (e.g. "I tried but X is still broken" with no
  new action).

Implemented as `sha256(normalized_summary)[:16]`. Cheap. Will miss
near-duplicates that differ in phrasing — that's an acceptable
false-negative since it's a backstop, not the primary signal.

### Layer C: until-condition evaluator (existing)

Unchanged. Still consulted when `until_condition` is non-empty and
the agent's self_assessment didn't already terminate the loop.
Mostly relevant for hand-authored cards with deliberate conditions;
goal-synthesized cards no longer set this.

### Goal synthesis change

Stop putting the goal text into `until_condition`. Goal-synthesized
cards now rely on Layer A as the primary exit signal:

```diff
-    until_condition=goal_text,
+    until_condition="",
```

The agent's self-assessment is the signal we want. The until-evaluator
is the wrong tool for action-phrased objectives.

## What this does NOT solve

- **Permission-blocked iterations.** When the agent can't do its
  work because it lacks permission, it currently emits
  `objective_met="false"` (or "partial"), which under Layer A means
  "keep iterating." That's exactly the wrong behavior — retrying
  with the same permissions will fail the same way.

  Handling this needs a new vocabulary value (e.g.
  `objective_met="blocked"`) and a corresponding change to
  `is_failure` so the loop stops cleanly. **Deferred to a follow-up
  doc** that also designs the user-facing permission-grant prompt.

- **Stuck iterations.** Currently no signal distinguishes "tried
  hard, can't" from "didn't really try." Layer B catches the
  pathological case (identical summaries) but a more nuanced
  detector ("two iterations with similar summaries despite tool
  activity") is future work.

- **Multi-block goal cards.** Today `synthesize_goal_card` emits
  `Until[Task]`. If goals grow to multi-step plans, the
  self_assessment of the inner `Task` block isn't the right exit
  signal for the outer `Until`. Worth revisiting if/when goal cards
  gain structure.

## Test plan

1. **Vacuous satisfaction.**
   `/goal find any places in app/api/commands.py that swallow
   exceptions silently and add logger.warning calls`
   Expected: iter 0 ends with `objective_met="true"`, loop terminates
   immediately. Run total ≈ 20s.

2. **Real work.**
   `/goal add a module docstring to app/utils/bead_prompt.py`
   Expected: iter 0 produces a diff, agent waits for approval, after
   approval iter 0 ends with `objective_met="true"`, loop terminates.
   (This still depends on the permission-grant work for a clean
   user experience; pre-grant it should still terminate after the
   diff is approved and the agent re-enters the loop with the file
   in its desired state.)

3. **Convergence backstop.**
   Synthetic test: a card whose body just emits a constant summary
   with `objective_met="unknown"` for every iteration. Expected:
   stops after iter 1 (two iterations same → converged).

4. **Hand-authored cards still honored.**
   A card with explicit `until_condition="contains DONE"` and an
   inner block that does *not* emit self_assessment should still
   be evaluated by the existing `until_evaluator` model call.
   Layer A returns "no signal", Layer C runs as before.

## Migration

- Existing goal cards in storage have `until_condition=<goal_text>`
  set. They'll continue to consult the until-evaluator, which will
  continue to mis-classify vacuous cases. **Acceptable**: existing
  staged/finished goals don't re-execute. New goals get the fixed
  synthesis path.
- No data model changes. `Artifact.self_assessment` already exists.
- No new tools or prompts. The agent already emits the tag.

## Code changes

See `design/goal-patches-bundle.md` Part 2 for the concrete diffs.

## Addendum: the stall breaker (added after run 2e1fbe76)

The layering above has a hole that only shows up on hand-authored cards.
Layers A (self_assessment) and B (convergence) are both **disabled when an
explicit `until_condition` is set**, for good reasons documented inline: an
inner task's `objective_met` describes its own atomic success, not the loop's
exit, and summary similarity should not second-guess an evaluator the author
asked for. The consequence is that an explicit condition leaves `until_max` as
the *only* terminator.

That is fine when the condition is merely hard to satisfy. It is not fine when
the condition is **unsatisfiable by construction** — when the loop's exit test
depends on machinery that is broken. Run 2e1fbe76 (music-notation campaign)
required each fix to be "visually verified in a fresh render" while its deploy
step was broken, so verification could never happen. 35 consecutive iterations
did real work, each judge correctly refused to rule on a stale bundle, and the
loop ran to `until_max: 40` over nine hours.

**Stall breaker** (`_UNTIL_STALL_LIMIT`, default 3): active regardless of
`condition`, evaluated *before* the condition evaluator so an unsatisfiable
condition cannot starve it. An iteration counts as non-progressing when any of
these hold:

- `artifact.failed`
- `self_assessment.objective_met == "partial"` — the agent's designed channel
  for "I hit a real obstacle", distinct from a verdict on the goal
- its summary signature equals the previous iteration's

Three consecutive non-progressing iterations stop the loop with a decision
naming the reason. Three rather than layer B's two: layer B only runs when
there is no explicit condition and can afford to be eager, whereas this one
overrides an author's explicit instruction and must be certain the loop is
stuck rather than slow. The paired negative test matters as much as the
positive: a progressing loop with an unsatisfiable condition must still run
the full `until_max`, or the breaker has just become a lower cap.

**Known limit.** An iteration that reports distinct prose and claims success
while actually being blocked is invisible to all three signals. Closing that
would need the task executor to surface a first-class "blocked" verdict
separate from success/failure, which is a larger change than this addendum.

