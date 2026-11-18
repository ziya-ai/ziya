# Diff-apply hold: the turn waits for its diffs to be applied

Status: implemented 2026-11-11 (backend and frontend indicator).
Supersedes the 2026-11-04 `await_diff_apply` tool design (a consent-panel
wait the model called), which was never wired: the panel's Approve button
had no sensible meaning, and the model should not have to ask.

## Behaviour

In the web chat, when a response ends cleanly (`end_turn`/`stop`, no open
fence) and the turn's assistant text contains closed ```diff fences, the
executor does not end the turn.  It holds the tool chain open, making no
model call, until one of:

| Event | What the model gets, in the same chain |
|---|---|
| Every diffed file applied cleanly | Per-file report; it continues (tests etc.), or says in one line it has nothing left |
| Any apply partial or failed | That report at once (failed hunks and why), without waiting for the rest |
| User sends a message | The message as feedback, prefixed with the report so far |
| Stop / disconnect | Nothing; the turn ends |

No timer.  An idle hold costs nothing; the resumed call pays the same cache
cost a new user message would.

Never held: CLI, Task Cards, `ziya task`, delegates.  The opt-in is
`stream_with_tools(hold_for_diff_apply=True)`, passed only by the web chat
call in `app/server.py`.

## Pieces

- `app/utils/apply_ledger.py` — per-conversation apply results with a
  monotonic `seq`; `expected_from_text` (closed fences → `{path, count}`),
  `turn_text` (assistant turns since the turn or last wait, so a diff emitted
  before a tool call counts), `wait_state`, `format_report`, `chip_view`.
- `/api/apply-changes` → `apply_ledger.record(...)`.  An apply that lands
  while the model is still typing counts, because the wait counts results
  after the seq at turn start.
- Executor: the hold sits at the no-tools turn-end branch, before the
  pre-end feedback check.  After each wait the seq and conversation offset
  advance, so a second round of diffs in the same turn waits afresh.
- Stream event `diff_wait`: `{state: waiting|done, total, remaining,
  remaining_paths, outcome?}` — re-sent on every change; forwarded by
  `app/server.py`.

## A reply during the wait

A message ends the wait it arrives in, but not the turn's claim on its
diffs.  The executor records where the reply landed (`_dw_bounds`) and the
next hold in the turn builds its expected set with
`apply_ledger.expected_for_turn` over the segments on either side: diffs
from before the reply are still awaited, and applies made before it still
count (`_dw_since` moves only when a wait ends complete or failed).  A file
diffed again after the reply is taken as revised: only the newest fences for
that path are awaited, and an apply of a replaced fence does not count.

When the response after a reply has no diffs and nothing is left unapplied,
the turn ends without a second "all applied" round.

Because a reply no longer releases the turn, the wait has an explicit way
out: `StopStreamButton` renders "Stop waiting" while a turn is held (the
ordinary stop path).  The next turn's note lists what was left unapplied.

## Frontend

`frontend/src/utils/diffWaitStore.ts` holds the per-conversation state;
`chatApi.ts` feeds it from `diff_wait` events.  While a turn waits,
`StreamedContent` shows a chip in place of the spinner ("Waiting for 1 of 2
diffs to be applied; continues when done", remaining paths on hover) and
`StopStreamButton` renders "Stop waiting".  The state is cleared when the
conversation's stream ends, which covers a disconnect mid-wait.  The
composer works as usual.  In the conversation list a waiting row shows the
green check ("Waiting for you to apply its diffs") in place of
"Processing…" (`useDiffWaitLookup`, `awaitingApply` on the row).

## Between turns

The hold covers a turn that is still open.  A diff applied after its turn
ended (the hold was stopped, the tab was closed, the server predates the
hold) used to reach nobody.  `apply_ledger.begin_turn`, called by the
executor when the next web-chat turn starts, appends a note to the incoming
user message: every apply result the model has not been shown, and each
diff of its last response with no apply recorded since that turn began.
Results a hold reported are marked (`mark_state_reported`) and not repeated.

## Which diff an apply belongs to

Each apply is recorded with `diff_key`: a hash of the diff's added and
removed lines.  Headers, hunk headers and trailing whitespace are left out,
since the Apply button trims the text and rewrites hunk headers.  A fence
in the model's text gets the same key.  The hold ignores an apply whose key
belongs to a diff from before the turn, so an old diff for the same file
cannot end it.  The between-turns note labels such an apply ("a diff from N
responses back") and still lists the newer diff as having no apply recorded.

The client merges a headerless continuation fence into the headed diff
before it and gives the pair one Apply button
(`chainHeaderlessContinuationDiffs` in `MarkdownRenderer.tsx`).  The ledger
applies the same rule (`_chains_to_previous`), so the pair has one key and
counts as one diff.  The two rules are held together by one fixture table,
`tests/fixtures/diff_continuation_cases.json`, read by
`tests/test_diff_continuation_parity.py` and
`frontend/src/components/__tests__/diffContinuationParity.test.ts`.  A change
to either rule needs a case there, and both suites must pass.

## A diff revised within one response

The model often writes a diff and then a corrected version of it further
down the same response.  The UI fades the earlier one ("Superseded by a
later revision below", `findSupersededDiffParts` in
`frontend/src/utils/diffUtils.ts`).  Until 2026-11-17 the ledger counted
both, so the chip read "Waiting for 2 of 2 diffs" under a response offering
one, and the turn resumed only when both versions had been applied.

`_diff_files` now marks such a fence with the client's rule
(`_superseded_indices`: a later diff for the same file that overlaps its
lines, or repeats its change body at another anchor; not a
removal-then-addition pair).  `expected_from_text` leaves a marked fence
out of the expected set and returns its key in `superseded`.
`expected_for_turn` adds those keys to its own `superseded`, which the
executor already passes to `wait_state` as foreign, so applying the faded
version does not stand in for the live one.  `begin_turn` rules them out
the same way and no longer lists the faded version as "no apply recorded".

The two rules are held together by `tests/fixtures/diff_supersede_cases.json`,
read by `tests/test_diff_supersede_parity.py` and
`frontend/src/utils/__tests__/diffSupersedeParity.test.ts`.  The fixture
covers numeric hunk headers.  Headers that carry only a section hint are
rewritten by the client before detection (`synthesizeMissingHunkHeaders`,
not exported); the server's copy (`_synth_hunk_headers`) is tested on its
own in `tests/test_await_diff_apply.py`.

The rule is a heuristic and the ledger inherits its misses.

A removal-then-addition pair is told from a revision by what the two diffs
remove.  Until 2026-11-17 the test was by counts alone (the earlier diff
removes something and adds at most one line), so a one-line replacement
revised later read as a sequence: it was not faded, and both versions were
awaited.  Two diffs that remove the same source line are now a revision
whatever the counts, since once the earlier is applied that line is gone and
the later could not remove it again.  The CLI applicator already used this
test (`CLIDiffApplicator._is_sequential_pair`); it is now also in
`isSequentialPair` (`diffUtils.ts`), `_is_sequential_pair` (the ledger) and
`_diff_is_sequential_pair` (`conversation_exporter.py`, which drops
superseded diffs from exports).  The CLI's rule differs in one more way,
left alone: it has no limit on the earlier diff's added lines.

A fence the server refused at validation (`ZIYA_DIFF_REJECTED`) is not part
of this: validation runs after the stream ends, and the correction stream
that follows is not held.

An apply whose key matches no fence in the conversation (no text recorded)
counts by path, as before.  That keeps a hold from waiting forever on a
normalisation mismatch.

## Known gaps

- A partial or failed apply still ends the wait for every diff of the turn:
  the ones not yet applied are reported as `not_applied` and are not awaited
  again unless the model re-emits them.

- Undo (`/api/unapply-changes`) is not recorded: the request carries no
  conversation id.
- The post-stream diff-validation pass in `server.py` runs once the whole
  stream ends, i.e. after the model has continued past the wait.  It used to
  dry-run every fence of the turn against the file as it then was, so a diff
  that had been applied (and usually built on by a later one) was reported as
  failed and a correction requested.  Since 2026-11-14 the hook skips fences
  the ledger has a result for, and earlier fences for a file once a later one
  is applied (`apply_ledger.settled_parts`).  Still open: a broken diff is
  only caught by the user's Apply click, not before the wait starts;
  validating before the hold would need the hook called from the executor.
- The ledger is in memory.  Applies made just before a server restart are
  not reported to the next turn, and the first turn after a restart cannot
  say which diffs have no apply recorded.
