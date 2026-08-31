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
        'id': 'document_authoring',
        'name': 'Document Authoring',
        'description': 'Author and revise polished work-product documents with PDF export',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Author work-product documents (reports, memos, specs) as markdown IR in .ziya/documents/ and export high-fidelity PDFs',
        'keywords': ['document', 'report', 'pdf', 'export', 'memo', 'spec', 'work-product'],
        'prompt': '''You author DOCUMENTS (reports, memos, specs) as markdown IR files in
`.ziya/documents/` — a writable path. The IR file is the single editing
surface: create it with file_write, revise it with TARGETED edits, and never
regenerate the whole file for a small change. Render to PDF only when the
user wants the artifact.

## IR format — plain markdown + YAML front-matter (portable everywhere)

```
---
ziya-doc: 1
title: "Queue Depth Analysis"
author: "..."             # optional; becomes the PDF /Author
layout: report            # report = title block page header; plain = no chrome
page:
  margin: 18mm            # optional; one value or {top, bottom, left, right}
---

# First Section
Body is ordinary Ziya markdown: KaTeX math ($...$), mermaid / graphviz /
vega-lite / packet / chemfig / tikz fences, tables, code, diffs — all render
at full fidelity in the PDF.

<!-- ziya:pagebreak -->

# Next Section (starts on a new page)
```

## Rules
1. Keep SEMANTIC sources: math stays LaTeX, diagrams stay mermaid/graphviz
   source. Never paste pre-rendered images into the IR.
2. The PDF outline (bookmarks) is generated from the heading tree — use a
   clean h1/h2/h3 hierarchy.
3. Documents are work products, not transcripts: no conversational voice, no
   role labels, no tool chatter. When asked to turn an analysis into a
   document, EXTRACT and RESTRUCTURE the content into authored prose — do
   not paste the conversation.
4. `<!-- ziya:pagebreak -->` on its own line forces a page break; it is an
   invisible HTML comment in every other markdown viewer.
5. To render a PDF, POST to the local server (default port 6969):
   curl -s -X POST localhost:6969/api/export/document \\
     -H 'Content-Type: application/json' \\
     -d '{"name": "report.md"}' -o /tmp/report.pdf
   (or pass {"markdown": "..."} inline). Save output to /tmp or .ziya/ and
   tell the user where the file is. HTTP 501 means the server has no
   Playwright/Chromium — tell the user to run:
   pip install playwright && playwright install chromium''',
        'color': '#8b5cf6',
    },
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
        'keywords': [
            'test', 'tests', 'coverage', 'pytest', 'jest', 'regression',
            # The skill now covers integration seams as well as "write a
            # test", so a question about a wiring gap should match it.
            'wiring', 'integration', 'end-to-end', 'seam',
        ],
        'prompt': '''Create and validate test cases for every feature and function that
is added, enhanced, or repaired.

- Periodically evaluate the test architecture as a whole to ensure it stays
  organized and usable and that coverage is maintained. Watch for suites
  that pass while the feature is broken; no coverage metric shows that.
- Test the SEAM, not just the halves. The costliest defects are two correct
  pieces that never meet: defined but never called, added to a type but not
  to the provider that mounts it, a new map key the consumer's fallback
  still swallows. Assert the connection at every hop a change crosses.
- Prefer one end-to-end assertion over ten unit tests, and assert on what
  the outermost surface shows rather than an intermediate the test built.
- A test that passes against unpatched code certifies the bug. Confirm it
  FAILS without the fix, and pair every "does not happen" assertion with a
  positive one that the path ran at all.
- Anchor on identifiers, not formatting -- not list position, line
  adjacency, or a fixed-size file slice. Scope, then assert membership.
- Do NOT run tests against a diff patch you have no evidence has been
  applied. Providing a diff is not evidence that it was applied; only
  directly writing the file, or observing the change already present in the
  file content, is sufficient.
- Re-read files instead of trusting earlier output from the same session,
  and use the project's real test runner. Stale reads and missing
  transforms both look like regressions.
- Attribute every failure before reporting it: caused by this change,
  pre-existing, or pending unapplied work.
- Do NOT mark a test that demonstrates a legitimate bug as expected-to-fail.
  A failing test reflecting a real defect is a finding, not a nuisance.
- When a refactor legitimately breaks a test, update the assertion to FOLLOW
  the change; delete it only if the design it asserts was rejected. A test
  red for many sessions is no longer a signal.''',
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
- `"for_each"` — run once per item in `repeat_for_each_source`: a JSON
  array literal, or a reference to a prior task's emitted data part such
  as `{{sibling("plan").outputs.roster.slugs}}` (preferred — see
  Propagation below).  `{{item}}` is the current item.
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

Three constraints to author within:
- **Permissions do not cross the call.** The callee runs with its OWN
  approved scope; the caller's grants are not visible to it, and the
  caller cannot lend it permissions.  Do not put a scope on the Call block
  expecting the callee to receive it — grant what the callee needs on the
  callee itself.
- **Calls cannot be parameterized yet**, so a callee must already be
  self-sufficient.  The only things that reach it are run-scoped State
  givens and prior-sibling results.  A callee needing per-call inputs is a
  sign the work belongs inline in this card instead.
- **A name target must be unique in the deck.** Resolution tries the card
  id first, then falls back to a case-insensitive scan of every card in
  the project.  Two saved cards sharing a name make the target ambiguous
  and directory order decides which runs, so an orchestrator can silently
  call a stale duplicate.  Prefer distinct names, and delete superseded
  copies rather than leaving them saved alongside.

### Failure policy — on_failure

Any container (Group / Repeat / Until / Schedule) may set `on_failure`
for the implicit sequence formed by its body:

- `"continue"` (the DEFAULT when unset) — every later sibling runs even
  after an earlier one fails, and the failed result flows onward as
  `{{previous_sibling}}`.
- `"stop"` — the sequence halts at the first failed child; remaining
  siblings are marked skipped and the failure propagates upward.

**Set `"stop"` on any body whose stages depend on each other.** The
default exists for backward compatibility, not because it is usually
right: a dependent pipeline left on `continue` does not stop when its
foundation fails, it keeps running on missing or wrong input and produces
a confident, worthless result at the end.

This matters more than it looks, because **a container's artifact is its
LAST child's** — so under `continue` a failed early stage followed by a
passing late stage reports the container as SUCCEEDED, and a Call block
wrapping it reports success to its own caller.  The failure is not merely
tolerated, it becomes invisible.  `"stop"` is what keeps a failure
visible to the level above.

## Propagation (templating)

A block's `instructions` may reference prior results via `{{ }}` templates,
rendered at dispatch time:
- Inside a Repeat body: `{{index}}`, `{{item}}` (for_each), `{{previous}}`
  (propagate=last), `{{all}}` (propagate=all) — field access like
  `{{previous.summary}}`, `{{previous.decisions}}`.
- Inside a sequence: `{{previous_sibling}}`, `{{sibling("block-id")}}`.
- **Named artifact parts**: `{{previous_sibling.outputs.NAME}}`,
  `{{sibling("block-id").outputs.NAME}}`, `{{previous.outputs.NAME}}` —
  resolve a part a prior task declared with
  `emit_artifact(name="NAME", ...)`.  Append keys to reach inside a data
  part's object: `{{sibling("plan").outputs.roster.slugs}}`.
  There is no indexed form: parts are addressed by name, not position,
  because emission order is not a stable contract.
- Anywhere in scope of a State block: `{{var.NAME}}` for a declared
  variable; State's `state_context` prose needs no template at all — it
  is injected automatically.
Missing fields render as empty strings — never crash.  Unknown
placeholders (typos) are left verbatim so the mistake is visible.

### Driving a fan-out from structured output (preferred)

A `for_each` source may name an artifact part instead of relying on the
upstream task's prose.  This is the reliable way to fan out, because it
reads the exact value the planner declared rather than scanning its
summary for the first `[`:

```
{"block_type": "repeat", "repeat_mode": "for_each",
 "repeat_for_each_source": "{{sibling(\\"plan\\").outputs.roster.slugs}}",
 "repeat_parallel": true, "body": [ ]}
```

The planner task emits the list inside a data part:
`emit_artifact(name="roster", part_type="data", data={"slugs": [...]})`.
A data part must be a JSON **object**, so the list always lives under a
key — hence the trailing `.slugs`.

A source that is exactly one `outputs.…` reference is parsed STRICTLY
(whole-string JSON array only).  Any other templated source keeps the
lenient behavior of extracting the first JSON array found in the text.
Either way, a templated source that resolves to no array FAILS the
Repeat block rather than silently falling back to count-based iteration
over `item=null` — so a wide fan-out cannot quietly run over nothing.

With the lenient form you must instruct the upstream task to emit
exactly one JSON array and no other bracketed text; with the precise
form you do not.

Prefer the precise form for EVERY fan-out, not merely wide ones.  The
difference is not stylistic: a summary can be LOST while artifact parts
survive.  A summary is assembled from the prose a task streams, so if its
closing turn never arrives — an empty completion after the last tool call
— what remains is the narration BETWEEN tool calls, which reads like a
summary and holds no array.  `emit_artifact` parts are collected as they
are emitted and are unaffected.  Measured cost: a roster task wrote its
file and emitted its parts, lost only its final turn, and a 26-way
fan-out reading `{{previous_sibling.summary}}` dispatched zero iterations.

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

### Escalation needs a human signature

`shell_commands`, and `write` on a path outside the default safe-write
set (`.ziya/`, `/tmp/`), are a privilege ESCALATION. They do not take
effect just because you asked for them: they require an out-of-band
`ziya-approve` signature the user must make, and you cannot make it.
Until it exists, those blocks run at the default floor — so a task told
to run `npm` or write to `src/` will fail partway through rather than
being refused up front.

Two consequences for how you author a card:

- **Ask for the minimum.** Every escalating block adds a signing step
  before the card is useful. A task that only needs to write scratch
  state should write under `.ziya/`, which needs no signature.
- **Say so in the card's `description`** when any block does escalate,
  naming what it needs and why ("needs `npm` to build the frontend").
  The UI flags the escalation and offers the signing command, but it
  cannot explain why the card needs it — only you can, and the user is
  deciding whether to sign based on that.

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
preview it, **Save to deck** (persists it without running — also the
prerequisite for signing any escalation, since signatures key on
persisted block ids), or click **Start** to launch a run bound to the
chat, after which the inline tile shows live status.  If any block
escalates, the proposal block says so before the user commits, and Start
asks for confirmation — so escalate only where the work genuinely
requires it.

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
- Set `on_failure: "stop"` on every container whose body is a dependent
  pipeline.  Unset means `continue`, which runs later stages on failed
  input and — because a container reports its LAST child's result — can
  report the whole container as succeeded.
- A task's LAST act should be prose, not a tool call.  The summary is
  built from streamed prose, so a task that stops immediately after a
  tool call has no summary of its own work; that is detected and recorded
  as a failure rather than passing a partial transcript forward.  End
  instructions with something to say ("…then state what you found"),
  not with a bare write.
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
        'id': 'railroad_diagrams',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render railroad (syntax) diagrams for grammars, regexes, and text formats',
        'name': 'Railroad Diagrams',
        'description': 'Generate railroad syntax diagrams from a JSON spec',
        'keywords': ['railroad', 'syntax', 'grammar', 'bnf', 'ebnf', 'regex',
                     'production', 'syntax-diagram', 'format'],
        'prompt': '''Render railroad (syntax) diagrams with a fenced ```railroad code block
containing a JSON spec. Use them wherever "what strings does this accept" is
the question: grammars, regex structure, URL/config/file formats, CLI syntax.

Top level — one production or a grammar of named productions:
  {"title": "...", "diagram": <node>}
  {"title": "...", "rules": [{"name": "expr", "diagram": <node>}, ...]}

Node vocabulary (each node is exactly ONE of):
- "literal"                          bare string = terminal (rounded box)
- {"terminal": "if"}                 literal token text
- {"nonterminal": "expression"}      reference to another production (square box)
- {"comment": "note"}                italic annotation on the line
- {"skip": true}                     empty path (rarely needed directly)
- {"sequence": [n1, n2, ...]}        items in order (a bare array also works)
- {"choice": [n1, n2, ...]}          alternatives; FIRST gets the straight line,
                                     so put the most common case first
- {"optional": n}                    bypass line above the item
- {"oneOrMore": n}                   loop back under the item (1+)
- {"oneOrMore": n, "separator": {"terminal": ","}}
                                     the return path shows the separator
- {"zeroOrMore": n}                  optional loop (accepts "separator" too)
- {"group": n, "label": "..."}       dashed box around a sub-expression

Example — a JSON-style number:
```railroad
{
  "title": "number",
  "diagram": {"sequence": [
    {"optional": {"terminal": "-"}},
    {"oneOrMore": {"nonterminal": "digit"}},
    {"optional": {"sequence": [
      {"terminal": "."},
      {"oneOrMore": {"nonterminal": "digit"}}
    ]}}
  ]}
}
```

Rules of thumb:
- Terminals are literal characters/tokens; nonterminals name other rules.
- Prefer several small named rules over one deeply nested diagram.
- JSON only — no functions or bare identifiers. Trailing commas and
  // comments are tolerated, but do not rely on that.''',
        'color': '#a855f7',
    },
    {
        'id': 'timing_diagrams',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render digital timing diagrams (WaveDrom): clocks, buses, handshakes',
        'name': 'Timing Diagrams',
        'description': 'Generate WaveDrom digital timing diagrams from WaveJSON',
        'keywords': ['wavedrom', 'timing', 'waveform', 'clock', 'bus', 'signal',
                     'setup', 'hold', 'handshake', 'strobe', 'spi', 'i2c', 'uart'],
        'prompt': '''Render digital timing diagrams with a fenced ```wavedrom code block
containing WaveJSON. Use them for clock/data relationships, bus transactions,
handshakes, and setup/hold or latency questions.

Top level: { signal: [ <lane> | <group> | {} ], edge: [...], config: {...} }

A lane: { name: 'clk', wave: 'p......' } — one wave character per cycle:
- p / P   positive clock (capital = arrow on the edge); n / N negative clock
- 0 / 1   low / high level
- x       don't-care / undefined (hatched)
- z       high impedance
- = 2-9   data cycles (distinct fills); label them with data: ['A', 'B']
- .       extend the previous cycle
- |       gap marker (elided time)

Extras:
- Groups: ['bus', {name:'addr',...}, {name:'data',...}]; a bare {} is a spacer
- Timing arrows: mark lanes with node: '.a....b.' then edge: ['a~>b tRC']
  (~ curved, - straight; <, > arrowheads; trailing text is the label)
- period and phase per lane; config: { hscale: 2 } widens every cycle
- Title: { head: { text: 'SPI write', tick: 0 } }

Example — req/ack handshake with a latency annotation:
```wavedrom
{ signal: [
  { name: 'clk',  wave: 'p......' },
  { name: 'req',  wave: '01..0..', node: '.a.....' },
  { name: 'ack',  wave: '0..1.0.', node: '...b...' },
  { name: 'data', wave: 'x..=.x.', data: ['D0'] }
], edge: ['a~>b t_ack'] }
```

Rules of thumb:
- Every lane's wave string should be the SAME length — cycles align by index.
- Unquoted keys and single quotes are fine (WaveJSON is JSON5).
- A register bit-field layout ({reg: [...]}) renders too, but prefer the
  ```packet renderer for protocol frame layouts.''',
        'color': '#f59e0b',
    },
    {
        'id': 'flame_graphs',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render interactive flame graphs from profiler output or a nested call tree',
        'name': 'Flame Graphs',
        'description': 'Visualize performance profiles as click-to-zoom flame graphs',
        'keywords': ['flamegraph', 'flame', 'profile', 'profiling', 'performance',
                     'py-spy', 'perf', 'cprofile', 'pprof', 'hotspot', 'slow',
                     'collapsed', 'stacks', 'latency'],
        # The fence marker is assembled from chr(96) rather than typed as a
        # literal triple backtick, matching the pgfplots skill below: a literal
        # one in this source terminates the enclosing markdown fence whenever
        # the file is quoted by tooling.
        'prompt': (
            'Render performance profiles with a ' + chr(96) * 3 + 'flamegraph\n'
            'fenced block. Frame width is time (or samples); clicking a frame\n'
            'zooms to it. Reach for this whenever the question is "why is this\n'
            'slow" or "where does the time go".\n'
            '\n'
            'TWO input forms -- prefer collapsed stacks when you have real\n'
            'profiler output.\n'
            '\n'
            '1. COLLAPSED STACKS, one line per unique call path:\n'
            '    main;handle_request;parse_json 120\n'
            '    main;handle_request;db_query 480\n'
            '    main;handle_request;db_query;tcp_wait 300\n'
            '    main;render 45\n'
            '   This is what py-spy record --format collapsed, perf script |\n'
            '   stackcollapse-perf.pl, and flamegraph.pl emit, so real\n'
            '   profiler output pastes in unchanged. Frame names may contain\n'
            '   spaces (parse (app.py:42)); only the trailing number is the\n'
            '   count. Lines starting with # are ignored.\n'
            '\n'
            '2. NESTED JSON, when you already hold a tree:\n'
            '    {"name": "main", "value": 645, "children": [\n'
            '      {"name": "handle_request", "value": 600, "children": [\n'
            '        {"name": "parse_json", "value": 120},\n'
            '        {"name": "db_query", "value": 480}\n'
            '      ]},\n'
            '      {"name": "render", "value": 45}\n'
            '    ]}\n'
            '   value is the INCLUSIVE total for that frame and everything\n'
            '   under it, so a parent is never smaller than the sum of its\n'
            '   children. The terse {n, v, c} spelling is also accepted.\n'
            '\n'
            'RULES OF THUMB\n'
            '- Prefer form 1. Emitting stack lines is more reliable than\n'
            '  summing a tree by hand, and a wrong parent total renders a\n'
            '  visibly broken chart.\n'
            '- Deepest frame last: outer;middle;inner count reads bottom-up\n'
            '  on screen.\n'
            '- Give real frame names from the profile. Invented names make the\n'
            '  chart a fiction, which is worse than a table of the numbers you\n'
            '  actually have.\n'
            '- Use a flame graph for a PROFILE (self/total time by call path).\n'
            '  For a request walkthrough use a sequence diagram; for latency\n'
            '  over time use a chart.'
        ),
        'color': '#ef4444',
    },
    {
        'id': 'pgfplots',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render typeset function/data plots with pgfplots: math axes, log scales, 3D surfaces',
        'name': 'Typeset Plots (pgfplots)',
        'description': 'Generate publication-quality function and data plots with pgfplots',
        'keywords': ['pgfplots', 'plot', 'chart', 'axis', 'graph', 'function', 'curve',
                     'log', 'semilog', 'loglog', 'surface', '3d', 'colorbar',
                     'contour', 'errorbar', 'error bars', 'boxplot', 'histogram',
                     'queueing', 'latency', 'throughput', 'fit', 'regression'],
        # The fence marker is assembled from chr(96) rather than typed as a
        # literal triple backtick: a literal one in this source terminates the
        # enclosing markdown fence whenever the file is quoted by tooling.
        'prompt': (
            'Render typeset plots with a ' + chr(96) * 3 + 'pgfplots fenced\n'
            'block. Compiled by a local TeX install; the body is auto-wrapped\n'
            'in tikzpicture, so do NOT add \\documentclass, \\usepackage or\n'
            '\\begin{document} -- those are rejected.\n'
            '\n'
            'Prefer this over vega-lite/plotly when the LABELS are\n'
            'mathematical, when a fitted analytic curve sits alongside\n'
            'measured points, or when the plot should read continuously with\n'
            'KaTeX derivations elsewhere in the answer. Prefer vega-lite for\n'
            'interactive or faceted data exploration.\n'
            '\n'
            'Usually you write just the axis:\n'
            '    \\begin{axis}[xlabel={$t$}, ylabel={$v(t)$}, domain=0:10,\n'
            '                 samples=200]\n'
            '    \\addplot[thick, blue] {exp(-x)*sin(deg(x))};\n'
            '    \\addlegendentry{$e^{-t}\\sin t$}\n'
            '    \\end{axis}\n'
            '\n'
            'THE TRAP THAT PRODUCES WRONG OUTPUT SILENTLY\n'
            'pgfmath trig takes DEGREES, not radians. {sin(x)} over\n'
            'domain=0:6.28 compiles cleanly and draws an almost-straight\n'
            'line -- a plausible-looking, factually wrong plot, which is\n'
            'worse than an error. Convert with deg():\n'
            '    \\addplot[domain=0:6.28, samples=200] {sin(deg(x))};\n'
            '\n'
            'ALREADY IN THE PREAMBLE (do not re-declare)\n'
            '  compat    \\pgfplotsset{compat=newest} is already set\n'
            '  amsmath   \\dfrac \\text \\boldsymbol \\substack \\big* --\n'
            '            safe anywhere, including a legend entry (the\n'
            '            server reserves the row height; see LEGEND\n'
            '            ENTRIES below).\n'
            '  amssymb   \\mathbb \\mathfrak \\leqslant \\nleq\n'
            '  xcolor    svgnames + dvipsnames, so Crimson/Navy/Teal\n'
            '            resolve; blue!15 fractions work as usual\n'
            '  libraries fillbetween, statistics, polar, dateplot,\n'
            '            groupplots\n'
            '  siunitx   \\si \\SI \\qty -- loaded ONLY IF INSTALLED. If a\n'
            '            unit vanishes from a label, siunitx is absent on\n'
            '            this machine (tlmgr install siunitx); everything\n'
            '            above it is guaranteed.\n'
            '\n'
            'Any OTHER pgfplots library needs its own\n'
            '\\usepgfplotslibrary{...} as the first line of the body.\n'
            'mathtools is NOT loaded: \\coloneqq and\n'
            '\\DeclarePairedDelimiter are unavailable.\n'
            '\n'
            'LEGEND ENTRIES -- two traps that apply ONLY to legend text\n'
            '1. \\to is pgfplots OWN argument delimiter, so a \\to inside\n'
            '   \\addlegendentry{...} or legend entries={...} aborts the\n'
            '   compile with a misleading "\\def cs{...}" error naming\n'
            '   neither the legend nor \\to. The server rewrites it to\n'
            '   \\rightarrow and reports the fix -- the glyph is identical\n'
            '   (\\to is \\let to \\rightarrow) -- so either spelling works,\n'
            '   but \\rightarrow avoids the round trip.\n'
            '2. \\dfrac in a legend overflows its row: the default row\n'
            '   separation does not grow to fit a display-size fraction,\n'
            '   so it collides with the neighbouring entry. The server\n'
            '   adds legend style={row sep=6pt} and reports it, keeping\n'
            '   the fraction at the size you wrote -- so \\dfrac is fine\n'
            '   in a legend. If you set row sep YOURSELF it is respected\n'
            '   untouched: 2pt still overlaps, 4pt clears a simple\n'
            '   \\dfrac, 6pt clears a nested one.\n'
            'Both are fine in xlabel/ylabel/ticks/nodes.\n'
            '\n'
            'AXES\n'
            '  axis, semilogxaxis, semilogyaxis, loglogaxis, polaraxis\n'
            '  keys       width=, height=, xmin/xmax/ymin/ymax,\n'
            '             grid=both, legend pos=north west,\n'
            '             legend cell align={left}\n'
            '  categories symbolic x coords={alpha,beta} with xtick=data\n'
            '  dates      date coordinates in=x, xticklabel style={\n'
            '             rotate=45, anchor=east}\n'
            '\n'
            'PLOTS\n'
            '  function   \\addplot[domain=0:10, samples=200] {expr};\n'
            '  points     \\addplot coordinates {(0,1) (1,4) (2,9)};\n'
            '  table      \\pgfplotstableread{...}\\mytable then\n'
            '             \\addplot table[x=t, y=v] {\\mytable};\n'
            '  bars       ybar, or ybar stacked on the axis; bar width=\n'
            '  boxplot    \\addplot+[boxplot prepared={median=3,\n'
            '             upper quartile=4, ...}] coordinates {};\n'
            '  3D         \\addplot3[surf, shader=interp] {expr}; with\n'
            '             view={35}{30} and colorbar on the axis\n'
            '  error bars \\addplot[error bars/.cd, y dir=both, y explicit]\n'
            '               coordinates {(1,2) +- (0,0.3)};\n'
            '  band       name two bounding plots name path=hi and\n'
            '             name path=lo, then\n'
            '             \\addplot[blue!15] fill between[of=hi and lo];\n'
            '\n'
            'ANNOTATION -- anchor to DATA space, not the canvas\n'
            '  \\node[anchor=west] at (axis cs:0.8,16) {$\\rho=0.8$};\n'
            '  \\draw[gray, dashed] (axis cs:0.8,0) -- (axis cs:0.8,20);\n'
            'A bare at (0.8,16) is canvas coordinates: it renders happily,\n'
            'in the wrong place, and rescaling the axis moves it further\n'
            'off.\n'
            '\n'
            'RULES OF THUMB\n'
            '- Every \\addplot ends with a semicolon; omitting it gives a\n'
            '  confusing error pointing at a later line.\n'
            '- \\addlegendentry goes immediately AFTER its own \\addplot, or\n'
            '  set legend entries={a,b} once on the axis.\n'
            '- Helper/bounding plots you do not want in the legend need\n'
            '  forget plot.\n'
            '- For small multiples use the groupplots library, not several\n'
            '  fences.\n'
            '- Micro/degree/plus-minus signs typed literally in labels are\n'
            '  transliterated automatically and reported as an autofix, so\n'
            '  you may write them directly.'
        ),
        'color': '#0ea5e9',
    },
    {
        'id': 'structure_trees',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render labelled trees (forest) and proof trees (bussproofs) in field-standard notation',
        'name': 'Trees & Proof Trees',
        'description': 'Generate labelled trees and inference-rule proof trees in the notation their fields actually use',
        'keywords': ['forest', 'tree', 'syntax-tree', 'syntax tree', 'parse tree',
                     'constituency', 'taxonomy', 'phylogeny', 'decision tree',
                     'game tree', 'bussproofs', 'prooftree', 'proof', 'proof-tree',
                     'derivation', 'natural-deduction', 'sequent', 'typing rule',
                     'inference'],
        # Fence markers are assembled from chr(96) rather than typed as literal
        # backticks: a literal triple backtick in this source terminates the
        # enclosing markdown fence whenever the file is quoted by tooling.
        #
        # Every construct below was verified by COMPILING it through the real
        # renderer.  The first draft of this prompt documented four things that
        # do not work (`roof` without its library, a phantom node as an arrow
        # target, \def to set \fCenter, and no warning about \DisplayProof);
        # tests/test_latex_tree_profiles.py now compiles each claim.
        'prompt': (
            'Render labelled trees with a ' + chr(96) * 3 + 'forest fence and\n'
            'proof trees with a ' + chr(96) * 3 + 'bussproofs fence. Both\n'
            'compile through a local TeX install, and the body is auto-wrapped\n'
            '(forest -> \\begin{forest}, bussproofs -> \\begin{prooftree}), so\n'
            'do NOT add \\documentclass, \\usepackage or \\begin{document} --\n'
            'those are rejected.\n'
            '\n'
            'YOU CANNOT DEFINE MACROS. \\def, \\newcommand, \\renewcommand,\n'
            '\\let, \\edef and \\gdef are all rejected before compiling (they\n'
            'can build unbounded expansions). Write the notation out longhand\n'
            'instead of abbreviating it.\n'
            '\n'
            'WHICH ONE\n'
            '  forest      any tree whose NOTATION matters: constituency and\n'
            '              syntax trees, taxonomies, decision and game trees,\n'
            '              phylogenies, parse trees.\n'
            '  bussproofs  derivations read as premises-over-conclusion:\n'
            '              natural deduction, sequent calculus, typing rules.\n'
            'Prefer graphviz or mermaid when the shape is a generic hierarchy\n'
            'and the field has no notational conventions. forest earns its\n'
            'place through roofs, forked edges and movement arrows -- not\n'
            'through drawing a tree at all.\n'
            '\n'
            '=== forest ===\n'
            'Bracket syntax; a node label runs to the first comma, [ or ]:\n'
            '    [S\n'
            '      [NP [D [the]] [N [dog]]]\n'
            '      [VP [V [barks]]]\n'
            '    ]\n'
            'Node options follow a comma:  [S, draw, red [NP]]\n'
            'Whole-tree styling goes BEFORE the bracket:\n'
            '    for tree={s sep=12pt, l sep=18pt, align=center}\n'
            '    [S [NP] [VP]]\n'
            '\n'
            'THE PARSER TRAP\n'
            '[ ] and , are STRUCTURE, not text. A label containing any of them\n'
            'must be braced, or the tree reshapes silently or aborts:\n'
            '    [{NP, coordinated}]   not   [NP, coordinated]\n'
            '    [{a[i]}]              not   [a[i]]\n'
            '\n'
            'OPTIONS WORTH KNOWING\n'
            '  roof          triangle over an elided constituent:\n'
            '                [NP, roof [the big dog]]\n'
            '  forked edges  before the bracket, for the forked style used in\n'
            '                syntax and phylogeny trees\n'
            '  name=x        handle for drawing arrows later\n'
            '  tier=t        force nodes onto the same vertical level\n'
            '  align=center  multi-line labels, broken with \\\\\n'
            '  s sep=/l sep= sibling / level separation\n'
            '\n'
            'MOVEMENT AND CO-INDEX ARROWS -- plain TikZ after the tree, still\n'
            'inside the fence. The tree\'s named nodes are in scope:\n'
            '    [CP\n'
            '      [DP, name=wh [what]]\n'
            '      [C [VP [V [saw]] [DP, name=gap [$t$]]]]\n'
            '    ]\n'
            '    \\draw[-Stealth, dashed] (gap) to[out=south west, in=south] (wh);\n'
            'The arrow target must be a REAL node. A ``phantom`` node occupies\n'
            'space without producing a shape, so aiming an arrow at one fails\n'
            'with "No shape named ... is known" -- give the trace an explicit\n'
            'label such as [$t$] and name that.\n'
            '\n'
            '=== bussproofs ===\n'
            'A STACK MACHINE, which is the one thing to get right. \\AxiomC\n'
            'pushes a pending subproof; every inference command CONSUMES\n'
            'pending subproofs and pushes its conclusion:\n'
            '    \\AxiomC      consumes 0, pushes 1\n'
            '    \\UnaryInfC   consumes 1\n'
            '    \\BinaryInfC  consumes 2\n'
            '    \\TrinaryInfC consumes 3\n'
            'Emitting \\BinaryInfC with only one pending subproof is a hard\n'
            'abort with no image, and so is leaving more than one pending at\n'
            'the end. Write the premises left to right, then the inference\n'
            'that joins them:\n'
            '\n'
            '    \\AxiomC{$\\Gamma \\vdash A \\to B$}\n'
            '    \\AxiomC{$\\Gamma \\vdash A$}\n'
            '    \\RightLabel{\\scriptsize $\\to$E}\n'
            '    \\BinaryInfC{$\\Gamma \\vdash B$}\n'
            '\n'
            'DO NOT WRITE \\DisplayProof. The wrapper emits it for you; a\n'
            'second one finds the stack empty and aborts with "Proof tree\n'
            'badly specified".\n'
            '\n'
            'RULES\n'
            '- Node text is TEXT mode: wrap math in $...$.\n'
            '- \\RightLabel and \\LeftLabel go BEFORE the inference command\n'
            '  they annotate, never after.\n'
            '- Line-style modifiers apply to the NEXT line drawn: \\noLine,\n'
            '  \\doubleLine, \\dashedLine, \\solidLine.\n'
            '- \\insertBetweenHyps{\\hskip 1cm} widens the gap between\n'
            '  premises.\n'
            '- \\EnableBpAbbreviations turns on \\AXC \\UIC \\BIC \\RL \\LL,\n'
            '  worth it for a long derivation.\n'
            '\n'
            'SEQUENT STYLE -- the no-C commands align on \\fCenter, which is\n'
            'ALREADY SET to a turnstile for you (you could not define it\n'
            'yourself; see above). Use it as the separator and omit \\vdash:\n'
            '    \\Axiom$\\Gamma \\fCenter A$\n'
            '    \\UnaryInf$\\Gamma \\fCenter A \\vee B$\n'
            'For a different separator (a sequent arrow, say), use the C-form\n'
            'commands and write it inside the math yourself.\n'
            '\n'
            'ALREADY IN THE PREAMBLE (do not re-declare)\n'
            '  amsmath, amssymb  \\dfrac \\text \\mathbb \\vdash \\Gamma\n'
            '  xcolor            svgnames + dvipsnames, so Crimson, Navy and\n'
            '                    Teal resolve; red!60 fractions work as usual\n'
            '  forest libraries  linguistics (roof) and edges (forked edges)\n'
            '  tikz libraries    arrows.meta (-Stealth and friends),\n'
            '                    positioning, calc\n'
            '  siunitx           \\si \\SI \\qty, loaded ONLY IF INSTALLED\n'
            '\n'
            'If a fence comes back as an install notice rather than a diagram,\n'
            'the package is simply absent on this machine\n'
            '(tlmgr install forest bussproofs) -- nothing about the body is\n'
            'wrong, so do not rewrite it.'
        ),
        'color': '#7c3aed',
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

Duration codes: `w` whole, `h` half, `q` quarter, `8` eighth, `16` sixteenth,
`32` thirty-second, `64` sixty-fourth, `128` (for the fastest ornamental runs).
Append `.` for dotted, and stack dots for double- and triple-dotted values up to
four (`q.` dotted quarter, `q..` double-dotted, `q...` triple-dotted) -- ordinary
in Baroque and Romantic rhythm.  Write the pitch as letter + optional accidental
 + octave with NO slash: `C4` is middle C, `C#5` and `Bb4` carry accidentals.
The duration MUST be one of those codes -- any OTHER value (a code that is not a
duration like `4x`, an out-of-range code like `999`, or a huge integer) is
treated as an error: the fenced block falls back to a quarter note with a console
warning, and the inline form declines to render, rather than freezing the staff.

The PITCH is guarded the same way.  A malformed pitch -- a mistyped accidental
(`ef4` for `Eb4`) or an octave outside the playable range (`C99`) -- makes the
inline form decline to render and fall back to showing the raw text, and the
fenced block drops the bad note with a warning, rather than freezing the staff.
Spell accidentals as `#`, `##`, `b`, `bb` or `n` -- e.g. `Eb4`, `F#5`.

The inline form supports notes only -- there is no annotation syntax. Text
above or below a note requires the fenced block below, whose `annotations`
field does this properly. If you write `^"text"` or `_"text"` inline the form
DECLINES to render and falls back to showing the raw text, rather than
silently dropping the notes after the annotation -- so use the fenced block
for any annotated phrase.

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
      "duration": "q",                       // w h q 8 16 32 64 128, "." adds a dot (up to "q...")
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

Durations are the same codes as notes: `w h q 8 16 32 64 128`, and `"q."` (or a
double-dotted `"q.."`) for a dotted rest.  A whole-measure silence is one
`{"rest": true, "duration": "w"}`.

Rests occupy beat time exactly as notes do, so count them when filling a bar.
They also occupy an INDEX in the notes array: a slur, tie, hairpin or bracket
referencing note 3 counts rests among the first three entries.  Do not anchor
a slur or tie to a rest — attach spans to the sounding notes on either side.

### Multi-measure rest

In an instrumental PART, a run of empty bars is written as ONE symbol -- a
thick horizontal H-bar with the number of bars above it -- so the player counts
"8 bars rest" from a single mark instead of reading eight identical empty bars.
Use a `measures`-based staff and give the resting bar `"multiRest": N` (the bar
count) and NO notes:

```
{
  "type": "music",
  "clef": "treble",
  "timeSignature": "4/4",
  "measures": [
    {"notes": [{"keys": ["c/5"], "duration": "q"}, {"keys": ["d/5"], "duration": "q"},
               {"keys": ["e/5"], "duration": "q"}, {"keys": ["f/5"], "duration": "q"}]},
    {"multiRest": 8},
    {"notes": [{"keys": ["g/5"], "duration": "h"}, {"keys": ["e/5"], "duration": "h"}]}
  ]
}
```

`multiRest` must be an integer of 1 or more; any other value is skipped with a
console warning.  The bar is silent, so any `notes` given alongside `multiRest`
are ignored.  This is a per-measure field (it lives on a `measures[]` entry),
so it works on any staff of a grand staff as well.

### Key change (modulation)

A piece can change key mid-score.  On a `measures`-based staff give the bar
where the new key begins a `keySignature` (any name the top-level
`keySignature` accepts, e.g. `"G"`, `"Bb"`, `"F#"`); it stays in force until
the next change, exactly as an engraved modulation reads.  The renderer prints
the new signature at that bar -- with the naturals that cancel the old
sharps/flats -- and, from that bar on, filters accidentals against the NEW key
(so, as always, spell every note as its true sounding pitch and let the
renderer decide which accidentals print):

```
{
  "type": "music",
  "clef": "treble",
  "keySignature": "C",
  "timeSignature": "4/4",
  "measures": [
    {"notes": [{"keys": ["e/4"], "duration": "q"}, {"keys": ["g/4"], "duration": "q"},
               {"keys": ["c/5"], "duration": "h"}]},
    {"keySignature": "G",
     "notes": [{"keys": ["d/5"], "duration": "q"}, {"keys": ["b/4"], "duration": "q"},
               {"keys": ["g/4"], "duration": "h"}]}
  ]
}
```

Omit `keySignature` on the first measure (the opening key is set by the
top-level `keySignature`) and on any bar whose key is unchanged.  Like
`timeSignature`, it is a per-measure field, so it works on any staff of a grand
staff.  An unrecognised key name is ignored (the previous key stays in force)
rather than blanking the modulation.

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

Tremolo (a note's `tremolo` field): an integer `1`, `2` or `3` giving the
number of slashes drawn through the note's stem -- a single-note (measured)
tremolo, meaning the note is rapidly repeated (`3` is the common
"as-fast-as-possible" buzz).  It applies to one note or chord:

```json
{ "keys": ["g/4"], "duration": "q", "tremolo": 3 }
```

Use it for a repeated-note tremolo on a single pitch; a two-note (alternating)
tremolo between different pitches is not yet supported.  A value outside 1..3
is ignored.

## Breath marks and caesuras (a note's `breath` field)

Put `breath` on a note to draw a phrasing break ABOVE the staff, just AFTER
that note -- where a wind or vocal player breathes, or (a caesura) the
"railroad-tracks" grand pause:

```json
"notes": [
  {"keys": ["a/4"], "duration": "q", "breath": "comma"},
  {"keys": ["c/5"], "duration": "q", "breath": "caesura"}
]
```

Values: `comma` (the ordinary breath, a small curved comma -- also `true` as a
shorthand), `tick` (a terser slanted stroke), `caesura` (the two parallel
diagonal strokes marking a grand pause -- also `grand-pause`/`railroad`), and
`caesura-curved` (the curved-stroke caesura).  The mark belongs to the gap
after its note, so it is drawn off the note's right edge; an unknown value is
skipped with a warning.

## Accidentals are ABSOLUTE, not inherited from the key signature

A `keys` entry is the SOUNDING pitch, so spell the accidental you want and do
not rely on `keySignature` to supply it.  In Eb major, `"eb/5"` is E-flat and
prints bare (the signature already carries the flat), while `"e/5"` is
E-NATURAL and prints an explicit natural to cancel the signature:

    "keySignature": "Eb",
    "notes": [{"keys": ["eb/5"], "duration": "q"},   // E-flat, no printed sign
              {"keys": ["bb/5"], "duration": "q"},   // B-flat, no printed sign
              {"keys": ["e/5"],  "duration": "q"}]   // E-NATURAL, prints natural

This is the opposite of most text notation input, where a plain letter inherits
the signature.  It is the easiest way to get a whole score wrong: writing the
plain letters of a tune in a flat or sharp key yields the right SHAPE at the
wrong PITCHES, cluttered with a natural on nearly every note.

The check: a diatonic passage in a key with accidentals should render with
almost NO accidentals on the noteheads.  A natural on nearly every note means
the pitches are being cancelled rather than inherited.

An accidental holds for the rest of its BAR: once a note has printed a sharp,
flat or natural, later notes of the SAME pitch (same letter AND octave) in the
same measure print bare, and the sign is re-shown after the next barline --
exactly as published scores engrave it.  Keep spelling every entry as its true
sounding pitch (`"f#/5"` even on the third F-sharp of a bar); the renderer
drops the redundant repeated glyph for you, so you never write a note bare to
suppress a sign.  A DIFFERENT pitch or a changed accidental (an F-natural after
an F-sharp) still prints, since it is new information.

Set `"cautionaryAccidentals": true` on the spec to add courtesy (cautionary)
accidentals -- the parenthesised reminder published editions print when a pitch
altered in one bar returns in the NEXT bar sounding differently.  An `f#/4` in
bar 1 followed by a plain `f/4` in bar 2 gets a parenthesised natural on that
`f`, reassuring the reader the sharp no longer applies across the barline; the
mark is added only to a note that would otherwise print bare (a note carrying
its own accidental is already its own reminder) and only for a one-bar-back
change.  Leave it off for a clean, minimally-marked score; turn it on to match
the Dorico/Finale house style where such reminders are expected.  As always,
keep spelling every note as its true sounding pitch -- the renderer decides
where a courtesy mark is warranted.

A pitchless entry -- no `keys`, or an empty `keys` array -- is drawn as a REST
of its duration, with a console warning.  `{"rest": true}` is the documented
spelling and the one to use; the fallback exists because emitting a note with
no pitch previously hung the renderer for 30s and lost the entire score.

## Beaming

Set `"autoBeam": true` on the spec to beam eighths and shorter into the
meter's natural beat groups.  **Without it every eighth and sixteenth draws an
individual flag**, which is only correct for isolated notes — so any passage
of running eighths wants `autoBeam`.

On a multi-staff score put it at the SPEC level (beside `timeSignature`), not
on a `measures[]` entry, where it has no effect.  A `staves[]` entry may set
its own `autoBeam` to override the spec for that one part — including
`false`, to leave one part flagged while the others beam.

```
{"type": "music", "timeSignature": "4/4", "autoBeam": true, "notes": [ ... ]}
```

Beaming runs per measure, so a group never crosses a barline.  Rests break a
beam group.  Notes a quarter or longer are never beamed, so `autoBeam` is
harmless on music that has none.

By default `autoBeam` groups by the meter's natural beat: a quarter-note beat
in 2/4, 3/4 and 4/4, and a dotted-quarter beat in the compound meters (6/8
beams as two groups of three, 9/8 as three, 12/8 as four).  This follows a
mid-score meter change too: if a later measure sets its own `timeSignature`
(e.g. 4/4 changing to 6/8), the bars after the change beam by the NEW meter's
beat -- the 6/8 bars in dotted-quarter beats, not the opening 4/4's.
`beamGroups` OVERRIDES that default, as [numerator, denominator] pairs -- e.g.
`"beamGroups": [[1, 4]]` forces 6/8 into quarter-note groups instead of its
default dotted-quarter beats.  Each number must be a whole number >= 1; a pair
with a zero or negative value is ignored (a bad entry is skipped and the
remaining valid ones still apply, falling back to the meter's default grouping
if none are left).

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
mean different things, so pick by meaning, not appearance.  A tie between two
CHORDS ties every pitch the two chords share automatically -- one arc per
common note -- so `{"from": 0, "to": 1}` on two C-E-G chords draws three ties,
not one; you do not name the individual notes.

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
number of notes and `inSpaceOf` to 2.  Both must be whole numbers from 1 to 99
(they set how the notes' beat time is rescaled); a `num` or `inSpaceOf` of 0, a
negative/fractional value, or an out-of-range count above 99 (no real tuplet is
that dense) is rejected and the tuplet is skipped.  For other tuplets give both:

```
"tuplets": [{"from": 0, "to": 4, "num": 5, "inSpaceOf": 4}]   // quintuplet
```

`ratioed: true` prints the full "5:4" instead of just "5"; `bracketed` forces
the enclosing bracket on or off (default: on for unbeamed notes, off for
beamed); `position` is `above` (default) or `below`.  The number always honours
`position` regardless of stem or beam direction, so a beamed run of high notes
keeps its "3" above the staff, clear of any lyrics or dynamics below.  Beam a
tuplet with `autoBeam` or an explicit `beams` span exactly as you would any
other notes -- tuplets and beams are independent.

## Grace notes (appoggiatura, acciaccatura, ornamental runs)

Attach `graceNotes` to any note for small notes played BEFORE it.  They carry
no beat time, so adding them never shifts where the main notes fall.  Each
grace note gives `keys` and `duration` just like a normal note; set
`slash: true` for the acciaccatura (the "crushed" grace, drawn with a slash
through its stem), and leave it off for the appoggiatura.  Spell any
accidental right in the key, exactly like a main note -- `c#/5`, `bb/4`,
`cn/5` -- and the sharp/flat/natural sign is engraved on the grace notehead
(grace notes are not affected by the key signature, so write the sign you
want to see).

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
exactly like a normal chord.  A grace note whose pitch is unspellable (a
mistyped accidental) is dropped with a console warning rather than freezing the
render; a grace chord keeps its other, valid notes.

## Cue notes (small editorial / ossia notes)

Set `cue: true` on a note to engrave it small -- a CUE note, the roughly
two-thirds-size note published scores use for an editorial suggestion, a
colla-parte lead-in, or an ossia alternative.  Unlike a grace note a cue note
KEEPS its beat time and occupies real rhythmic space in the bar; only its
size changes.  The whole note shrinks together -- notehead, stem and flag --
so it stays internally consistent.  Use a number 0.3..1 instead of `true` to
set an explicit scale.

```music
{
  "type": "music", "clef": "treble", "timeSignature": "4/4", "autoBeam": true,
  "notes": [
    {"keys": ["c/5"], "duration": "q"},
    {"keys": ["e/5"], "duration": "8", "cue": true},
    {"keys": ["f/5"], "duration": "8", "cue": true},
    {"keys": ["g/5"], "duration": "q", "cue": true},
    {"keys": ["c/5"], "duration": "q"}
  ]
}
```

`cue` is ignored on a rest (a rest has no notehead to shrink).

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
syllable.  The extender line spans the WHOLE melisma — it runs from the
syllable to the last note sung on it, i.e. the note just before the next
syllable in the same verse (or the final note when this is the last syllable),
exactly one continuous line however many notes the word is held over:

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

A note's `tremolo` draws measured-tremolo slashes across its stem: `1` = repeat
in eighths, `2` = sixteenths, `3` = thirty-seconds (the count is clamped to
1-3).  `true` is shorthand for a single slash.  It works on single notes and on
chords (the slashes cross the shared stem).

```
{"keys": ["g/4"], "duration": "q", "tremolo": 3}
{"keys": ["c/5", "e/5", "g/5"], "duration": "h", "tremolo": 2}
```

A note's `arpeggio` draws a chord roll -- the vertical wavy line to the left of
a chord meaning "spread the notes instead of striking them together".  Use
`"arpeggio"` (or `true`) for the plain directionless roll, `"arpeggio-up"` /
`"arpeggio-down"` to add the directional arrow published scores use.  Guitar
strokes `"brush-up"` / `"brush-down"` / `"rasgueado-up"` / `"rasgueado-down"`
are also available.  It attaches to the whole chord, so only put it on a note
whose `keys` name two or more pitches; an unknown name is skipped.

```
{"keys": ["c/4", "e/4", "g/4"], "duration": "h", "arpeggio": "arpeggio"}
{"keys": ["d/4", "f/4", "a/4"], "duration": "h", "arpeggio": "arpeggio-up"}
```

## Sustain pedal (piano)

`pedals` is a spec- or staff-level list of sustain-pedal markings, each a
`{"from", "to"}` pair of note indices into the staff's own note list (the same
index convention as `slurs`/`hairpins`): the pedal is pressed at `from` and
released at `to`.  It is drawn on its own band below the staff, beneath the
dynamics.  `style` chooses the notation: `"bracket"` (default) is the modern
line-with-hooks bracket; `"text"` is the older `Ped. ... *`.  Like the other
spans a pedal must stay within one system; one that would cross a line break is
skipped with a console warning.

```
{
  "type": "music", "clef": "bass", "timeSignature": "4/4",
  "notes": [
    {"keys": ["c/3", "e/3", "g/3"], "duration": "q"},
    {"keys": ["d/3", "f/3", "a/3"], "duration": "q"},
    {"keys": ["e/3", "g/3", "c/4"], "duration": "q"},
    {"keys": ["g/3", "c/4", "e/4"], "duration": "q"}
  ],
  "pedals": [{"from": 0, "to": 3}]
}
```

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

`trillLines` draws a full trill across a run of notes.  For the default
`trill` wiggle the leading `tr` glyph is added automatically, so a trill line
is self-sufficient — you do NOT need to add a separate ornament:

```
"notes": [{"keys": ["c/5"], "duration": "q"}, ...],
"trillLines": [{"from": 0, "to": 3}]        // renders  tr~~~~~
```

`wiggle` selects the glyph: `trill` (default), `vibrato`, `vibrato-wide`,
`sawtooth`.  The `vibrato`/`sawtooth` wiggles are NOT trills and draw the wavy
line alone (no `tr`).  A per-note `trill` ornament marks ONE note; a trill line
spans a range.  If you put a `trill` ornament on the start note yourself the
auto `tr` is suppressed so it is not printed twice.

## Piano pedal lines (spec or staff level)

```
"pedals": [{"from": 0, "to": 3}]
```

`pedals` draws a piano pedal line beneath the staff, spanning from the note
where the pedal is depressed (`from`) to where it is released (`to`) — the
same `{from, to}` note-index span as slurs and hairpins.  The common damper
pedal is the default; use it under a run of notes that should ring together:

```
"notes": [{"keys": ["c/3","e/3","g/3"], "duration": "q"}, ...],
"pedals": [{"from": 0, "to": 3, "style": "bracket"}]
```

`pedal` chooses which of the three real pedals: `sustain` (default, damper,
right foot), `sostenuto` (middle), or `una-corda` (soft, left).

`style` chooses how a SUSTAIN pedal is drawn: `bracket` (default, the modern
L-shaped line), `text` (the traditional "Ped." … release star ✱), or `mixed`
("Ped." then a bracket).  `style` is ignored for `sostenuto` / `una-corda`,
which always print their own engraved wording ("Sost. Ped." / "una corda").

Add `"line": N` (stave-line units below the staff) to drop the pedal line
clear of lyrics or a dynamic sharing the band.  Like every other span, a
pedal line cannot cross a system break.

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
  "marks": ["segno", "to-coda"],  // navigation marks, see list below
  "mark": "to-coda",              // single-mark shorthand; `marks` wins
  "beginBar": "repeat-begin",     // opening barline
  "endBar": "repeat-end",         // closing barline
  "voltas": [                     // repeat endings; see below
    {"type": "begin-end", "label": "1.", "measures": [2, 2]},
    {"type": "begin-end", "label": "2.", "measures": [3, 3]}
  ],
  "measureNumber": 12,             // number stamped on the FIRST system only
  "measureNumbers": true,          // number the first bar of EVERY system
                                   // (running count from measureNumber, else 1)
  "pickup": true,                  // first measure is a pickup (anacrusis):
                                   // it is NOT numbered, so the first FULL bar
                                   // is bar 1 (published convention).  Only
                                   // affects numbering; write the short upbeat
                                   // as measures[0] and it engraves as-is.
  "section": "B",                  // rehearsal-mark style label
  "notes": [ ... ]
}
```

`tempo` renders "Allegro (♩ = 132)".  Give `name` alone for a word-only
marking, or `bpm` alone (`{"bpm": 120}`) for a plain "♩ = 120" -- the beat
unit defaults to a quarter, so `duration` is only needed for a non-quarter
metronome mark (e.g. `{"duration": "8", "bpm": 160}`); `dots` puts
augmentation dots on the beat unit.  `bpm` must be a positive number: a
non-numeric, zero or negative value is ignored (the mark shows the `name`
alone rather than a dangling "♩ =").

A repeat ending is a bracket ("1.", "2.") scoped to the measures of ONE
ending, not the whole line.  A real repeat needs BOTH a 1st and a 2nd
ending, so use the `voltas` LIST (one entry per ending) and anchor each with
a 1-based inclusive measure range over its own bars:

```
"voltas": [
  {"type": "begin-end", "label": "1.", "measures": [2, 2]},
  {"type": "begin-end", "label": "2.", "measures": [3, 3]}
]
```

draws the "1." bracket over measure 2 (before the `repeat-end` barline) and
the "2." bracket over measure 3 (the alternate ending after it).  `type`
chooses the end-hooks: `begin` (left hook, ending continues), `end` (right
hook), `begin-end` (both — a fully-enclosed ending), `mid` (no hooks).  Each
ending must carry its own `measures` range or they overlap.  For a single
ending, one-element `voltas` (or the legacy singular `volta` field, same
object shape) works; a lone `volta` with `measures` omitted falls back to the
measure carrying a `repeat-end` barline, or the last measure.

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

A meter CHANGE mid-score goes on the measure where it begins: give that
measure a `timeSignature` and the new signature is engraved there, then reads
as governing every following bar until the next change (a change persists
forward, exactly as printed scores read).  Do NOT repeat it on later bars in
the same meter.  The spec-level `timeSignature` is the OPENING meter only.

```
{
  "type": "music", "timeSignature": "4/4",
  "measures": [
    {"notes": [ ...4 beats... ]},
    {"timeSignature": "3/4", "notes": [ ...3 beats... ]},
    {"notes": [ ...3 beats... ]},
    {"timeSignature": "4/4", "notes": [ ...4 beats... ]}
  ]
}
```

`measures` works inside a `staves` entry too, for a multi-measure grand staff.
Span indices (`slurs`, `ties`, `brackets`, `hairpins`, `trillLines`, `pedals`)
count notes across the whole staff and ignore the measure divisions, so a slur or
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
`glissandos`, `hairpins`, `brackets`, `trillLines`, `pedals`, `tuplets` and explicit
`beams` are drawn between two notes on the SAME line — the underlying
renderer has no way to split one into two partial arcs, and given endpoints on
different systems it would draw a single arc sprawling down the page.  Such a
span is skipped and reported rather than drawn wrongly, so keep phrase marks
inside a line or move the break (`systemBreak`) to suit the phrasing.

On a multi-staff score, give each staff a `shortName` alongside `name`:
the full name is printed beside the first system and the short form beside
each continuation system, as published scores do.

## Multi-measure rest (measure level)

In an instrumental PART, a run of silent bars is NOT written as one empty
whole-rest bar after another -- a published part consolidates them into a
single multi-measure rest: the thick horizontal H-bar with the number of bars
engraved above it, which the player counts once instead of tracking a row of
identical empty measures.  Set `multiRest` on a measure to that bar count
(`multiMeasureRest` is accepted as an alias for the same field):

```
{
  "type": "music", "clef": "treble", "timeSignature": "4/4",
  "measures": [
    {"notes": [{"keys": ["g/4"], "duration": "q"}, ...]},
    {"multiRest": 4},          // ONE H-bar marked "4": four bars of rest
    {"notes": [{"keys": ["c/5"], "duration": "q"}, ...]}
  ]
}
```

- The value is the number of bars the rest stands for (a positive whole
  number).  It replaces the whole bar: any `notes` on that measure are ignored,
  since the bar is silent by definition.
- Prefer this over writing N separate `{"rest": true, "duration": "w"}` bars
  whenever a part rests for two or more consecutive measures -- that is what a
  reader expects to see.
- Works mid-line or as a whole system, and coexists with wrapping, dynamics
  and multi-voice staves.  A non-positive or fractional value is ignored (the
  measure then draws whatever `notes` it carries).

```
"staves": [
  {"clef": "treble", "name": "Flute", "shortName": "Fl.", "measures": [...]},
  {"clef": "bass",   "name": "Cello", "shortName": "Vc.", "measures": [...]}
]
```

Navigation marks (`mark`): `coda segno fine to-coda da-capo
da-capo-al-coda da-capo-al-fine dal-segno dal-segno-al-coda
dal-segno-al-fine`, plus `coda-right` / `segno-right` to place the symbol at
the right of the measure instead of the left.  For a full jump scheme use the
`marks` LIST -- a real D.S./D.C.-al-Coda needs several symbols at once (a
`segno` at the target, a `to-coda` where the jump leaves, a `dal-segno-al-coda`
at the source, and a `coda` at the destination), e.g.
`"marks": ["segno", "to-coda", "dal-segno-al-coda", "coda-right"]`.  The
singular `mark` remains as a shorthand for a single symbol; when both are given,
`marks` wins.  A `tempo` and any navigation mark may be given together: the
tempo is lifted onto its own row above the marks so the two never overprint.
Several marks that anchor to the SAME side of the measure (e.g. a `to-coda`,
a `fine` and a `coda-right`, which all sit at the right) are stacked on
separate rows rather than printed on top of one another, so a full jump scheme
stays legible.

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

## Cross-staff beaming (keyboard)

A running keyboard figure often flows from the bass staff up into the treble
(or back) under ONE continuous beam.  A per-staff `beams` / `autoBeam` cannot
join it — each addresses a single staff's own notes — so name the run at the
SPEC level with `crossStaffBeams`.  Each entry lists its members as
`[staffIndex, noteIndex]` pairs into the addressed staves' own `notes`, IN
PLAYING ORDER (left to right), so the beam can thread between staves in the
order the two note lists cannot encode alone.  `stemDirection` ("up" default,
or "down") forces the whole group's stems onto one side, which a beam requires;
"up" places the beam between the staves, the usual keyboard case.

```
{
  "type": "music", "timeSignature": "4/4",
  "staves": [
    {"clef": "treble", "notes": [
      {"keys": ["c/5"], "duration": "8"}, {"keys": ["e/5"], "duration": "8"}]},
    {"clef": "bass", "notes": [
      {"keys": ["c/3"], "duration": "8"}, {"keys": ["g/3"], "duration": "8"}]}
  ],
  "crossStaffBeams": [
    {"stemDirection": "up",
     "notes": [[1, 0], [1, 1], [0, 0], [0, 1]]}
  ]
}
```

Here the run plays bass c3, bass g3, then treble c5, e5 under a single beam.
An out-of-range pair, or a run whose members land on different systems, is
skipped with a warning rather than drawn wrongly.  Only meaningful with
`staves`; on a single staff use `beams` / `autoBeam` instead.

## Cross-staff slurs and ties (keyboard)

When a phrase or a held pitch is passed from one hand's staff into the other,
the arc joining the two notes lives on DIFFERENT staves, which a per-staff
`slurs` / `ties` entry cannot reach — each addresses one staff's own notes.
Name it at the SPEC level with `crossStaffSlurs`.  Each entry has a `from` and
a `to`, and each of those is a `[staffIndex, noteIndex]` pair into the
addressed staff's own `notes`.  `curve` chooses "slur" (default, the phrase arc
between two different pitches) or "tie" (holding ONE sustained pitch across the
staff change).

```
{
  "type": "music",
  "staves": [
    {"clef": "treble", "notes": [
      {"keys": ["g/4"], "duration": "q"}, {"keys": ["c/5"], "duration": "q"}]},
    {"clef": "bass", "notes": [
      {"keys": ["c/3"], "duration": "q"}, {"keys": ["g/4"], "duration": "q"}]}
  ],
  "crossStaffSlurs": [
    {"curve": "slur", "from": [1, 0], "to": [0, 1]},
    {"curve": "tie",  "from": [1, 1], "to": [0, 0]}
  ]
}
```

The slur arcs from bass c3 up to treble c5; the tie holds the g4 shared between
bass n1 and treble n0.  An out-of-range endpoint, or a span whose ends land on
different systems, is skipped with a warning.  Only meaningful with `staves`;
within one staff use `slurs` / `ties`.

## Independent voices on one staff (keyboard, SATB, counterpoint)

When two simultaneous lines share a single staff — soprano above alto, or a
melody over an independent inner part — give the staff a `voices` list instead
of `notes`.  Each entry has its own `notes` (or `measures`) and its own
`stemDirection` ("up" / "down"), so the reader follows each line separately.
This is NOT the same as a chord (stacked `keys`): a chord forces one shared
stem and one shared rhythm, so an eighth-note upper line against a quarter-note
lower line can ONLY be written as two voices.  The voices are formatted
together and their notes align vertically by beat.

```
{
  "type": "music", "timeSignature": "4/4",
  "voices": [
    {"stemDirection": "up",   "notes": [
      {"keys": ["e/5"], "duration": "q"}, {"keys": ["f/5"], "duration": "q"},
      {"keys": ["g/5"], "duration": "q"}, {"keys": ["a/5"], "duration": "q"}]},
    {"stemDirection": "down", "notes": [
      {"keys": ["c/4"], "duration": "h"}, {"keys": ["e/4"], "duration": "h"}]}
  ]
}
```

Put `voices` on a `staves[]` entry for a multi-voice staff inside a grand staff
(e.g. a piano right hand carrying two voices).  The staff's own `slurs`, `ties`,
`beams` and `tuplets` address the FIRST voice — a span between two independent
voices is not something the engine can draw.  `autoBeam` beams every voice,
each on its own stem side.  Set forced stem directions on a two-voice staff:
without them the lines overlap ambiguously.

Rests in a multi-voice staff are positioned automatically: the first (upper)
voice's rests are raised off the centre line and the lower voice's are lowered,
so two rests on the same beat sit on their own lines instead of overprinting
into one -- write rests in each voice as usual (`{"rest": true, ...}`) and the
engine offsets them for you.  A single-voice staff keeps its rests centred.

Two spellings express the same multi-voice, multi-measure music, and you may
use whichever nests more naturally:
- VOICE-MAJOR — `voices: [ {stemDirection, measures: [...]}, ... ]` — one list
  per line, each carrying that line's bars (shown above);
- MEASURE-MAJOR — `measures: [ {voices: [...], endBar, ...}, ... ]` — one list
  per bar, each carrying that bar's voices.  Bar-level fields (`endBar`,
  `timeSignature`, `systemBreak`, `multiRest`) stay on the measure and
  apply to every voice in it.

```
{
  "type": "music", "timeSignature": "4/4", "autoBeam": true,
  "measures": [
    {"voices": [
      {"stemDirection": "up",   "notes": [
        {"keys": ["e/5"], "duration": "8"}, {"keys": ["f/5"], "duration": "8"},
        {"keys": ["g/5"], "duration": "8"}, {"keys": ["a/5"], "duration": "8"}]},
      {"stemDirection": "down", "notes": [{"keys": ["c/4"], "duration": "h"}]}]},
    {"endBar": "final", "voices": [
      {"stemDirection": "up",   "notes": [{"keys": ["g/5"], "duration": "h"}]},
      {"stemDirection": "down", "notes": [{"keys": ["e/4"], "duration": "h"}]}]}
  ]
}
```

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
