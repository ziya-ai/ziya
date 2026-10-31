# Tributary fold: replay a quest turn as its reward

Status: **slices 1–3 implemented** (measurement; planner + renderer;
`recall_tributary` in `app/utils/tributary_recall.py` +
`app/mcp/tools/tributary_tools.py`, prompt paragraph in
`session_context_prompt._format_fold_section`, default on).  Slice 4 (fold
line, pin UI, token badge) next; the `foldPinned` field is already honoured
by the planner.
Related: `design/tool-result-aging.md` (Tier 0 shipped, Tier 1 unbuilt — this is
the tier that should be built instead of Tier 1), `app/utils/tool_history_rewrite.py`,
`scripts/measure_context_composition.py`, `app/models/project.py`
(`ContextManagementSettings`), `app/server.py` (`build_messages_for_streaming`).

## The problem muting cannot reach

A conversation is a stream with forks.  Very often the user asks for one data
point and the model goes on a hunt — grep, a dead end, a narrower grep, a
`sed -n` range, two or three lines of interim narration — and returns the
value.  From then on every turn replays the hunt.  The user wanted the
reward; the context holds the quest.

Muting is per message.  The quest and its reward are the *same* assistant
message, so the user can mute both or neither.  Tool-result aging
(`tool-result-aging.md` Tier 1) is per tool body, by age, and explicitly
does not touch assistant prose — the interim narration ("that's two entries,
I need the second one") survives it.  Neither lever has a notion of *value*.

The survey behind the aging design put tool bodies at 55% of replayed
history and assistant prose at 36%, and concluded prose "has no lossless
lever other than mute".  A fold has one: the model already wrote the
compressed form of the hunt — the reward paragraph — with the whole hunt in
context, at zero extra cost.  Replay the reward, fold the rest behind a
handle.

## Shape of a quest turn

In the record an assistant message is one string.  With the tool blocks
located by `find_tool_blocks` it has the shape

    prose₀  [block₁  prose₁]  [block₂  prose₂] … [blockₙ]  prose_final

A **tributary** is `text[block₁.start : blockₙ.end]` — every tool call, every
result, and every interim prose segment between them.  The **reward** is
`prose_final`.  `prose₀` is kept: it is short pre-hunt text, and in the
"here is the answer, let me verify" shape it IS the answer (open question 1).

```
record:   user ──► prose₀ ─ grep ─ "two hits" ─ sed ─ "that's it" ─ REWARD
replay:   user ──► prose₀ ─ ⤷ tb-9c41e0 (2 calls, 1 empty) ────────── REWARD
```

### The structural rule (no model call)

A message is a **quest turn** and its tributary is folded when ALL hold:

1. it is an assistant message with at least `fold_min_calls` (2) tool blocks;
2. `prose_final` is non-empty after stripping — a turn that ends on a tool
   block either aborted or has the result as its deliverable, and is left
   alone;
3. the tributary is at least `fold_min_chars` (1,500) long, so the
   placeholder is a real saving;
4. the message is not muted (muted messages are absent from replay anyway);
5. **grace:** at least `fold_grace_turns` (2) human messages follow it in the
   replayed history.  The turn right after a hunt most often continues it
   (asks a follow-up that needs the same bytes, or edits the lines just
   read); folding at N+1 would force a re-read.  N+2 is the starting point;
   slice 1 measures what N+1 would cost.

Single-block turns are deliberately exempt even when large.  "Read `foo.py`"
→ one `file_read` → "here's what it does" has the *read* as the deliverable
far more often than a two-call hunt does, and the aging survey found 1,300+
range reads whose exact lines the next turn used.  The exemption is cheap
to lift later; the measurement reports how much it leaves on the table.

### What a judge would add, and why it is not in the baseline

A model asked to pre-mark a hunt as tributary while it is still hunting
cannot know whether the next call is the last, and a marker it must remember
to close will sometimes stay open.  A *post-hoc* trailer avoids both: at the
end of a tool turn the model may emit one line, in the same family as the
task-run `<self_assessment …/>` tag:

    <reward keep="block:3,prose:final"/>

naming the spans that are the deliverable.  When present it overrides the
structural split; when absent the structural rule applies.  This is slice 5,
opt-in, after the baseline has numbers.  Cases where the trailer earns its
keep: "run the tests and show me" (the pytest body is the reward), a turn
whose final prose says "see the output above" (open question 2).

## Placeholder

Replaces the tributary span on replay.  Absolute coordinates and counts only
— nothing age-relative, so the bytes never change again once written (see
prompt-cache below):

    [Ziya: tributary tb-9c41e0f3a2 folded on replay — 4 tool calls (1 returned
     nothing), 2 interim notes, ~2,100 tokens. Calls:
       grep -n token_limit app/config/models_config.py
       sed -n 667,694p app/config/models_config.py
       …
     Full span: recall_tributary(handle="tb-9c41e0f3a2"); mode="commands" for
     the calls alone, mode="results" for bodies, grep= to slice. The text that
     follows was written with the whole span in view and stands.]

The call labels are listed (capped by `fold_placeholder_max_labels`, 12)
because they are the part of a hunt the model reaches back for: which
queries were tried, and which returned nothing.  "`grep X` found nothing" is
often the fact needed again, and it is the cheapest fact in the span to
keep as one line.  Labels come from the existing envelope header
(`_clean_label`), so no new parsing.

"Returned nothing" is a conservative heuristic over `payload_for_block`:
empty after strip, or the shell's no-match/not-found shapes.  Undercounting
is fine; the count is informational.

The last sentence carries the epistemic framing `INLINE_IMAGE_NOTE` and the
Tier 1 design both use, for the same reason: the model must not read the
fold as evidence the earlier result was unsound.

## Handle and recall

**Content-addressed:** `tb-` + first 10 hex of `sha1(span_text)` over the raw
record substring.  Positional handles break because replay is not the
record (muted turns absent, synthetic system messages present, trimmed
histories).  A hash needs no side store.

`recall_tributary(handle, mode="full"|"commands"|"results", grep?, max_chars?)`
— builtin, DIRECT, scoped to the calling conversation.  Re-reads the chat
record (same decrypt path as `chat_read`), recomputes fold spans with the
same planner, returns the span whose hash matches, re-encoded through
`rewrite_assistant_text` so it arrives in the inert ‹tool_result› form, not
the renderer's fence form.  `commands` returns just the labels, one per
line, so the model can re-run them against current state instead of
re-reading a stale result ("re-grab a new raw result from a similar
query").  Unknown handle → "not found in this conversation", never an
error that reads as a verdict on the earlier work.

This is the recall tool Tier 1 specified and never shipped; a fold is a
stronger reason to build it than aging was, and Tier 1 can reuse it.

## Where it lives

A third planner in `tool_history_rewrite.py`, `plan_folds(history, settings)
-> {(msg_idx, seg_idx): FoldSpan}`, pure like `plan_elisions`.
`rewrite_assistant_text` gains a `fold: Optional[FoldSpan]` argument and
emits `defang(prose₀) + placeholder + defang(prose_final)`, rendering no
envelopes for the folded blocks.

**Ordering with Tier 0.**  Folds are planned first.  `plan_elisions` must
then treat blocks inside a folded span as NOT in the model's view: a later
read that lives in a folded tributary cannot stand in for an earlier body
(the "contained" rule's justification is that the bytes are still
downstream).  Concretely, entries in folded spans are dropped from the
candidate list of later stand-ins, and elision decisions targeting them are
moot.  A seam test pins this; without it Tier 0 silently loses content.

The plan is computed once per user turn in `build_messages_for_streaming`,
never inside the tool loop, so iterations within a turn see identical
history (same rule as aging).

## Prompt-cache effect

A prefix cache misses from the earliest message whose bytes changed.  A
fold changes message *i*'s bytes exactly once, when the second following
human message arrives — at which point *i* is already inside the three- to
four-message tail every turn pays for.  After that the placeholder is
constant.  So, unlike every aging policy in the Tier 1 table, folding costs
no cache misses beyond the ones the new turn causes anyway.  Two rules keep
this true:

* the placeholder carries no age-relative or history-relative text (no
  "message 12 of 40" — the Tier 1 placeholder's "of N" would change every
  turn and should be dropped there too);
* the fold is monotone in practice.  It is a pure function of replayed
  history, so muting a later human turn could in principle drop a tributary
  below the grace and un-fold it; this is rare and self-correcting.  A
  latch on the chat record is the fix if measurement shows it matters.

## Prerequisite

`tool_result_sanitizer._cap_size` truncates at 100K chars before the text
reaches the record, so recall cannot return what was never persisted.  This
is the same prerequisite Tier 1 states: the cap becomes a live-view cap
(full text to the record, capped text to the model on the live turn).
Folding itself does not depend on it — a truncated span folds like any
other — but `recall_tributary` is only lossless once it lands.

## UI

The fold is visible, never hidden.  Under the folded message's tool
section a single line — `⤷ tributary tb-9c41e0 · 4 calls · folded from
replay` — with:

* **expand** — show the span (it is already rendered; this only toggles);
* **pin** — keep this tributary in replay permanently (`foldPinned: true`
  on the message record; the planner skips pinned messages).  The sub-turn
  counterpart of un-muting.

The token badge counts folded tokens separately so the mechanism is seen
working ("~18K folded").

## Model-facing prompt

One paragraph, gated on the setting, in the turn-stable system block:
"Some earlier tool-heavy turns in this history are replayed as their final
text only, with the search that produced it folded behind a `tb-…` handle.
The full span is in the record; call `recall_tributary` if you need the
calls or results again.  Conclusions you drew when the span was in view
stand.  Your own tool-heavy turns will be replayed the same way, so
anything a later step needs from the middle of a search — ids, paths, the
query that worked, an intermediate figure — belongs in your final text or
in a store, not only in the tool output."

## Settings (`ContextManagementSettings`)

| Field | Default | Meaning |
|---|---|---|
| `fold_tributaries` | `false` until `recall_tributary` ships, then `true` | master switch |
| `fold_grace_turns` | `2` | human turns that must follow a quest turn before it folds |
| `fold_min_calls` | `2` | tool blocks required for a turn to be a quest |
| `fold_min_chars` | `1500` | tributary span below this is left alone |
| `fold_placeholder_max_labels` | `12` | call labels listed in the placeholder |
| `fold_pressure_ratio` | `0.8` | nothing folds until the unfolded replay is this fraction of the limit; `0` = fold every eligible span |

## Pressure gate (added 23 Oct 2026)

Running with the structural rule alone, in real coding sessions, the fold
made the model measurably worse below any context pressure.  The reasons
are mechanical: a diff needs the exact context lines of a file, not a
conclusion about it; the placeholder lists the calls, not what in them
mattered, so deciding to recall requires remembering the content well
enough to know it is needed; and the "put ids and paths in your final
text" contract pads answers with material that was already in the tool
output.  The 65% reclaim figure counts tokens, and tokens are the wrong
currency when the window is not full.  Below pressure a fold is pure cost.

So the fold is now cache eviction, not tidying.  `plan_folds` takes a
`FoldPressure(tokens, limit)` — the previous turn's size as recorded for
the handoff nudge (`record_context_pressure`, the server estimate or the
provider-billed count from the executor) **plus what that turn's folds
removed** (`record_fold_savings`, written by `rewrite_tool_history` when
given a `conversation_id`).  That sum is the *unfolded* size.  Nothing
folds under `fold_pressure_ratio` × limit; above it, eligible spans fold
oldest first until the projected size is under the ratio minus
`FOLD_PRESSURE_HYSTERESIS` (0.15).

Two properties follow from gating on the unfolded figure.  It is monotone
in practice (it only grows with the conversation), so the folded set is a
prefix that only extends: a span folded once stays folded, which is what
keeps the prompt-cache argument above true — gating on the *measured*
size would un-fold on the turn after a fold and re-fold the turn after
that.  And the hysteresis band means folds arrive in batches with stable
stretches between, rather than one cache-missing fold per turn while the
conversation hovers at the threshold.

Unknown pressure (`None`: the first turn after a server restart, a caller
without a conversation such as the token-count route) folds nothing —
unknown must not read as "full".  The token badge therefore overstates by
the folded amount on the turn folding begins; the conservative direction.
The Session Context paragraph was reworded from "some earlier turns are
replayed folded" to "once this conversation nears its context limit, the
oldest tool-heavy turns are…": a claim that was false below pressure
taught the model to recall spans that were in view.

Open: `grace` could become turns-since-last-reference (a later diff or
prose naming a path read in a span resets it), and content-shaped results
(`file_read` of a path not in pinned context) could be exempt from folding
while search-shaped ones (grep, ls, ast_search, analyze) stay eligible.
Both wait on experience with the gate.

Env kill switch `ZIYA_DISABLE_TRIBUTARY_FOLD=1`; `ZIYA_DISABLE_TOOL_HISTORY_REWRITE=1`
still disables everything.

## Division of responsibility: the fold, and what survives it

The fold is structural and knows nothing about value.  It does not try to
work out which parts of the middle of a quest later steps will need; that
is the model's job *during the turn*, and the fold's existence is the
reason to tell it so.  Two places persist across a fold: the reward prose,
and the stores (`dataset_register`, `ledger_record`, `store_output`, an
append-only JSONL under `.ziya/`).  Anything a later iterative step needs
— ids, paths, the query that worked, the intermediate figure — must land in
one of them, because the span will not be in view two turns later.

So the hint lives in the model-facing prompt, not in the placeholder.  The
existing "Working with data" guidance already sends rows to datasets and
numbers to the ledger; the fold adds one sentence connecting it to replay:
*the search that produced a result is not kept; the result and its
coordinates are, so put them in the final text or a store.*  This matters
most for bigdata and operational-research turns, where the middle of the
quest holds exactly the things the next step iterates on.

The placeholder stays minimal — call labels and counts.  It does not try to
detect store artefacts; a reward written under the hint above already names
them.  Where a hunt ends in a store, "recall" is a query against that store
rather than `recall_tributary`, so the recall-needed figure from slice 1
(28.6% at grace 2) is a ceiling for the current prompt, not a property of
the design.

## Survey results (slice 1, full store, 17 Oct 2026)

464 long chats (≥ 20 msgs), 79M replayed tokens, 5,388 quest turns.
Tributary spans are 71.7% of all replayed history; 16% of a span is interim
prose, which aging cannot reach.  Single-block exemption leaves 1.5%;
`prose₀` median 136 chars, p90 616.  Steady-state reclaim net of
placeholders: 68% / 65% / 63% at grace 1 / 2 / 3.  Chats ever crossing 200K
fall from 162 to 26 at grace 2.  Recall needed (first verbatim reuse of a
span line after the fold): 56.9% / 28.6% / 19.2% at grace 1 / 2 / 3 — grace
1 is wrong, 2 is the default, 3 is defensible.  Only 3 chats cross 500K and
2 cross 1M: the store was shaped by 200K-era limits, so the crossing table
says nothing about 1M-context sessions; for those the per-turn share (72%)
is the operative number.  Ledger: `fold_survey.quest_span_share`,
`fold_survey.recall_needed_grace2`; dataset `.ziya/fold_survey.json`.

## What it does to the curve

Slope, not asymptote.  Human text and reward prose accumulate linearly, and
a design conversation with no tool calls has no tributaries.  But on a
tool-heavy conversation the quest turns are where the volume is, and this
removes both their bodies and their interim prose — the combination no
other lever reaches.  Mute, fork and handoff remain the tools for the
asymptote; this should make them necessary much later.  "Should" becomes
a number in slice 1.

## Slices

### 1. Measure
`measure_context_composition.py --simulate-fold [--grace N] [--min-calls N]`.
Per chat and aggregate: share of replayed chars in tributary spans, split
into bodies vs interim prose; the 100K/150K/200K crossings with folds applied
turn by turn (a fold at position *i* counts only for replays from *i*+grace
on); share left on the table by the single-block exemption; and a reuse
proxy — how often a later assistant message quotes a distinctive (≥40-char)
line from an earlier folded span, with and without the grace, which is the
direct estimate of how often recall would be needed and of what N+1 would
cost over N+2.  No tests: this is the evidence the rest rests on.

### 2. Planner and renderer, off by default
`FoldSpan`, `plan_folds`, `rewrite_assistant_text(fold=…)`, settings,
kill switch, Tier 0 ordering.  **Seam tests:** a 3-call quest turn with two
later human turns replays as `prose₀ + placeholder + prose_final` with the
blocks absent and the placeholder listing the labels; the same turn with one
later human turn is untouched (grace); a single-block turn is untouched;
a turn ending on a block is untouched; a muted later turn is not counted
toward grace; the handle equals `tb-` + sha1 of the exact record substring;
the placeholder bytes are identical when replayed from turn N+2 and turn
N+9 (cache rule); Tier 0 does not elide a body whose only later stand-in is
inside a folded span — asserted on the outermost surface, the rewritten
history handed to the provider, not on the plan dict.

### 3. `recall_tributary` and the prompt paragraph; default on
**Tests:** `full` returns the span bytes re-encoded as envelopes, not
fences; `commands` returns the labels only; `grep` slices; a handle from
another conversation is "not found"; a handle for a message that was since
muted still resolves (the record is the source, not the replay).  Then the
default flips to on.

### 4. Fold line, pin, token badge
**Tests:** pinned message never folds; badge count equals the planner's
folded total for the same history.

### 5. `<reward …/>` trailer (opt-in)
Overrides the structural split when present; stripped from replay like the
self-assessment tag.  Only after slice 1 says which exemptions are costing
the most.

## Open questions

1. `prose₀`: keep (baseline) or fold?  "Let me check the config…" is pure
   pollution; "The limit is 1M — verifying" is the reward.  Slice 1 can
   report `prose₀` length distribution on quest turns.
2. A reward that points into the span ("the second hit above") dangles after
   a fold.  The handle is right there, so the model can resolve it; whether
   it does is a thing to watch in real use, and the trailer (slice 5) is the
   structured fix.
3. Should the grace be counted in human turns (as here, matching aging) or
   in assistant tool turns?  A long design discussion after a hunt satisfies
   the grace without the hunt ever having been "left behind"; probably fine.
4. Task-card runs replay their own transcripts through the same executor; a
   `Repeat` body that hunts every iteration is the strongest case for this
   and should be covered by the same planner once slice 2 lands.
