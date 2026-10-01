# Documentation and self-description remediation plan

Source: competitive-landscape study, synthesis run `s1-20260910`
(`.ziya/complandscape/60-synthesis/s1-20260910/REPORT.md` §3, §10 Tier 0;
`19-ziya-ledger.json`; reintegration run `r1-20260901`; both critique files).
Status: **proposed — not started.**

## 1. What the study actually found

The study's headline about documentation is not "the docs are thin"; it is
that the docs describe Ziya by *mechanism name* while users, competitors and
the study's own first pass search by *capability vocabulary*. Measured
consequences, all in the corpus:

| finding | count | where |
|---|---:|---|
| Ledger capabilities whose `doc_discrepancy` says **shipped but undocumented / invisible** | 123 (107 at maturity ≥3; every one of those 107 is in the contested or unique queue, i.e. competitively relevant) | `19-ziya-ledger.json` |
| Ledger capabilities whose docs **over-claim** (feature described as fuller/more reliable than the code) | ~20 | `19-ziya-ledger.json` |
| Reintegration gaps classed `false_or_missing_shipped_capability` (docs wrong about what exists) | 105 of 128 | `41-reintegration-summary.json` |
| **Terminology artifacts** — apparent gaps that were shipped under a non-marketing name | 24 (Critic A: 18 solid, 6 are partial matches stretched to FOUND) | `terminology_defects_capability_ids` |
| **Deliberate non-goals** confirmed by mechanism, none of them stated anywhere as a decision | 28 | `deliberate_non_goals_capability_ids` |
| Critic A **inflated scores** that the docs must not echo | 12 | `60-critique-ziya.json` |
| Alias strings already recorded per capability (the raw material for a vocabulary index) | 4,233 across 462/467 capabilities | ledger `aliases` |

Which files the defects name (reintegration + ledger combined):
`Docs/FeatureInventory.md` (144 mentions), `Docs/Capabilities.md` (118),
`Docs/competitive-analysis.md` (54 — last edited 2026-05-19, described by
the roster as "paper-based, 3mo stale"), `README.md` (25), then a long tail
(`Skills.md`, `Enterprise.md`, `CLITasks.md`, delegate docs).

Root cause, in one sentence: there is no layer that maps *what a reader
would call it* to *what Ziya calls it*, so a capability is discoverable only
by someone who already knows Ziya's internal noun for it.

## 2. Principles

1. **Correctness before coverage.** Remove or qualify every over-claim before
   adding a single under-claim. An under-claim costs visibility; an over-claim
   costs trust, and the critics found twelve places where the study itself
   nearly over-claimed.
2. **Docs may not exceed the ledger.** A capability is documented at the
   maturity the ledger supports after critic correction, never higher. Where
   the ledger says PARTIAL, the doc says what is present *and what is missing*.
   The six Critic-A "partial stretched to FOUND" items (`visual-agent-flow-builder`,
   `diff-llm-resolver-stage`, `automatic-mindmap-generation`,
   `lint-test-fix-repair-loop`, `custom-personas-assistants`,
   `user-authored-project-rules`) are the test cases for this rule.
3. **Every edit is traceable to a corpus record.** Each doc change cites the
   capability id it closes, so a later study can diff docs against ledger by id.
4. **Dead code is not a feature.** `post-instruction-framework-dead`, the
   `app/shadow/journal.py` pieces, and `sched-timezone-declared-not-implemented`
   are documented as experimental/dead or deleted — not written up as
   capabilities. Their fate is the report's Tier 1 item 5 ("backlog hygiene")
   and is a *prerequisite* for the doc decision, not part of it.
5. **A non-goal is a decision, not a gap that is inconvenient.** The 28
   non-goals get one sentence of rationale each, taken from the disposition
   `recommendation`, plus what the decision costs.
6. **The fix must not recur.** The plan ends with a conformance check, not
   with a rewritten doc.

## 3. Work streams

### WS0 — Extract the worklist (mechanical; `scripts/`)

`scripts/complandscape_docdefects.py` reads the resolved corpus and emits
`DOC-DEFECTS.json` + `DOC-DEFECTS.md` into the synthesis run dir. One row
per (capability, target doc): defect class (`over` / `under` / `terminology`
/ `non-goal` / `partial`), the doc line currently cited, ledger maturity,
critic-corrected maturity where one exists, the `aliases` list, the
`ziya_internal_name` from Stage A, and the evidence citation. Classification
of the free-text `doc_discrepancy` is keyword-based and *must* be reviewed;
the script marks each row `auto`/`reviewed`.

Output is the single checklist every later stream ticks off. ~250 rows.
Same projection discipline as `complandscape_appendix.py`: no judgment,
only extraction.

### WS1 — Over-claims (correctness) — small, do first

~20 ledger over-claims + 12 Critic-A inflated scores, mostly in
`FeatureInventory.md` and `README.md`. Pattern: the doc states a capability
without its limit (swarm requires Bedrock; `.doc` accepted but not parsed;
paste-detection state exists but is unwired; "accurate token counts" job is
disabled). Each fix is one sentence of qualification or one deletion.
Deliverable: every WS0 `over` row closed, with a regression assertion in
WS6 for the ones that were literally false.

### WS2 — Vocabulary layer (the structural fix)

New `Docs/CapabilityGlossary.md` — a genuinely new category: a
reader-vocabulary → Ziya-name cross-reference, **generated** from the ledger
`aliases` field by WS0's script, then hand-edited only for the 24
terminology artifacts, whose entries also carry the competitor whose term it
is (e.g. *"computer history (codex-cli) → progress_notes trail + memory
store; PARTIAL, ~40%"*). Grouped by domain; each entry links to the
`Capabilities.md` anchor. Regenerable, so it cannot rot independently of the
ledger.

In addition, the first mention of each of the 24 in `Capabilities.md` gets
an inline *"also known as / competitors call this"* line. This is the cheap
half of Critic B's "aliases rule" concern applied to Ziya's own docs.

### WS3 — Under-claims (coverage) — the bulk

107 mature (≥3), competitively relevant capabilities absent from the docs.
Fan out **per subsystem**, in this order (count of under-claims):
scheduling-and-automation 14, context-and-file-tree 10,
frontend-conversation-management 10, document-ingestion 10,
mcp-security-and-hallucination 9, then the rest. Per capability: one line in
`FeatureInventory.md` (the index) and one paragraph in `Capabilities.md`
(the detail) at the ledger's stated maturity, plus the alias line. Skip the
16 under-claims at maturity ≤2 unless they are the "reduced version" of a
Tier-1 build item.

### WS4 — Deliberate non-goals

New section in `Docs/DesignPhilosophy.md` (which already holds "What I'd Do
Differently" and is where readers look for the reasoning): *"What Ziya
deliberately does not do"*. 28 entries under the five reason themes the
reintegration summary already identified (local-first trust model; served
SPA + CLI rather than in-IDE editor; refusal to provision infrastructure;
BYO-credentials broker; text-first output). Each: what it is, why not, what
it costs. Source text is the disposition `recommendation` field.

### WS5 — Replace `Docs/competitive-analysis.md`

Retire the May document (keep under `Docs/old-*` or delete). Replace with a
version derived from the report: the ~15–20 surviving differentiators
**with their absent / not_applicable / unknown split**, the real gaps after
critic discounting, the roster with abandonware noted, and a provenance
line naming the corpus runs (`m3-20260827` / `r1-20260901` /
`r2-20260830` / `s1-20260910`). Rule from the report carried over: no claim
of leading rests on `not_applicable` cells. This file becomes the one place
that is *allowed* to compare, so the rest of the docs stop editorialising.

### WS6 — Anti-recurrence

`tests/test_docs_ledger_conformance.py`:

* every ledger capability with maturity ≥3 is findable in `Docs/` by id,
  name, or any alias (fails today for 107; the target is 0);
* every WS1 over-claim that was literally false has a negative assertion
  (the old sentence must not reappear);
* the glossary is byte-identical to a fresh regeneration from the ledger.

Plus a `Docs/CONTRIBUTING.md` note: a changelog entry for a new capability
names the user-facing vocabulary, not only the class name.

## 4. Order, dependencies, effort

```
WS0 extract ──► WS1 over-claims ──► WS3 under-claims (per subsystem, parallel)
            └─► WS2 glossary (generated) ──┘            │
            └─► WS4 non-goals                            ▼
            └─► WS5 competitive-analysis rewrite ──► WS6 conformance test
Tier-1 #5 backlog hygiene (code decisions on dead paths) ─► gates the 3 dead-code rows in WS1/WS3
```

| stream | kind | size | writes |
|---|---|---|---|
| WS0 | mechanical | half a day | `scripts/`, `.ziya/` |
| WS1 | judgment, small | 1 day | `Docs/FeatureInventory.md`, `README.md`, `Docs/Capabilities.md` |
| WS2 | generated + 24 hand entries | half a day | `Docs/CapabilityGlossary.md`, `Docs/Capabilities.md` |
| WS3 | judgment, bulk | 3–4 days (parallelisable by subsystem) | `Docs/FeatureInventory.md`, `Docs/Capabilities.md` |
| WS4 | judgment | half a day | `Docs/DesignPhilosophy.md` |
| WS5 | judgment | 1 day | `Docs/competitive-analysis.md` |
| WS6 | mechanical | half a day | `tests/`, `Docs/CONTRIBUTING.md` |

Every target path is inside the project's default writable set
(`Docs/`, `*.md`, `tests/`, `scripts/`, `.ziya/`), so a Task Card for this
needs **no escalation and nothing to sign**.

## 5. Guardrails for whoever executes it

* Read the ledger record and the critic files for a capability before
  writing about it; the doc paragraph is a projection of the ledger entry,
  not a fresh description of the code.
* Do not merge or rename capability ids in the docs even where Critic A
  found over-splitting; report the count-level correction in
  `competitive-analysis.md` instead. Ids are frozen (CL5 registry keys on them).
* Never write into `Docs/` for a capability whose ledger `maturity` is ≤1
  except to mark it experimental or dead.
* Every edit's commit message or changelog line names the capability id.

## 6. Verification

* WS0 row count reconciles to the summary counts (±keyword-classification
  slop, which the reviewed flag makes explicit).
* `tests/test_docs_ledger_conformance.py` green.
* Re-run CL1 (the code-grounded ledger audit) after WS3: the
  `doc_discrepancy` under-claim count should fall from 123 toward the
  maturity-≤2 residue (~16). That re-run is the only honest measure; a doc
  that reads as complete is not evidence.

## 7. Open questions for the owner

1. Should `FeatureInventory.md` become a *generated* index from the ledger
   (as the glossary will be), or stay hand-written with the conformance
   test as the guard? Generated removes the drift class entirely but changes
   the doc's voice.
2. The three dead-code items need a code decision first (delete vs. mark
   experimental). Make that decision before WS1, or let WS1 write
   "experimental, unwired" and revisit?
3. WS5 replaces a document some readers may link to. Keep the old file at a
   redirect stub, or remove it?
