# Making the competitive landscape study re-auditable

Status: design agreed, tooling built and tested, run 2 not yet executed.

## The problem in one number

The first CL5 run produced **2,135 scored cells**. If it were re-run today,
**101 of them (5%)** could be aligned to the new run. The rest would be a
second, unrelated snapshot.

A cell's key is `(capability_id, dimension_id, tool)`. Two thirds of that key
were stable and the middle third did not exist:

| key part | state in run 1 |
|---|---|
| `capability_id` | stable — 108 ledger-derived kebab ids |
| `dimension_id` | **absent — no such field** |
| `tool` | 87% bare roster ids, but 96 distinct labels for 26 tools |

Dimensions were handed to depth agents as a prose list of
`measurable_dimensions` in `32-contested-queue.json` — a *suggestion*, not a
contract. Measured against that list, the 601 dimensions the agents actually
scored were:

- **52% novel** (no declared counterpart)
- **43% reworded**
- **5% verbatim**

and the dimension *count* differed from the declared count in 65 of 108
capabilities.

Dimensions are the **axes of the comparison** — they are schema, not findings.
Letting the thing being measured regenerate its own measuring scale each run
means there is no time series, only a sequence of unrelated studies.

## Three further defects found while measuring

### 1. Contender lists were prose, and vague

`competitors_in_contention` was free text. Of 239 contender tokens:

- **40 contained a placeholder** — literally `"many"` (×29), `"various"`,
  `"all"`, `"others"`
- **50 (21%) named no roster tool at all**
- **16 capabilities named zero resolvable tools** — e.g. `stream-stop-abort` →
  `["all (stop generation)"]`

Where the queue said "many", Stage 2 had to decide who "many" was. The tools it
reached for skew hard toward whoever has the most public documentation:
**cursor +9, open-webui +7, claude-code +7, opencode +6, cline +6, chatgpt +6**
beyond what was declared. That is availability bias, not relevance.

Adherence where the list *was* specific was good — after canonicalising both
sides to roster ids (n=92): 70% exact, 28% superset, 2% subset, **0% disjoint**.
The agents never substituted a different roster; they filled a blank.

### 2. Absence was ambiguous, and one kind of absence was invisible

A tool missing from a comparison could mean any of several unrelated things,
and the figure rendered all of them as the same blank. Deriving contenders
mechanically from `30-matrix.json` exposed how bad this is. Of the 108×26
contested grid:

| | pairs | share | |
|---|---|---|---|
| matrix score ≥2 | 862 | 31% | contender — must be scored |
| matrix score 0–1 | 363 | 13% | **real signal** — they lack it |
| **no matrix cell at all** | **1,583** | **56%** | **no signal** — never assessed |

A missing cell is not a zero: 471 explicit zeros *are* recorded, so absence of a
cell means CL2/CL3 never assessed that pair. **The single largest fact about
this study is that 56% of the contested grid was never assessed**, and nothing
in the corpus said so.

### 3. Nothing was dated, and nothing was versioned

- **0 of 2,135** cells carried an `as_of` date
- no depth file carried a `run_id`; a re-run overwrites the same 108 paths
- 1 verdict field held a 200-word essay instead of an enum value; 2 confidence
  values were off-vocabulary (`medium-high`)

Undated cells make a re-audit delta unattributable, which matters because
**71% of competitor cells are C or D tier and 35% are D**. On any re-audit,
score movements caused by *us reading more* will outnumber movements caused by
*a competitor shipping something*. Unpartitioned, the report will show constant
competitive churn that is not happening.

## The design

### Frozen dimension registry — `25-dimension-registry.json`

Built by `scripts/complandscape_registry.py build --write` from the existing
artifacts. Dimensions are **harvested from run 1**, not taken from the queue:
the agents' dimensions were authored *after* reading the implementing code,
which is exactly why 52% were novel and why they are the better set. Run 1 is
therefore reinterpreted as the scoping exercise that produced the schema, and
its scores are discarded.

- **601 frozen dimensions**, median 6 per capability (the queue's own guidance
  was 4–8)
- each carries `dimension_id` (`<capability>::<slug>`), `name`, `name_hash`,
  `aliases`, `provenance`, `kind`
- declared axes that restate a harvested one fold in as **aliases** (63% of
  them, at a 0.35 Jaccard threshold over annotation-stripped names)
- declared axes with no harvested counterpart become **188 candidates**
  (`status: awaiting_review`) — *not* frozen. An axis a code-reading agent
  passed over is a candidate, not an omission, and freezing 188 unvetted axes
  would inflate every future run's cost for cells nobody judged worth scoring
  once.

`name_hash` is the load-bearing part. Editing a dimension's wording in place
would silently re-point a stable id at a different measurement, and every diff
across it would compare unlike things. `check` detects that and demands a MAJOR
`registry_version` bump; the diff tool refuses those cells outright.

#### Similarity needed fixing before it worked

Declared axis strings carry their answers inline — `# languages indexed (Ziya
~25 via tree-sitter+py+ts)`. Against the harvested `# languages indexed by the
background build` that scored **0.20**, plainly the same axis. Stripping
parenthetical annotations and trailing `— Ziya=…` clauses before comparing
raised the median declared→harvested similarity from **0.38 to 0.50** and cut
unmatched candidates from 232 to 188.

### Mechanical contender lists

`derive_contenders()` partitions the roster per capability straight from the
matrix — no prose, no placeholders, reproducible. Contenders carry a `rank` (by
matrix score) so a budget-limited run covers the strongest first and what it
drops is predictable rather than being whichever tools the agent happened to
research.

Every capability also records `coverage.assessed_fraction`, so a short
contender list is distinguishable from an under-assessed one.

### Absence taxonomy

Six states, with an explicit split between those that carry information about
the competitor and those that only describe our own coverage:

| status | signal | meaning |
|---|---|---|
| `scored` | — | a number is present |
| `below_threshold` | **real** | matrix scores them 0–1; they lack it |
| `not_applicable` | **real** | axis meaningless for that architecture — do not average as zero |
| `unknown` | none | has the capability; this axis undeterminable from available evidence |
| `not_assessed` | none | agent did not reach it (budget/time) |
| `not_in_matrix` | none | CL3 never scored the pair — a gap in an *earlier* phase |

`not_assessed` exists because of the run-1 `repeat_max` clip: 48 capabilities
were never dispatched and nothing recorded it. That state now has a name.

### Per-cell provenance

Every scored cell requires `score`, `evidence_tier` ∈ {A,B,C,D}, `citation`,
and **`as_of`**. Validation rejects a scored cell without a date.

### Run-versioned output

Runs write to `50-depth/<run_id>/` rather than overwriting `50-depth/`. Each
file carries `run_id`, `registry_version`, `schema_version: 2`.

### Three-way diff — `scripts/complandscape_diff.py`

| class | rule | how to read it |
|---|---|---|
| **REAL** | score moved, evidence tier unchanged | the only class that is competitive news |
| **EVIDENCE** | score moved *and* tier moved | our knowledge moved, not the tool |
| **COVERAGE** | cell appeared/vanished, or absence reason changed | not a finding |
| **SCHEMA** | dimension absent from one registry, or `name_hash` differs | incomparable — refused, not diffed |

Ziya's own cells are diffed through the identical machinery. A separate path for
our own numbers is how a report ends up applying a kinder standard to itself.

Verdict flips record `driven_by_real_change`, computed from the supporting cell
classes — so a verdict that moved because we read more documentation cannot be
reported as a competitor having moved.

## Cost of run 2

| | run 1 | run 2 |
|---|---|---|
| capabilities | 108 (60 dispatched, 48 clipped) | 108 |
| dimensions | 601 (invented per run) | 601 (frozen) |
| competitor cells | 2,135 | **4,877** |
| median cells/capability | ~20 | 30 |
| max cells/capability | — | 176 (`cli-session-persistence`) |

The 2.3× increase is entirely from **complete** contender lists (mean 8 from the
matrix vs median 4 from prose). That is the honest cost of not having the roster
decided by documentation availability.

26 capabilities exceed 80 cells. Those should be budgeted explicitly — score in
`rank` order, record the remainder as `not_assessed`. A recorded cap, not a
silent clip.

## What this still cannot do

Only **8 of 2,135** competitor cells were A-tier (read the code / ran it), and
that is structural: most of the roster is closed-source. This ledger measures
**our position against what competitors publicly claim**. A re-audit therefore
tracks changes in their claims plus changes in our code — which is the best
obtainable, but narrower than "how we're doing" implies, and the report must say
so rather than let the reader assume otherwise.

## Files

| path | role |
|---|---|
| `scripts/complandscape_registry.py` | `build` / `check` / `validate-run` |
| `scripts/complandscape_diff.py` | three-way partitioned diff |
| `.ziya/complandscape/25-dimension-registry.json` | the frozen schema |
| `.ziya/complandscape/26-depth-protocol.md` | the Stage 2 contract agents read |
| `tests/test_complandscape_registry.py` | 37 tests |
| `tests/test_complandscape_diff.py` | 24 tests |

The load-bearing test is
`TestAgainstTheRealCorpus::test_run_one_corpus_fails_validation`: the validator
must **reject** the actual first-run output. Without it the suite could pass
against a validator that approves anything. It currently reports missing
`registry_version`, missing `dimension_id`, and off-vocabulary `confidence` on
all 108 files.

---

# Addendum: CL3 was the upstream blocker (2026-08-26)

The work above made CL5 rigorous. Measuring it then showed the gap it inherits
is upstream, in CL3, and larger.

## CL3 v1 transcribed; it did not interrogate

| does the capability's `vendor_aliases` name the tool? | a cell exists |
|---|---|
| yes | 380/404 — **94%** |
| no | 1681/12726 — **13%** |

A cell existed almost exactly when some dossier author volunteered the pairing,
so coverage tracks *what 26 writers chose to enumerate* rather than what the
tools do. Nobody ever asked "does tool X have capability Y".

```
full grid              505 capabilities x 26 tools = 13130 pairs
cells present                                        2061 (16%)
never assessed                                      11069 (84%)
```

No tool exceeds 58% coverage and the ordering tracks public-documentation
volume: codex-cli 58%, windsurf 56%, open-webui 54% … amp 23%, anythingllm 21%,
notebooklm 21%.

The instruction that caused it, verbatim from the v1 card: *"Populate every
cell … **taking competitor cells from the dossiers**"* — one agent, 13,130
cells, sourced from volunteered claims.

## It lands on the positioning argument, not the footnotes

`33-unique-queue.json` — the "only Ziya does this" list — is the study's
headline deliverable:

| 205 unique claims | |
|---|---|
| pairs never assessed | 4952/5330 — **93%** |
| claims resting on **zero** competitor assessment | **117 of 205** |
| claims with partial assessment | 88 |
| claims where all 26 tools were checked | **0** |

More than half of "only Ziya has this" currently means "nobody looked."

## Why order matters

CL5's completeness now *inherits* from the matrix, which is correct for rigor
and exactly why CL3 gates it. Running CL5 first and backfilling CL3 later would
change every affected contender list, so the first re-audit diff would be
dominated by COVERAGE churn — the noise class the three-way partition exists to
isolate — and the run would be paid for twice. **CL3 first.**

This supersedes the earlier suggestion that CL5 run 2 could be the baseline.

## The fix

`scripts/complandscape_matrix.py` + `.ziya/complandscape/24-matrix-protocol.md`
+ a rewritten CL3 card. One rule: **every (capability, tool) pair carries
exactly one cell with an explicit status; there is no "not assessed".**

Fan-out is 364 slices of (tool, domain) — by tool because tool knowledge is the
expensive reusable context, and by domain because a whole tool is 505
determinations in one agent, which is how v1 ended up transcribing. Median 35
determinations per slice.

Absence is disaggregated because the states are not interchangeable:

| status | signal |
|---|---|
| `absent` | a **finding** — the tool lacks it |
| `unknown` | a **limit on the study** — must name `what_would_resolve` |
| `not_applicable` | **real signal, not a deficiency** — must name the architectural mismatch |
| `unresolved` | Ziya-only, competitor-sourced, settled by CL4 — never 0 |

`not_applicable` earns its place: scoring a browser-rendering capability as
`absent` for a pure CLI manufactures a competitive win we have not earned.
`unknown` without a resolution path is refused, because otherwise an
unresolvable cell is indistinguishable from an unexamined one — the exact
ambiguity being removed.

Grid completeness is an **error**, not a warning. Capability ids are frozen:
`25-dimension-registry.json` keys on them, so a re-cluster silently orphans the
CL5 schema — the same failure mode as a whole-tree card write minting fresh
block ids and orphaning a signed approval.

## Corrections to the analysis above

- **`coverage.zero_cells: 507` was correct**, not a defect: 471 competitor + 36
  Ziya. Only its *name* failed to say whose cells it counted, which made a
  competitor-filtered recount look like a 36-cell discrepancy. v2 reports
  `zero_cells_competitor` / `zero_cells_ziya` / `zero_cells_all`.
- **The "plausible holder" filter for bounding research failed.** Domain
  co-membership left a median of 19 of 26 tools unchecked per unique claim —
  barely a filter. Domains are too coarse; plausibility is per-capability
  judgment, not arithmetic. The full grid sidesteps the question entirely.

## Cost

13,130 determinations across 364 large-tier agents at the default concurrency of
8. Raisable via `repeat_max_concurrency` where the provider tolerates it; left
at the default because the cap exists for rate limits, and a throttled fan-out
surfaces as N task failures rather than one slow queue.

---

# Addendum 2 — CL4 reintegration, and why a re-run is cheap

A complete CL3 grid changes the gap queue, so CL4 must re-run. Measuring what
that costs surfaced three defects and one correct insight.

## The insight: the two stages have different dependencies

|          | asks                                  | depends on          | carried? |
|----------|---------------------------------------|---------------------|----------|
| Stage A  | does Ziya's code implement this?      | Ziya's source       | **yes**  |
| Stage B  | given what rivals ship, what to do?   | the competitor grid | **never**|

A Stage A verdict is a statement about Ziya's source. A complete competitor
grid does not change Ziya's source, so the verdict survives. A Stage B
disposition weighs the gap against what rivals actually deliver — and the grid
went from 16% assessed to 100%, so every disposition is re-derived. Carrying
the expensive half and refreshing the half that actually changed is what makes
the re-run affordable without preserving a stale judgment.

Measured against the real corpus: **110 carry-forward, 2 fresh**.

## Defect 1 — Stage B improvised when its input was missing

27 dispositions were hand-quarantined as "corrupted". They parse fine. The
fault is legible in one of their own fields:

> `"stage_a_verdict": "PARTIAL (reconstructed) — the Stage A file
> autonomy-level-controls-stageA.json was ABSENT from 40-reintegration/ (the
> 'previous step' content handed to Stage B was a carry-over of the
> human-takeover-mode record). Stage B therefore performed the second-look
> code audit itself…"`

That names two independent faults. A parallel-iteration binding leak handed
Stage B another iteration's Stage A — since fixed, and
`tests/test_parallel_iteration_binding_isolation.py` describes this same
incident in its docstring. And separately, Stage B responded to a missing Stage
A record by auditing the capability itself, writing a disposition
indistinguishable from a paired finding. Nothing checked; a human noticed and
moved files aside.

A missing or mismatched Stage A record is now a hard stop that writes nothing.
Three independent checks catch the shape after the fact:

1. the pair must exist (orphan disposition is an error)
2. `stage_a_verdict` must be a bare vocabulary word
3. it must **equal** the paired Stage A's `verdict`

Check 3 matters because two individually well-formed records can still describe
different capabilities, which is exactly what the binding leak produced.

## Defect 2 — vocabulary fields carried prose, pervasively

Not confined to the quarantined files. `stage_a_verdict` held
`"PARTIAL (~40% present)"` and a 227-character narrative; `effort_class` held
`"SMALL for the reduced version; LARGE for full parity."` A field whose purpose
is to be counted cannot carry a qualifier. Percentages now go in
`fraction_present_if_partial`, nuance in `effort_reasoning`.

`ARCHITECTURAL` stays a separate effort class from `LARGE` rather than a bigger
version of it: "a month of work" and "this cuts against a load-bearing
assumption" are different findings and must not collapse.

## Defect 3 — no provenance at all

Across **43 distinct keys in 225 records**, not one date or commit. So nothing
could judge whether an audit was still true. New records carry `as_of` and
`audited_at_commit`.

Staleness is judged from evidence-path existence, since the first-run records
have no dates. That check needed a fix of its own: a naive `os.path.exists` on
`evidence[].path` declared **34 of 112** records stale, none of which had moved.
The field specified as a path was used as prose:

```
"app/routes/,app/api/"
"app/services/pdf_exporter.py + html_exporter.py + latex_renderer.py"
"app/providers/token_master.py / base.py:283 / bedrock.py:429,642"
"app/mcp/tools/context_management.py-and-memory_tools.py"
"repo-root"
```

Candidates are now extracted from the string, and **one** surviving path is
enough — a refactor that moved one of five files does not void a finding. The
2 remaining refusals are the records that invented their own schema
(`searched_by_mechanism` instead of `searched`, no `evidence` at all).

## Two corrections that travelled with this

**`check_matrix` tolerated incoherent Ziya cells.** `status="unresolved"`
carrying `score=3` passed. That cell reads unresolved to the queue derivation
while holding a real score, so the capability is re-queued for an audit that
just answered it. Status/score agreement is now enforced on every cell, and
Stage 4 applies dispositions through `apply-dispositions` rather than by hand —
the invariant cannot be maintained by editing 100+ cells in a JSON document.

**The registry rebuild moved from CL3 to CL4.** CL4's ledger corrections change
which capabilities are *contested*, and the registry is keyed on that
membership. Built a phase early it omits whatever CL4 reclassifies as FOUND —
precisely the manual patch the first CL5 run performed for 16 entries. CL3's
text now says so, and tells an operator running CL3 alone to rebuild manually.

## Ordering, and why it is not optional

```
merge fix → CL3 (13,130 cells) → gap queue shrinks → CL4 modify → CL4 run
          → ledger corrections → registry rebuild → CL5
```

Backfilling CL3 *after* CL5 would make the first re-audit diff dominated by
COVERAGE churn — the exact noise class the three-way partition exists to
isolate — and the run would be paid for twice.

## Test posture

40 tests. Non-vacuity verified by mutation: disabling the orphan-pair check,
the verdict vocabulary gate, the cross-file agreement check, the
`capability_id` guard, the citation parser, or writing score without status
each turns the suite red.

Two are load-bearing. `test_the_real_first_run_fails_validation` requires the
validator to **reject** the shipped 225-record corpus — if it ever passes,
either the corpus was repaired or the validator stopped validating.
`TestProtocolDocMatchesTheValidator` parses the JSON examples out of
`43-reintegration-protocol.md` and validates them, because a doc/validator
disagreement is invisible until a whole run has been paid for: every agent
conforms to the protocol, every file is rejected, and nothing says which of the
two is wrong.

---

## Addendum: CL6 — corpus resolution and report provenance

### The defect versioning created

Making CL3/CL4/CL5 run-versioned solved re-auditability and broke CL6, which
predates versioning and reads two paths directly:

| CL6 read | what it now holds |
|---|---|
| `50-depth/` (loose) | run 1's depth records |
| `40-reintegration/` (loose) | run 1's Stage A + dispositions |

Both are deliberately retained — `40-reintegration/` is CL4's carry-forward
source, and both are the diff baseline. So they cannot simply be deleted, and a
phase that reads them gets a *plausible* corpus rather than an empty one.

Measured on the real corpus, `resolve` reports LEGACY for `40-reintegration`
(224 files) and `50-depth` (108 files). Run the rebuilt CL3–CL5 and CL6 would
have synthesised those: a coherent, well-formed report of superseded numbers,
with nothing anywhere erroring. Identical in shape to the CL3 transcription
defect this whole rebuild started from — correct output, wrong input, no
signal — which is worth stating plainly, because it means the failure mode
recurs at every phase boundary and not just at the one where it was first
noticed.

### Resolution is mechanical, and a legacy fallback is blocking

`resolve` prefers `<base>/CURRENT_RUN`, falls back to a legacy directory *but
flags it*, and reports an absent phase rather than silently resolving to
nothing. The flag is the whole mechanism: refusing legacy outright would make
the tool useless on the current corpus, while accepting it silently is the bug.
A new CL6 Stage 0 gates the phase on `facts`, whose blocking conditions each
name a conclusion that could not be supported:

- matrix `schema_version != 2.0` — an absent competitor cell means *never
  assessed*, so every uniqueness claim is unfounded
- `grid.never_assessed > 0` — same, for the specific pairs
- a phase resolved to a legacy directory
- depth records carrying a different `registry_version` than the registry —
  their dimension ids may not mean what the registry says
- an orphan disposition, or any unparseable record

### Absence semantics, and why they change the critique

The v2 grid records three kinds of not-having-it, and the report must keep them
apart:

| status | supports "Ziya leads"? |
|---|---|
| `absent` | yes — determined not to have it |
| `not_applicable` | **no** — that tool was never in this race |
| `unknown` | unfalsified, not falsification-resistant |

Collapsing them is how a report flatters the project while every individual
number stays true. "Ziya renders diagrams in a browser and this CLI does not"
is a category error dressed as a finding.

This also *narrows* Critic A's job rather than widening it. The original
instruction — "for every entry in `33-unique-queue.json`, actively search for a
tool that has it" — was right when the matrix had assessed 16% of its pairs and
a uniqueness claim usually meant nobody had looked. With the grid complete,
re-searching all 205 claims re-derives work already done; the two remaining
failure modes are claims resting on `not_applicable` (a category error) and
claims with a material `unknown` residue (where the search budget belongs).

Critic B gains a symmetric obligation. Roughly 84% of the grid is newly
determined, produced by agents working from documentation *under a completeness
obligation* — which is exactly the pressure that yields a confident guess where
`unknown` was the honest answer. So the new cells are themselves a sampling
target, with two specific shapes to look for: an `absent` with a thin citation,
and a `present` inferred from a tool's general sophistication.

### Provenance is declared, not inferred

A report is a claim about a specific corpus state, and prose cannot be diffed.
`check-report` does **not** parse narrative — inferring a corpus state from text
would produce confident wrong answers. The report carries one
`corpus-provenance` HTML comment (invisible when rendered) declaring each
phase's run id and the headline counts; every field is verified, and a missing
or disagreeing field fails with the field named.

`PROVENANCE_FIELDS` is an explicit table mapping each declared field to the
fact it is checked against, and a test corrupts each field in turn to prove it
is falsifiable. A declared-but-unverified field reads as a guarantee and is not
one.

### What is mechanical and what is judgment

The verifier was asked to "recompute the counts and flag every discrepancy"
across ~450 files. That is both expensive and unfalsifiable: a verifier that
miscounts agrees with a report that miscounts, and the agreement looks like
validation. `facts` computes them once, so the verifier compares two numbers
instead of deriving one, and spends its judgment where no tool can help —
whether a cited `file:line` is load-bearing, whether a `FOUND` verdict is a
real match or a stretched partial mechanism, whether an effort estimate
respects the integration points it names, whether confidence language matches
evidence tier.

### Seams pinned

Three, each of which would otherwise surface only after a full run was paid
for:

- the **real corpus** must report legacy fallbacks and must not be
  synthesis-ready; if that test ever passes clean, either the phases were
  re-run or the detection broke
- the **provenance block the protocol documents** must declare exactly the
  fields the checker verifies — no more, no fewer — and the protocol's phase
  table must name the bases the resolver uses
- every one of the **14 subcommands** the protocols instruct agents to run must
  be advertised by its script's own CLI, catching a rename in either direction

### One defect of the implementation's own

Found by running it rather than by reading it: an absent `legacy_base` joined
onto the corpus root, so a phase with no output "resolved" to
`.ziya/complandscape/` itself and reported the 8 queue and matrix files it does
not own. Now a regression test.

### Cost

CL6 is 2 frontier critics + 1 frontier synthesizer + 1 large verifier — four
agents, negligible beside CL3's 364. Its value is entirely in refusing to run
on a corpus that cannot support a report.

## Addendum (2026-08-26): the target set itself was incomplete

The CL3 full-grid rerun (`m1-20260826`) landed clean: 364/364 slices,
13,130/13,130 cells, 100% completeness, statuses present 3,940 / absent 3,456
/ not_applicable 4,237 / unknown 1,497 (every `unknown` carrying a resolution
path), tiers A 209 / B 1,988 / C 2,992 / D 2,236. Matrix merged to v2,
`never_assessed: 0`, queues re-derived.

Auditing whether the grid's TARGET SET was complete found it was not, on both
axes — and neither hole came from the fan-out caps:

* **CL1 (capability axis):** 20 rostered Ziya subsystems, 17 audit files.
  `providers-and-model-config`, `task-cards-and-blocks` and
  `execution-sandbox-and-approvals` never produced audits. The holes were
  recorded in the ledger's `coverage_holes` — a field nothing downstream read.
  The `repeat_max=20` cap sat exactly at the roster size but did not clip;
  three dispatched agents simply produced nothing.
* **CL2 (tool axis):** 27 rostered competitors, 26 dossiers. `aider` —
  rostered as "the canonical git-native pair-programming CLI" — never got a
  dossier, hence no matrix column, hence 505 cells that do not exist. 10 of
  108 run-1 depth files cited aider informally regardless. CL3 v2's Stage 1
  roster cross-check could not catch this: it compares dossier files against
  the plan's tools, and both derive from the matrix, which already lacked
  aider — the roster's 27 ENTRIES were never the comparison baseline.

Both are the study's recurring defect shape — a missing question, not a bad
answer — surfacing one phase further upstream each time it is closed.

Remedies: CL1/CL2 uncapped; CL1's merge now leads with coverage holes; CL2
gains a completeness gate keyed on roster entries (first line
`COMPLETE`/`INCOMPLETE`); and a `CL-delta` card runs the four missing
artifacts in parallel, folds them ADD-only under frozen-id discipline, and
calls CL3 for a full re-determination over the 27-tool space. Full CL1/CL2
reruns were rejected: a CL1 rerun regenerates the ledger, orphaning CL4's
`corrected_by` corrections and re-minting frozen capability ids — the same
destroy-prior-work shape the CL4-preservation fix closed in the matrix merge.

One environment note from validating the delta card: nine task-card files in
the project store are encrypted and unreadable without `ZIYA_ENCRYPTION_KEY`,
and Call-by-name resolution silently skips them. CL3 resolved uniquely here,
but a Call target living in an encrypted card would be reported "not found"
rather than "unreadable" — worth a better error if it ever bites.
