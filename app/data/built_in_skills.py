"""
Built-in skills that ship with Ziya.

Skill Visibility:
  - model_discoverable: Advertised to the model via a compact catalog in the
    system prompt.  The model calls ``get_skill_details`` to load full
    instructions on-demand.  Always available unless the user hides them.
  - user_selectable: Only active when the user explicitly enables them via
    the UI.  Prompt is injected into the system message while active.
"""
from typing import List, Dict, Any

# Visibility constants
MODEL_DISCOVERABLE = 'model_discoverable'
USER_SELECTABLE = 'user_selectable'


def get_model_discoverable_skills() -> List[Dict[str, Any]]:
    """Return only skills the model should see in its catalog."""
    return [s for s in BUILT_IN_SKILLS if s.get('visibility') == MODEL_DISCOVERABLE]


def get_user_selectable_skills() -> List[Dict[str, Any]]:
    """Return only skills the user can toggle in the UI."""
    return [s for s in BUILT_IN_SKILLS if s.get('visibility') == USER_SELECTABLE]


def get_skill_by_id(skill_id: str) -> Dict[str, Any] | None:
    """Look up a skill by its stable ID."""
    return next((s for s in BUILT_IN_SKILLS if s.get('id') == skill_id), None)

BUILT_IN_SKILLS: List[Dict[str, Any]] = [
    {
        'id': 'code_review',
        'name': 'Code Review',
        'description': 'Detailed analysis with security and best practices focus',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Deep security audit, performance analysis, and best-practices review',
        'keywords': ['review', 'security', 'audit', 'best-practices', 'code-quality'],
        'prompt': '''When reviewing code, provide:
1. Security considerations and potential vulnerabilities
2. Performance implications
3. Best practice violations
4. Suggested improvements with explanations
5. Edge cases that may not be handled

Be thorough but constructive. Focus on high-impact issues first.''',
        'color': '#3b82f6',
    },
    {
        'id': 'debug_mode',
        'name': 'Debug Mode',
        'description': 'Step-by-step debugging and root cause analysis',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Systematic hypothesis-driven root cause analysis',
        'keywords': ['debug', 'troubleshoot', 'fix', 'error', 'root-cause'],
        'prompt': '''When debugging, follow this approach:
1. Reproduce the issue - clarify exact steps and symptoms
2. Form hypotheses about potential causes
3. Systematically verify or eliminate each hypothesis
4. Identify the root cause, not just symptoms
5. Suggest fixes with explanation of why they work

Work methodically. Avoid guessing.''',
        'color': '#ef4444',
    },
    {
        'id': 'concise',
        'name': 'Concise',
        'description': 'Minimal explanations, code-focused responses',
        'visibility': USER_SELECTABLE,
        'keywords': ['concise', 'brief', 'short', 'minimal', 'terse'],
        'prompt': '''Be concise. Provide code solutions with minimal explanation. Skip preamble. 
Use comments in code instead of prose explanations when possible. 
Get straight to the solution.''',
        'color': '#06b6d4',
    },
    {
        'id': 'educational',
        'name': 'Educational',
        'description': 'Detailed explanations for learning',
        'visibility': USER_SELECTABLE,
        'keywords': ['learn', 'explain', 'teach', 'tutorial', 'understand'],
        'prompt': '''Explain concepts thoroughly as if teaching. Include:
- Why, not just how
- Related concepts and connections
- Common misconceptions
- Examples that build intuition
- Analogies where helpful

Take time to build understanding, not just provide solutions.''',
        'color': '#8b5cf6',
    },
    {
        'id': 'continuous_documentation',
        'name': 'Continuous Documentation',
        'description': 'Keep Docs/ and CHANGELOG.md current as changes land',
        'visibility': USER_SELECTABLE,
        'keywords': ['docs', 'documentation', 'changelog', 'readme', 'writeup'],
        'prompt': '''Keep the files in Docs/ up to date and organized after changes are
applied. They should be readable by users to help them understand the
capabilities and operation of the system.

- Keep docs concise. Avoid creating new documentation files for a particular
  enhancement unless a genuinely new CATEGORY of documentation is required.
- When adding changelog entries, ALWAYS add them to the ## [Unreleased]
  section at the top of CHANGELOG.md — never to a numbered version section,
  which is already published.
- The version in pyproject.toml is the LAST PUBLISHED version, not the next
  one. Do not bump it to describe unreleased work.''',
        'color': '#0891b2',
    },
    {
        'id': 'test_everything',
        'name': 'Tests for Everything',
        'description': 'Test every feature added, enhanced, or repaired',
        'visibility': USER_SELECTABLE,
        'keywords': ['test', 'tests', 'coverage', 'pytest', 'jest', 'regression'],
        'prompt': '''Create and validate test cases for every feature and function that
is added, enhanced, or repaired.

- Periodically evaluate the test architecture as a whole to ensure it stays
  organized and usable and that coverage is maintained.
- Do NOT run tests against a diff patch you have no evidence has been
  applied. Providing a diff is not evidence that it was applied; only
  directly writing the file, or observing the change already present in the
  file content, is sufficient.
- Do NOT mark a test that demonstrates a legitimate bug as expected-to-fail.
  A failing test reflecting a real defect is a finding, not a nuisance.''',
        'color': '#65a30d',
    },
    {
        'id': 'web_research',
        'name': 'Web Research',
        'description': 'Ground responses in current web information with citations',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Search the web for current information and cite sources',
        'keywords': ['search', 'web', 'current', 'research', 'citations', 'news'],
        'prompt': '''When the user asks about current events, recent releases, live data,
or anything that may have changed after your training cutoff, use the
nova_web_search tool to look it up before answering.

Always cite your sources using the references returned by the tool.
Prefer multiple searches for complex topics — search once for overview,
then follow up on specifics.

If nova_web_search is not available, say so and answer from your
training data with an appropriate caveat.''',
        'color': '#f59e0b',
    },
    {
        'id': 'task_decomposition',
        'name': 'Task Decomposition, Delegation & Swarm',
        'description': 'Spawn parallel delegate agents (swarm), with optional coordinator and verifier roles, dependency ordering, and crystal handoff',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Spawn parallel delegate agents (swarm) with coordinator and verifier roles, dependency ordering, and crystal handoff',
        'keywords': ['decompose', 'parallel', 'delegate', 'orchestrate', 'split', 'swarm',
                     'agent', 'multi-agent', 'coordinator', 'verifier', 'crystal', 'handoff',
                     'concurrent', 'fan-out', 'pipeline', 'test', 'delegation',
                     'planner', 'researcher', 'critic', 'judge', 'synthesizer',
                     'debater', 'sub-agent', 'review', 'analyze', 'research'],
        'prompt': '''You can spawn multiple independent AI agents (a "swarm") to work on tasks
in parallel, then coordinate and verify their outputs. Use this capability whenever:
- The user asks to "send agents", "delegate", "swarm", "run in parallel", or "coordinate agents"
- A task naturally splits into independent units that don't share files
- There is a need for a coordinator to synthesize results from multiple workers
- There is a need for a verifier/reviewer to validate combined outputs
- Large refactors, multi-file feature work, test generation, or research tasks

AGENT ROLES you can assign:
- **worker**: Does the actual work on specific files/topics. Runs in parallel with peers.
- **planner**: Analyzes the full task and produces a structured plan crystal consumed by
  workers. Use when the work is ambiguous and workers need a shared blueprint first.
- **researcher**: Gathers information (reads code, searches docs, explores the codebase)
  without modifying files. Feeds findings to workers or coordinator via crystal.
- **coordinator**: Assembles and merges outputs from multiple workers into a unified result.
  Give it dependencies on all workers it needs to merge.
- **synthesizer**: Like coordinator but transforms heterogeneous outputs into a coherent
  whole — use when workers produce different formats, languages, or concerns that need
  more than mechanical assembly.
- **critic**: Reviews a *specific* worker's output for flaws, gaps, and edge cases.
  Feeds corrective feedback back into the plan. Not the same as verifier (which checks
  the whole); critic is scoped to one worker's output.
- **verifier**: Checks that the full combined output meets acceptance criteria.
  Give it dependencies on coordinator/synthesizer (or all workers if no coordinator).
- **judge**: Scores or ranks competing outputs when multiple workers produce alternative
  solutions. Picks the best or explains the tradeoffs. Use for architecture decisions,
  algorithm choices, or A/B comparisons.
- **debater**: Pair two debater agents arguing opposing positions; add a judge to decide.
  Use for tradeoff analysis, design reviews, or when you want adversarial pressure on
  an idea before committing.
- **sub-agent**: Spawned by a worker for a narrow sub-task. Enables recursive delegation.
  Use sparingly — only when a worker's scope is genuinely too large for one agent.

CRYSTAL HANDOFF: When a delegate finishes, it produces a "crystal" — a compressed
summary of its work. Downstream delegates (coordinators, verifiers) receive these
crystals automatically via their dependencies list.

Follow this process:
1. Identify distinct units of work that can run in parallel (workers)
2. Determine if a planner or researcher is needed before workers start
3. Determine if critics are needed to review individual worker outputs
4. Determine if a coordinator or synthesizer is needed to merge worker outputs
5. Determine if a verifier or judge is needed to validate/rank the final result
6. Ensure no two delegates modify the same file (avoid merge conflicts)
7. Order dependencies: planner/researcher → workers → critics → coordinator/synthesizer
   → verifier/judge

Output the decomposition as a fenced JSON block with the language tag
`delegate-tasks`. The format must be exactly:

```delegate-tasks
{
  "name": "Short plan name",
  "description": "What this plan accomplishes",
  "delegates": [
    {
      "delegate_id": "kebab-case-id",
      "name": "Human-readable name",
      "emoji": "🔧",
      "scope": "Detailed description of what this delegate does",
      "files": ["src/path/to/file.ts"],
      "dependencies": [],
      "role": "worker",
      "model_tier": "small"
    },
    {
      "delegate_id": "coordinator",
      "name": "Coordinator",
      "emoji": "🎯",
      "scope": "Synthesize outputs from all workers into a unified result. Reference each worker crystal.",
      "files": [],
      "dependencies": ["kebab-case-id"],
      "role": "coordinator"
    },
    {
      "delegate_id": "verifier",
      "name": "Verifier",
      "emoji": "✅",
      "scope": "Verify that the coordinator output correctly incorporates all worker inputs.",
      "files": [],
      "dependencies": ["coordinator"],
      "role": "verifier"
    }
  ]
}
```

Rules:
- Each delegate_id must be unique and kebab-case
- dependencies lists delegate_ids that must complete first
- files should be specific paths, not globs (empty array [] is valid for coordinators/verifiers)
- role must be one of: "worker", "coordinator", "verifier"
- Go wide when the work is parallelizable — use as many delegates as there are separably useful units of work
- Don't create delegates just to fill a quota; each one should have a distinct, non-overlapping responsibility
- Workers should run in parallel (no dependencies on each other)
- If the task is simple enough for a single response, just answer directly — don't over-delegate
- KEEP AGENTS NARROW: Prefer many small focused agents over few large general ones.
  Smart orchestration (dependency ordering, crystal handoff) handles complexity —
  not individual agent scope. A narrow agent that does one thing well beats a broad
  agent that does many things poorly. ("dumb agents, smart orchestration")
- Include an emoji that represents each delegate's purpose
- The scope field must be detailed enough for a fully independent agent to execute
- For coordinators: explicitly name which worker crystals to incorporate
- For verifiers: explicitly state what acceptance criteria to check
- OPTIONAL per-delegate `model_tier`: a portable cost/capability rung —
  one of `xsmall`, `small`, `medium`, `large`, `frontier` (`medium` is the
  default/average model). Assign cheap tiers (`xsmall`/`small`) to
  mechanical, well-scoped executor work and higher tiers (`large`) to
  synthesis, judgment, or coordination. This is the "cheap executors under
  a smarter supervisor" pattern. Reserve `frontier` for work that truly
  needs it — it runs ~20x the cost of `large` with heavy throttling. Omit
  to inherit the conversation's model.''',
        'color': '#6366f1',
    },
    {
        'id': 'task_cards',
        'name': 'Task Cards & Loops',
        'description': 'Compose a repeatable/loopable task card and launch it inline in the chat',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Author a Task Card: Task / Repeat / Parallel blocks, runs inline in chat with live status',
        'keywords': ['task', 'card', 'loop', 'repeat', 'iterate', 'fuzz',
                     'until', 'for each', 'retry', 'attempts', 'passes',
                     'parallel', 'count', 'do this N times', 'try again',
                     'keep trying', 'until it passes', 'over and over',
                     'batch', 'iteration', 'explore', 'sweep', 'schedule',
                     'recurring', 'cron', 'daily', 'every hour', 'stages',
                     'multi-step', 'multi-stage', 'pipeline', 'state', 'artifact',
                     'variables', 'given', 'assume', 'call', 'reuse',
                     'subtask', 'invoke', 'another card', 'named task'],
        'prompt': '''You can author a **Task Card** — a small block tree the user launches
from the conversation.  A task card runs inline in the chat and reports live
status; the user can cancel, inspect, and query it.  Use a task card when:

- The user asks to repeat an action N times, retry until it passes, run a
  for-each over a list, or otherwise *iterate* structured work
- The same task needs to produce many outcomes (fuzz-testing, exploration,
  sampling) so failures can be clustered and inspected
- A piece of work has a clear loop/sequence/parallel shape that benefits
  from being a reusable object rather than an ad-hoc chat turn
- The user describes a fixed multi-stage pipeline ("first do A, then B,
  then C") that should be tracked and re-runnable as a unit
- The user wants something to run on a recurring schedule (hourly, daily,
  cron) rather than once right now

Use `delegate-tasks` instead (not this) when the work is a *fan-out of
different specialized roles* (planner → workers → verifier) with
dependencies and crystal handoff.  Task cards are for iteration and
structured repetition; delegates are for multi-agent orchestration.

## Block grammar

Six block shapes compose the tree.  Any block's `body` can contain any
other block (except Task and State, which are leaves with no body).

**Task** — atomic action, one model invocation, returns one Artifact.

```
{
  "block_type": "task",
  "name": "Generate a random spec",
  "instructions": "Emit a random but plausible packet-diagram JSON spec.",
  "scope": {
    "paths": [{"path": "app/services/diagram_renderer.py", "read": true}],
    "tools": ["render_diagram"],
    "skills": [],
    "model_tier": "small"
  }
}
```

**Repeat** — wraps a body, runs it N times.  Modes:
- `"count"` — run exactly `repeat_count` times
- `"until"` — run until the body artifact does not fail, max `repeat_max`
- `"for_each"` — run once per item in a list
Orthogonal: `repeat_parallel: true|false`, `repeat_propagate: "none"|"last"|"all"`.

```
{
  "block_type": "repeat",
  "name": "Fuzz loop",
  "repeat_mode": "count",
  "repeat_count": 10,
  "repeat_parallel": true,
  "body": [ ]
}
```

**Parallel** — runs its body blocks concurrently (different work, not copies
of one block — that is Repeat's job).  Implicit sequence: stacking multiple
blocks in any `body` runs them top-to-bottom.

```
{ "block_type": "parallel", "name": "Fan out", "body": [ ] }
```

**Group** — a plain sequence of DISTINCT one-shot stages, run top-to-bottom
exactly once, no repetition and no fan-out.  This is the shape for "do step
1, then step 2, then step 3" multi-stage processes: each stage is its own
Task with its own instructions and scope.  Use a Group root whenever the
user describes more than one distinct stage that each runs once — do NOT
collapse multiple stages into one Task's instructions, and do NOT wrap them
in a Repeat(count=1) just to have a body.

```
{
  "block_type": "group",
  "name": "5-stage migration",
  "body": [
    { "block_type": "task", "name": "Stage 1: ...", "instructions": "..." },
    { "block_type": "task", "name": "Stage 2: ...", "instructions": "..." }
  ]
}
```

**Until** — repeats its body until a MODEL-EVALUATED condition holds, not a
substring match.  Distinct from Repeat's `until` mode: on each iteration,
after the body runs, a separate evaluator model is asked "given this
result, is `<until_condition>` true?"  If yes, the loop stops; if no and
`until_max` isn't reached, it repeats.  Use this for conditions that need
judgment ("the tests pass", "the counter is above 300", "the output looks
correct") rather than a literal string appearing in the summary — use
Repeat's `until` mode instead when a literal substring check is sufficient
(it's cheaper — no evaluator call).

```
{
  "block_type": "until",
  "name": "Fix until tests pass",
  "until_mode": "model",
  "until_condition": "all tests pass with no errors",
  "until_max": 5,
  "body": [
    { "block_type": "task", "name": "Propose fix", "instructions": "..." },
    { "block_type": "task", "name": "Apply and re-test", "instructions": "..." }
  ]
}
```

Leave `until_condition` blank to fall back to "stop on the first
non-failed iteration" (goal-card style, driven by the inner task's own
self-assessment instead of a separate evaluator call).

**Schedule** — a recurring TRIGGER, not a loop.  Wraps any body (Task,
Repeat, Parallel, Until, or a nested Schedule) and causes the in-process
scheduler to fire an independent run of that body each time the trigger
condition is met.  Modes:
- `"interval"` — every N minutes/hours/days (`schedule_interval_value` +
  `schedule_interval_unit`)
- `"at"` — once at a specific ISO-8601 timestamp (`schedule_at_iso`)
- `"daily_at"` — every day at `"HH:MM"` local time (`schedule_daily_at`)
- `"cron"` — a 5-field cron expression (`schedule_cron`)
A card with a Schedule root is launched once to *register* the trigger;
each subsequent fire produces its own independent TaskRun.  "Run now" on
such a card runs the body immediately as a one-off, in addition to (not
instead of) its scheduled fires.

```
{
  "block_type": "schedule",
  "name": "Nightly report",
  "schedule_mode": "daily_at",
  "schedule_daily_at": "02:00",
  "body": [
    { "block_type": "task", "name": "Generate report", "instructions": "..." }
  ]
}
```

**State** — a read-only declaration of run-scoped givens.  A leaf (no
body) like Task, but it doesn't invoke the model — it just sets context
for the tasks that follow it.  Two forms, usable together:
- `state_context`: freeform prose ("assume prod, migration already ran,
  feature flag is off") automatically prepended to every subsequent
  in-scope task's context — no templating needed. This is the primary,
  most natural form.
- `state_variables`: a name → literal map for tasks that want to
  reference a specific value by name via `{{var.NAME}}` templating.
Placement is the reset policy: a State block that runs once (before a
loop, or at the top of a Group) sets its givens once for the whole run.
The SAME state block placed INSIDE a Repeat/Until body re-applies its
literals at the start of every iteration, resetting to baseline each
cycle — use this when you want a loop to always start from the same
known state rather than drifting.

```
{
  "block_type": "state",
  "name": "Assume staging environment",
  "state_context": "Target environment is staging, not production. The database migration has already run.",
  "state_variables": { "environment": "staging" },
  "body": []
}
```

**Call** — invoke a SEPARATELY-DEFINED unit of work by name: another task
card in this project, or a named file task from `tasks.yaml`.  A leaf (empty
`body`); the callee's tree is resolved at run time and runs inline in this
run, and the callee's artifact becomes the Call block's artifact, so a later
sibling sees it through `{{previous_sibling}}` exactly as if the work had
been written here.

Use a Call — rather than copying the callee's blocks into this card — when
the same work is already defined elsewhere and should stay defined in ONE
place.  Editing the callee then changes every caller, which a copied
subtree cannot do.  Copy instead when the work only *resembles* the callee
and will diverge.

```
{
  "block_type": "call",
  "name": "Run the shared test sweep",
  "call_target": "Full test sweep",
  "call_target_kind": "card"
}
```

`call_target_kind` is `"card"` (the default, omit it) or `"file_task"`.  A
card target is addressed by card id or by card name (case-insensitive); a
file task by its name in `tasks.yaml`:

```
{
  "block_type": "call",
  "name": "Cut the release",
  "call_target": "release",
  "call_target_kind": "file_task"
}
```

Two constraints to author within:
- **Permissions do not cross the call.** The callee runs with its OWN
  approved scope; the caller's grants are not visible to it, and the
  caller cannot lend it permissions.  Do not put a scope on the Call block
  expecting the callee to receive it — grant what the callee needs on the
  callee itself.
- **Calls cannot be parameterized yet**, so a callee must already be
  self-sufficient.  The only things that reach it are run-scoped State
  givens and prior-sibling results.  A callee needing per-call inputs is a
  sign the work belongs inline in this card instead.

## Propagation (templating)

A block's `instructions` may reference prior results via `{{ }}` templates,
rendered at dispatch time:
- Inside a Repeat body: `{{index}}`, `{{item}}` (for_each), `{{previous}}`
  (propagate=last), `{{all}}` (propagate=all) — field access like
  `{{previous.summary}}`, `{{previous.outputs[0].text}}`.
- Inside a sequence: `{{previous_sibling}}`, `{{sibling("block-id")}}`.
- Anywhere in scope of a State block: `{{var.NAME}}` for a declared
  variable; State's `state_context` prose needs no template at all — it
  is injected automatically.
Missing fields render as empty strings — never crash.

## Scope (what a task can touch)

Each Task block may carry a `scope` object narrowing what that specific
task is allowed to do — scope does NOT cascade to children, every Task
sets its own:
- `paths`: list of `{path, is_dir, read, write, context}` entries. `read`
  is advisory; `write` is enforced (file_write is blocked outside granted
  writable paths); `context` preloads that file's content into the
  task's system prompt.
- `tools`: allowlist of tool names the task may call. Empty = unrestricted.
- `skills`: skill ids to activate for this task only.
- `shell_commands`: per-task shell-command grants (literal first-token
  match, or `"re:<pattern>"` against the full command line). Additive
  over the base shell policy; never unlocks `sudo`/`vi`/etc.
- `model_tier`: OPTIONAL portable model rung this task runs on — one of
  `xsmall`, `small`, `medium`, `large`, `frontier` (`medium` is the
  default/average model; `frontier` is the rare, ~20x-cost, throttled
  top). Unlike the permission fields above, model selection DOES flow
  down: a tier set on a container block (Repeat/Parallel/Until/Group) or
  the card applies to every task beneath it, and a leaf can override for
  itself (most specific wins). Put a smart tier (`large`) on the card and
  cheap tiers (`small`) on mechanical leaf tasks to run "cheap executors
  under a smarter supervisor". An unmapped rung rounds UP to the nearest
  available model, so it never silently under-serves a task. `model_name`
  / `model_id_override` pin a SPECIFIC model instead — an escape hatch
  that is not portable across endpoints, so prefer `model_tier`.
  PLACEMENT: model_tier and the model_name / model_id_override /
  model_endpoint fields live INSIDE this scope object — never at the
  block's top level. A model_tier placed on the block itself (a sibling
  of "scope") is silently ignored and the block runs on the inherited
  model. The same holds for container blocks: a container's tier goes in
  the container's own scope.
Omit `scope` entirely for a task that needs only its default (safe,
read-mostly) permissions — most tasks don't need to set this.

## Output artifacts (what a task preserves)

A task returns a summary string plus a list of **declared artifacts**.
The summary is prose the caller reads; artifacts are the durable work
products. Anything a task produces but does not declare is NOT preserved
— it lives only in that task's sandboxed conversation, which is
discarded.

A task declares one by calling the `emit_artifact` tool, which accepts:
- `text` — a short conclusion, rationale, or finding
- `file_path` — a file the task produced (validated against the task's
  granted paths)
- `data` — a JSON object of structured values
- `diagram={"type": ..., "definition": ...}` — rendered through the
  headless renderer AT EMIT TIME and frozen as a PNG, so the exact
  pixels are preserved with the run. If the render fails, the error
  evidence is preserved as the artifact instead — for a broken spec the
  failure IS the output worth keeping.

**`render_diagram` vs `emit_artifact` — look vs keep.** These are not
interchangeable and their image lifetimes differ:

- `render_diagram` is a SCRATCH LOOK. It returns the image so you can
  judge it. That image stays in your context for a short window of tool
  iterations and is then replaced by its text summary, because a task
  card run is one turn made of many iterations and re-sending every
  render forever would exhaust the context.
- `emit_artifact(diagram=...)` is the DURABLE record. Pixels are frozen
  with the run and survive indefinitely.

When a render is elided you will see a placeholder in place of the
image. It means only that the pixels were dropped to save context — you
DID see that image, and a judgement you made while it was in view was
based on direct observation and still stands. Do not retract or
re-litigate a visual finding because the image is no longer displayed.

The placeholder carries a handle like `img-3f9a1c04`. To see those exact
pixels again — not a re-render — call `recall_image(handle=...)`. Use it
when you need NEW detail from an earlier render or want to compare it
against a current one, never merely to reassure yourself about a
conclusion you already reached. If you know up front that you will be
comparing successive attempts, pass `retain="turn"` to `render_diagram`
so several stay in view at once and no recall is needed.

**Granting the tool.** `tools` is a strict allowlist: a Task that lists
any tools must list `emit_artifact` explicitly, or the task will be told
to declare artifacts and have no tool to do it with. Tasks that omit
`scope.tools` entirely get it automatically.

**Grouping.** Three optional fields relate parts; the viewer picks a
layout from their SHAPE, so no label text is special-cased:
- `group` — id linking related parts (`"issue-3"`, `"region-eu"`)
- `label` — display name within the group (`"before"`, `"after"`,
  `"attempt 2"`, `"us-east"`)
- `seq` — 0-based ordering within the group

Two labeled parts in one group lay out side by side; parts carrying
`seq` lay out as a sequence; small groups become a grid; anything else
lists. Which block and loop iteration emitted a part is recorded
automatically — instructions never need to mention it.

**Write instructions that say what to emit.** A task will not invent a
useful grouping scheme on its own. Name the artifacts you want:

```
"instructions": "Render the spec with render_diagram and judge it. Then
emit_artifact the rendered diagram with group=\\"spec-<N>\\" and
label=\\"rendered\\", and emit_artifact a text part named \\"verdict\\"
in the same group summarizing whether it is readable and complete."
```

For before/after work, emit both halves into one `group` with distinct
`label`s so they render as a comparison:

```
"instructions": "... After the fix, emit_artifact BOTH renders into
group=\\"issue-<N>\\": the failing spec with label=\\"broken\\" and the
fixed one with label=\\"fixed\\", each passing diagram={type, definition}
so the rendered form is frozen with the run."
```

## Carrying state between iterations (the run blackboard)

A loop iteration can only see the PREVIOUS iteration's summary via
`{{previous}}`. It cannot read an earlier iteration's artifacts. So a
backlog established in an early stage is invisible three iterations
later, and each iteration re-derives what it should have been told —
re-running builds, re-checking deploy hashes, re-litigating a defect an
earlier iteration already fixed.

Fix this with a plain file. `.ziya/` is writable by default, no scope
grant needed. Give the loop a file and instruct every iteration to read
it first and update it last:

```
"instructions": "FIRST: file_read .ziya/<card>-state.json (it may not
exist yet — treat a missing file as an empty backlog). It holds
{\\"backlog\\": [...], \\"done\\": [...], \\"facts\\": {...}}. Work the
highest-priority item in backlog that is NOT in done. LAST: file_write
the file back with your item moved to done, any newly discovered defect
appended to backlog, and any durable fact (a deployed bundle hash, a
verified path) recorded in facts so the next iteration does not have to
re-derive it."
```

This is what keeps a long loop from re-treading ground: the file is the
iteration-to-iteration memory that `{{previous}}` cannot be.

## Long-running commands

If any task runs a command that legitimately takes minutes — a
production frontend build, a full test sweep — set
`scope.shell_timeout_secs` on that task (or on a container, since it
merges as a MAXIMUM). The base ceiling is 300s; a command that exceeds
it is killed mid-flight and the failure surfaces as an opaque timeout
that the model will usually retry, burning the same minutes again.
Declaring the need once in scope is far more reliable than hoping the
model passes `timeout` on every invocation:

```
"scope": {"shell_commands": ["npm"], "shell_timeout_secs": 1200}
```

## Output format

Emit a fenced JSON block with language tag `task-card`.  The user can
preview it and click **Start**; launching creates a run bound to the chat
and the inline tile shows live status.

## Choosing a root block — decision guide

- One single action, no repetition → **Task** root.
- Same action repeated N times, or once per item in a list, or until a
  cheap substring check passes → **Repeat** root.
- Different actions running at the same time (no shared ordering) →
  **Parallel** root.
- Several DIFFERENT, DISTINCT steps that each run exactly once, in a
  fixed order ("first do A, then B, then C") → **Group** root. This is
  the most commonly needed shape for described multi-stage processes —
  do NOT collapse the stages into one Task's instructions, and do NOT
  wrap them in a trivial `Repeat(count=1)` just to have a body.
- Repeat something until a model has to JUDGE whether a condition holds
  (not just check a literal string) → **Until** root.
- The work should fire on a recurring schedule rather than immediately →
  **Schedule** root, wrapping whichever of the above shapes the
  triggered work actually needs.
- Need to fix some givens before a loop runs, or reset a loop to a known
  baseline each cycle → put a **State** block first in the relevant body
  (a Group root's body if the givens apply to the whole run; inside a
  Repeat/Until body if they should reset every iteration).

## Example: fuzz test the renderer 10 times

Root is a Repeat(count=10, parallel=true) wrapping a Task that generates
a random spec, followed by a Task that renders it.  The renderer's
instructions reference the generator output via templating.  On failure,
the iteration is marked failed and its signature clustered automatically.

## Example: keep trying to fix X until the tests pass

Root is an Until block (until_condition="all tests pass", until_max=4)
wrapping a sequence: Task(propose fix) then Task(apply and re-test).
Exits as soon as the evaluator judges the condition true.  Use Repeat's
`until` mode instead if a plain substring match in the summary would do.

## Example: a 5-stage migration

Root is a Group with 5 Task children, each with its own name and
instructions, run top-to-bottom exactly once.  Each stage's task can
reference the prior stage's result via `{{previous_sibling}}`.

## Example: nightly report at 2am

Root is a Schedule (mode="daily_at", schedule_daily_at="02:00") wrapping
a single Task that generates the report.

## Rules

- The root is ONE block; use a Group root for a fixed sequence of distinct
  one-shot stages, or a Repeat/Parallel root to run a body of repeated or
  concurrent work, or an Until root when termination needs judgment, or a
  Schedule root when the work should recur.
- `instructions` should be self-contained — the Task runs in a sandboxed
  conversation and cannot see anything outside its scope.
- Any task whose work product is worth keeping (a file, a chart, a
  measurement, a verdict) should be told in its `instructions` to
  `emit_artifact` it, with the `group`/`label` to use. If that task
  restricts `scope.tools`, include `emit_artifact` in the allowlist.
  Un-emitted work is discarded with the task's sandbox — only the
  summary and declared artifacts survive.
- Don't over-decorate: if the user asks for a single action, emit a card
  with a single Task root, not a trivial Repeat(count=1).
- Keep `repeat_count` reasonable for default runs (5-50).  Larger sweeps
  (1000+) are supported but should be explicit in the user's request.
- Use Until (not Repeat's until mode) whenever the stopping condition
  needs interpretation rather than a literal substring match.
- Do not include `id`, `created_at`, or other server-assigned fields —
  they are filled in on create.''',
        'color': '#eab308',
    },
    {
        'id': 'packet_diagrams',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render bit-level protocol frame / header / wire-format layout diagrams',
        'name': 'Packet Diagrams',
        'description': 'Generate bit-level protocol frame layout diagrams',
        'keywords': ['packet', 'protocol', 'frame', 'header', 'bitfield', 'bytefield', 'wire-format', 'rfc'],
        'prompt': '''You can render packet / protocol frame diagrams using ```packet``` code blocks.
The content must be a JSON object with this schema:

{
  "title": "Frame Name",                    // required
  "subtitle": "Description line",           // optional
  "bitWidth": 8,                            // bits per row (default 8; use 32 for RFC-style)
  "sections": [                             // required, at least 1
    {
      "label": "Section Name\\n<size>",      // left-column label (\\n for 2-line)
      "color": "transport",                  // named theme, hex string, or {"bg","border","text"}
      "rows": [                              // each row is an array of [name, bits] or [name, bits, colorOverride]
        [["Field A", 4], ["Field B", 4]],    // fields in one row must sum to bitWidth
        [["Full-width field", 8]]
      ],
      "brackets": [                          // optional right/left bracket annotations
        {"start_row": 0, "end_row": 1, "label": "Group", "side": "right"}
      ]
    }
  ]
}

Color themes (auto dark-mode adapted): header, transport, security, control,
payload, metadata, reserved, error, network, highlight, accent, purple, dark.
Or pass a hex string like "#B2E0F0" — border and text auto-derived.
Or pass {"bg":"#B2E0F0","border":"#4BA3C7","text":"#1A5276"} for full control.
Omit color entirely and sections get distinct hues automatically.

Field color overrides: third element in field tuple overrides section color.
  [["Reserved", 2, "reserved"], ["Data", 6]]

Brackets nest automatically — overlapping ranges on the same side get
increasing depth. Use "side": "left" for left-side brackets.

Multi-byte fields: show as separate rows with bit-range notation:
  [["Addr [15:8]", 8]], [["Addr [7:0]", 8]]

Example:
```packet
{
  "title": "Simple Protocol Frame",
  "bitWidth": 8,
  "sections": [
    {"label": "Header\\n<2B>", "color": "transport", "rows": [
      [["Version", 4], ["Type", 4]],
      [["Length", 8]]
    ]},
    {"label": "Payload", "color": "payload", "rows": [
      [["Data (variable)", 8]]
    ]}
  ]
}
```''',
        'color': '#0ea5e9',
    },
    {
        'id': 'music_notation',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render annotated music notation: inline snippets or complete staves/scores',
        'name': 'Music Notation',
        'description': 'Generate sheet music notation, from a short inline phrase to a full annotated score',
        'keywords': ['music', 'sheet-music', 'notation', 'staff', 'stave', 'score', 'chord',
                     'melody', 'vexflow', 'harp-pedal', 'tablature'],
        'prompt': '''You can render music notation two ways depending on how much you need to show.

**Inline (conversational, no chrome)** — for a short phrase mentioned in passing,
use a single-backtick codespan starting with `music:` followed by VexFlow
EasyScore note syntax (`key/duration` pairs, comma-separated). Renders as a
small inline staff with no border/card, matching how `$x=0$` renders inline
for math:

`music: C4/q, D4/q, E4/q, F4/q`

Duration codes: `w` whole, `h` half, `q` quarter, `8` eighth, `16` sixteenth.
Append `.` for dotted (`q.`). Write the pitch as letter + optional accidental
 + octave with NO slash: `C4` is middle C, `C#5` and `Bb4` carry accidentals.
The duration MUST be one of those codes -- any other value (a bare number like
`4`, an out-of-range code like `999`, or a huge integer) is treated as an error:
the fenced block falls back to a quarter note with a console warning, and the
inline form declines to render, rather than freezing the staff.

The inline form supports notes only -- there is no annotation syntax. Text
above or below a note requires the fenced block below, whose `annotations`
field does this properly. Do not write `^"text"` or `_"text"` inline: the
parser stops at the `^` and silently drops every note after it, so the staff
renders short rather than reporting an error.

**Complete scores (full chrome, multi-note/multi-voice)** — for anything with
more than a few notes, multiple voices, tablature, or harp pedal diagrams, use
a ```music``` fenced code block with a JSON spec:

```
{
  "type": "music",
  "clef": "treble",                         // treble | bass | alto | tenor | percussion
  "keySignature": "C",                       // e.g. "C", "G", "Bb", "F#"
  "timeSignature": "4/4",
  "notes": [
    {
      "keys": ["c/5"],                       // one entry per note in a chord
      "duration": "q",                       // w, h, q, 8, 16 (+ "." for dotted)
      "annotations": [                       // optional text above/below the note
        {"text": "Cmaj7", "position": "above"}
      ],
      "articulations": ["staccato"],         // see list below
      "ornaments": ["trill"],                // see list below
      "dynamic": "pp",                       // dynamic mark under THIS note
      "harpPedal": "^v-|vv-^"                // optional, see below
    },
    {"keys": ["d/5"], "duration": "q"}
  ],
  "slurs":       [{"from": 0, "to": 2}],                      // phrase curve
  "ties":        [{"from": 0, "to": 1}],                      // same-pitch tie
  "glissandos":  [{"from": 2, "to": 3, "text": "gliss."}],    // pitch slide
  "hairpins":    [{"from": 0, "to": 3, "type": "cresc"}]      // cresc | dim
}
```

## Rests

A rest is a note entry with `"rest": true` and a `duration`; `keys` is not
needed and is ignored if given.  The renderer positions the rest correctly for
the active clef on its own.

```
"notes": [
  {"keys": ["c/5"], "duration": "q"},
  {"rest": true, "duration": "q"},
  {"keys": ["e/5"], "duration": "h"}
]
```

Durations are the same codes as notes: `w h q 8 16`, and `"q."` for a dotted
rest.  A whole-measure silence is one `{"rest": true, "duration": "w"}`.

Rests occupy beat time exactly as notes do, so count them when filling a bar.
They also occupy an INDEX in the notes array: a slur, tie, hairpin or bracket
referencing note 3 counts rests among the first three entries.  Do not anchor
a slur or tie to a rest — attach spans to the sounding notes on either side.

Do not put `articulations`, `dynamic`, `fingering` or `chordSymbol` on a rest;
those belong on sounding notes.

All four span lists take 0-based indices into the `notes` array above.

Dynamics: `ppp pp p mp mf f ff fff sf sfz rfz fp` — attach with a note's
`dynamic` field.  Anything outside that set is dropped with a console warning,
because the renderer can only typeset marks built from f/p/m/s/z/r glyphs.
Use `hairpins` for gradual changes; a hairpin is a wedge spanning notes, not
a per-note mark.

Articulations (a note's `articulations` list): `staccato staccatissimo accent
tenuto marcato fermata-above fermata-below harmonic open-string upbow downbow`.

`harmonic` is the open-circle harmonic indicator used for harp and all string
instruments (a harp harmonic, a violin natural harmonic).  It draws above the
notehead; there is no separate harp-specific name for it.  `open-string` is a
different marking (snap pizzicato), not a harmonic.

Ornaments (a note's `ornaments` list): `trill mordent mordent-inverted turn
turn-inverted`.

## Beaming

Set `"autoBeam": true` on the spec to beam eighths and shorter into the
meter's natural beat groups.  **Without it every eighth and sixteenth draws an
individual flag**, which is only correct for isolated notes — so any passage
of running eighths wants `autoBeam`.

```
{"type": "music", "timeSignature": "4/4", "autoBeam": true, "notes": [ ... ]}
```

Beaming runs per measure, so a group never crosses a barline.  Rests break a
beam group.  Notes a quarter or longer are never beamed, so `autoBeam` is
harmless on music that has none.

`beamGroups` overrides the beat grouping, as [numerator, denominator] pairs:
`"beamGroups": [[3, 8]]` beams 6/8 as two groups of three rather than three
groups of two.

For a grouping the automatic pass cannot express, give explicit `beams` by
note index (0-based, into that staff's own note list, counting rests):

```
"beams": [{"from": 0, "to": 3}, {"from": 4, "to": 7}]
```

`beams` is ADDITIVE with `autoBeam`, so use one or the other for any given run
of notes — enabling both over the same notes double-beams them.  A range needs
at least two notes and is skipped with a warning if out of range.

Use the FRIENDLY NAMES above verbatim.  Do not pass raw VexFlow codes such as
`a.` or `a>`, and do not invent names: an unrecognised name is skipped and
warned about rather than guessed at.

`slurs` vs `ties`: a tie joins two notes of the SAME pitch into one sustained
sound; a slur groups DIFFERENT pitches into a phrase.  They look similar but
mean different things, so pick by meaning, not appearance.

## Tuplets (triplets, quintuplets, ...)

A tuplet fits an unusual number of notes into a beat -- three eighths in the
time of two (an eighth triplet), five in the time of four, and so on.  There is
no duration code for "an eighth of a triplet", so write the notes at their FACE
value and add a `tuplets` span; the renderer both draws the number bracket and
rescales the notes so the group occupies the right amount of time.

```
{
  "type": "music", "timeSignature": "4/4", "autoBeam": true,
  "notes": [
    {"keys": ["c/5"], "duration": "8"},
    {"keys": ["d/5"], "duration": "8"},
    {"keys": ["e/5"], "duration": "8"},
    {"keys": ["f/5"], "duration": "q"},
    {"keys": ["g/5"], "duration": "q"},
    {"keys": ["a/5"], "duration": "q"}
  ],
  "tuplets": [{"from": 0, "to": 2}]
}
```

`from`/`to` are 0-based indices into the note list (counting rests), inclusive,
same as `slurs` and `beams`.  A bare span is a triplet: `num` defaults to the
number of notes and `inSpaceOf` to 2.  For other tuplets give both:

```
"tuplets": [{"from": 0, "to": 4, "num": 5, "inSpaceOf": 4}]   // quintuplet
```

`ratioed: true` prints the full "5:4" instead of just "5"; `bracketed` forces
the enclosing bracket on or off (default: on for unbeamed notes, off for
beamed); `position` is `above` (default) or `below`.  Beam a tuplet with
`autoBeam` or an explicit `beams` span exactly as you would any other notes --
tuplets and beams are independent.

## Grace notes (appoggiatura, acciaccatura, ornamental runs)

Attach `graceNotes` to any note for small notes played BEFORE it.  They carry
no beat time, so adding them never shifts where the main notes fall.  Each
grace note gives `keys` and `duration` just like a normal note; set
`slash: true` for the acciaccatura (the "crushed" grace, drawn with a slash
through its stem), and leave it off for the appoggiatura.

```music
{
  "type": "music", "clef": "treble", "timeSignature": "4/4",
  "notes": [
    {"keys": ["c/5"], "duration": "q", "graceNotes": [{"keys": ["b/4"], "duration": "8"}]},
    {"keys": ["d/5"], "duration": "q", "graceNotes": [{"keys": ["e/5"], "duration": "8", "slash": true}]},
    {"keys": ["e/5"], "duration": "q", "graceNotes": [{"keys": ["f/5"], "duration": "16"}, {"keys": ["g/5"], "duration": "16"}]},
    {"keys": ["c/5"], "duration": "q"}
  ]
}
```

Give two or more grace notes to write an ornamental run; they are beamed
together automatically.  A grace chord uses several `keys` in one grace note,
exactly like a normal chord.

## Lyrics (vocal underlay)

Attach a `lyric` to any sounding note to underlay a sung syllable beneath it.
A bare string is the common case; the object form adds verses, word hyphens
and melisma extenders:

```
"notes": [
  {"keys": ["c/5"], "duration": "q", "lyric": {"text": "Twin", "syllabic": "begin"}},
  {"keys": ["c/5"], "duration": "q", "lyric": {"text": "kle", "syllabic": "end"}},
  {"keys": ["g/5"], "duration": "q", "lyric": {"text": "lit", "syllabic": "begin"}},
  {"keys": ["g/5"], "duration": "q", "lyric": {"text": "tle", "syllabic": "end"}}
]
```

One syllable per note.  The renderer aligns every syllable on a single
baseline below the staff (below the dynamics band when a `dynamic` is present),
so the underlay reads as one line across the system.

Split a word across notes with `syllabic`: `begin` on the first syllable,
`middle` on any interior ones, `end` on the last.  `begin`/`middle` draw the
connecting hyphen ("Twin-kle"); `single` (the default) and `end` do not — use
`single` for a whole word sung on one note, and never leave a trailing hyphen
on the last syllable of a word.

For a word held across several notes (a melisma), set `"extend": true` on the
syllable to draw the held extender line to the next note:

```
{"keys": ["a/5"], "duration": "8", "lyric": {"text": "joy", "extend": true}}
```

Multiple verses stack: put `"verse": 2` (3, ...) on the second verse's
syllables and they render on their own line below the first.  Hyphens only
join syllables within the SAME verse.

Do not put a `lyric` on a rest — attach syllables to the sounding notes.

## Fingering and string numbers (per note)

```
{"keys": ["c/5"], "duration": "q", "fingering": "1"}
{"keys": ["c/5"], "duration": "q", "fingering": {"number": "3", "position": "above"}}
{"keys": ["c/5"], "duration": "q", "stringNumber": "3"}
```

A bare `fingering` value sits below the staff (the piano convention);
`position` accepts `above below left right`.  `stringNumber` is for bowed and
plucked instruments and always draws above.

## Labeled chords and triads (per note)

`chordSymbol` accepts a plain string for the simple case, or an object that
reaches the engraved symbols a string cannot express:

```
{"keys": ["c/4"], "duration": "q", "chordSymbol": "Cmaj7"}
{"keys": ["c/4"], "duration": "q",
 "chordSymbol": {"text": "C", "superscript": "maj7"}}
{"keys": ["b/3"], "duration": "q",
 "chordSymbol": {"text": "B", "glyph": "halfDiminished"}}
{"keys": ["g/3"], "duration": "q",
 "chordSymbol": {"text": "V", "superscript": "7", "position": "below"}}
```

Glyphs: `diminished halfDiminished augmented majorSeventh minor + - # b over
leftParen rightParen leftBracket rightBracket`.  These render as real music
symbols (the dim circle, the slashed circle) rather than as letters.

Use `"position": "below"` for roman-numeral harmonic analysis under the
staff; chord charts stay above (the default).

## Placement brackets and trill lines (spec or staff level)

```
"brackets":   [{"from": 0, "to": 3, "text": "8", "superscript": "va"}],
"trillLines": [{"from": 0, "to": 3}]
```

`brackets` draws an 8va/8vb/15ma octave-displacement bracket, or any spanning
direction (`"text": "rit."`).  `position` may be `above` (default) or
`below`; the line is dashed unless `"dashed": false`.

An above-staff bracket is raised automatically when a `tempo` is also present,
since both occupy the band above the top staff.  Add `"line": N` (stave-line
units from the staff) only if you need to clear something else as well.

`trillLines` draws the wavy line that extends a trill across a run of notes.
Pair it with a `trill` ornament on the first note for a full `tr~~~~~`:

```
"notes": [{"keys": ["c/5"], "duration": "q", "ornaments": ["trill"]}, ...],
"trillLines": [{"from": 0, "to": 3}]
```

`wiggle` selects the glyph: `trill` (default), `vibrato`, `vibrato-wide`,
`sawtooth`.  A per-note `trill` ornament marks ONE note; a trill line spans a
range — use both together, not one instead of the other.

## Title block (title, subtitle, composer, lyricist)

Give a complete score a published-style title block with four spec-level
fields.  `title` is centred above the system in large type; `subtitle` is a
smaller centred line beneath it; `composer` is right-aligned and `lyricist`
left-aligned on a credits line below the title.  Any subset may be given —
supply only `title`, or a title with just a composer.

```
{
  "type": "music", "clef": "treble", "keySignature": "D", "timeSignature": "4/4",
  "title": "Ode to Joy",
  "subtitle": "Theme from Symphony No. 9",
  "composer": "Ludwig van Beethoven",
  "lyricist": "Friedrich Schiller",
  "notes": [ ... ]
}
```

These belong on the top-level spec, not on a staff or a note.  On a grand
staff the block spans the whole system.  The renderer reserves the headroom
and pushes the first system down, so the title never collides with the staff,
tempo mark or brackets above it.

## Structural markings (spec level, not per note)

```
{
  "type": "music", "timeSignature": "4/4",
  "tempo": {"name": "Allegro", "duration": "q", "bpm": 132},
  "mark": "to-coda",              // navigation mark, see list below
  "beginBar": "repeat-begin",     // opening barline
  "endBar": "repeat-end",         // closing barline
  "volta": {"type": "begin-end", "label": "1.", "measures": [2, 2]},
  "measureNumber": 12,
  "section": "B",                  // rehearsal-mark style label
  "notes": [ ... ]
}
```

`tempo` renders "Allegro (♩ = 132)".  Give `name` alone for a word-only
marking, or `bpm` alone (`{"bpm": 120}`) for a plain "♩ = 120" -- the beat
unit defaults to a quarter, so `duration` is only needed for a non-quarter
metronome mark (e.g. `{"duration": "8", "bpm": 160}`); `dots` puts
augmentation dots on the beat unit.

A `volta` is a repeat-ending bracket ("1.", "2.").  It is scoped to the
measures of ONE ending, not the whole line, so anchor it with a 1-based
inclusive measure range: `"volta": {"type": "begin-end", "label": "1.",
"measures": [2, 2]}` draws the "1." bracket over measure 2 only.  `type`
chooses the end-hooks: `begin` (left hook, ending continues), `end` (right
hook), `begin-end` (both — a fully-enclosed ending), `mid` (no hooks).  If
`measures` is omitted the bracket falls on the measure carrying a
`repeat-end` barline, or the last measure.  For a 1st AND 2nd ending, anchor
each with its own range over its own bars.

## Measures and barlines

Barline names, used everywhere a barline is specified: `single double end
final repeat-begin repeat-end repeat-both none`.

`beginBar` / `endBar` at the SPEC level are the OUTER barlines of the whole
system — the far left and far right edges.  A `final` ending barline belongs
here.  A repeat sign usually does NOT: with one measure of music it encloses
everything and there is nothing to repeat.

For music with more than one bar, replace `notes` with a `measures` list.  A
real barline is drawn between each pair, which is what makes repeats and
double bars meaningful:

```
{
  "type": "music", "timeSignature": "4/4",
  "measures": [
    {"notes": [ ...4 beats... ], "endBar": "repeat-end"},
    {"notes": [ ...4 beats... ]},
    {"notes": [ ...4 beats... ]}
  ],
  "endBar": "final"
}
```

The barline between two measures comes from the earlier measure's `endBar`,
or the later one's `beginBar` if that is absent, defaulting to a plain
`single` bar.  Use whichever reads more naturally: `endBar: "repeat-end"`
closes a repeated section, `beginBar: "repeat-begin"` opens one.

A repeated section spanning measures 1-2 of four is therefore:

```
"measures": [
  {"notes": [...], "beginBar": "repeat-begin"},
  {"notes": [...], "endBar": "repeat-end"},
  {"notes": [...]},
  {"notes": [...]}
]
```

Keep `timeSignature` as the meter of ONE bar (`"4/4"`, not `"12/4"` for three
bars) — the renderer does not require the content to sum to it.  Each
measure's `notes` should hold roughly one bar's worth; an over- or underfull
bar still renders rather than failing.

`measures` works inside a `staves` entry too, for a multi-measure grand staff.
Span indices (`slurs`, `ties`, `brackets`, `hairpins`, `trillLines`) count
notes across the whole staff and ignore the measure divisions, so a slur or
tie may cross a barline.

Prefer a flat `notes` list for a single bar; reach for `measures` as soon as
there are two.

### Long scores wrap onto several systems

A score with many measures no longer stretches into one ever-wider line.  Once
a system exceeds a width budget the renderer starts a new one below it,
re-printing clef, key and time signature exactly as a printed score does at a
line break.  Write the whole movement as ONE spec and let it wrap:

```
{
  "type": "music", "timeSignature": "4/4", "keySignature": "G",
  "measures": [ ...as many bars as the music needs... ]
}
```

Controls, all optional:

- `maxSystemWidth` — px budget for one system (default 1200).  Larger fits
  more bars per line at smaller scale.  The budget is spent by note DURATION,
  not note count: a bar of beamed 16ths or 32nds packs tight and several such
  dense bars share one line, just as they do in engraved scores — you do not
  need a wider budget or manual breaks to keep fast passagework from wrapping
  one-bar-per-line.
- `systemSpacing` — px gap between stacked systems (default 36).
- `"systemBreak": true` on a measure — force a new system there, for a break
  at a musical seam the width budget would not have chosen.
- `width` on the spec — pins the canvas and turns wrapping OFF entirely, on
  the basis that an explicit width is you choosing the layout.  A pinned
  width that is too narrow crowds the notes rather than breaking the line.

One real constraint: a span cannot cross a system break.  `slurs`, `ties`,
`glissandos`, `hairpins`, `brackets`, `trillLines`, `tuplets` and explicit
`beams` are drawn between two notes on the SAME line — the underlying
renderer has no way to split one into two partial arcs, and given endpoints on
different systems it would draw a single arc sprawling down the page.  Such a
span is skipped and reported rather than drawn wrongly, so keep phrase marks
inside a line or move the break (`systemBreak`) to suit the phrasing.

On a multi-staff score, give each staff a `shortName` alongside `name`:
the full name is printed beside the first system and the short form beside
each continuation system, as published scores do.

```
"staves": [
  {"clef": "treble", "name": "Flute", "shortName": "Fl.", "measures": [...]},
  {"clef": "bass",   "name": "Cello", "shortName": "Vc.", "measures": [...]}
]
```

Navigation marks (`mark`): `coda segno fine to-coda da-capo
da-capo-al-coda da-capo-al-fine dal-segno dal-segno-al-coda
dal-segno-al-fine`, plus `coda-right` / `segno-right` to place the symbol at
the right of the measure instead of the left.  Only ONE mark per spec.  A
`tempo` and a `mark` may be given together: the tempo is lifted onto its own
row above the navigation mark so the two never overprint.

Fermata is an ARTICULATION, not a structural mark: put `fermata-above` in a
note's `articulations`.

## Grand staff (piano, two or more staves)

Replace `notes` with a `staves` list.  Each entry is its own staff with its
own clef, notes and span lists; a brace joins them automatically.  Meter,
tempo, marks and barlines stay at the SPEC level because they belong to the
system as a whole.

```
{
  "type": "music", "timeSignature": "4/4",
  "staves": [
    {"clef": "treble", "notes": [ ... ], "slurs": [{"from": 0, "to": 3}]},
    {"clef": "bass",   "notes": [ ... ]}
  ]
}
```

Span indices are per staff: a slur in the bass staff indexes the bass
staff's own `notes`.  Omitting `clef` defaults the first staff to treble and
the rest to bass, which is the common piano case.

Give a staff a `name` to print its instrument / part label in the left gutter
— essential for any ensemble score, where several staves are otherwise
indistinguishable.  The label is right-aligned before the clef and vertically
centred on the staff; the system insets automatically to make room, so a long
name does not run off the edge.  For a single-staff spec put `name` at the top
level instead (it falls through to the lone staff like `clef` does).

```
{
  "type": "music", "timeSignature": "4/4",
  "staves": [
    {"name": "Violin", "clef": "treble", "notes": [ ... ]},
    {"name": "Viola",  "clef": "alto",   "notes": [ ... ]},
    {"name": "Cello",  "clef": "bass",   "notes": [ ... ]}
  ]
}
```

Harp pedal diagrams: attach a `harpPedal` string to any note using LilyPond's
compact encoding — `^` flat, `-` natural, `v` sharp, one character per pedal
in order D C B | E F G A (the `|` divides left-foot from right-foot pedals).
Renders as a small glyph row above the stave at that note's position.

Guidance:
- Prefer inline `music:` for anything you'd describe in one sentence of music.
- Use the fenced block once you need more than ~4 notes, a chord symbol on
  every note, tablature, or a harp pedal diagram — trying to cram those into
  the inline form produces an unreadable one-liner.
- Don't fabricate `harpPedal` strings unless the user's context specifies
  actual pedal positions; it's a niche feature, not a default decoration.''',
        'color': '#9333ea',
    },
    {
        'id': 'circuit_diagrams',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render circuit / RF / signal-chain schematics with circuitikz (server-side LaTeX)',
        'name': 'Circuit Diagrams',
        'description': 'Generate electronic, RF and signal-chain schematics using circuitikz',
        'keywords': ['circuit', 'schematic', 'circuitikz', 'rf', 'analog', 'transceiver',
                     'mixer', 'amplifier', 'filter', 'oscillator', 'antenna', 'signal-chain',
                     'heterodyne', 'superhet', 'adc', 'dac', 'transistor', 'opamp'],
        'prompt': '''Render circuits with a ```circuitikz``` fenced block.  Compiled by a local
TeX install; the body is auto-wrapped in `circuitikz` (don't add
\\documentclass, \\usepackage or \\begin{document} -- they are rejected).

THE ONE RULE THAT CAUSES MOST FAILURES
--------------------------------------
circuitikz has two different kinds of component and they are NOT
interchangeable.  Using the wrong form fails SILENTLY -- the label renders and
the symbol does not, so you get a valid-looking diagram with invisible parts.

  BIPOLES  -> two terminals, drawn ALONG a path:  \\draw (a) to[amp] (b);
  SHAPES   -> multi-terminal, placed as a NODE:   \\node[mixer] (m) at (x,y){};

Bipoles (use `to[...]`):
  passive    R, C, L, D, switch, battery1, V, I, piezoelectric
  RF/signal  amp, iamp, vamp, bandpass, bandstop, lowpass, highpass, allpass,
             adc, dac, dsp, fft, detector, phaseshifter, vco,
             piattenuator, tattenuator, biast, fiber
  Label a bipole with `l=` (label above/left) or `l_=` (below/right):
      \\draw (0,0) to[amp,l=$LNA$] (2,0) to[bandpass,l_={IF BPF}] (4,0);

Shapes (use `\\node[...]`):
  mixer, adder, oscillator, circulator, wilkinson, splitter, coupler, gyrator,
  antenna, txantenna, rxantenna, bareantenna, dinantenna,
  transistors (nmos, pmos, npn, pnp, njfet, hemt...), logic gates, ground

ANCHORS (getting these wrong puts wires on the wrong side)
----------------------------------------------------------
  mixer / circulator / oscillator -- numbered ports, counter-clockwise from west:
      .1 = west (left)   .2 = south (bottom)   .3 = east (right)   .4 = north (top)
    mixer:      .1 RF in, .3 IF out, .2/.4 LO injection
    circulator: circulates .1 -> .3 -> .2 (clockwise).  For a T/R duplexer:
                antenna on .1, receiver on .3, transmitter on .2
  antenna     -- feed point is `.south`, NOT `.center`:
                    \\node[antenna,anchor=south] (a) at (0,0){};
                    \\draw (a.south) -- (next);
  ground      -- a node placed at the end of a wire: `-- (0,-2) node[ground]{};`

DIRECTIONAL SYMBOLS MIRROR -- draw them left-to-right
-----------------------------------------------------
A bipole whose glyph contains lettering or an arrow is drawn in the direction
of its path, so drawing right-to-left MIRRORS it.  Verified: `to[dac]` drawn
right-to-left renders the letters as "A/D", i.e. it silently becomes an ADC
symbol -- a factually wrong diagram that compiles cleanly.

For a right-to-left signal chain (a transmit path under a receive path), draw
that one segment left-to-right and let the arrows convey flow:
    \\draw (16.7,-2.5) to[dac,l={DAC}] (18.7,-2.5);   % correct D/A glyph
Always label ADC/DAC explicitly so orientation is never the only cue.

ROUTING
-------
Use `|-` / `-|` for orthogonal (Manhattan) routing.  A plain `--` between
points that differ in both x and y draws a DIAGONAL, which looks wrong on a
schematic:
    \\draw[-{Latex[length=2mm]}] (lo.4) |- (7.3,1.5) -| (m.2);   % right
    \\draw (lo.4) -- (m.2);                                      % diagonal

Arrowheads: `\\draw[->]` or `\\draw[-{Latex[length=2mm]}]` for a filled head.

CROSSINGS: EVERY INTERSECTION MUST SAY WHETHER IT CONNECTS
----------------------------------------------------------
Two wires meeting at a point is ambiguous unless marked, and on a schematic
that ambiguity changes what the circuit DOES.  Mark every intersection:

  CONNECTED     -> `\\node[circ] at (x,y){};`   a filled junction dot
  NOT CONNECTED -> a hop (bridge/bypass) in one of the two wires

Draw the hop as an ARC inside the travelling wire's own `\\draw`.  The radius
0.3 matches the default component scale:

    % horizontal traveller hopping a vertical wire at x=xc
    \\draw (x0,y) -- (xc-0.3,y) arc (180:0:0.3) -- (x1,y);
    % vertical traveller hopping a horizontal wire at y=yc
    \\draw (x,y0) -- (x,yc-0.3) arc (270:90:0.3) -- (x,y1);

Arcs chain, so one traveller can hop a whole bus in a single `\\draw`:
    \\draw (0,0) -- (0.7,0) arc (180:0:0.3) -- (1.7,0) arc (180:0:0.3) -- (3,0);

TRAP: `\\node[jump crossing]` is a DECORATION, not a cut.  It paints a hop
glyph on top of whatever is already there -- if you drew the crossed wire with
an ordinary `\\draw`, that wire is still painted straight through the gap and
the crossing stays just as ambiguous as before.  Verified: a `\\draw (0,-1) --
(0,1)` with a `jump crossing` node on top renders the vertical line
continuously THROUGH the arc.  To use the shape you must break the wire
yourself:
    \\draw (0,-1) -- (0,-0.3);          % stop short
    \\node[jump crossing] at (0,0){};
    \\draw (0,0.3) -- (0,1);            % resume past it
Three statements and two magic numbers versus one `\\draw` for the arc form,
which is why the arc is preferred.  (`plain crossing` draws no hop at all --
it is the deliberately-unmarked case, not a bridge.)

T-junctions (three wires, an L or T shape) cannot mean anything but a
connection, so a dot there is technically redundant -- but add it anyway.  Once
EVERY intersection carries either a dot or a hop, a bare crossing is visibly a
mistake instead of something the reader has to reason about.  Two shorthands
place a dot without a separate node:
    \\draw (a) to[C,*-] (b);      % `*` = dot at that end of a bipole
    \\draw (a) to[short,-*] (b);

SIZING AND LABELS
-----------------
    \\ctikzset{RF/scale=0.8}                                    % shape size
    \\tikzset{every node/.append style={font=\\scriptsize}}      % label size
`siunitx` is loaded, so `\\SI{4.7}{\\kilo\\ohm}` works in labels.

Component aliases already provided: `quartz`, `crystal` and `xtal` all resolve
to circuitikz's real `piezoelectric` key.

EXAMPLE -- superheterodyne receiver front end
```circuitikz
\\ctikzset{RF/scale=0.8}
\\tikzset{every node/.append style={font=\\scriptsize}}
\\node[antenna,anchor=south] (ant) at (0,0) {};
\\draw (ant.south) -- (1,0);
\\draw (1,0) to[amp,l={LNA}] (3,0) to[bandpass,l={image BPF}] (5,0);
\\node[mixer] (m) at (6.3,0) {};
\\draw (5,0) -- (m.1);
\\node[oscillator] (lo) at (6.3,-2) {};
\\node[below=3pt] at (lo.south) {LO};
\\draw[-{Latex[length=2mm]}] (lo.4) -- (m.2);
\\draw (m.3) -- (7.6,0);
\\draw (7.6,0) to[bandpass,l={IF BPF}] (9.6,0) to[amp,l={IF amp}] (11.6,0)
      to[adc,l={ADC}] (13.6,0);
```

Guidance:
- Dark mode is handled automatically -- black TeX ink is recoloured on the
  client.  Don't hand-colour components to compensate; if you use colour, use
  it to carry meaning (distinguishing signal paths), and it will be lightened
  for contrast with its hue preserved.
- Plan the layout so wires cross as little as possible, then mark the crossings
  that remain.  A diagram needing many hops usually wants a different layout --
  moving a component or routing a bus around the outside is better than a row
  of bridges.
- Prefer `to[...]` bipoles for anything in a signal chain: the label placement
  is automatic and the wire routing follows the path.
- If a symbol renders as a bare label with no glyph, you almost certainly used
  a bipole as a `\\node` or a shape in a `to[...]`.  Check the lists above
  before assuming the name is wrong.
- For a schematic that is mostly boxes and arrows rather than real components,
  Mermaid or Graphviz is a better fit than circuitikz.''',
        'color': '#14b8a6',
    },
]
