# Bead Branching — Context Management via Branch Points

Status: **design** (accepted direction, unbuilt). Companion to
`design/work-primitives-taxonomy.md` (beads/work-items/memory boundaries) and
`design/conversation-graph-tracker.md` (the eventual graph view this is Phase 0
of).

## The core idea

Every bead already records **where in the conversation it was born** —
`Bead.message_index` ("Index of the message that spawned this bead"). That
field is the whole hinge: a parked bead is not just "a thread I noticed," it is
**"a thread I noticed, and here is exactly where it diverged."** That index is
a natural, semantically meaningful cut line.

This makes a bead an **un-taken branch point** of the conversation. It unifies
two things that were designed separately:

- the **bead tree** (`app/storage/beads.py`) — parked threads, each with an
  origin point, and
- the **conversation graph** (`design/conversation-graph-tracker.md`) — where
  "a delegate is a branch in the conversation graph."

A parked bead is the same construct as a delegate branch: a divergence from the
main line, recorded with its seam. **Splitting on a bead is taking the branch.**

## Why this is shedding, not compaction

Ziya does not compact (see the no-compaction principle in the taxonomy doc).
Branching at a bead's seam is the *opposite* of compaction:

- Compaction **summarizes** to fit more into the window (lossy).
- Bead-split **sheds** context by cutting at a semantic boundary — keep the
  prefix relevant to the thread, drop the accumulated tangents (lossless: a
  clean truncation at a meaningful point, nothing summarized).

The difference from today's fork is precise:

- Today's `forkConversation` copies `M0…Mn` — context just as full.
- Bead-split copies `M0…M[bead.message_index]` — context **smaller**. The seam
  is what turns a duplication into a context-management operation.

## Two split modes

Both need the same primitive (a seam + a branch). They differ only in what
happens to the *current* line:

### Mode 1 — Branch (non-destructive)

The original conversation stays intact in full. A new conversation is created
holding only `M0…M[seam]`, with the chosen bead promoted to active. The
"interim context retention" concern is satisfied trivially: nothing is deleted
from the original — the post-seam work lives on there. This is the refinement
of the existing fork: **truncate at the seam instead of copying everything.**

### Mode 2 — Rewind-and-retain

The *main* line rewinds to the seam and continues from the bead's clean origin;
the interim context (the tangents after the seam) is archived as a **sibling
branch** rather than discarded. This is the case where the bead *was* the real
main thread and the stuff after it was the digression — "delete everything
since then, but keep it retrievable."

## Plain fork vs. split-from-bead (the "b2" shared-root model)

Split-from-bead (above) is *divergence*: take one thread into its own space,
inherit a subset of beads (`message_index <= seam`), each with a backward
`origin_*` reference. A **plain fork** (`forkConversation` — "continue this
same work in a fresh conversation") is the opposite intent: *continuation*,
not divergence. It should start with **all** the beads, and — resolved here as
**"b2"** — the fork and its source share **one** bead tree, not divergent
copies.

Mechanism: beads key to a **lineage root**, not to a conversation id. A plain
fork stamps `lineageRootId = source.lineageRootId || source.id` (flat: a fork
of a fork points at the *same* ultimate root, never chains). `load_bead_tree` /
`save_bead_tree` resolve `conversation_id → lineageRootId → the root's chat
record`, so every conversation in a lineage reads and writes the same tree:
state-synced (complete a bead in the fork, it completes in the parent).
Self-root conversations (no `lineageRootId`) resolve to themselves — the
non-fork path is unchanged. A `lineageRootId` whose root record is missing
(deleted / never-synced) falls back to the conversation's own record so a
dangling pointer can't strand bead writes.

Contrast with the per-bead `origin_*` references used by split-from-bead: those
model *divergence with a backlink* (separate trees, navigable lineage); `b2`
models *shared identity* (one tree, no copy). Split = diverge-with-reference;
fork = continue-with-shared-root.

## Which beads come along on a split

Resolved by the timeline, with no ambiguity: when you split on bead `B`, the
beads that come along are those with `message_index <= B.message_index`, with
`B` promoted to active (any other active bead parked). Beads born *after* the
seam belong to threads that hadn't happened yet in that branch and stay behind.
The temporal cut decides everything — this is also the answer to the original
"what beads come along on fork?" question.

### Fresh ids + origin backlink

Inherited beads get **fresh ids**, not the source ids. Beads are
conversation-scoped, so reusing source ids across the branch would create
cross-conversation collisions and — worse — make the per-bead origin reference
(below) degenerate: under kept ids `origin_bead_id` always equals `id` at every
fork hop, carrying no information. Fresh ids make the lineage chain meaningful
and walkable (`c1 → origin (B,b1) → origin (A,a1)`). `parent_id` is remapped
within the inherited set so the tree structure survives the renumbering (a
parent's `message_index` is always `<=` its child's, so every inherited bead's
parent is also inherited or `None`).

Each inherited bead records its origin:

```
origin_conversation_id   the conversation it was forked from
origin_bead_id           the bead id in that conversation it descends from
```

Both are `None` on natively-created beads; set only on inherited ones.

### The backlink question (open — data built, behavior deferred)

The origin reference is the **scoping reference** that makes a bead-level
backlink possible. It deliberately does *not* (yet) imply any cross-conversation
state sync. The open question it enables: **should completing a forked bead
resolve its origin?** The argument for: a parked origin bead means "a thread I
noticed but didn't follow *here*"; if you followed it in the fork and finished
it, that note is now a stale lie. This is narrow and defensible where general
bidirectional sync was not — it is *unidirectional along a known lineage edge*
(fork-completion → origin-resolution) and a *single signal* (completion), not
"sync everything." Deferred deliberately: the data (origin refs) is built now;
the propagation direction, trigger, and user control are unresolved. Whatever
the answer, it travels along the `origin_*` edge — which is why the edge is
recorded even though nothing consumes it yet.

## Two structural options (phased)

- **Fork (disconnected)** — two independent conversations. Implementable now on
  existing plumbing; the only new logic is "truncate messages and beads to the
  seam." Cost: the shared prefix `M0…M[seam]` is duplicated and the
  relationship between branches is not modeled (recovered by the lineage
  metadata below).
- **Conversation tree (retained branches)** — the
  `conversation-graph-tracker.md` vision: `M0…M[seam]` is a shared trunk, the
  conversation branches at the seam, both the original continuation and the new
  focused thread are children of the seam node, the prefix is shared (not
  copied), and branches are navigable.

Phasing:
1. **Now:** `forkFromBead(beadId)` — a fork truncated to the seam. Delivers
   Mode 1 and the context-shedding win on existing fork plumbing.
2. **Later:** the conversation graph makes the prefix shared and both modes
   navigable; "split from here" becomes "take this branch"; beads render as the
   branch points of the graph.

## Lineage metadata (the data this needs)

Three fields on the branched conversation record. Small, and **forward-
compatible**: the eventual graph panel reads the *same* fields — the breadcrumb
bar below is Phase 0 of the graph renderer, not throwaway.

```
branchedFrom:            parentConversationId
branchedAtMessageIndex:  number   // the bead's message_index — the seam
branchedFromLabel:       string   // the bead content, for display
```

## Making it legible (the UI is the design)

A branch that's only clear at creation time is confusing five conversations
later. Lineage must be visible in **three places**:

1. **The branch moment** — never silent. A transition + toast: *"Branched. The
   original is preserved — ↰ in the bar above."*
2. **A persistent lineage bar** — always at the top of any branched
   conversation. The full breadcrumb to trunk (not just the immediate parent,
   so nested branches don't lose the trunk), each segment clickable, plus the
   seam note and sibling count.
3. **Sidebar nesting** — branches render *under* their origin with a branch
   glyph and the existing indent guides (reuses the delegate-swarm-as-children
   pattern already in `MUIChatHistory`), so the tree shows lineage at rest.

### Mental model

The framing that makes this natural to someone who hasn't had the design
conversation: **a branch is "reply in thread" for the whole conversation.**
People already understand Slack threads / email reply-chains. Never expose
"message index" or "seam" in the UI — say *"branched from **Network capacity
analysis**, where you raised **microburst drops**."* The
bead-is-a-branch-point-with-a-message_index understanding is the engine; the
surface speaks in terms of *what was said*.

### Lineage bar — the two load-bearing phrases

- **"branched where you raised 'X'"** — anchors the seam to *what was said*,
  never to a message number.
- **"everything before that point came along; the original keeps going past
  it"** — one sentence that answers the entire "what about the interim context
  / how do I get back" worry, and reassures that nothing was lost.

### Vocabulary (load-bearing — "feel natural" is the whole bar)

- Action verb in the bead popover: **"Split from here"** (conveys the cut).
- Result noun everywhere else: **"branch"** (implies retained relationship +
  return path — unlike "fork," which carries git's diverge-and-never-merge
  connotation).
- Return affordance: the **parent's actual title** with an ↰ glyph — never the
  word "main" (ambiguous once branches nest).
- Seam description: **"where you raised X"** — never "at message N."

### The bead-popover action

Each parked bead gets a **"split from here"** action alongside the existing
**"resume"**:

- **resume** — switch the active thread *within* the current conversation
  (current behavior).
- **split from here** — create a branch at the bead's `message_index` (Mode 1
  fork now, a graph branch later).

Read as: *resume = work on it here; split = give it its own clean space
starting from where it came up.*

## Prerequisite gap (must fix first)

`message_index` is **declared** on the `Bead` model but `BeadCreateTool.execute`
never populates it — it is always `None` today. The entire split-at-seam flow
depends on it being recorded at creation time. The bead tools run server-side
during streaming, so the current conversation message count must be threaded in
at create time. **This is the first concrete thing to build** before any split
UI works.

## Deferred

- **Sibling discoverability from the trunk** — inline markers in the *trunk*
  conversation at the points branches came off it. Lovely, but it is closer to
  the full graph view than to the breadcrumb bar; sidebar nesting already shows
  the branches exist. Deferred to arrive with the graph panel, so we don't
  build the graph by accident.
