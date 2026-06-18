# Work & Knowledge Primitives — Taxonomy

Status: **accepted** (taxonomy); the *work-item queue* described here is **not yet built** —
it is a committed design direction with an open implementation thread.

Ziya has three distinct primitives for tracking state across reasoning, work,
and knowledge. They sit on **different axes** — they are not tiers of one
pipeline, and (critically) work does **not** flow up into durable memory.
This document fixes the boundaries so future work doesn't re-conflate them.

## The three primitives

| Primitive | Scope | Nature | Lifecycle | Visibility | Backed by |
|---|---|---|---|---|---|
| **Beads** | conversation | *noticed* — attention-debt | active / parked / completed / abandoned | agent-internal (invisible to user) | `app/storage/beads.py`, per-conversation tree |
| **Work items** | conversation | *committed* — agreed work | todo → doing → done (terminal) | user-visible queue | **not yet built** (see below) |
| **Memory** | cross-session | *settled* — durable knowledge | active / contested / archived | user-owned, reviewable | `app/memory/`, `~/.ziya/memory/` |

The distinguishing dimension is **commitment × scope**, not "task vs. fact":

- A **bead** is something the agent *noticed in passing* — a fork it didn't
  take, an aside, a "come back to that later." It is conversational debt. It
  lives and dies with the conversation's reasoning and is never shown to the
  user.
- A **work item** is something the user (or agent, with the user's assent)
  *committed to doing*. It is **conversation-scoped** — committed work belongs
  to the conversation that committed it, and is visible. Completing it is
  terminal. Note that when a work item spawns a swarm, the swarm too is scoped
  to the conversation, not the project; work items and the execution they
  launch stay at conversation scope. (Project scope is where the *Big Idea /
  initiative* primitive lives — see below.)
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

## Open implementation thread

The work-item queue is committed in direction but unbuilt. Design questions to
resolve when it is picked up:

- Storage shape and scope key (conversation-scoped persistent queue).
- The `bead → work item` promotion affordance (a bead action, presumably).
- Surface: sidebar queue vs. inline vs. its own panel.
- Whether/how a work item launches a Task Card (the execution hand-off).
- Status model (todo / doing / done, plus blocked/abandoned?).

This thread is itself the canonical first "committed work item" — currently
tracked as a bead, because the queue that would hold it does not exist yet.
