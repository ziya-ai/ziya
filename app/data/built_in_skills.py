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
                     'multi-step', 'multi-stage', 'pipeline', 'state',
                     'variables', 'given', 'assume'],
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

Add a text annotation above or below a note with `^"text"` / `_"text"`
immediately after that note:

`music: C4/q ^"Cmaj7", G4/h`

Duration codes: `w` whole, `h` half, `q` quarter, `8` eighth, `16` sixteenth.
Append `.` for dotted (`q.`). Octave is the number after the pitch letter
(`c/4` = middle C). Use `##`/`bb` for double sharp/flat, `#`/`b` for single.

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
      "harpPedal": "^v-|vv-^"                // optional, see below
    },
    {"keys": ["d/5"], "duration": "q"}
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
]
