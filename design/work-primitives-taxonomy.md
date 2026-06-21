# Work & Knowledge Primitives — Taxonomy

Status: **accepted** (taxonomy + scope model); the *work-item queue* described
here is **not yet built** — committed design direction with an open
implementation thread. The bead-as-branch-point context model that came out of
this taxonomy lives in a companion doc, `design/bead-branching.md`.

Ziya has three distinct primitives for tracking state across reasoning, work,
and knowledge. They sit on **different axes** — they are not tiers of one
pipeline, and (critically) work does **not** flow up into durable memory.
This document fixes the boundaries so future work doesn't re-conflate them.

## The three primitives

| Primitive | Scope | Nature | Lifecycle | Visibility | Backed by |
|---|---|---|---|---|---|
| **Beads** | conversation | *noticed* — attention-debt | active / parked / completed / abandoned | agent-internal (invisible to user) | `app/storage/beads.py`, per-conversation tree |
| **Work items** | session **or** project | *committed* — agreed work | todo → doing → done (+ blocked / abandoned) | user-visible queue | **not yet built** (see below) |
| **Memory** | cross-session | *settled* — durable knowledge | active / contested / archived | user-owned, reviewable | `app/memory/`, `~/.ziya/memory/` |

The distinguishing dimension is **commitment × scope**, not "task vs. fact":

- A **bead** is something the agent *noticed in passing* — a fork it didn't
  take, an aside, a "come back to that later." It is conversational debt. It
  lives and dies with the conversation's reasoning and is never shown to the
  user.
- A **work item** is something the user (or agent, with the user's assent)
  *committed to doing*. It exists at one of **two scopes**, distinguished by
  timing horizon rather than structure:
    - **session** — "do this now / in the next few hours, in this
      conversation." May auto-clear at session end.
    - **project** — backlog. No specific timing. Never auto-clears.
  Both are user-visible and carry status; completing one is terminal. A work
  item is a *tracking* record, not an *execution* engine — it is satisfied when
  the work is done, however that happens (manually, by asking the agent, or by
  launching a Task Card). See "Scope model" below for the two-scope rationale
  and why an intermediate "feature" tier collapsed into Task Cards.
- A **memory** is *settled truth* — a domain fact, an architecture decision,
  a vocabulary term, a lesson learned. It is durable across sessions and
  owned by the user (proposed, reviewed, editable, archivable).

## The flows that ARE allowed

```
bead (noticed) ──promote──▶ work item (committed) ──▶ done. terminal.

memory (settled knowledge) ◀── extraction / explicit save
                                (NOT from the work pipeline)
```

- **bead → work item**: a parked bead the user decides to commit to graduates
  into a tracked work item. The bead is conversational; once committed, the
  work belongs on the project queue.
- **memory** is fed by post-conversation **extraction** and **explicit save**
  (`/remember`, `memory_save`). It is *not* an endpoint of the work pipeline.

## The flow that is FORBIDDEN

**Work items must NOT auto-promote to durable memory.** Completing a work item
does not deposit a "decision memory." This is the conflation to avoid:

> A design decision about in-flight or just-completed work is *work state*,
> not settled knowledge. Putting it in the durable store makes the store lie
> the moment the approach changes.

The rare legitimate case — where *doing* the work taught a durable fact — is
handled by an **explicit save of that fact**, authored deliberately, not by an
automatic "work done → memory" deposit. A completed-work record and a
knowledge memory are different objects with different lifecycles; the former is
terminal, the latter is durable and user-owned.

(Concretely: during the interference-forgetting design work, an "Option A,
N=21" *decision* was briefly proposed into the memory queue. That was the
conflation. The decision belonged on the work thread (a bead), not in durable
memory — it only becomes a durable `architecture`/`decision` memory if and
when the work lands and the decision is settled, and even then by explicit
save.)

## Why a separate work-item queue (not a bead tier, not a memory tier)

Two independent signals point to "its own thing":

1. **The axes are genuinely orthogonal.** Beads are agent-internal
   conversational debt; memory is user-owned settled knowledge. Committed,
   conversation-scoped, user-visible work-with-status is neither. Forcing it into
   a bead *status* would leak agent-internal reasoning state into a
   user-facing surface; forcing it into memory would pollute durable knowledge
   with transient work state.
2. **Convergent design in peer tools.** Coding agents such as Kiro maintain a
   **standalone task queue**, kept separate from both scratch reasoning and
   long-term knowledge. That a mature peer converged on a distinct queue is
   independent evidence the work-item layer wants to be its own primitive.

### Relationship to Task Cards

Task Cards (`design/task-cards.md`) are adjacent but **not** the work-item
queue. Task Cards are an *execution engine* — Repeat / Parallel / Schedule
blocks that actually run. A work item is a lightweight *agreed-work record with
status* that may or may not ever be executed by a Task Card. A committed work
item could *launch* a Task Card; it is not itself one.

## Scope model (resolved)

A work item is **one primitive with a scope discriminator**, not several
primitives. Session and project items share structure; they differ only in
timing horizon, storage location, and lifecycle policy.

```python
class WorkItemScope(BaseModel):
    type: Literal["session", "project"]
    key: str    # conversation_id | project_id
```

| | session | project |
|---|---|---|
| Timing expectation | now / next few hours | backlog, no specific timing |
| Storage | per-conversation | per-project |
| Expiry | may auto-clear at session end | never auto-clears |
| Promotion target | → project | — |
| Surface | inline panel | project board |

Promotion is a single path: **`session → project`**, used when a near-term item
didn't get done and needs to outlive the conversation. There is no other
promotion direction.

### Why no "feature" tier

An earlier draft considered a middle tier (Kiro's spec-derived `tasks.md` is
exactly this: bigger than a session checklist, more concrete than a backlog
item). Ziya doesn't need it as a *tracking* primitive because **Task Cards
already occupy that territory** — `/goal` → Task Card is the "planned unit of
work with execution" case. The feature tier collapses into Task Cards; the
WorkItem model needs only `session` and `project`.

## Unified WorkItem model (shared factory, divergent routing)

The three things that vary between scopes are all derivable from `scope.type`,
so the data model and status machine are written once:

```python
class WorkItem(BaseModel):
    id: str
    content: str
    status: Literal["todo", "doing", "done", "blocked", "abandoned"]
    scope: WorkItemScope          # the discriminator
    conversation_id: str          # always — originating conversation
    created_at: int
    order: Optional[int]
    notes: Optional[str]
```

Thin factories over one constructor:
`WorkItem.for_session(conversation_id, content)` /
`WorkItem.for_project(project_id, content)`; shared CRUD, shared status machine,
one `promote(new_scope)`. Storage is one class parameterized by scope.

**Boundary to hold:** beads do **not** share this model. A bead is a *tree*
(parent_id, context_hint, message_index) of agent-internal noticed debt; a work
item is a *list* of user-visible committed work. Unifying "everything
task-like" into one model makes beads wrong. Different primitives, different
models. Task Cards are likewise separate — an execution engine a WorkItem may
*reference*, never *is*.

## No compaction (architectural principle)

Ziya has **zero compaction capability** and avoiding it is a guiding principle
— validated by years of continuous use on complex projects. The context window
is treated as a feature of the architecture, not a ceiling to engineer around.
Consequently:

- "Context exhaustion kills parked beads" is **not** a problem to solve with
  summarization. Beads dying when a conversation ends is the spec, not a bug.
- The legitimate context-management move is to **shed** context by *branching*
  at a semantic seam (a bead's origin point), not to *summarize* to fit more
  in. See `design/bead-branching.md`.

## Beads as branch points

The synthesis that came out of this taxonomy: **a parked bead is an un-taken
branch of the conversation, recorded with its divergence point** (the bead's
`message_index`). This unifies the bead tree with the conversation-graph vision
(`design/conversation-graph-tracker.md`) — "a delegate is a branch in the
conversation graph"; a parked bead is the same thing, a branch not yet taken.
The full design (split modes, lineage metadata, the branched-conversation UI)
is in `design/bead-branching.md`.

## Research validation

The four-tier shape (bead → session todo → project work item → memory) is not
invented complexity — three independent bodies of work converged on it:

- **GTD (Getting Things Done):** Someday/Maybe ↔ parked bead, Next Actions ↔
  session work item, Projects ↔ project work item, Reference ↔ memory. 25 years
  of refinement settled on the same Next-Actions-vs-Projects scope split.
  GTD has no analog for beads — there is no reasoning agent in GTD, so
  agent-internal attention-debt is Ziya's own contribution.
- **Kiro:** spec → `tasks.md` proves product demand for a feature-scoped
  execution checklist — the tier Ziya fills with Task Cards rather than a
  tracking primitive.
- **Agent-memory research ("prospective memory"):** memory of *intended future
  actions* is a recognized category, explicitly distinguished from semantic /
  episodic / working memory, and explicitly **not** well served by
  retrieval-based memory — it wants a task queue / goal tracker. This validates
  keeping work items out of the memory system entirely.

## Still open

- Surface for the session work-item panel (inline vs. its own panel).
- Whether session items auto-clear at session end or require explicit dismissal.
- The `bead → work item` promotion affordance (a bead action; correction, not
  pipeline — see below).

`bead → work item` promotion is a **correction affordance**, not a normal
pipeline step: it fires only when the commitment level changes after the fact
("actually, let's commit to that"). Work correctly identified as committed at
creation time should be created as a work item directly — a bead that holds
real committed work is a *misclassification*, not a feature awaiting promotion.