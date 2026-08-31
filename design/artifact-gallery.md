# Artifact galleries — periodic and end-of-run comparison display

Status: vocabulary and backend landed; run-level gallery surface outstanding.

Companion to `design/task-cards.md` §Artifacts, which defines what an
artifact *is*. This document covers how a run accumulates **visual
evidence** over its lifetime and how that becomes a periodic progress
view and an end-of-run report without either one passing through a model
context.

## The problem this exists for

A long sweep produces evidence in two shapes:

- **Comparison** — the same subject captured under two conditions.
  Before/after a fix, baseline vs candidate, two regions, two themes.
- **Progression** — the same subject captured repeatedly as work
  proceeds. Attempt 1, 2, 3; or one capture per loop iteration.

Both want to be *visible while the run is still going* and *collected at
the end*. Before this work, neither happened: an agent could emit
hundreds of standalone images with no declared relationship, so the
viewer had nothing to pair or sequence, and the only way to present a
comparison was to hand-carry images into the chat transcript — which is
exactly the token load the artifact system exists to avoid.

## Vocabulary: the emitter declares structure, never presentation

This is the load-bearing constraint, inherited from
`frontend/src/utils/artifactGroups.ts`. The emitting agent says what
relates to what. The viewer derives layout from the *shape* those
relations imply. A `display: "gallery"` or `layout: "sideBySide"`
parameter is deliberately **not** offered, for two reasons:

1. Shapes nobody anticipated still render sensibly, instead of falling
   through to nothing because the model named a layout that does not
   exist.
2. A model asked to choose presentation will choose it inconsistently
   across a 500-task run, and the report becomes a patchwork.

Three fields, all optional:

| field | meaning |
|---|---|
| `group` | the **subject** these parts are about |
| `label` | what this part **is** within that subject |
| `seq` | position, when the group is a progression |

### `group` is a subject, and it is reusable

Parts sharing a group are shown together. The rule that makes
accumulation work: **reuse the same group id whenever you are talking
about the same subject** — across loop iterations, and across later
tasks in the same run. That is what gathers a subject's evidence into
one entry rather than scattering it across dozens of unrelated cards.

A group id may be a **path**, `section/subject`:

```
group="d2/D-020"        group="d2/D-030"        group="tikz/D-010"
```

Sections gather subjects under a heading, and nest further if needed.
This replaces what would otherwise be a fourth field, and it keeps the
hierarchy structural — a path is a fact about relatedness, not a
rendering instruction.

### `label` is the comparison axis

Two parts in one group with different labels read as a comparison.
`before`/`after`, `baseline`/`candidate`, `us-east`/`eu-west` — all the
same shape, all laid out the same way. No label string is magic, and
status colouring comes from the recorded `status` field (set factually
at emit time), never from label text.

**Extra axes go in the group id, not the label.** This matters and is
easy to get wrong. Four labels in one group —
`before-light`/`after-light`/`before-dark`/`after-dark` — loses which
part pairs with which. Faceting the group keeps each pair intact:

```
group="d2/D-020/dark",  label="before"   ✅ two clean pairs
group="d2/D-020/dark",  label="after"
group="d2/D-020/light", label="before"
group="d2/D-020/light", label="after"

group="d2/D-020", label="before-dark"    ❌ one 4-part blob
group="d2/D-020", label="after-dark"
group="d2/D-020", label="before-light"
group="d2/D-020", label="after-light"
```

### `seq` is for progressions

When the parts are ordered stages rather than alternatives, `seq`
orders them and the viewer draws them as a sequence.

## Periodic vs final is derived, not declared

The model does **not** say whether an output is a progress update or a
final result, and it does not say which iteration it is in. The harness
already stamps every part with the emitting `block_id`, the loop
`iteration`, and `iteration_owner` (`build_part`, "hierarchy stamping").

So the distinction falls out of where the emit happened:

- A part emitted inside a Repeat/Until body **carries an iteration**.
  Emitting the same `group` + `label` once per iteration therefore
  produces a progression over time, visible as the run proceeds.
- A part emitted from a task outside any loop carries **no iteration**.
  That is a final output.

Nothing in the emit call changes between the two cases. This is why the
model-facing instruction can stay short: it only has to explain subject,
label and order, and the temporal dimension is free.

## Cross-run evidence: `from_run`

A before/after against a baseline captured by an *earlier* run was
previously impossible. `build_part` validated `file_path` against the
project root plus read grants, and had no concept of a source run — so a
PNG sitting in `<project>/task_runs/<other>/artifacts/` was unreachable.

`emit_artifact(part_type="file", from_run=..., file_path="<bare filename>")`
makes that blob usable as this run's own artifact.

### What "this run" means, and why `self` is not a convenience

A **Call block executes inline in the caller's run** — `CL0` calling
`CL1..CL6` produces exactly one run record, owned by `CL0`
(`app/api/task_runs.py::get_callee_context`). So a multi-card *stack* is
one run with one artifacts dir, while separately launched cards are
separate runs. Both topologies occur in practice, and an aggregating
report has to reach both.

The same-run case was **unreachable**, not merely awkward: `file_path`
resolves against the project root, and the artifacts dir lives under the
Ziya home, so no grant covers it. A later block could not reference a
blob an earlier block in its own run had emitted. `from_run="self"` is
the affordance that closes that hole.

Note the asymmetry: a foreign blob is **copied**, a same-run blob is
**referenced in place**. Copying within a run would give one piece of
evidence two names in the same report.

### `from_run` and `diagram` are mutually exclusive

`diagram` renders NEW content at emit time; `from_run` names content
captured ELSEWHERE. One part cannot be both, and the render path takes no
`from_run` — so the original code dropped it silently: the call reported
success, one part was recorded (the fresh render), and the prior evidence
never appeared.

That is the worst available failure for the case that motivates passing
both, a before/after: the report renders, the run succeeds, and the half
that made it a comparison is missing with nothing anywhere saying so. The
combination is refused, and the error names the shape that does work —
two parts sharing one `group` with distinct `label`s — because an error
that only forbids strands a caller who had a legitimate goal.

The guard tests truthiness, not `is not None`: an explicitly-null
`from_run`, which is what a caller filling every field or a wrapper
forwarding `**kwargs` emits, is absence rather than conflict.

### Resolution forms, in precedence order

| Form | Resolves to | Cost |
|---|---|---|
| `self` / `current` / `this` / own run id | this run | none |
| a run id with a sibling artifacts dir | that run | one `is_dir` |
| a card id, or a card **name** | that card's most recent *finished* run that has artifacts | reads run records |

Card-name resolution exists because **a card author cannot know a run
id** — the run does not exist when the card is written. The name is
matched against each run's `card_snapshot`, so it resolves as of the run
that used it rather than as of the card's present name; the live card
list is only a fallback for runs predating snapshots.

Ambiguity is **refused, never guessed**: a name matching runs of two
different cards returns an error naming the count, because silently
picking one attributes a baseline to the wrong card.

### Resolution is memoized, deliberately

Two reasons, and the first is correctness rather than speed:

1. A report emitting hundreds of parts against "the Stage 1 sweep" must
   compare against **one** baseline throughout. Re-resolving per part
   would let the baseline shift mid-report if another run of that card
   finished while this one was still emitting.
2. Run records are ~100 KB each and encrypted at rest, so re-reading the
   whole history per part would be pathological for exactly the
   large-gallery case this feature exists to serve.

### Discovery is the other half

`list_run_artifacts(from_run=...)` returns an index — `name`, `group`,
`label`, `seq`, `filename`, `media_type`, `status`, `block_id`,
`iteration` — and **never** payloads: no bytes, no inline text, no
absolute paths.

Without it, `from_run` is unauthorable at scale: copying by filename
presumes the aggregator already knows the filenames, which for a sweep
that emitted several hundred it does not. Excluding payloads is what
makes the index affordable to put in a model's context; the blobs stay on
disk and reach the browser through the blob route.

The filename is passed to `resolve_artifact_blob_path` **unnormalized**.
Taking `Path(filename).name` would silently accept `"sub/dir/x.png"` as
`"x.png"` from the artifacts root — handing back a same-named but
different file than the author asked for. Refusing separators is the
honest answer for an evidence reference.

**Copy, not reference**, deliberately:

- A run's artifact record must stay self-contained. A reference rots
  silently when the source run is pruned.
- The audit-trail property — a run is reconstructable from its own
  directory — must hold.
- Blob size is trivial against the value of the comparison.

The part records `source_run_id`, because a comparison against an older
baseline is only honest if the report can say which run the baseline came
from.

Confinement is **structural** rather than checked. The destination is
`<project>/task_runs/<run>/artifacts`, so the source is resolved as a
*sibling* of the current run's directory: nothing outside this project's
`task_runs` is addressable even in principle. `resolve_artifact_blob_path`
then applies the same traversal guards as the HTTP blob route — necessary
because both the run id and the filename originate in model output.

`size_bytes` is intentionally absent on copied parts: with at-rest
encryption enabled the on-disk size is the *encrypted* size, and
reporting that as the artifact's size would be wrong. An absent field is
honest; a misleading number is not.

## Two budgets, because the two part kinds cost differently

`MAX_PARTS_PER_TASK = 50` applied to everything, which capped an
evidence gallery at 50 images — an artificial ceiling on the system's
main purpose.

- **Inline parts** (`text`, `data`) carry their payload in the run JSON
  and can reach a model context when a downstream task templates them by
  name. The runaway-loop cap belongs here: **50**.
- **File parts** carry a path plus metadata (~200 bytes in the run
  record) and are fetched lazily by the browser through the blob route.
  They never enter a context unless templated explicitly. Cap: **400**.

Budgets are per *task*, so a Repeat-over-subjects structure multiplies
available capacity — which is the shape that wants it anyway.

## Layout derivation (unchanged)

`selectLayout` remains a pure function of shape; order of checks is the
specification.

| shape | layout |
|---|---|
| 1 part | `card` |
| exactly 2, both labeled | `sideBySide` |
| any explicit `seq` | `sequence` |
| ≤ 6 parts | `grid` |
| otherwise | `list` |

`list` stays universally reachable, so a layout misfire can never hide
data.

## Outstanding

1. **Group-path sections.** `groupArtifactParts` buckets on the exact
   group string, so `d2/D-020` and `d2/D-030` are unrelated siblings.
   Splitting on `/` to build section headings is the remaining step that
   makes the path form actually do something.
2. **Progression detection across iterations.** Parts with equal
   `group` + `label` and differing `iteration` are a progression. Today
   they land in whatever bucket their per-task artifact produced.
3. **Run-level gallery.** `ArtifactViewer` renders one artifact's
   `outputs`. An end-of-run report needs aggregation of every group
   across every task and iteration in the run, into one scrollable
   surface. This is the genuinely new UI.
4. **Lazy image loading.** The viewer eager-loads every `<img>`. At
   gallery scale that is hundreds of concurrent requests.

Items 1–2 are pure functions with cheap unit tests and should land
before 3, so the gallery consumes a grouping layer that is already
correct.
