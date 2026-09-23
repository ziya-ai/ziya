# Conversation Handoff — deliberate continuation across context limits

Status: **design accepted, unbuilt**. Supersedes the fork/branch parts of
`design/bead-branching.md` where they conflict (one lineage edge model, below).
Companion to `design/conversation-graph-tracker.md`.

## What this is, and what it is not

A handoff is a **conscious, user-initiated** move from one conversation
("segment") to a fresh one when the first has accumulated more context than is
useful. It is not auto-compaction and must never be triggered automatically:

- Nothing is summarized *away*. The source segment stays whole, searchable and
  fully usable.
- The new segment starts **empty of transcript** and carries a **handoff
  document** — a short, user-editable prelude with backreferences into the
  source — and relies on the **existing** `chat_search` / `chat_read` tools
  (with a general conversation filter added to `chat_search`) so the model can
  pull any prior turn at full fidelity when it needs it. No handoff-specific
  retrieval tool is introduced.
- The two segments are visibly **chained** in the chat list and inside both
  conversations.

Compaction replaces history with a summary. Handoff keeps history and adds an
*index* into it.

## One lineage edge model (fork / branch / handoff)

Today plain fork stamps only `lineageRootId` (no visible chaining), while
branch-from-bead stamps `branchedFrom` + seam fields (nested, LineageBar). This
design unifies them. Every derived conversation carries:

| field | fork | branch | handoff |
|---|---|---|---|
| `branchedFrom` (parent id) | ✓ *(new)* | ✓ | ✓ |
| `lineageKind` *(new)* | `fork` | `branch` | `handoff` |
| `branchedAtMessageIndex` / `branchedFromLabel` | — | ✓ | — |
| `lineageRootId` (shared bead tree, b2) | ✓ | — | ✓ |
| `handoff` *(new)* | — | — | ✓ |
| transcript copied | all | up to seam | **none** |

The source of a handoff additionally gets `handedOffTo: <child id>`.

Applies to **newly created** forks only; existing forks are not migrated
(migration would move rows in every user's sidebar).

### Beads: shared, not copied

Fork and handoff are *continuation*, so both share the lineage's single bead
tree via `lineageRootId` (the existing b2 resolution in `app/storage/beads.py`).
Completing a bead in any segment completes it for the lineage. No `origin_*`
copies for handoff — those remain a branch (divergence) construct.

Consequence for the handoff document: its **Open threads** section is not
stored text. It is rendered from the shared tree when the prelude is built, so
it is always current in every segment.

The parked "self-root / detach lineage" action is the one place a bead
*snapshot* is acceptable: the user is explicitly severing, the child takes a
copy, the parent keeps its own. Out of scope here.

## The handoff document

Stored on the **child** conversation record, not as a message:

```
handoff: {
  sourceId: string            # == branchedFrom
  sourceMessageCount: int     # turns in source at handoff time
  sourceTitle: string
  document: string            # markdown, model-drafted, user-edited
  generatedAt: int
  editedAt: int | null        # set when the user has changed it
  coverageChecked: bool       # a "Check coverage" pass was accepted/dismissed
}
```

Injected every turn as a context prelude (same channel as `additionalPrompt`),
so an edit at any point takes effect on the next turn without re-running a
turn. Editing message #1 would have required edit-and-resubmit, which becomes
unworkable once the child has history.

### Delivery is split for prompt caching

Providers put the cache marker on the system block (and on the last history
message). Anything that changes turn-to-turn inside the system prompt re-bills
the whole cached prefix — all files, all history — and the run-up to a handoff
is exactly when that prefix is largest. So `split_handoff_prelude` produces
two parts (`app/utils/handoff_prelude.py`):

| part | contents | changes when | delivered to |
|---|---|---|---|
| system | inherited document + predecessor ids + retrieval instructions | user edits the document | `system_prompt_addition` |
| turn | live open threads (bead state), the living draft + merge instruction, the pressure nudge | every bead change; every draft merge; crossing the threshold | appended to the **final user message** after a `[Ziya — handoff status for this turn]` marker |

The final user message is never inside a cache boundary on any provider. A
mid-history `system`-role message is **not** a safe alternative: the Anthropic
wrapper overwrites `system_content` with every `SystemMessage` it meets, and
the Bedrock wrapper merges all system messages into the leading block. The
nudge text carries no percentage for the same reason — it must be
byte-identical across turns (the exact fraction is available via
`handoff_read`). Pinned by `tests/test_handoff_draft_prelude.py::TestCacheStability`.

### Ownership on the sync path

`handoffDraft`, `handoff` and `handedOffTo` are **server-owned**, like
`_beads`: written by `handoff_write` and the handoff endpoints, only mirrored
on the client. The mirror goes stale the moment the model merges a draft
mid-turn, so the end-of-turn bulk-sync must never be allowed to push it back.
`conversationToServerChat` strips the three; the bulk-sync guard in
`app/api/chats.py` carries the on-disk value forward unconditionally. User
edits and clears go through the PATCH endpoints. `lineageKind` is the
exception — the client authors it on a plain fork — so it keeps the
omitted-vs-cleared (`model_fields_set`) rule.

Drafted sections (model-authored, stored): **Objective · Current state ·
Decisions (with turn backreferences) · Gotchas / dead ends · Suggested next
step.** Derived sections (rendered live, not stored): **Open threads** (shared
bead tree) · **Working set** (file context carried over) · **Predecessor
segments** (ids + titles back to the lineage root).

Backreferences are written as `↗ turn N` / `↗ turns N–M` of a named
conversation id; the prelude tells the model these resolve via `chat_read`.

### Who drafts

**Drawer → "Ask the model to draft it"** runs a **background, non-persisted
turn** on the conversation's own model with its full live context: the same
history / checked files / skills / model pin a normal send carries, through
the same `/api/chat` path, but the stream is drained and discarded by the
client. Nothing goes into the composer and nothing streams into the
transcript. The turn's only durable effect is the `handoff_write` the model
performs server-side. The drawer shows a spinner and the draft appears in
place when it lands; the transcript gets a **local notice** (role `system`,
`muted: true` — excluded from every send path, never seen by the model) with
a "Review handoff" link back to the drawer. Disabled while the conversation
is streaming (two turns on one conversation is the one thing this path must
not do) and for a non-active conversation (its messages are not in memory).
Decided 2026-09-21 after the composer-inject shortcut was rejected. — the living draft

The **conversation's own model**, through a standardized tool rather than a
one-shot generation. Observed behaviour today: as context tightens the model
starts maintaining a handoff file of its own devising, with a made-up name and
location, updating it as part of each turn's tool chain. That instinct is
right; the interface is what was missing.

The draft lives on the **source** record as `handoffDraft` (same `HandoffDoc`
shape as the child's `handoff`) and is maintained through two builtin tools in
the `chat_history` category:

- `handoff_read()` — the current draft for this conversation, or none.
- `handoff_write(document | sections, mode='replace'|'merge')` — upsert.
  `merge` takes section-keyed text (`objective`, `state`, `decisions`,
  `gotchas`, `references`) so a per-turn update touches one section instead of
  rewriting the whole document.

The drawer reads and edits the same record, so the model-initiated and
user-initiated paths converge on one document.

**Instruction states** (rendered by the prelude module, per turn):

| conversation state | prelude |
|---|---|
| no draft, pressure < threshold | nothing |
| no draft, pressure ≥ threshold | nudge: context is at N%; consider starting a handoff draft with `handoff_write` |
| draft exists | the draft + "if this turn produced a decision, state change or gotcha worth carrying forward, update it with `handoff_write(mode='merge')` before finishing" |
| continuation child, no draft | the inherited `handoff` prelude only — **no** drafting instruction |
| continuation child, pressure ≥ threshold | inherited prelude + the nudge (the next hop's draft starts fresh, not from the inherited doc) |

**Pressure** is computed server-side from the messages actually assembled in
`build_messages_for_streaming`, against the model's context limit — the number
the model hears matches what it is about to be sent. Threshold
`ZIYA_HANDOFF_NUDGE_RATIO`, default 0.7 (the same 70% the frontend's
`TokenCountDisplay` uses for its warning colour). Never automatic: the nudge
is text; the user commits.

There is no "Regenerate": a same-model redraft mostly rewords, and the label
invites the question "is this a different model?". The drawer's *Draft it*
button (used when no draft exists yet) sends a non-persisted turn whose
instruction is simply to write the handoff via `handoff_write`.

Cost note: under pressure with a draft present, each turn carries one small
`merge` tool call. That is what the model does anyway with an ad-hoc file;
if it proves noisy, "update at most every K turns" is a one-line change to
the prelude text.

### Check coverage (optional, explicit)

A second, independent agent — clearly labelled as such in the UI — receives
the draft and *read access to the source transcript* (`chat_read` over the
source id; not the live context) and returns **findings**: decisions,
constraints or open questions present in the transcript but missing from or
misstated in the draft. Each finding is accept / dismiss; accepting appends it
to the document. Nothing is rewritten silently.

Why it earns a button: the in-line model has recency bias and can drop a
turn-12 decision it stopped attending to. A reader walking the transcript
top-to-bottom does not. Runs as a skill-directed task on a service-tier model.
Never automatic.

## Retrieval from the new segment

The prelude names every predecessor segment id. Retrieval uses the general
tools, not a handoff-specific one:

- `chat_search` gains a general `conversation_ids: [...]` filter — restrict to
  listed conversations. Today there is **no** per-conversation filter at all;
  this is a gap in the tool regardless of handoff (e.g. "find where we decided
  X in *this* thread"), and handoff is simply its first hard requirement.
  Without it a "search the prior thread" nudge returns hits from every
  conversation in the project.
- `chat_read` already takes a single id and needs no change.
- The prelude tells the model which ids to pass. Nothing in the tool layer
  knows about handoffs or lineage; the knowledge lives in the prelude text.

This ships **with** the first pass, not as later polish — it is what makes the
backreferences followable.

## Source segment after handoff

Banner at the foot of the source: *"Continued in → ‹child title›"* (click to
navigate). Row and body **dimmed**, not locked — enough to signal "this is not
the most recent part of the conversation", nothing more. Sending in the source
still works; the first time someone needs one more question in the old thread,
a lock would be wrong.

## UX surfaces

1. **Trigger.** Row action menu: Fork / Branch / **Hand off…**; slash command
   `/handoff`; and a quiet offer beside `TokenCountDisplay` once context passes
   a threshold (default 70% of the model window). An offer, never a nag, never
   automatic; dismissible per conversation.

2. **Handoff drawer.** Header: source title, turn count, token estimate
   before → after. Editable sections as above (derived sections shown read-only
   with their provenance: "from beads", "from file context"). Working-set chips
   removable. Footer: *Check coverage* (labelled "runs a separate agent") ·
   Cancel · **Create continuation ⇢**. Commit creates the child, navigates to
   it, and pre-fills (not sends) the composer with "Pick up from the handoff."

3. **In the child.** A collapsible **Handoff card** in the LineageBar slot
   (LineageBar already renders there for branches; the card is its
   handoff-flavoured sibling) with *Edit* — reopens the drawer against the
   stored document. Predecessor breadcrumb for hop-by-hop navigation.

4. **Chat list.** Divergence nests, continuation chains:
   - fork / branch: nested under the parent (existing behaviour), glyph ⑂.
   - handoff chain A→B→C: **one row** titled/sorted by the newest segment,
     glyph ⛓ and a "N segments" pill, expandable to the segment list. Nesting
     each handoff a level deeper would indent a long-running track off the
     right edge — wrong shape for a linear thing.

## API

```
tools (model-facing, chat_history category):
  handoff_read()                                 → { draft | null, pressure }
  handoff_write(document|sections, mode)         → { draft }   upserts source.handoffDraft

GET   /api/chats/{id}/handoff/draft     → { draft | null }      the drawer reads this
PUT   /api/chats/{id}/handoff/draft     { document } → 200      user edit (sets editedAt)
POST  /api/chats/{id}/handoff/draft     → 202                   "Draft it": non-persisted turn
       on the conversation's model, instruction = write it via handoff_write;
       used only when no draft exists yet
POST  /api/chats/{id}/handoff/check     { document } → { findings: [{kind, text, turns}] }
       skill-directed service-tier agent with chat_read over {id}
POST  /api/chats/{id}/handoff/commit    { document, workingSet } → ChatSummary (child)
       copies the (possibly edited) draft to child.handoff; creates child with
       branchedFrom, lineageKind=handoff, lineageRootId, additionalFiles=workingSet;
       sets source.handedOffTo.  source.handoffDraft is LEFT IN PLACE — it keeps
       living if the user continues in the source.
PATCH /api/chats/{childId}/handoff      { document } → 200      edit the inherited doc
```

`ChatSummary` gains `lineageKind`, `handedOffTo`, and a boolean `hasHandoff`
so the sidebar can render the chain and dimming from the listing alone (same
reason `flags` is declared on the summary — the sidebar never has the full
record).

## Build order

1. **Done.** Storage + models: `lineageKind`, `handoff`, `handedOffTo`; plain
   fork sets `branchedFrom` + `lineageKind='fork'` (`forkLineageFields` in
   `frontend/src/utils/lineage.ts`); `ChatSummary` fields. Tests:
   `tests/test_handoff_lineage_fields.py`, `frontend/.../forkLineage.test.ts`.
2. **Done.** `chat_search(conversation_ids)` — a general filter on the
   existing tool, not a handoff-specific one; the tool layer knows nothing
   about handoffs. Prelude: `app/utils/handoff_prelude.py`, appended to
   `system_prompt_addition` in `build_messages_for_streaming` (the one choke
   point web/CLI/delegates share). The prelude walks `branchedFrom` back
   through *handoff* hops only (a fork/branch ancestor copied its transcript
   into the source, so it is not a separate segment). Tests:
   `tests/test_handoff_prelude.py` (seam: document + predecessor ids + open
   beads from the shared root tree; closing a bead via the child removes it
   from the next prelude), `tests/test_chat_search_conversation_ids.py`.
3. Living draft: `Chat.handoffDraft`; `handoff_read` / `handoff_write` tools;
   prelude instruction states (draft-present → per-turn merge instruction;
   pressure ≥ threshold → nudge; continuation child → no drafting
   instruction). Server-side pressure computed from the assembled messages.
   Seam tests: a `handoff_write` call changes the next turn's prelude; a
   child with an inherited doc and low pressure gets **no** drafting
   instruction; the nudge appears exactly at the threshold.
4. **Done.** `app/api/handoff.py`: GET state, PATCH draft (source; empty
   clears), PATCH inherited doc (child), POST commit (child record with
   `lineageKind=handoff`, shared `lineageRootId`, source `handedOffTo`;
   title auto-increments "(N)"). Frontend: `HandoffDrawer` (row menu "Hand
   off…"; edit / save / commit; "Ask the model to draft it" places a
   `handoff_write` request in the composer — a *persisted* turn, not the
   non-persisted turn first proposed: honest, visible, and the source is
   about to be left anyway), `HandoffBanner` (source notice + continuation
   card with inline edit) in the LineageBar slot, sidebar row dimmed with ⇢
   when handed off and ⛓ on a continuation. Tests:
   `tests/test_handoff_api.py` (seam: commit → child prelude carries
   document + predecessor + shared open beads; closing via the child drops
   the source's openBeadCount), `frontend/.../HandoffBanner.test.tsx`.
   Interim: a continuation currently nests under its source like a fork
   (the chain row is step 5).
5. **Done.** Chat-list chain row. `buildHandoffChains` in
   `frontend/src/utils/lineage.ts` is the pure rule: a segment link A→B
   requires `B.lineageKind='handoff'`, `B.branchedFrom=A` and
   `A.handedOffTo=B` (all three — a superseded second continuation is not a
   link and nests under its parent like a fork). The tail owns the row;
   predecessors become its `handoffSegment` children, hidden by
   `flattenVisibleNodes` until the "N segments" pill expands them; the
   tail's other children (forks, task-plan folders) stay inline. The row
   sorts by the tail's own activity — a send into an old segment does not
   bump the chain. Tests: `frontend/.../handoffChains.test.ts`.
6. Check-coverage agent.

## Tests that matter

- Handoff child shares the bead tree: complete a bead in the child, the
  source's open-bead count drops (asserted on the summary listing).
- Editing the document in the child changes the next turn's prelude without
  creating a message.
- `chat_search(conversation_ids=[source])` returns only source hits.
- New plain fork nests under its parent; an old fork's placement is unchanged.
- Source after handoff still accepts a send (not locked).

## Decisions log

- Handoff shares beads via `lineageRootId` (no copies) — user, 2026-09-20.
- New forks only get `branchedFrom`; no migration.
- Source: banner + dimmed, never locked.
- No "Regenerate". Optional, clearly-labelled second-agent coverage check.
- Lineage-scoped `chat_search` ships in the first pass.
- The draft is a living document on the SOURCE, maintained by the model via
  `handoff_write` each turn once it exists; a continuation child does not
  start its own draft until asked or nudged by context pressure — user,
  2026-09-20. Replaces the one-shot "draft endpoint" design.
- Review findings, 2026-09-20: volatile prelude parts moved out of the system
  prompt onto the final user message (cache busting on every turn once a
  draft existed); `handoffDraft`/`handoff`/`handedOffTo` made server-owned on
  bulk-sync (a stale client mirror reverted model draft merges). Both were
  green under the original tests, which exercised only the omitted-field
  case and never asserted on placement.
