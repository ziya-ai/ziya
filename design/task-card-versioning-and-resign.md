# Task Card Versioning + Re-Sign-on-Scope-Change

**Status:** Proposed — design locked, implementation pending.
**Date:** 2026-09-01
**Related:** `design/task-cards.md`, `design/task_card_project_binding.md`,
`app/api/task_cards.py` (launch snapshot), `app/models/task_run.py`
(`card_snapshot` / `permissions_snapshot`).

## Motivation

Two user-reported gaps, both rooted in the same confusion:

1. Editing a card's writable scope silently requires re-signing, but the
   requirement is only surfaced passively (a banner / status hook). There
   is no explicit acknowledge step at the moment the change is made.
2. It is unclear — to authors, not just observers — whether editing a deck
   card affects (a) runs currently executing, (b) cards attached to
   conversations but idle, or (c) only future launches. Because the live
   card carries no version, drift between "what a run executed" and "what
   the card says now" is invisible.

This doc first pins down the *actual* propagation semantics from the code,
then specifies the two features.

## Ground truth: what an edit propagates to

There are three distinct objects. Only one is live-mutable.

| Object | What it is | Mutable? |
|---|---|---|
| **Deck card** | The one canonical definition per card id, in `project_dir/task_cards/*.json` | Yes — this is what the editor changes |
| **Binding** (`TaskBinding`) | A thin pointer `{chat_id, card_id, run_id}` anchoring a card to a chat. References the card **by id**; holds no copy | Immutable pointer |
| **Run** (`TaskRun`) | A launched execution. Card is **snapshotted** at launch (`card_snapshot` + `permissions_snapshot`, `app/api/task_cards.py:732`) | Snapshot frozen at launch |

Propagation of a deck-card edit:

| Target | Affected by edit? | Why |
|---|---|---|
| Run in progress | **No** | Executes `card_snapshot`, captured at launch |
| Completed run (replay / audit) | **No** | Immutable snapshot |
| Resume / retry of a launched run | **No** | Replays the *source run's* snapshot tree, not the live card |
| Idle binding in a chat (staged, `run_id = null`) | **Yes** | Binding is a pointer by id; card read live at launch time |
| Any new launch (this chat or new) | **Yes** | Reads the live deck card, snapshots fresh |
| Duplicated card (separate id) | **No** | Genuine independent copy |

**Key correction to the mental model:** nothing is "copied out" to a
conversation. A card attached to a chat is a *pointer*, not a copy. An idle
(staged, never-launched) binding therefore silently picks up edits the next
time it is launched. The only true frozen copies are the per-run snapshot
and an explicit **Duplicate** (new card id).

This is why existing deployed runs are already immune to edits — and
exactly why versioning is worth adding: the live card is a single mutable
definition with no history, and the only frozen copies are buried inside
run snapshots where the inventory cannot see them.

## Feature 1: Version tags

### Model

Add a monotonic integer `version` to `TaskCard` (default `1`). It is
**not** a user-edited field; it is bumped by the storage layer.

- `app/models/task_card.py`: `version: int = 1`.
- `frontend/src/types/task_card.ts`: `version: number` on the card type.

### Bump policy

Bump `version` on any save whose **structure or scope** changed. Do **not**
bump for pure-metadata edits (name / description only). Rationale: the
version exists to track drift that matters to execution and signing;
churning it on a description typo makes "did the behaviour change?"
unreadable. Implementation compares the incoming block tree + scope against
the stored one before writing (a normalized structural compare, excluding
`updated_at`).

Simpler fallback if the structural diff proves fiddly: bump on every save.
Acceptable but noisier. Decision: **bump on structure/scope change only**;
fall back to bump-on-every-save only if the compare is unreliable.

### Snapshot stamping

At launch, record the card's `version` into `card_snapshot`
(`app/api/task_cards.py`, alongside `name` / `description` / `root`). A run
tile can then show `executed v3 · deck card now at v5`, making drift
visible. Runs predating this field show no version (treated as unknown).

### Surfacing

- `TaskCardsLibrary` inventory: show the current `version` per card.
- Run tiles: show the snapshot's `version` and, when the deck card is
  ahead, a subtle "deck card updated since this run" affordance.

## Feature 2: Re-sign dialog on scope change

### Trigger

On save from `TaskCardEditor`, when the save **widens or otherwise changes
writable scope / shell grants** (the class of change that invalidates an
existing signature), show a modal *before* the save is committed — or
immediately after, but blocking, so the acknowledge is unavoidable.

Reuse the existing signature-status plumbing rather than reinventing the
"does this need signing" computation:
- `frontend/src/components/TaskCard/useCardSignatureStatus.ts`
- existing tests: `cardSignatureSurfacing.test.tsx`,
  `proposalSigningNotice.test.ts`, `tileSignCommandSurfacing.test.ts`,
  `signatureSurfacingWiring.test.ts`.

### Enforcement ground truth (traced 2026-09-01)

A launch is **never blocked** by a missing/stale signature today. The gate
lives at *execution* time, not launch time: `app/agents/task_executor.py`
(§ ASR F-001, `authorize_scope`) routes each privilege-bearing block's
scope through the signed-approval chokepoint, and an unauthorized
escalation is **silently clamped to floor** — `shell_commands` dropped,
write flags stripped — while the task still runs, un-escalated. This is
**surface-agnostic**: interactive launch, the scheduler
(`app/agents/task_scheduler.py`, cron/trigger), and Call-block launches
(`block_executor._execute_call`) all hit the identical silent clamp. There
is currently **no differentiation by launch context**, and the clamp is the
defect the dialog/gate is meant to make legible.

Signing is **out of band**: the user runs `ziya-approve` in a terminal;
approvals are keyed by block id + scope and cannot be minted by the agent
(root key). This is why `useCardSignatureStatus` refreshes on window focus.

### Launch-context policy (the three-way taxonomy)

The silent clamp is replaced by a context-dependent decision. Context is a
property of *how the launch was invoked*, not of the card:

| Context | HITL present? | Behaviour on unsigned/stale scope |
|---|---|---|
| **Interactive** (user clicks Run / launches from editor or tile) | yes | **Launch gate dialog**: sign-first, or proceed clamped with acknowledge. Never silent. |
| **Batch with a HITL channel** (Call block inside an interactive orchestrator run) | deferred | **Hold** the run at the block (reuse `held_at_block_id` / infra-hold + resume-from-block), surface a fixable prompt; do not silently clamp. |
| **Headless batch** (scheduler cron/trigger; no attached session) | no | **Fail loudly** (held-with-reason, not silent clamp) — or escalate to a notification/visual path. A neutered silent run is the worst outcome here. |

The mechanism for the middle and right columns already exists — the
**held-run** machinery (`held_at_block_id`, `held_reason`, infra-hold,
resume-from-block) and the Ask-block gate. The work is to (a) thread a
launch-context signal into `_launch_run_for_card` / the scheduler, and (b)
replace the silent floor-clamp with the context-appropriate decision at the
`authorize_scope` seam.

### Dialog contents

1. Plain statement that the scope change requires re-signing. Per the
   enforcement trace above, an unsigned card **does launch today, silently
   clamped to floor** — so the interactive dialog's job is to convert that
   silent clamp into an explicit *sign-first or proceed-clamped* choice,
   not to describe a launch block that does not exist.
2. The exact signing command / instructions (`ziya-approve …`), copyable.
3. If versioning (Feature 1) is in: "this save bumps vN → vN+1".
4. A statement of blast radius, derived from the semantics above:
   *"This affects future launches and any idle staged copies in
   conversations. Runs already in progress and completed runs are
   unaffected — they executed a frozen snapshot."*
5. An **acknowledge checkbox** ("I understand this card must be re-signed")
   gating the confirm button.

### Why the dialog needs the version

The dialog can state the concrete transition ("v5 → v6, re-sign required")
only if the version exists. This is why Feature 1 lands first.

## Sequencing

1. **Version-tag backend** — `version` field, bump-on-change in storage,
   stamp into `card_snapshot`, expose in API + TS type.
2. **Version surfacing** — inventory + run-tile display.
3. **Re-sign dialog** — modal on scope-changing save, consuming the version
   for its "vN → vN+1" line.

Each step is independently shippable and testable.

## Test coverage (per "Tests for Everything")

- Storage: save with structural change bumps `version`; metadata-only save
  does not; concurrent-safe monotonicity.
- Launch: `card_snapshot.version` equals the card's version at launch;
  subsequent deck edits do not change an existing run's snapshot version
  (the immutability guarantee, asserted end-to-end).
- Dialog: a scope-widening save opens the modal; confirm is disabled until
  the checkbox is checked; a metadata-only save does **not** open it
  (negative assertion paired with the positive).
- Semantics regression: an idle binding launched after an edit executes the
  **new** version; a resume of a prior run executes the **old** snapshot
  version — the two-sided assertion that encodes the propagation table.

## Non-goals

- Full version *history* / rollback of card definitions. `version` is a
  drift marker, not a VCS. Run snapshots already preserve the executed
  definition for audit; reconstructing an arbitrary past deck version is
  out of scope.
- Cross-project card import re-validation (tracked separately in
  `design/task_card_project_binding.md` point 2).
