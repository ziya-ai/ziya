# Task Card: roster completeness assertion

**Status:** backend implemented (§10 steps 1–4: item_key end to end, plan-time
refusals, exit assertion, D9). Shared key derivation in
`app/utils/roster_keys.py`; tests in
`tests/test_repeat_roster_require_complete.py` (41 tests, green; 430 green
across the twenty-nine suites that touch the loop, resume and run-storage
paths — no regression from `item_key` or the outcome tracking). OUTSTANDING:
editor surface (§3.5, incl. the D2 mode-switch fix) and the R3 sequencing
decision (member-level resume, §5) — `repeat_require_complete` is
deliberately dark in the UI until both are resolved, so the executor code is
inert for every existing card. Originally reviewed against `b38a4ef9`
(2026-08-27).

**Non-vacuity evidence** (§6's requirement that each assertion fail against
unpatched code):

- Pre-patch, 18 of the 41 failed on the not-yet-existing symbols.
- `roster_keys.py` mutated twice — dropping the duplicate-key check and
  dropping the unkeyed-item check each turned 4 tests red, the seam test
  among them both times.
- The exit assertion cannot be mutated in place (`block_executor.py` is not
  directly writable), so its non-vacuity rests on a stronger construction
  instead: `test_one_failed_member_fails_the_block_naming_it` and
  `test_unset_preserves_the_silent_hole` run the SAME fixture — a serial
  loop under `on_failure=continue` whose middle member fails — and differ
  only in `repeat_require_complete`. Without it the block returns
  **passing**, because `failed` is read off the last iteration and two
  members ran after the failure. That pair is simultaneously the regression
  guard, the proof the assertion causes the failure, and a live
  demonstration of the §2 defect.

**Scope note on when the assertion runs:** normal loop exit only. An infra
fault re-raises and a cancellation raises `BlockExecutionCancelled`, both
before the exit path, so a held or cancelled run is never reported as a
coverage shortfall — a hold preserves its resume position and has not
finished making its claim about coverage.

**Two additions the proposal did not anticipate, both found by running it:**

1. `_plan_iterations`' count-FALLBACK path (an unparseable literal source)
   had to refuse under `require_complete` as well. Left alone it produces
   anonymous `item=None` iterations, so the assertion would have diffed an
   empty key set against itself and passed — the assertion silently vacuous
   on exactly the malformed input it should refuse.
2. §3.1 states duplicate keys and unkeyed dict items are plan-time errors,
   but §6 also requires "unset → byte-identical behaviour". Both cannot hold
   unconditionally: existing cards legitimately fan out over `["a","b","a"]`.
   Resolved by gating the REFUSALS on `repeat_require_complete` while
   recording `item_key` unconditionally; pinned by the regression test.

**Scope:** one primitive — let a `for_each` Repeat declare the set it must
cover, and fail when it doesn't. Nothing else.

---

## 1. Why a primitive rather than another validator

A six-phase competitive-landscape study surfaced 22 defects over five
sessions. **21 of them were the same failure**: a missing question, not a
wrong answer. Every artifact was well-formed. Every coverage field was
accurate about what it contained. Nothing errored anywhere. The gap was
always between what the card *asked for* and what would have to be true for
the output to mean what a reader assumes.

Fixing them took five external validator scripts:

| | bytes |
|---|---|
`scripts/complandscape_{matrix,registry,reintegration,corpus,diff}.py` | 128,097 |
their test suites | 115,807 |

Those five scripts implement **three** distinct mechanisms, hand-rolled five
times: a frozen schema with stable ids; a completeness assertion over a
declared plan; run versioning with a pointer and a resolver. None of it is
domain logic. It is task-card infrastructure that does not exist, so each
phase reinvented it — and reinventing it introduced a *new* defect (CL6 read
the previous run's directories, caused by the versioning work itself).

The external-validator pattern demonstrably works: the study is now correct.
The argument for moving it into the executor is that it took five sessions
and 244 KB, it only worked because someone kept asking "is the target set
complete?", and the next campaign starts from zero.

---

## 2. The finding that reframes the design

The two worst coverage holes in the study were **not** caused by a cap.

| phase | cap | roster | dispatched | produced |
|---|---|---|---|---|
CL1 Stage 2 | `repeat_max=20` | 20 subsystems | 20 | **17** |
CL2 Stage 2 | `repeat_max=30` | 27 roster entries | 27 | **26** |
CL3 run `m3` | uncapped | 378 slices | 378 | **377** |

In all three, **no clipping was possible** — the cap equalled or exceeded the
roster, so `items[:repeat_max]` planned every item — and in all three the
loop returned and the deck proceeded to its next stage. The missing
artifacts were *production* failures: an item was planned and no file
appeared. CL1's three unaudited subsystems went into a `coverage_holes`
field nothing read (its own reason text reads "no `10-ziya/<x>.json`
produced by any auditor"); CL2's missing `aider` dossier meant no matrix
column, and "absent from the matrix" reads downstream as "not a
competitor".

Precision on what is and is not established here: the no-clipping claim is
arithmetic and certain. That the loops returned and the decks proceeded is
observable from the downstream artifacts (a merged ledger, a built matrix, a
CL3 run that reached its merge stage). Whether the *individual* iterations
ended `passed` or `failed` is **not** verifiable — those run records are
encrypted. In a parallel Repeat a failed iteration does not halt the loop
anyway, so "the loop succeeded while the set was incomplete" holds at loop
level either way, which is the defect. §4 turns on this distinction.

**Consequence for the design: the assertion must be about what was
*produced*, not what was *dispatched*.** A dispatch-level check — which is
what a cap fix gives you — would have caught none of these three.

The cap defects are real and separate:

- **D1** CL5 `repeat_max=60` clipped a 108-item roster; 48 never run, run
  reported a complete pass.
- **D2** `repeat_max` survives a mode switch. Set as an `until` bound, it is
  neither rendered nor editable in `for_each` mode, so it acts as an
  invisible clip. This is D1's mechanism.
- **D3** CL4 `repeat_max=112` — the previous queue's exact size, hardcoded.
- **D4/D5** CL1 `max=20`/20 and CL2 `max=30`/27 — sitting exactly at the
  boundary, one addition from silent loss.
- **D9** The wide-fan-out concurrency warning cannot fire for an uncapped
  `for_each`: `planned = block.repeat_count or block.repeat_max or 0`
  (`task_card_validation._check_repeat`), so the shape most needing the
  warning is the one that never gets it.

---

## 3. Design

### 3.1 The load-bearing prerequisite: iteration → item identity

**This does not exist today and is the real work.**
`app/models/task_run.IterationSummary` records `index`, `status`,
`signature`, `duration_ms`, `tokens`, `has_artifact`, `replayed`. An
iteration's identity is its *ordinal position only*. `_record_iteration` is
not even passed the item (`block_executor._record_iteration(block, ctx,
index, artifact)`).

Without item identity you can count a shortfall but never *name* it, and a
gap-fill cannot target the missing members. That is exactly why closing
CL3's one missing slice needed a hand-authored card.

Proposed:

- `IterationSummary.item_key: Optional[str]` — stable string identity.
  `model_config = {"extra": "allow"}` already, so this is
  forward-compatible, and `append_iteration_summary` is called
  unconditionally (outside the `keep_full` branch), so keys survive the
  50-pass artifact retention cap.
- `Block.repeat_item_key: Optional[str]` — dotted path into the item.
  Defaults to `str(item)` when the item is a scalar. **Every observed case
  is a scalar** (slice ids, capability ids, tool slugs), so the default
  covers all of them.
- A dict/list item with no declared key path is a **plan-time error**, not
  a guess.
- Duplicate keys within a roster are a **plan-time error** — an ambiguous
  roster cannot be asserted over.

Record `item_key` unconditionally, not only when the assertion is on: it
independently makes the run map's iteration dots meaningful and is what a
targeted re-dispatch would key on.

### 3.2 The assertion

`Block.repeat_require_complete: bool = False`. Opt-in; default preserves
today's behaviour byte-for-byte.

When true:

1. The resolved roster **before any clipping** is the expected set.
2. `repeat_max` finite + `require_complete` is a **contradiction** — a cost
   ceiling and a completeness requirement cannot both hold. Refuse at
   validation time (error, not warning).
3. At loop exit, diff expected keys against keys whose terminal status is
   `passed`. On shortfall, return a **failed** artifact naming the missing
   keys, and record the full list in a structured field.

Point 3 is deliberately a **failure**, not a decision line. The existing
truncation path (`_execute_repeat` exit, reading `ctx.roster_truncations`)
appends a decision and does not fail, because a cap is legitimate.
`require_complete` is the author stating that partial is not success, so the
enclosing container's `on_failure` should govern.

### 3.3 Executor changes

Anchored on function names; line numbers are hints only.

| site | change |
|---|---|
`_plan_iterations` (~2080) | compute `item_key` per descriptor; refuse cap+require_complete; refuse duplicate keys |
`_run_one` → `_record_iteration` (~2139) | thread `item_key` into `IterationSummary` |
`_execute_repeat` exit (~1919) | when `require_complete`, diff and fail; emit an event; record on block state |
`ExecutionContext` | a `roster_shortfalls` store mirroring the existing `roster_truncations` shape (`{roster, produced, missing: [keys]}`) |

`set_block_planned_iterations` already persists the `for_each` denominator
to `TaskRunBlockState.planned_iterations`, so the numerator/denominator pair
is half-built already.

### 3.4 Validation hook — `task_card_validation._check_repeat`

- `require_complete` + finite `repeat_max` → **error** (contradiction)
- `require_complete` + duplicate keys in a literal roster → **error**
- `require_complete` on `count`/`until` → **error** (no roster exists)
- fix D9: for `for_each`, derive `planned` from the literal roster size when
  known (`_literal_roster_size` already exists), and for a templated source
  say "roster-sized" rather than silently reading the cap

### 3.5 Editor surface — `RepeatBlockEditor.tsx`

The `for_each` section (~line 81) gains a "must cover the whole roster"
checkbox beside the existing cap input. When checked, the cap input is
disabled with an inline explanation that the two contradict.

Plus the cheap fix for **D2**: clear `repeat_max` on mode switch, or scope
it per mode. D2 exists *because* a field was unauthorable in one mode, so
any fix that leaves it invisible-but-live is not a fix.

---

## 4. What this does NOT catch

**An iteration that reaches `passed` while producing nothing.** This
primitive's notion of coverage is terminal status. If an agent reports
success and writes no file, the roster looks covered.

That matters for the three headline cases in §2. Whether CL1's three
subsystems, CL2's `aider`, and CL3's `cline::visualization` ended `failed`
or `passed`-with-no-output **cannot be determined** — those run records are
encrypted and unreadable from the working tree. So:

- if those iterations failed → this primitive catches all three
- if they passed while writing nothing → they need the *output contract*
  primitive (declare an output path and required shape; validate per
  iteration before recording `passed`)

Honest accounting of the nine:

| caught by this primitive | |
|---|---|
definitely | D1, D3, D4, D5 (cap clipping is dispatch-level and decidable), D9 (validation logic), D2 (with the editor fix) |
only if the iteration ended non-`passed` | D6 (CL1 subsystems), D7 (CL2 aider), D8 (CL3 slice) |

The output contract is the natural successor and the two compose: contract
decides whether an iteration really passed, roster decides whether the set
is covered.

---

## 5. Adjacent, deliberately out of scope

**Member-level resume.** Once `item_key` is recorded, "re-run the members
with no passed iteration" is mechanically derivable. Today
`resume_targets.resolve_iteration_resume` correctly *refuses*
iteration-level resume for parallel loops — an index has no ordering
meaning there, so "resume at 3" would just run fewer iterations while
reporting the loop complete. But *member*-level resume is well defined for
a parallel loop, because the missing keys are exactly the work to redo.

This is the follow-on that would have removed the need for a hand-built
gap-fill card, and it is coupled to risk **R3** below. Not in this scope.

---

## 6. Test plan, mapped to defects

Backend (`tests/`):

| test | defect |
|---|---|
cap + `require_complete` → validation error | D1, D3, D4, D5 as a class |
duplicate item keys → plan-time error | (new hazard the primitive introduces) |
dict item, no `repeat_item_key` → plan-time error | (same) |
roster of 5, one iteration fails → block **fails** naming that key | D6/D7/D8 shape |
roster of 5, all pass → block passes, **no decision noise** | negative control |
`require_complete` unset → behaviour byte-identical to today | regression guard |
`item_key` recorded for every iteration incl. `replayed` ones | prerequisite |
`item_key` survives past the 50-pass retention cap | prerequisite |
wide-fan-out warning fires for uncapped `for_each` with a literal roster | D9 |
old `IterationSummary` with no `item_key` → treated as unknown coverage, no crash | backward compat |

Frontend (`frontend/src/components/TaskCard/__tests__/`):

| test | defect |
|---|---|
checkbox rendered only in `for_each` mode | — |
checking it disables the cap input and explains why | contradiction is visible |
mode switch `until` → `for_each` clears `repeat_max` | D2 |

**Seam test.** The executor's assertion and the validator's contradiction
check must agree on what `require_complete` means. Two suites that each pass
against their own reading of one field is the failure shape that produced
the editor-shows-3 / runtime-plans-1 defect earlier in this work.

Existing suites that must stay green: `test_repeat_roster_truncation_visibility.py`,
`test_repeat_cap_scope_loss_class.py`, `test_block_executor_failure_policy_and_foreach.py`,
`test_task_card_validation.py`, `test_api_launch_validation.py`,
`repeatConcurrencyControl.test.tsx`, `repeatUntilMaxControl.test.tsx`.

Every new assertion must be confirmed to **fail** against unpatched code
before the change lands. Several of these would otherwise pass against a
deleted assertion rather than a working one.

---

## 7. Risks and open questions

**R1 — Coverage is status-shaped, not output-shaped.** §4. The primitive
will read as "guarantees complete coverage" and does not. Documented above;
worth naming in the field's own help text too.

**R2 — Key derivation on non-scalar items.** Refusing at plan time is the
safe default but may be too strict for a legitimate list-of-objects roster.
No observed case needs it, so refuse until one appears.

**R3 — RESOLVED BELOW 50 ITERATIONS, OPEN ABOVE.** *(Updated after the
resume work landed; measured against HEAD, not predicted.)*

The concern was that failing the block leaves the operator without a cheap
remedy. A remedy now exists and is production-wired:
`resume_targets.parallel_replay_indices` (called from
`app/api/task_runs.py:575`) makes a **block-level retry** of a parallel loop
replay every banked iteration and re-run only those that did not finish.

The two mechanisms compose exactly, which is worth stating because it was
not designed jointly. The assertion's shortfall set is
`iter_outcomes[i] != "passed"`; replay banks `status == "passed" AND
has_artifact`. So the iterations a retry re-runs *are* the members the
assertion named. **No deadlock** — my earlier worry that a
passed-but-produced-nothing iteration would be re-banked forever is real in
principle but is exactly the §4 limit, not a new failure: the assertion
never fails on that case in the first place, so there is nothing to
deadlock.

**The open half is scale, and it is sharp.** `PASS_ARTIFACT_RETENTION_CAP`
is a hardcoded 50 (`block_executor.py:50`, not env-overridable) and applies
per block to parallel loops. Passes 51+ get `has_artifact=False`, and
`parallel_replay_indices` requires an artifact — correctly, since replaying
an absent one would drop its outputs while counting it done. Measured:

| roster | passed | banked | **re-run** |
|---|---|---|---|
| 20 (the docstring's motivating case) | 19 | 19 | **1** |
| 378 (CL3's actual fan-out) | 377 | 50 | **328** |

So the remedy works at the scale it was written for and largely evaporates
at the scale the assertion is most valuable. It degrades *silently*: the
operator clicks retry expecting "re-run 1" and pays for 328. Three possible
responses, none obviously right:

* raise or make the cap configurable — trades disk for resumability, and
  the cap exists because a 378-iteration artifact set is not free;
* retain artifacts for *keyed* iterations regardless of pass count — the
  assertion already implies the author cares about members individually;
* make the shortfall path itself the resume unit — i.e. §5's member-level
  resume after all, now cheap to express because `item_key` exists.

**No test exercises this.** The largest roster in any resume suite is 20
(`test_resume_through_call.py`, `test_resume_mid_loop.py`) — under the cap,
so the degradation is invisible to the suite. A test at 60 would pin it.

**R4 — Frontend cost unmeasured.** I have not estimated the editor work.
D2 exists because a field was unauthorable in one mode, so "the backend
supports it, the editor will follow" is the specific mistake to avoid.

**R5 — Retention interaction: the shallow reading missed the real one.**
I flagged only whether `item_key` survives the cap (it does —
`append_iteration_summary` is unconditional, `block_executor.py:2310`).
The consequential interaction is the one in R3 above: the cap governs
*resumability*, not just artifact availability. Recorded here because the
original note was correct and unhelpful — it checked the field and not the
mechanism the field feeds.

---

## 8. Non-goals

- No schema/output-contract language. That is a separate primitive with real
  design risk, and conflating them makes both harder to review.
- No run-scoped output directories or `{{run_dir}}`.
- No executor-stamped provenance.
- No changes to `count` or `until` semantics.
- No re-clustering of anything: the `for_each` source stays exactly as
  authored, and the assertion is purely additive.

---

## 9. Verified code facts

Checked at `b38a4ef9`, 2026-08-28. Function names are the durable anchor;
line numbers drift.

- `app/models/task_card.py` — `class Block` at 254, `model_config =
  {"extra": "allow"}` at 263, the nine `repeat_*` fields at 313-333. The
  `extra="allow"` means a new field is forward-compatible with cards written
  by older versions. (Note there are three `extra="allow"` declarations in
  this file — `ScopeEntry`, `ArtifactPart`, `Block` — so grep alone is
  ambiguous; 263 is `Block`'s.)
- `app/agents/block_executor.py`
  - `_plan_iterations` (~2080) — `for_each` branch clips
    `items[:repeat_max]` and records
    `ctx.roster_truncations[block.id] = {roster, dispatched, dropped}`
    (~2119).
  - `_execute_repeat` — persists the denominator via
    `set_block_planned_iterations` for `for_each` only (~1532); reads
    `roster_truncations` at exit (~1921) and appends **decisions**, not a
    failure.
  - `_record_iteration` (~2139) — not passed the item;
    `append_iteration_summary` (~2163) runs unconditionally, outside the
    `keep_full` guard (~2151).
  - `ExecutionContext.roster_truncations` (~134) — `Dict[str, Dict[str, Any]]`.
- `app/models/task_run.py`
  - `IterationSummary` (~105) — `index`, `status`, `signature`,
    `duration_ms`, `tokens`, `has_artifact`, `replayed`. **No item
    identity.** `extra="allow"`.
  - `TaskRunBlockState.planned_iterations` (~241).
- `app/storage/task_runs.py:311` — `set_block_planned_iterations`, writing
  `state.planned_iterations` (~330).
- `app/utils/task_card_validation.py`
  - `_literal_roster_size` (~194) — literal JSON arrays only; returns None
    for a templated source.
  - `_check_for_each_cap` (~210) — warns on a clipping cap.
  - `_check_repeat` (~252) — contains the D9 bug:
    `planned = block.repeat_count or block.repeat_max or 0` (~295).
- `frontend/src/types/task_card.ts:148-159` — the mirrored `repeat_*` fields.
- `frontend/src/components/TaskCard/RepeatBlockEditor.tsx` — three mode
  sections at ~41 (`count`), ~52 (`until`), ~81 (`for_each`).
- `app/utils/resume_targets.py` — `resolve_iteration_resume` refuses
  iteration resume for `repeat_parallel` loops, by design.

---

## 10. Suggested order of work

1. ~~`item_key` end to end~~ — **done.** Model, executor threading, tests.
   Recorded unconditionally, so the run map's dots are nameable for every
   `for_each` loop whether or not the assertion is on.
2. ~~Plan-time refusals~~ — **done.** Cap contradiction, duplicate keys,
   non-scalar without a key path, plus one the proposal missed: an
   unresolvable source under `require_complete`, which would otherwise fall
   through to anonymous count iterations and make the assertion vacuous.
3. ~~The exit-time assertion and its failure artifact~~ — **done**, plus
   `ctx.roster_shortfalls` and a `roster_shortfall` event.
4. ~~D9~~ — **done.** D2 is still open (it lives in the editor).
5. **Editor surface — open.** The checkbox, the cap-input disable, and D2's
   mode-switch clearing. Held back deliberately rather than incidentally:
   R4 warns against "the backend supports it, the editor will follow", and
   D2 exists *because* a field was live but unauthorable — so shipping a
   second field in that state would repeat the exact defect. Until this
   lands, `repeat_require_complete` is settable only by a card written by
   hand or by a tool.
6. **R3 half-answered by the resume work; the remaining half is the
   retention cap.** See the rewritten R3 in §7 for the measurement. Summary:
   a block-level retry of a parallel loop already re-runs exactly the
   members the assertion names — the two mechanisms' criteria align — so at
   ≤50 iterations the cheap remedy exists and R3 is settled. Above 50,
   `PASS_ARTIFACT_RETENTION_CAP` stops artifacts being banked and a retry
   re-runs everything past the cap: measured 328 of 378 for CL3's fan-out.
   So the sequencing question narrows usefully — it is no longer "build
   member-level resume or not", it is **"what makes a >50 fan-out resumable"**,
   and the three candidate answers (raise/configure the cap, retain by
   keyed-ness, or make the shortfall set the resume unit) are all cheaper to
   evaluate than the original open-ended question. Still a decision for the
   reviewer, but a smaller one.
7. **Add a resume test above the cap.** Nothing in the resume suites exceeds
   20 iterations, so the degradation in §7/R3 is invisible to the tests. A
   60-iteration parallel loop with one failure would pin it — and would fail
   today's code in the sense of documenting 10 banked / 50 re-run, which is
   worth having on the record before anyone tunes the cap.
