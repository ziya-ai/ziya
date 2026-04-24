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

Artifacts are referenceable by templating: when a downstream task's
instructions contain `{{previous.artifact.outputs[0]}}`, it gets
rendered at dispatch time.

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
