# Tool-result aging: lossless-in-record elision of old tool output on replay

Status: **design** (Tier 0 shipped; cross-turn cache fix shipped; Tier 1 not started)
Related code: `app/utils/tool_history_rewrite.py`, `app/server.py`
(`build_messages_for_streaming`), `app/models/project.py`
(`ContextManagementSettings`), `scripts/measure_context_composition.py`

## Goal

Extend the number of turns a user can work before context becomes something
they have to think about, **without discarding anything from the record**
and with the smallest possible effect on the model's efficacy.  Cost is not
the objective; runway and correctness are.

## What the store says (Sept 2026, 350 conversations ≥ 20 messages, ~49M replayed tokens)

| Bucket | Share of replayed history |
|---|---|
| Assistant prose | 36% |
| Human text | 9% |
| Tool bodies | 55% (median chat 54%, IQR 33–66%) |
| ↳ bodies > 4K chars | 29% |
| ↳ provably redundant with a later result (Tier 0) | **0.4%** |

Median long chat crosses 100K tokens at message #20 and 200K at #52 —
*before* user-pinned tree files, which the record does not hold and which
add to every turn on top.

Two findings that shape the design:

1. **Tier 0 is negligible.**  The only elision that is lossless *by
   construction* — a body literally contained in, or identical to, a later
   result — is 0.4% of history.  An earlier survey figure of 4% used the
   rule "any later read of the same path supersedes"; 84% of that set turned
   out to be *disjoint partial reads* (`offset`/`max_lines` paging) with
   `<40%` line overlap.  Eliding them would have lost content.  The persisted
   header is `file read: PATH` with no range, so this cannot be told apart
   from an edited re-read by header alone.
2. **The model reads files in ranges because it intends to use those exact
   lines.**  1,300+ such blocks.  Any age-based scheme must treat file-content
   bodies more conservatively than command/fetch/search output.

## Tiers

### Tier 0 — redundant bodies (shipped)

`plan_elisions()` in `tool_history_rewrite.py`.  On replay, a tool body is
replaced by a one-line note if (a) it is a read of path P and a LATER read of
P contains it verbatim, or (b) an identical body for the same tool recurs
later.  Bodies under `MIN_ELIDABLE_CHARS` (200) are left alone.  Nothing is
summarised.  Per-project switch `contextManagement.elide_redundant_tool_results`
(default on); env kill switch `ZIYA_DISABLE_TOOL_RESULT_ELISION=1`.

Cache note: a Tier 0 elision changes the bytes of an *older* message at the
moment the superseding result arrives, so that turn misses the cached prefix
from the elided message onward.  It is monotone (a body once redundant stays
redundant) and rare (0.4% of history), so the cost is an occasional one-turn
miss rather than a per-turn tax.  Acceptable; it removes stale file copies,
which is an efficacy win independent of size.

### Tier 1 — aging (this design)

Old tool bodies are replaced on replay by **head + tail + a recall handle**.
The record is untouched; the model can page the full body back in one call.
This is *lossless in the record, recoverable in one call*, but it is a
weaker guarantee than Tier 0 because recovery depends on the model choosing
to recall.  It should be stated that way in the UI.

## Tier 1 design

### Eligibility and ordering

Two policies, combinable, both configurable:

* **Age (turns).**  A body is *eligible* once at least `aging_turns` human
  turns have followed the message that contains it.  File-content bodies
  (`read_path_for_block()` returns a path: `file_read`, `cat`, `sed -n`, …)
  use `aging_turns_reads` instead, default twice as long, because of finding 2.
* **Epochs (batching).**  Eligible bodies are NOT aged one at a time as
  they cross the age floor.  The history is cut into epochs of
  `aging_epoch_turns` consecutive human turns, anchored at message 0; an
  epoch is aged only once *every* message in it is eligible, and then all
  of its bodies age together.  This is the cache-complementary choice — see
  "Prompt-cache effect" below.  `aging_epoch_turns = 1` degenerates to
  per-message aging.
* **Fill (fraction of the model's input window) — latched enable only.**
  When `aging_fill_threshold > 0`, aging is *inactive* for a conversation
  until the estimated prompt fill first exceeds the threshold.  Once it has,
  aging stays on for that conversation permanently (a boolean
  `_agingActivated` on the chat record), even if fill later drops because the
  user unpinned files.  Fill decides **whether** aging runs, never **how
  much**; the age/epoch rules alone decide the latter.  `aging_fill_threshold
  = 0` means aging is always active.

    fill = (history_tokens + pinned_file_tokens + system_tokens) / max_input_tokens

  `build_messages_for_streaming` is the one place that knows all three
  numerators and the active model, so it computes the fill estimate, applies
  the latch, and passes an `aging_active: bool` to `rewrite_tool_history`.
  The rewrite stays a pure function of `(history, settings, aging_active)`.

  A *budget-driven* variant ("elide oldest-first, just enough to get back
  under the threshold") was considered and rejected: it moves the elision
  boundary a little every turn while fill hovers at the threshold, and
  couples the boundary to the pinned file set, so any pin/unpin invalidates
  the cached history prefix.  Under real cross-turn caching it is the
  worst of the three options.

Bodies under `aging_min_body_chars` are never aged: the placeholder plus
head/tail would not be smaller than the body.

### Placeholder

Inside the existing `‹tool_result …›` envelope, so the label (command, path,
tool) is unchanged and the model still knows the call happened:

    [Ziya: 8,412-char result aged out of context (message 12 of 40).
     First 15 and last 10 lines kept; 213 middle lines elided.
     Full body: recall_tool_result(handle="tr-3f9a1c04e2") — optional
     lines="a-b" or grep="pattern" to slice.]
    <head lines>
    [… 213 lines elided …]
    <tail lines>

`aging_head_lines` / `aging_tail_lines` are line counts (tool output is
line-oriented); each side is additionally capped at `aging_side_max_chars`
so a body of three enormous lines still shrinks.

### Recall handle

**Content-addressed**, not positional: `tr-` + first 10 hex of
`sha1(normalized_tool + "\0" + payload)`.  Positional handles
`(chat_id, message_index, block_ordinal)` break because the replayed history
is not the persisted record — muted turns are absent, synthetic system
messages are injected, and the frontend may send a trimmed history.  A hash
survives all of that and needs no side store: the recall tool re-reads the
current chat record (same decrypt path as `chat_read`), scans assistant
messages with `find_tool_blocks`, and returns the block whose hash matches.
Collisions within one conversation are not a practical concern; if two
blocks match, either is by definition the same bytes.

### `recall_tool_result` tool (builtin, DIRECT)

    recall_tool_result(handle, lines?: "a-b", grep?: regex, max_chars?: int)

Returns the exact persisted body (sliced if asked), wrapped in the normal
live tool_result envelope.  It is scoped to the calling conversation — the
handle is meaningless outside it — mirroring `image_recall`'s isolation
rule.  Unknown handle → honest "not found in this conversation" rather than
an error the model might read as evidence the earlier result was unsound.

### Prerequisite: the record must actually be complete

Today `tool_result_sanitizer._cap_size` truncates at `TOOL_RESULT_MAX_CHARS`
(100K) **before** the text reaches either the model or the frontend, so the
tail never reaches the record and recall could not return it.  For Tier 1 to
be lossless-in-record this cap must become a *live-view* cap: full text to
the frontend/record, capped text to the model on the live turn (which is
exactly the Tier 1 placeholder applied at age 0 for oversize bodies).  Same
shape for `agent.py`'s 5,000-char error-path truncation.  These are
`tool_execution.py` / `agent.py` changes and need diffs.

### Prompt-cache effect

**Prerequisite (done):** `prepare_cache_control` in `app/providers/bedrock.py`
and `app/providers/anthropic_direct.py` returned history unmarked when
`iteration == 0`.  Since `iteration` restarts at 0 on every user turn, the
history marker was only ever placed mid-tool-loop, so replayed history was
never cache-read at the start of a turn and, on tool-less turns, never
cache-written either (origin: commit 17101794, "first iteration … no
conversation caching needed", which conflated first-iteration with
first-turn).  The `iteration == 0` term was removed; the length floors stay.
Verify with the per-call usage log (`cache_read` on the iteration-0 line of
a second turn should now include the history, not just the system block).
Cross-turn hits require the next turn to arrive inside the provider cache
TTL (~5 min), so this pays in active sessions.

**Verified (usage ledger, this conversation, 17 Sep):** every pre-fix turn
started with ~154K `cache_write` (system only) and processed the whole
history as fresh input (41K → 118K → 147K → 153K → 165K, growing per
turn).  The first turn on the fixed server wrote 320K (system + history)
and fresh input fell to 6,140.  Pinned by
`tests/test_providers/test_cache_control_iteration_zero.py` (fails 4/10
with the old guard reinstated).

**Second prerequisite (done): the system block itself must be turn-stable.**
A two-message conversation (`91acef70`, turns 15 s apart) still read 0
from cache: the ~200K system block changed by 137 tokens between turns, so
the whole prefix was rewritten.  Three turn-varying pieces lived in it:

1. the `## Message Timing` paragraph, emitted only when `chat_history` was
   non-empty (so turn 1 and turn 2 differed) — now unconditional;
2. the bead-check nudge (appears at turn ≥ 3, vanishes when a bead is
   parked) — moved to the current user message;
3. the memory overview's live counts and domain handles — split into
   `get_memory_prompt_sections() -> (stable, volatile)`; guidance stays in
   the prefix, everything derived from live memory state is appended to the
   current user message.

Rule going forward: **anything that can differ between two consecutive
turns of the same conversation goes AFTER the cache boundary** — the
current user message (where `CurrentDateTime` and the shadow-session tag
already live) or a second, unmarked system text block.  Pinned by
`tests/test_prompt_volatile_tail.py` (system block byte-identical across
turns; volatile text still reaches the model).  The Tier 1 placeholder
follows the same rule: absolute coordinates only, no age-relative text.

**Accounting under working cross-turn caching.**  A prompt cache is a
prefix cache: a turn's miss region starts at the earliest message whose
bytes changed.  Without aging that is the two newest messages.  Any change
to an older message invalidates everything after it, so:

| Policy | Miss region per turn |
|---|---|
| per-message age (floor A) | last A messages, **every** turn |
| epoch age (floor A, epoch K) | A+K messages once per K turns → ≈ 1 + A/K amortized |
| budget-driven oldest-first | essentially the whole live history, every turn |

With A = 8, K = 8 the amortized cost is ~2 messages/turn — the same order as
no aging at all.  That is why epochs are the primitive.  Two further rules
keep it true:

* The placeholder carries **only absolute coordinates** (message index of N,
  handle).  No age-relative text ("aged 12 turns ago"), or every placeholder
  changes bytes every turn.
* The plan is computed **once per user turn** in `build_messages_for_streaming`,
  never inside the tool loop, so iterations within a turn see identical
  history.
* Aging is **monotone**: a body once aged stays aged.  The epoch rule gives
  this for free (eligibility only grows), and the fill latch prevents
  un-aging when fill drops.

### Model-facing prompt

One paragraph in the system prompt, gated on the setting being on:
"Some older tool results in this history are shown as head + tail with a
`tr-…` handle.  The full result is in the record; call `recall_tool_result`
if you need the middle.  Conclusions you drew from the full result when you
first saw it remain valid."  (Same epistemic framing as
`IMAGE_SEEN_PLACEHOLDER`, for the same reason.)

### What it does NOT do

* No summaries, digests, or LLM calls.  Head/tail are verbatim slices.
* Never touches human messages or assistant prose.
* Never changes the persisted record.

## Settings (`ContextManagementSettings`; UI: Project settings → Advanced)

| Field | Default | Tier | Meaning |
|---|---|---|---|
| `elide_redundant_tool_results` | `true` | 0 | Replace bodies provably redundant with a later result. |
| `tool_result_aging` | `false` until recall ships, then `true` | 1 | Master switch for aging. |
| `aging_turns` | `8` | 1 | Human turns before a command/fetch/search body is eligible. |
| `aging_turns_reads` | `16` | 1 | Same, for file-content bodies. |
| `aging_epoch_turns` | `8` | 1 | Age bodies in batches of this many consecutive turns once the whole batch is eligible (cache-friendliness; `1` = per message). |
| `aging_fill_threshold` | `0.5` | 1 | Aging stays off until prompt fill first exceeds this fraction of the input window, then stays on (latched). `0` = always on. |
| `aging_head_lines` | `15` | 1 | Verbatim lines kept at the top. |
| `aging_tail_lines` | `10` | 1 | Verbatim lines kept at the bottom. |
| `aging_side_max_chars` | `2000` | 1 | Per-side char cap on head/tail. |
| `aging_min_body_chars` | `1500` | 1 | Bodies smaller than this are never aged. |

Env kill switches: `ZIYA_DISABLE_TOOL_RESULT_ELISION=1` (Tier 0),
`ZIYA_DISABLE_TOOL_RESULT_AGING=1` (Tier 1),
`ZIYA_DISABLE_TOOL_HISTORY_REWRITE=1` (everything, pre-existing).

## Expected effect

Tool bodies are 55% of replayed history and 29% is in bodies > 4K chars.
With `aging_turns=8`, on the median long chat roughly the older two-thirds of
tool volume is eligible at any point past turn ~12; head+tail retains ~10–20%
of an aged body.  Back-of-envelope: replayed history shrinks by ~30–35% in
steady state, moving the 200K crossing from message ~52 to ~75–80.  That is
the honest ceiling for tool-side work; assistant prose (36%) has no lossless
lever other than mute.

## Measurement to run before/after

`scripts/measure_context_composition.py` reports the tiers and the
turn-at-which-N-tokens crossings.  Add an `--simulate-aging` mode that applies
the Tier 1 planner to each stored chat and re-reports the crossings, so the
"~75–80" above is replaced by a measured number before anything ships.
A second measurement worth adding: how often a later assistant message
quotes a distinctive line from a tool body older than `aging_turns` — a
direct estimate of how often recall would actually be needed.

## Open questions

1. Should file-content bodies be *exempt* rather than merely slower to age?
   Finding 2 argues for caution; the 10.8% they represent argues against a
   blanket exemption.  Default to slower (2×) and measure.
2. Frontend: show "N results aged, ~M tokens" in the token badge so the
   mechanism is visible.  Cheap once the planner returns its decisions.
3. `_compact_older_assistant_turns` (refusal-recovery rung 2) truncates
   assistant prose to 1,200 chars with no handle.  Once the head/tail renderer
   exists it should be reused there, with a handle into the record.
