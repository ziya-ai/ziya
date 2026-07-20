# Bead Backlog Browser

A project-scoped surface for browsing parked beads (unfollowed threads) across
conversations without opening them, with peek-before-commit triage actions:
**jump**, **resume**, **branch**, **abandon**.

**Status:** design agreed, implementation in progress.

## Motivation

Beads capture threads set aside mid-conversation, but they are only visible
from inside the owning conversation (the BeadTree panel). Threads parked in a
conversation you stopped opening are effectively lost. The backlog browser is
the sweep surface: *"what did I leave on the floor, anywhere in this
project?"*

## Constraints from the storage layer (`app/storage/beads.py`)

1. **Beads live on chat JSON records** (the `_beads` field); there is no
   bead-only read path.
2. **Lineage forks share one tree**, stored on the lineage ROOT record — a
   scan over chat files sees each tree exactly once, but attribution (title)
   must come from the root record.
3. **The fallback store** (`~/.ziya/beads/<id>.json`) has no project
   association. It is excluded from the backlog in v1; beads appear once they
   migrate onto the chat record (existing behavior).
4. **`Bead.message_index` is nullable** (pre-feature beads, fallback writes).
   Branch and jump-to-seam degrade gracefully: they are hidden.

## Aggregation: scan-on-demand + mtime memo cache (no persistent index)

`GET /api/v1/projects/{project_id}/backlog` iterates the project's chat files.
A process-lifetime cache keyed on `(path, mtime)` holds a small extract per
file:

```
{conversation_id, title, folderId, beads[], seam_snippets{}}
```

The first request pays a full scan; later requests re-parse only files whose
mtime changed. `save_bead_tree` bumps the chat file mtime, so writes
invalidate naturally.

**Rejected alternative — write-through `bead_index.json`:** beads are also
mutated by frontend chat sync, chat deletion, and fallback migration, none of
which route through `save_bead_tree`; a persistent index drifts and needs the
scanner anyway as its rebuild path. The cache module exposes
`invalidate(conversation_id)` as the seam to add one later without changing
the API contract.

**Seam snippets:** on a cache miss the full chat JSON is already in hand, so
the message at `messages[message_index - 1]` is extracted as
`{role, text[:240]}` per open bead — this powers the peek drawer with no
second read and no extra endpoint.

## Endpoint contract

```
GET /api/v1/projects/{project_id}/backlog?status=parked
```

Status filter: `parked` (default), `abandoned`, or both comma-separated.

Response:

```
{
  items: [
    {
      bead: <full Bead dump>,
      conversation_id: <lineage root id>,
      conversation_title,
      folder_id,
      breadcrumb: [root content .. this content],
      descendant_parked_count,
      seam_snippet: {role, text} | null,
      age_ms,
      can_branch: bool  (message_index != null),
      origin: {conversation_id, bead_id} | null
    }
  ],
  counts: {parked, abandoned},
  scanned_chats
}
```

Grouping/sorting is client-side from one payload.

**Topmost-parked collapse:** an item is emitted only for parked beads whose
ancestors are not parked; parked descendants roll up into
`descendant_parked_count` (the peek drawer lists them). Computed with
`BeadTree.get_path_to_root`.

## Actions

- **Peek:** frontend-only; the data is already in the item.
- **Jump:** navigate + scroll to `message_index - 1`. Hidden if there is no
  seam.
- **Resume:** existing `POST .../beads/resume`, then navigate + inject
  `suggested_message`. Disabled while the target conversation is streaming.
- **Branch:** existing `POST .../beads/fork`, navigate to `new_chat_id`.
  Hidden if `can_branch` is false.
- **Abandon/Restore:** NEW
  `POST /api/v1/projects/{project_id}/chats/{chat_id}/beads/{bead_id}/status`
  with body `{"status": "abandoned" | "parked"}`. ONLY parked⇄abandoned
  transitions are allowed; 400 on any attempt to set active/completed (those
  belong to the model + resume flow, which preserves the one-active-bead
  invariant). Abandon is undoable: abandoned beads remain browsable under a
  filter chip with a restore action.

**Resume ordering from the backlog:** call resume (parks the target
conversation's current active bead), then navigate, then inject the suggested
message — the same contract the in-conversation BeadTree uses.

## Frontend structure

- `frontend/src/api/backlogApi.ts` — `getBacklog`, `setBeadStatus`.
- `frontend/src/components/BacklogBrowser/`:
  - `index.tsx` — sidebar tab body; fetch + filter state.
  - `BacklogList.tsx` — grouped-by-conversation view.
  - `BacklogTable.tsx` — flat age-sorted triage view.
  - `BeadPeekDrawer.tsx` — antd Drawer: breadcrumb, `context_hint`, seam
    snippet, action buttons.

The browser is a sidebar tab peer to the chat list. The badge is LAZY in v1:
the parked-count badge populates on the first backlog fetch, not eagerly at
startup. Peek is an antd Drawer overlay — the current chat stays mounted.

Staleness is presentation-only: amber ≥ 7 days, warning marker ≥ 14 days;
constants live in one place and are not configurable in v1.

The existing per-conversation `BeadTree.tsx` is unchanged; shared status glyph
conventions (◐ parked), no shared state.

## Out of scope for v1

- Cross-project backlog.
- Fallback-store beads.
- Persistent bead index (the seam exists).
- Editing bead content from the backlog.

## Test plan

**Backend:** aggregation over a multi-chat fixture (parked/abandoned
filtering, topmost collapse with descendant counts, lineage-root attribution,
null `message_index` → `can_branch` false, seam snippet extraction, mtime
cache hit/miss). Status endpoint: allowed transitions both ways; 400 on
active/completed attempts; 404 on unknown bead.

**Frontend:** `backlogApi` client (URL shape, 404 → empty), drawer action
guards (streaming disables resume; missing seam hides jump/branch).
