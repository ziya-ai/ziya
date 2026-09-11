# Memory Subsystem Redesign — Admission, Promotion, Organization, Measurement

**Date:** 2026-09-08 · **Basis:** 13-iteration improvement loop (`.ziya/memory-improvement/final-report.md`),
baseline diagnosis (`baseline.md`), rubric v1 (`rubric.md`), and a fresh code audit of
`app/memory/{extractor,lifecycle,organizer}.py`, `app/storage/{memory,proposals}.py`, `app/models/memory.py`.
**Product decision (user-approved, middle position):** layers `architecture`, `decision`,
`negative_constraint` promote on QUALITY alone after a short age with NO corroboration requirement;
`preference`, `domain_context`, `lexicon`, `process` and everything else keep the existing
corroboration-or-use requirement. Corroboration becomes a confidence bonus everywhere; contradiction
and search-use still drive UPDATE/archival. Decay applies only to low-grade, contradicted, or
non-fast-track proposals.
**Environment constraint:** embeddings are DISABLED in this workspace. Everything below must work
keyword/tag-only; embeddings are an optional improvement only.

---

## 0. Diagnosis verification (against code as of this audit)

All four diagnoses in the task givens are **CONFIRMED**, with two nuances:

1. **ADMISSION — CONFIRMED.** `app/memory/extractor.py:1363-1364`:
   `salience = _count_salience_hits(messages); if salience == 0: return {"skipped": True, "reason": "no_salience_signal", ...}`
   skips the extraction model call entirely. `_count_salience_hits` (`extractor.py:328-343`)
   iterates messages and `continue`s any role not in `("human", "user")` (role filter at
   l.337-339) — assistant turns never count. The same user-only check also gates each window (`extractor.py:1389`:
   `if _count_salience_hits(win) == 0: continue`). *Nuance:* reference detection
   (`extractor.py:1319-1348`) runs before the gate, so reference proposals survive a
   zero-salience conversation — but no extracted memories do. The prior loop measured 5/24 golden
   chats producing zero memories through this gate, and H16 (assistant-inclusive salience) was the
   only lever that moved coverage (0.47→0.57) and retrievability (0.45→0.55), at −0.04 precision.

2. **PROMOTION — CONFIRMED.** `app/memory/lifecycle.py:98-113` `_evaluate_promotion` promotes only on:
   `corroborations >= 1 and has_use` (l.104-105), `corroborations >= 2` (l.106-107),
   `layer == "reference" and has_use` (l.108-109), or `search_hit + response_match` (l.110-111).
   `_evaluate_archival` (`lifecycle.py:115-148`) returns `"decayed"` at
   `age >= ARCHIVAL_AGE_THRESHOLD` (=7, l.51) when `corroborations == 0 and not has_use`
   (l.135-136), with hard `EXPIRY_AGE_THRESHOLD = 21` (l.59). A durable fact taught once never
   corroborates (`ProposalsStore._project`, `app/storage/proposals.py`, counts only *distinct
   foreign* conversations), so ~88% of lifetime proposals decay (baseline: 66 active / 535 lifetime).

3. **ORGANIZATION — CONFIRMED.** `app/memory/organizer.py:30` `CLUSTER_BATCH_SIZE = 40`;
   `bootstrap_mindmap` clusters per batch (`organizer.py:381-382`) and merges across batches only
   by *identical* handle (the `merged` dict, `organizer.py:389-401`) or exact normalized-handle
   collision (`organizer.py:427-441`). `_find_matching_node` (`organizer.py:623-640`) matches
   roots only, requiring `score = 2*|handle_words∩| + 3*|tags∩| >= 4` — one shared tag (3) or one
   shared handle word (2) is not enough, so near-duplicate domains mint new roots. Baseline:
   119 nodes / 97 roots / 94 empty for 66 memories. *Nuance:* `MemoryStorage.repair_mindmap`
   (`app/storage/memory.py`, incl. `_merge_duplicate_roots`) already exists and would prune empties
   and merge identical-handle roots, but `reorganize` (`organizer.py:511-606`) never calls it.

4. **MEASUREMENT — CONFIRMED.** The prior loop's acceptance rule was "composite ≥ +0.01 over latest
   accepted" while the scorer's measured run-to-run noise is ~±0.03 (final-report.md: H14 rejected
   explicitly because its true effect was "an order of magnitude below the whole-pipeline scorer's
   ~±0.03 extraction-noise floor"). The bar was inside the noise; real sub-0.03 improvements were
   indistinguishable from noise and rejected.

---

## 1. ADMISSION — model triage replaces the user-only regex gate

### 1.1 What changes

`run_post_conversation_extraction` (`extractor.py:1299`) keeps its cheap structural pre-filter and
replaces the regex salience gate with a small-tier model **triage** call:

```
KEEP  (structural pre-filter, order unchanged):
  - reference detection (before all gates, as today)
  - human_turns >= MIN_HUMAN_TURNS (=3)                 extractor.py:1357
  - len(strip_conversation(messages)) >= 200           extractor.py:1367  (move this check
                                                        BEFORE the triage call so we never pay
                                                        a model call for a stub conversation)
REPLACE (extractor.py:1363-1364):
  - _count_salience_hits(messages) == 0  →  skip
WITH:
  - hints = await triage_conversation(full_stripped)
  - hints is None   → triage FAILED → fall back to regex: proceed iff
                      _count_salience_hits(messages) > 0  (current behavior, both the skip
                      and the proceed side; NEVER unconditional extraction)
  - hints == []     → skip, reason "triage_none"
  - hints non-empty → proceed to windowed extraction, passing hints in
```

### 1.2 The triage call

New module-level constants and function in `extractor.py`:

```python
TRIAGE_MAX_HINTS = 8            # hard cap on hints kept from the triage response
HINT_ADMITTED_WINDOW_CAP = 2    # max windows admitted per conversation SOLELY via hint overlap
```

```python
async def triage_conversation(stripped: str) -> Optional[List[Dict[str, str]]]:
    """One small-model call over the whole stripped conversation (user AND
    assistant turns — strip_conversation already labels both roles).
    Returns [] (nothing durable), a list of hint dicts, or None (call failed)."""
```

**Service-model category:** `memory_triage`, added to `SERVICE_MODEL_OVERRIDES`
(`app/config/models_config.py:166`) pinned to the same Haiku-4.5 tier as `memory_extraction`:

```python
"memory_triage": {
    "bedrock": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "google": "gemini-2.0-flash",
    "openai": "gpt-5.5-mini",
    "anthropic": "claude-haiku-4-5-20251001",
},
```

Rationale: `call_service_model(category=...)` resolution (`app/services/model_resolver.py:108-178`)
falls back to the endpoint default (Nova Lite on Bedrock) for unlisted categories, and this codebase
has twice measured lite-tier failing at exactly this kind of judgment (`memory_extraction` override
comment; `intent_judge` measured 5/9 on a simpler yes/no task). Triage is the single gate deciding
whether extraction ever sees the conversation — a false NONE loses everything — so it gets the
Haiku tier. Per-run override comes free via the existing env convention:
`ZIYA_MEMORY_TRIAGE_MODEL` (resolver line 122), mirroring how eval.py A/Bs
`ZIYA_MEMORY_EXTRACTION_MODEL`. Call parameters: `max_tokens=768, temperature=0.0`.

**Triage system prompt** (verbatim; new constant `TRIAGE_SYSTEM_PROMPT`):

```
You are a TRIAGE filter for a memory system.  You read a transcript of a
conversation between a USER and an ASSISTANT (you are not a participant) and
decide whether it contains any DURABLE KNOWLEDGE worth remembering across
sessions: facts, decisions with reasons, vocabulary, constraints, or
principles that would still be true and useful in three months to someone
starting a brand-new conversation.

Durable knowledge may be taught by EITHER role: users teach facts, and
assistants state architecture, constants, and constraints that the user
accepts.  Count both.

Do NOT count: bug symptoms being debugged, editing instructions, refactoring
notes, code narration, build/test state, TODO items, or anything that becomes
false the moment the current task finishes.

Output a single JSON object, first character '{', no markdown, no prose:
{"candidates": [
   {"gist": "<one sentence naming the durable fact, self-contained>",
    "layer": "<one of: domain_context|architecture|lexicon|decision|negative_constraint|preference|process|personal>",
    "quote": "<up to 15 verbatim words from the transcript where this is established>"}
]}
Return {"candidates": []} if nothing qualifies.  List at most 8 candidates —
the MOST durable ones.  This is triage, not extraction: gists may be rough;
a downstream extractor verifies each one against the transcript.
```

The user message is the same BEGIN/END-marked transcript wrapper `extract_memories` uses
(`extractor.py:887-891`), reusing `full_stripped` (no second stripping pass).

**JSON output contract and parsing:** the response must contain a JSON object with key
`candidates` holding a list of objects with string fields `gist`, `layer`, `quote`. Parsing:
find the first `{`, walk balanced braces (same technique as `_extract_json_array`,
`extractor.py:501`, generalized or duplicated as `_extract_json_object`). Validation per item:
`gist` non-empty; `layer` must be in `MEMORY_LAYERS` minus `reference`/`active_thread`, else
coerced to `domain_context`; `quote` optional (default `""`), truncated to 120 chars. Keep at most
`TRIAGE_MAX_HINTS` items. Any of: exception from `call_service_model`, no balanced object, non-dict,
`candidates` not a list → return `None` (⇒ regex fallback). A parsed `{"candidates": []}` → `[]`.

**Fallback rule (explicit):** `None` (failure) → the caller applies the CURRENT regex behavior
verbatim: skip when `_count_salience_hits(messages) == 0`, proceed (without hints) otherwise.
Failure must **never** admit a conversation the regex would have skipped — no unconditional
extraction path exists.

### 1.3 Per-window admission and the hint budget

In the window loop (`extractor.py:1387-1409`), replace the skip rule:

```
admitted_by_hint = 0
for i, win in enumerate(windows):
    salient = _count_salience_hits(win) > 0        # user-turn regex, UNCHANGED
    hint_match = hints and _window_matches_hint(win, hints)
    if not salient:
        if not hint_match:                          continue
        if admitted_by_hint >= HINT_ADMITTED_WINDOW_CAP:  continue
        admitted_by_hint += 1
    ... proceed exactly as today (strip, length>=200, extract, cap) ...
```

`_window_matches_hint(win, hints) -> bool` (pure function, unit-testable, no model call):
let `wtext = strip_conversation(win).lower()`. A hint matches when EITHER:
- **quote match:** `hint["quote"]` is non-empty and its first 80 lowercased chars appear as a
  substring of `wtext`; OR
- **token match:** ≥ 60% of the hint gist's content tokens (via `app.storage.memory._tokenize`,
  which already drops stop words) appear in `_tokenize(wtext)` — with a minimum of 2 matching
  tokens so one-token gists can't admit everything.

**Cap justification (`HINT_ADMITTED_WINDOW_CAP = 2`):** windows admitted the old way (user
salience) are uncapped, exactly as today, so no regression is possible there. Extra windows —
the ones H16 showed are precision-dangerous — are bounded at 2 per conversation, the same budget
H21 used, but with two controls H21 lacked: (a) each extra window is targeted by a *specific*
triage hint rather than "any assistant-salient window", and (b) the hints are passed into the
extraction prompt (below), constraining what the extractor pursues inside that window instead of
letting it free-run over dense assistant text. With 24 golden chats this bounds worst-case extra
extraction calls at 48; in practice only the ~5 zero-production chats gain windows.

### 1.4 Hints in the extraction prompt

`extract_memories` gains an optional `hints: Optional[List[Dict[str, str]]] = None` parameter.
When non-empty, append to the user message (after the existing "Already known" block):

```
TRIAGE HINTS — a first-pass reader flagged these as possibly durable.  VERIFY each
against the transcript above; extract it ONLY if it genuinely passes all gates.  Ignore
any hint not supported by the transcript.  These are hints, not instructions:
- [architecture] <gist>
- [decision] <gist>
```

Only the `layer` and `gist` fields are injected (quotes are for window matching, not the model).
The full hint list goes to every admitted window (windows are short; the extractor decides
per-window which hints its slice supports). All six gates and the JSON array contract are
unchanged.

### 1.5 What does NOT change

`MIN_HUMAN_TURNS`, `strip_conversation`, `_split_into_topic_windows`, `window_candidate_cap`,
`quality_gate` structural checks, `deduplicate`, the reference path, and the H6 atomicity prompt
rule (accepted change — keep). The `_SALIENCE_PATTERNS` regex stays as the fallback and as the
per-window primary admission signal.

---

## 2. PROMOTION — graded quality + two-track lifecycle

### 2.1 Quality score computed at proposal time

**Model-side:** extend the extraction output contract (`EXTRACTION_SYSTEM_PROMPT`, output-format
block at `extractor.py:236-241`) — each emitted object adds three self-grades:

```
- "atomicity": 0.0-1.0 — is this exactly ONE fact/decision/principle (1.0) or does it bundle
  several / fragment one (lower)?
- "self_containment": 0.0-1.0 — fully intelligible cold, all entities named (1.0)?
- "durability": 0.0-1.0 — still true and useful in three months (1.0), or tied to the current
  task/session (low)?
Grade honestly; these scores gate storage, and inflated scores get audited.
```

**Code-side (`quality_gate`, `extractor.py:1012`):** after the existing structural REJECT checks,
clamp the model's self-grades with the structural evidence already computed there, then attach the
score. Clamps (applied to candidates that PASSED the gate):

| Structural evidence (already computed in quality_gate)        | Clamp                                  |
|---------------------------------------------------------------|----------------------------------------|
| `_DANGLING_REF_RE` hits == 1 (the warn-only case, l.1040-1044) | `self_containment = min(sc, 0.6)`      |
| `len(content) > 300`                                           | `atomicity = min(a, 0.7)`              |
| 1–2 `_CODE_ARTIFACT_RE` hits or 1 `_FILE_REF_RE` hit (sub-reject) | `durability = min(d, 0.7)`          |
| field missing / non-numeric / out of [0,1]                     | that field = 0.0 (never default-high)  |

Composite: `quality = round(0.40*durability + 0.35*atomicity + 0.25*self_containment, 3)`.
Durability weighted highest because gate-3 session artifacts were the #1 baseline failure (57% of
Opus violations); atomicity next (granularity is the unmet 0.85 hard gate); self-containment least
because the structural regex already backstops it.

**Storage:** `MemoryProposal` (`app/models/memory.py`) gains explicit fields
`quality: Optional[float] = None` and
`quality_components: Optional[Dict[str, float]] = None` (`{"atomicity": .., "self_containment": ..,
"durability": ..}`). `run_post_conversation_extraction` copies them onto the proposal before
`proposals_store.add(...)`. `model_config extra="allow"` means old rows deserialize unchanged.

### 2.2 Two-track `_evaluate_promotion` (`lifecycle.py:98`)

New constants in `lifecycle.py`:

```python
FAST_TRACK_LAYERS = {"architecture", "decision", "negative_constraint"}
FAST_TRACK_QUALITY_THRESHOLD = 0.75
FAST_TRACK_MIN_AGE = 2          # activity ticks; see justification below
```

Rule order (first match wins; rules 1–3b are today's rules, verbatim — the CORROBORATION TRACK):

```
1.  corroborations >= 1 and has_use                  -> "corroborated_and_used"
2.  corroborations >= 2                              -> "highly_corroborated"
3.  layer == "reference" and has_use                 -> "reference_used"
3b. search_hit and response_match                    -> "searched_and_used"
4.  FAST TRACK (new):
      layer in FAST_TRACK_LAYERS
      and proposal.get("quality") is not None        # explicit opt-in; see §2.4
      and proposal["quality"] >= FAST_TRACK_QUALITY_THRESHOLD
      and _proposal_age(proposal, current_counter) >= FAST_TRACK_MIN_AGE
      and not _has_signal(proposal, "contradicted")
                                                     -> "quality_fast_track"
```

`_evaluate_promotion` gains a `current_counter: int` parameter (the sweep already holds it at
`lifecycle.py:339`; the archival evaluator's internal promotion re-check at `lifecycle.py:129-131`
passes it through). `preference`, `domain_context`, `lexicon`, `process`, `active_thread`,
`personal`, `reference` and any unknown layer never hit rule 4 — their behavior is bit-identical
to today.

**`FAST_TRACK_MIN_AGE = 2` justification (relative to `ARCHIVAL_AGE_THRESHOLD = 7`):** the age
counter ticks once per extraction run (`_next_activity_count`, `extractor.py:1267`) and lifecycle
runs after each extraction, so age≥2 means at least two subsequent knowledge-bearing events have
completed since the proposal was created. That is the minimum window in which the two demotion
signals that should beat a fast-track promotion — a `contradicted` signal or a same-content
re-extraction that would instead corroborate/UPDATE — can possibly arrive (age 0/1 would promote
within the same or the immediately-next conversation, before any counter-evidence can exist).
It is also well inside the decay window (2 < 7 = `ARCHIVAL_AGE_THRESHOLD`), so a fast-track-eligible
proposal always reaches its promotion age 5 ticks before the old decay rule would have killed it —
the promotion/archival race that produced the 88% decay rate cannot occur for this class. Not
higher: 7/2≈3 would leave only a 4-tick margin and adds no additional signal source, since nothing
else about a never-mentioned-again fact changes between tick 2 and tick 3.

**Corroboration as confidence bonus (both tracks):** in `_promote_proposal` (`lifecycle.py:199`),
set on the new `Memory`:

```python
memory.importance = min(1.0, 0.5 + 0.1 * proposal.get("corroborations", 0))
```

(0.5 is the model default today.) Search already multiplies by `(0.5 + importance)`
(`app/storage/memory.py` search scoring), so corroborated memories rank higher without any new
search code. Corroboration counts continue to be copied onto the memory (`corroborations`,
`corroborated_by`) exactly as today.

**Contradiction signal:** a new signal name, `"contradicted"`, written via the existing
`ProposalsStore.record_signal(pid, name="contradicted", value=<memory_or_conv_id>)`. Writers:
(a) the comparator path in `run_post_conversation_extraction` when `compare_memory` returns a
contradiction verdict against content matching an open proposal, and (b) any future user-dismissal
flow. Until a writer ships, the check is vacuously true — it costs nothing and hardens the rule.
A `contradicted` signal blocks rule 4 permanently (the proposal stays on the corroboration track
and is archivable, §2.3).

### 2.3 `_evaluate_archival` (`lifecycle.py:115`) — decay must not eat fast-track proposals

Insert a fast-track guard between the promotion re-check and the decay rule:

```python
fast_track_eligible = (
    proposal.get("layer") in FAST_TRACK_LAYERS
    and proposal.get("quality") is not None
    and proposal["quality"] >= FAST_TRACK_QUALITY_THRESHOLD
    and not _has_signal(proposal, "contradicted")
)
if corroborations == 0 and not has_use:
    if fast_track_eligible:
        return None          # never "decayed" for lack of corroboration
    return "decayed"
```

Note the promotion-first sweep order (`lifecycle.py:339-346`) plus `FAST_TRACK_MIN_AGE(2) <
ARCHIVAL_AGE_THRESHOLD(7)` makes this guard almost unreachable — an eligible proposal promotes at
age 2 before archival ever fires at age 7 — but it is REQUIRED for the invariant stated at
`lifecycle.py:127-131` ("the two evaluators must not disagree if a caller consults archival
alone"). Decay therefore applies only to: below-threshold proposals (`quality < 0.75` or missing),
contradicted proposals, and non-fast-track layers — exactly the product decision.
The redundancy rule (cos ≥ 0.85, embeddings-only) and `EXPIRY_AGE_THRESHOLD = 21` hard expiry are
unchanged and still apply to everything, including fast-track-eligible rows that somehow linger
(e.g. promotion write failures).

### 2.4 Backward compatibility (existing proposals without quality fields)

The real store has ~53 open probationary rows with no `quality` field. Rules:
- `proposal.get("quality") is None` → **never fast-track** (rule 4 and the archival guard both
  require an explicit non-None value). Conservative default = ineligible, not a synthetic score.
- Their corroboration-track behavior is unchanged — they promote via rules 1–3b or decay as today.
- No migration/backfill pass: quality is only assigned at extraction time by the model that
  emitted the candidate; retro-scoring old rows would use a different judge and pollute the cohort.
- UI/diagnostics that want to display a number may show `quality if quality is not None else "—"`;
  never substitute a numeric default that could later be mistaken for a real grade.

### 2.5 Interaction with `feedback.py` and `rem.py`

- **feedback.py:** unchanged. It records `response_match` on open proposals
  (`feedback.py:461`) and memory_tools records `search_hit` (`memory_tools.py:168`); both keep
  feeding rules 1/3b, which remain the ONLY promotion path for corroboration-track layers, and act
  as accelerators (earlier than age 2) for fast-track layers. Its labile/use tracking on active
  memories is untouched.
- **rem.py:** unchanged. `rem_phase` operates on mature mind-map nodes and active memories
  (synthesis + staleness), never on proposals; nothing in this design feeds it or reads from it.
  Its staleness detection remains the post-promotion safety net for fast-tracked memories that age
  badly — which is the designed compensation for promoting without corroboration.

---

## 3. ORGANIZATION — global reorganize with fixed depth and occupancy

### 3.1 Invariants (enforced at the end of every `reorganize`)

- **Depth ≤ 2:** roots (domains) and their children (topics). No grandchildren.
- **Occupancy ≥ 2:** every node holds ≥ 2 `memory_refs` (children) or, for roots, ≥ 2 refs
  counting descendants. Below it, the node folds (child → parent; root → best sibling).
- **Node-count target:** `N_target = clamp(round(active_memories / 4), 3, 24)` total nodes,
  hard bounds `MIN_ROOTS = 3` (when memories ≥ 6), `MAX_ROOTS = 24`.
- **All keyword/tag based.** Embeddings, when available, only ADD merge candidates (§3.4).

New constants in `organizer.py`:

```python
MIN_NODE_OCCUPANCY = 2
MAX_ROOTS = 24
MIN_ROOTS = 3
TARGET_MEMBERS_PER_NODE = 4
ROOT_MERGE_HANDLE_JACCARD = 0.5   # merge roots whose handle-word Jaccard >= this
ROOT_MERGE_TAG_JACCARD = 0.5      # or whose tag Jaccard >= this AND >=1 shared handle word
```

### 3.2 `bootstrap_mindmap` (`organizer.py:345`) — global clustering

Per-LLM-call batching stays (`CLUSTER_BATCH_SIZE = 40` is a prompt-budget cap, not the problem),
but every batch call now receives the CURRENT global root inventory, refreshed between batches:

1. Compute orphans as today (`organizer.py:352-360`).
2. For each batch: build `existing_domain_info` from the LIVE node set *including roots created by
   earlier batches in this same run* (today it is computed once at l.368-374 and never refreshed —
   that staleness is what mints parallel roots). Include a target hint in the user message:
   `"Aim for about {max(1, len(batch)//TARGET_MEMBERS_PER_NODE)} domains for this batch; strongly
   prefer existing domains."`
3. `_find_matching_node` (strengthened, §3.3) resolves each returned domain against live roots.
4. After ALL batches: run the **consolidation pass** (§3.4). Bootstrap alone can no longer leave
   duplicate or empty roots behind.

### 3.3 `_find_matching_node` (`organizer.py:623`) — stronger merge-in

Keep the scored match but widen acceptance and normalize:

- Normalize handle words: lowercase, strip a trailing `s` from words of length ≥ 4 (cheap plural
  fold: "Diagrams"≈"diagram"); drop the generic words `{"design","system","architecture","memory",
  "memories","management","misc","general"}` from the OVERLAP count only (they still count for
  Jaccard denominators, preventing "Design System" ≡ "Memory System").
- Accept when `score = 2*|handle∩| + 3*|tags∩| >= 4` (today's rule) **OR** handle-word Jaccard
  ≥ `ROOT_MERGE_HANDLE_JACCARD` **OR** (tag Jaccard ≥ `ROOT_MERGE_TAG_JACCARD` and ≥ 1 shared
  handle word).
- Still roots-only; returns best match by score with Jaccard as tiebreak.

### 3.4 The consolidation pass — runs on EVERY `reorganize` (new Phase 1.5)

New `def consolidate_mindmap(store) -> Dict[str, int]` in `organizer.py`, called from
`reorganize` between bootstrap (Phase 1) and relations (Phase 2). Deterministic, no LLM calls:

1. **Repair:** call `store.repair_mindmap()` (`app/storage/memory.py`) — drops dangling refs,
   merges identical-normalized-handle roots, places unplaced actives by tag overlap, prunes empty
   leaves. (Currently never invoked from `reorganize`; this is the single highest-yield line.)
2. **Similar-root merge:** for every root pair, compute normalized handle-word Jaccard and tag
   Jaccard (per §3.3). Merge pairs meeting either §3.3 Jaccard criterion, survivor = larger
   `memory_refs∪descendants` count (tie: lexicographically smaller id). Union refs/tags/children/
   cross_links, re-point `scope.domain_node` (reuse the mechanics of
   `MemoryStorage._merge_duplicate_roots`, generalized to non-identical handles — implement in
   `organizer.py` calling storage primitives, keeping storage policy-free). Repeat until no pair
   qualifies. *Optional embedding improvement:* when the embedding cache is non-noop, also merge
   root pairs whose member-centroid cosine ≥ 0.80 (reuse `maintenance._node_centroid`); guarded by
   the same survivor rules; skipped silently when embeddings are disabled.
3. **Occupancy fold:** children with `len(memory_refs) < MIN_NODE_OCCUPANCY` fold refs into their
   parent and are deleted. Roots with total (self + descendants) refs `< MIN_NODE_OCCUPANCY` fold
   into the sibling root with highest tag-Jaccard (tiebreak: handle overlap, then larger root);
   if EVERY sibling scores 0 overlap, fold into a lazily-created `domain_general` root
   (handle "General", tags `[]`). `domain_general` itself is exempt from folding but is deleted by
   repair when empty.
4. **Bound enforcement:** while root count > `MAX_ROOTS`: merge the most-similar root pair (max
   tag-Jaccard, tiebreak smallest combined size) regardless of threshold. `MIN_ROOTS` is a floor
   for `maybe_divide_node` eagerness, not a splitting trigger — never synthesize domains to reach it.

### 3.5 `should_auto_organize` (`organizer.py:608`) — trigger on structure debt, not just orphans

```
return (orphan_count >= AUTO_ORGANIZE_ORPHAN_THRESHOLD)        # today's rule, kept
    or (roots > 0 and roots > max(12, 2 * N_target))           # root explosion
    or (nodes > 0 and empty_node_fraction > 0.3)               # hollow map
```

where `empty_node_fraction = nodes with 0 refs and 0 children / total nodes`. All three are
computable from `list_mindmap_nodes()` + `list_memories(status="active")`, both already loaded there.

### 3.6 `maintenance.maybe_divide_node` (`maintenance.py:359`) — depth + occupancy aware

Two changes: (a) **depth guard** — return `[]` immediately when `node.parent is not None`
(children never divide; enforces depth ≤ 2); (b) **occupancy guard** — require
`len(best_mids) >= max(CELL_DIVISION_MIN_CLUSTER, MIN_NODE_OCCUPANCY)` AND
`len(memories) - len(best_mids) >= MIN_NODE_OCCUPANCY` (never leave the parent below occupancy).
Threshold constants otherwise unchanged.

### 3.7 Expected shape (66 active memories)

`N_target = clamp(66/4, 3, 24) = 17` nodes total, all non-empty, ≤ 2 levels — versus today's
119 nodes / 97 roots / 94 empty. The progressive prompt (`prompt.py get_memory_prompt_section`)
then advertises ~a dozen meaningful handles, making `memory_context`/`memory_expand` walks land.

---

## 4. MEASUREMENT — acceptance evidence for stages 2–4

The prior loop's ±0.03 noise floor vs +0.01 accept bar rejected real improvements. Replace with:

### 4.1 Golden scorer, 3 runs per arm

For each stage: run the 24-chat golden scorer **3 times per arm** (baseline arm = code as of the
previous accepted stage, i.e. the `.ziya/memory-redesign/backup/<stage>/` copies restored;
candidate arm = the stage's change):

```bash
# baseline arm (restore backups first), r ∈ {1,2,3}:
python3 scripts/memory_quality_score.py --label s<N>-base-r<r>
# candidate arm (change applied):
python3 scripts/memory_quality_score.py --label s<N>-cand-r<r>
```

From the 3 scorecards per arm (`.ziya/memory-improvement/scorecards/<label>.json`), compute mean
and **sample sd** per dimension and for the composite; report the full 5×2 table.

**Accept iff BOTH:**
1. `mean_composite(cand) − mean_composite(base) > 2 × sd_composite(base)`  (with the observed
   ±0.03 noise, sd(base) ≈ 0.015–0.03, so the bar is ~0.03–0.06 — outside the noise by
   construction, unlike the old +0.01 bar), **and**
2. `mean_precision(cand) >= 0.85`  (the precision FLOOR; this **replaces** the prior loop's
   `extracted_count ≤ 198` ceiling, which is dropped — over-production is only a problem when it
   admits junk, and junk is what precision measures).

Additional standing gates from rubric v1 stay: granularity/self-containment means must not fall
below their prior accepted values by more than 2×sd(base) on those dimensions, and protected-store
hashes must be reported unchanged by every run (the scorer already asserts this).

### 4.2 Lifecycle simulation — primary end-to-end signal, with a downstream-utility metric

Command (both arms, identical seeds):

```bash
python3 scripts/run_memory_lifecycle_simulation.py --seed-count 10 --later-count 10 --split clustered
```

**Downstream-utility metric (`later_hit_rate`), computed from the sim's own sandbox state at the
end of the run:** after the SEED phase completes (extraction + full lifecycle over 10 seed chats,
producing a sandbox store of ACTIVE memories), for each of the 10 LATER-phase chats:

1. Take that chat's **first user message**, stripped via `extractor._strip_artifacts`, truncated
   to 300 chars → the query `q`.
2. Run `MemoryStorage.search(q, limit=3)` against the sim's sandboxed store (the same
   monkey-patched `get_memory_storage()` the sim already uses).
3. The chat scores 1 if ≥ 1 returned memory is a **seeded active** memory (id in the set promoted
   during the seed phase), else 0.

`later_hit_rate = hits / 10`. The sim script (writable under `scripts/`) gets a
`--report-utility` flag that prints `{"later_hit_rate": x, "per_chat": [...], "active_seeded": n,
"probationary_open": n}` as its final JSON line; both arms report it. Acceptance use:

- **Stage 2 (promotion):** expect `active_seeded` to rise materially (the 88%-decay class starts
  promoting) and `later_hit_rate` to rise or hold; REJECT if `later_hit_rate` drops > 0.1
  (one chat) below baseline arm.
- **Stage 3 (organization):** `later_hit_rate` must not drop (search is flat-store; organization
  must not perturb it); the stage's own evidence is structural: node count within
  `[MIN_ROOTS, MAX_ROOTS]`, zero empty nodes, zero occupancy violations, depth ≤ 2, printed by
  the sim from the sandbox mindmap.
- **Stage 4 / admission (stage numbering per implementation order):** expect `later_hit_rate` ↑
  via more seeds captured; same −0.1 floor.

Run each arm's sim **twice** (extraction is stochastic); report both; a metric "rises" only if
both candidate runs ≥ both baseline runs is too strict — use means with the −0.1 floor on means.

### 4.3 Store safety (every run, both instruments)

Record sha256 of `~/.ziya/memory/{memories.json,probationary.jsonl,mindmap.json,embeddings.npz,
activity_counter.json}` before and after every scorer/sim invocation; any change = abort the
stage and report. (The scorer already self-asserts; record independently anyway.)

---

## 5. RISKS and rollback, per stage

Backups: before editing any `app/` file, copy to `.ziya/memory-redesign/backup/<stage>/<basename>`.
Rollback for every stage = restore those copies verbatim (all changes are file-local; no data
migrations exist in this design — new proposal fields are optional, old rows deserialize as-is).

**Stage ADMISSION (extractor.py, models_config.py)**
- *Risk:* triage false-NONEs silently zero out extraction for a class of chats (worse than the
  regex it replaces). *Mitigation:* triage failure falls back to regex, and the 3×-golden-scorer
  coverage dimension directly measures it; the 5 known zero-production golden chats are the canary.
- *Risk:* precision regression via hint-admitted windows (the H16–H21 failure mode).
  *Mitigation:* `HINT_ADMITTED_WINDOW_CAP=2`, hints constrain the extractor in-window, precision
  floor 0.85 is a hard accept gate.
- *Risk:* +1 model call per conversation (cost/latency). *Mitigation:* Haiku-tier, 768 max tokens,
  fire-and-forget background path; triage replaces N window calls with 0 on NONE conversations,
  so total calls can *fall* on non-teaching traffic.
- *Rollback:* restore `extractor.py` + `models_config.py`; no persistent state involved.

**Stage PROMOTION (extractor.py prompt/quality_gate, models/memory.py, lifecycle.py)**
- *Risk:* self-graded quality is inflated (model grades its own homework) → junk fast-tracks into
  the active store. *Mitigation:* structural clamps cap the grades; threshold 0.75 with 0.40
  weight on durability; only 3 layers eligible; `rem.py` staleness + `cleanup_corpus` remain the
  post-promotion net; precision floor + lifecycle-sim `active_seeded`/`later_hit_rate` gate accept.
- *Risk:* active-store growth (the 88%-decay class now promotes). *Mitigation:* this is the
  POINT; bounded by quality threshold and the 3-layer scope; organization stage keeps the map
  navigable; monitor `active_seeded` in the sim for runaway (>3× baseline promotions ⇒ re-examine
  threshold before accepting).
- *Risk:* old proposals mis-handled. *Mitigation:* `quality is None` ⇒ never fast-track (§2.4),
  covered by explicit tests.
- *Rollback:* restore the three files. Any memories fast-track-promoted during evaluation live
  only in sandbox stores (hard rule (a)); the real store is untouched until the stage is accepted
  and deployed.

**Stage ORGANIZATION (organizer.py, maintenance.py)**
- *Risk:* aggressive merging fuses genuinely distinct domains (irreversible in-place). *Mitigation:*
  Jaccard 0.5 thresholds are conservative; survivor keeps union of refs so no memory is lost —
  the failure mode is a too-broad node, which `maybe_divide_node` can later re-split; evaluation
  runs on sandbox mindmaps only.
- *Risk:* occupancy folding churns `scope.domain_node` on many memories (many writes). *Mitigation:*
  use `save_many` batching; consolidation is idempotent so a crash mid-pass re-converges next run.
- *Risk:* retrievability regression — none expected (search never consults the mindmap), verified
  by the sim's `later_hit_rate` non-drop gate.
- *Rollback:* restore files. A real-store mindmap reshaped by a post-accept deployment can be
  rebuilt at any time: the mindmap is derived data (`memories.json` is authoritative);
  `repair_mindmap` + `reorganize` regenerate it.

**Stage MEASUREMENT (scripts only)**
- *Risk:* the 2×sd bar with n=3 is a weak sd estimate. *Mitigation:* sd is floored at 0.015 (half
  the documented ±0.03) when the 3-run sample sd comes out lower, so a fluke-tight baseline can't
  make the bar trivially passable.
- *Risk:* `later_hit_rate` on n=10 chats is coarse (0.1 granularity). *Mitigation:* it is a
  guardrail (−0.1 floor) and a direction check, never the sole accept criterion; the golden
  scorer remains the arbiter.
- *Rollback:* scripts are additive (`--report-utility` flag); restoring script backups suffices.

---

## Appendix: function/line anchors (audited 2026-09-08)

| Anchor | Location |
|---|---|
| conversation salience skip | `app/memory/extractor.py:1363-1364` |
| user-only salience scan | `app/memory/extractor.py:328-343` (role filter l.337-339) |
| per-window salience skip | `app/memory/extractor.py:1389` |
| window split | `app/memory/extractor.py:359` |
| extraction model call / category | `app/memory/extractor.py:851, 906-912` (`memory_extraction`) |
| quality_gate | `app/memory/extractor.py:1012` |
| activity counter | `app/memory/extractor.py:1267` |
| orchestrator | `app/memory/extractor.py:1299` |
| promotion rules | `app/memory/lifecycle.py:98-113` |
| archival rules / decay | `app/memory/lifecycle.py:115-148` (decay l.135-136) |
| ARCHIVAL_AGE_THRESHOLD=7 / EXPIRY=21 | `app/memory/lifecycle.py:51, 59` |
| promote-writes-memory | `app/memory/lifecycle.py:199` |
| sweep order (promotion first) | `app/memory/lifecycle.py:341-348` |
| CLUSTER_BATCH_SIZE=40 | `app/memory/organizer.py:30` |
| per-batch clustering | `app/memory/organizer.py:381-382` |
| stale existing_domain_info | `app/memory/organizer.py:368-374` |
| _find_matching_node score>=4 | `app/memory/organizer.py:623-640` |
| should_auto_organize | `app/memory/organizer.py:608` |
| reorganize pipeline | `app/memory/organizer.py:511-606` |
| maybe_divide_node | `app/memory/maintenance.py:359` |
| repair_mindmap / _merge_duplicate_roots | `app/storage/memory.py` (MemoryStorage) |
| search (H13 sub-token, keep) | `app/storage/memory.py` `MemoryStorage.search` |
| proposal projection / corroboration | `app/storage/proposals.py` `_project`, `add` |
| MemoryProposal model | `app/models/memory.py` |
| SERVICE_MODEL_OVERRIDES | `app/config/models_config.py:166` |
| category env override convention | `app/services/model_resolver.py:122` |
| proposal use signals | `app/memory/feedback.py:461` (`response_match`), `app/mcp/tools/memory_tools.py:168` (`search_hit`) |
