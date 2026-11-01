# Recipe Store — reusable procedures with a contract

**Status:** Accepted direction (2026-10-15), not implemented. Slices below.
**Depends on:** `structured-memory-system.md` (probation lifecycle, extraction pass),
`evidence-ledger.md` (claims, `derivation.code_ref`), `typed-findings.md` (schema
registry, `project:` refs), `capabilities-hub.md` (discovery tiers, Bench kinds),
`dataset-store.md` (`describe_dataframe`).
**Amends:** `work-primitives-taxonomy.md` (adds a fourth primitive; see §9).

---

## 1. Problem

A conversation that works out *how to answer a question* — a ClickHouse query that
finds operational impact by ticket, a script that mines a data shape, a predicate
that means "this condition is happening again" — leaves five fragments in five
stores, and nothing binds them or makes them findable by intent:

| Fragment | Lives in | Why it is lost |
|---|---|---|
| Procedure (script, query) | `/tmp`, `.ziya/task-runs/`, occasionally `~/.ziya/skills/<x>/scripts/` | Banking requires authoring a skill; nothing offers it mid-conversation |
| Shape (what the result really is) | `.ziya/schemas/project:*` — only ever written for task-card contracts | Discovered shapes are described in prose, never deposited |
| Meaning (what a row signifies, caveats, time semantics) | memory store, as facts | A fact that points at nothing executable is re-mined |
| Validation (it ran; here is the hash) | evidence ledger | Claims key to a run, not to a reusable procedure |
| Heritage (conversation, turn, ticket, known since) | `chat_search` | Reachable only if you remember to look |

The user re-mines data shapes mined weeks earlier, and operational signatures
("this query + this condition = impact attributable to ticket X, known since
date Y") are rediscovered per conversation.

## 2. The asset: a recipe

A recipe is one directory, **shaped as a skill** so every existing discovery,
tiering, placement and resource mechanism applies, with one additional
required file:

```
.ziya/recipes/opimpact-by-ticket/
  SKILL.md           # frontmatter: name, description, kind: recipe, domain, recipe_kind
                     # body: intent, meaning, caveats (prose for the model)
  signature.json     # the contract — machine-readable (§3)
  provenance.json    # heritage + lifecycle status (§4)
  procedure/         # script(s) or parametrised query template(s)
  references/        # optional, as for skills
```

Why `SKILL.md` and not `RECIPE.md`: `app/services/skill_discovery.py` already
scans `.ziya/skills`, `.agents/skills`, `.skills`, `SKILLS`, `~/.ziya/skills`
and the harness dirs, applies project-shadows-user by name, renders the
`skill_resources` manifest, and feeds the Bench. A recipe dir under
`.ziya/recipes/` (project) or `~/.ziya/recipes/` (user) is added to that scan;
`kind: recipe` in frontmatter selects the stricter validation (signature
required) and the Bench kind. Other harnesses that read the same tree see a
usable skill. Recipes never shadow skills: the shadowing key becomes
`(kind, name)`.

Recipes are **content-addressed**: `signature.sha256` is the canonical hash of
`signature.json` + every file under `procedure/` (same `canonical_json` /
`sha256_file` as `app/utils/evidence/store.py`). Every claim derived by a recipe
cites `recipe:<name>@<sha>`; an edit changes the sha and `ledger_verify` reports
the old claims as drifted. That is the whole versioning story.

## 3. `signature.json` — the ad-hoc contract

The part the output-contract validator cannot carry is the *meaning*; this file
holds both halves so a reader who was not in the originating conversation can
run the recipe and interpret its result.

```json
{
  "recipe_kind": "query",            // query | script | detector
  "intent": "Rows of operational impact attributable to a ticket over a window",
  "domain": ["domain_netscan_cards", "ops"],   // memory mind-map node ids + free tags
  "inputs": {
    "ticket":  {"type": "string", "required": true},
    "window_h": {"type": "integer", "default": 24, "unit": "hours"}
  },
  "output": {
    "shape": "rows",                   // rows | scalar | text | chart | file
    "schema": "project:opimpact-row",  // required for rows/scalar; null otherwise
    "row_means": "one (cell, hour) with impacted-minute count",
    "units": {"impact_min": "minutes"},
    "time": {"column": "ts", "zone": "UTC", "lookback_from": "now",
             "source_backfills": true, "settle_hours": 2},
    "baked_filters": ["excludes Fred-L cells", "status != test"]
  },
  "runtime": {"entry": "procedure/query.sql", "via": "ch.py", "source": "grafana:clickhouse"},
  "attribution": {                     // detector only
    "ticket": "KUIPER-12345", "known_since": "2026-09-30",
    "condition": "impact_min > 0 in two consecutive hours"
  },
  "sha256": "…"
}
```

Rules:
- `output.schema` is a `project:` ref in the existing `.ziya/schemas/` directory
  (or inline, ≤16 KB), validated by `app/utils/output_contracts.py` unchanged.
  Required for `shape: rows|scalar`; `null` allowed for `text|chart|file`.
- `inputs` is JSON-Schema-ish by design so `recipe_run` can validate without a
  second validator.
- `time` is mandatory for `query`/`detector`: the "it was true at the time"
  problem (sources back-fill) is the single most common cause of a re-mined
  result disagreeing with the banked one.
- `attribution` makes a detector a detector. A detector's procedure is a
  predicate (returns rows that satisfy the condition, or a boolean scalar).

## 4. `provenance.json` — heritage and lifecycle

```json
{
  "status": "probationary",            // probationary | active | archived
  "created_by": "harvest" | "bank" | "import",
  "origin": {"conversation_id": "…", "message_index": 41, "project_id": "…"},
  "ledger": {"namespace": "…", "obs_ids": ["obs_…"], "claim_ids": ["…"]},
  "datasets": ["ds_…"],
  "corroborations": 1, "corroborated_by": ["conv-…"],
  "used_by": [{"conversation_id": "…", "at": 1760500000000, "sha": "…"}],
  "activity_count_at_creation": 1234,
  "promoted_at": null, "archived_at": null, "archive_reason": null,
  "shared_to": null                      // repo path after promote-to-repo
}
```

Lifecycle mirrors `app/memory/lifecycle.py` exactly, with one rule changed to
suit procedures:

| Memory proposal rule | Recipe rule |
|---|---|
| corroborated ≥1 AND `response_match` | **used by a different conversation** (`recipe_run` or `recipe_get` from `conversation_id ≠ origin`) — the one signal that matters |
| corroborated ≥2 | harvest produced a near-duplicate (same normalised procedure hash, or intent-embedding ≥ 0.9) from a second conversation → corroborate, do not create a twin |
| age ≥ 7 ticks, no signal → archive | same, same thresholds, same activity counter |
| fast-track by quality | `recipe_bank` (explicit user deposit) is active immediately — an explicit save, as with `memory_save` |

Archive never deletes the directory; status flips and discovery hides it.

## 5. Harvest — how recipes come to exist without clerical work

Runs inside the existing post-conversation extraction pass
(`app/memory/extractor.py::extract_memories` is the entry; a sibling
`app/recipes/harvest.py::harvest_recipes` runs on the same transcript and the
same service tier). Candidates are **tool-call patterns, not content**, so the
heuristics are domain-agnostic:

| Pattern in the transcript | Candidate | Shape derivation |
|---|---|---|
| `file_write` of `*.py|*.sql|*.sh` to `/tmp` or `.ziya/`, then `run_shell_command` executing it with exit 0, **twice or more** (iterated, then accepted) | `script` or `query` | none unless the output was piped into `dataset_register` |
| `dataset_register` + ≥1 `analyze` whose result the model then reported from (claims recorded, or numbers in the reply matched the result) | `query` | result `describe_dataframe` → row JSON schema written to `.ziya/schemas/<auto-name>.json` |
| `ledger_record` claims whose `derivation.expression`/`query_text` is non-trivial | `query` | population/column from the claim |
| a predicate the user named as meaning something ("that's the signature of…", "whenever X > 0 the ticket is…") around a query | `detector` | as query, plus `attribution` filled from any ticket id / date in the surrounding turns |

The harvest model writes `SKILL.md` body (intent, meaning, caveats) and
`signature.json`; the executor fills `provenance.json` deterministically. A
candidate whose procedure references absolute paths outside the project, or
embeds a credential shape (`redaction.py` patterns), is dropped with a logged
reason, not written. Candidates land `probationary` in the **project** tier.

Harvest never runs code. The last observed successful run is the evidence.

## 6. Tools (all `[DIRECT]` builtins, `app/mcp/tools/recipe_tools.py`)

| Tool | Does | Signals |
|---|---|---|
| `recipe_search(query, domain?, output_shape?, kind?, include_probationary=True)` | match on intent embedding + domain tags + output shape; probationary hits flagged | `search_hit` |
| `recipe_get(name)` | SKILL.md body + signature + provenance + citing claims; adds the dir to `readable_skill_dirs` for this turn | `used` when caller ≠ origin |
| `recipe_run(name, params)` | validate `params` against `inputs`; run `runtime.entry` through the **shell tool path** (allowlist, write gate, consent all apply); validate output against `output.schema`; record a ledger observation + claim with `code_ref=recipe:<name>@<sha>` | `used` |
| `recipe_bank(name, procedure_path, signature, body)` | explicit mid-conversation deposit; active at once; the "bank this" verb | — |
| `recipe_retract(name)` | archive a recipe **this conversation** created (same ownership gate as `memory_retract_proposal`) | — |

`memory_search` falls through to `recipe_search` and reports a count the way it
reports probationary proposals. Per turn, the prompt gets a one-line nudge
("N recipes match this conversation's domain") using the project-hint domain
weighting memory already computes — a count, never content, so it costs nothing
until used.

## 7. Surfaces

- **Bench:** `kind: recipe` is a card kind alongside skill/mcp; placement
  semantics unchanged (`always` injects the SKILL.md body — rarely wanted;
  default `ondemand`). Detail drawer shows signature, provenance, citing claims
  (`ledger_query prefix=`), run count, and two verbs: **promote to user tier**
  (copy to `~/.ziya/recipes/`) and **share to repo** (copy to a configured team
  skills repo path, e.g. the KuiperAICapabilities checkout; writes `shared_to`).
- **Probation queue:** the existing memory-proposal review surface gains a
  recipes tab; same approve/dismiss verbs.
- **CLI:** `ziya recipes list|show|run|bank|share`.

## 8. Decisions recorded

1. **Propose-only harvest, auto-promote on cross-conversation use.** The user
   lives with a probation queue already; a wrong recipe that silently banks
   itself and gets run is worse than a queue.
2. **Detectors are born project-tier like everything else.** The team repo
   path may not exist on a given install; special-casing birth tier adds a
   failure mode. The Bench "share to repo" verb is one click and the harvest
   flags detectors with a share nudge.
3. **Output schema required for `rows`/`scalar` to leave probation**; `text` /
   `chart` / `file` recipes need `row_means`-equivalent prose only. A query
   whose shape cannot be derived stays probationary with the reason shown.
4. **Name: "recipe".** Considered playbook (implies a human procedure),
   capability deposit (accurate, unsayable), snippet (undersells the contract).
5. **Recipes are files, user-owned, editable.** Editing changes the sha; the
   ledger surfaces drift. No in-app editor in v1 (same as skills).
6. **Harvest never executes anything.** Evidence is the observed run.

## 9. Taxonomy amendment

`work-primitives-taxonomy.md` gains a fourth row:

| Primitive | Scope | Nature | Lifecycle | Visibility | Backed by |
|---|---|---|---|---|---|
| **Recipes** | project / user / team | *settled procedure* + contract | probationary / active / archived | user-owned, Bench + queue | `.ziya/recipes/`, `~/.ziya/recipes/` |

A recipe is on the knowledge axis with memory (settled, cross-session,
user-owned), not the work axis. The forbidden flow holds: completing work does
not bank a recipe; harvest is evidence-driven (a procedure that *ran and was
accepted*), and promotion requires reuse. A memory may point at a recipe; a
recipe's provenance points at claims; neither is derived from the other
automatically.

## 10. Slices

Each independently mergeable; tests named are the seam tests, written to fail
before the slice lands.

### 1. Model, layout, discovery
`app/models/recipe.py` (`Signature`, `Provenance`, `Recipe` wrapping `Skill`);
`app/recipes/store.py` (`RecipeStore`: roots per tier, `list(status)`, `get`,
`sha`, `write`, `set_status`, `record_use`, `corroborate`); `skill_discovery`
scans `.ziya/recipes` + `~/.ziya/recipes`, requires `signature.json` when
`kind: recipe`, shadowing key `(kind, name)`; recipe dirs in
`readable_skill_dirs()`.
**Tests:** a recipe dir discovers with kind=recipe; missing `signature.json`
rejected with reason; a skill and a recipe of the same name coexist; sha
changes when `procedure/` changes and not when `provenance.json` changes;
`ZIYA_HOME` honoured.

### 2. Search / get tools + memory fall-through
`recipe_tools.py`: `recipe_search`, `recipe_get`; registration in the builtin
registry; `memory_search` appends a recipe count and top hit.
**Tests:** search by intent finds a probationary recipe flagged; by output
shape; `recipe_get` from a different conversation writes `used_by`; from the
origin conversation does not; `memory_search` result carries the count (and
the existing memory assertions are unchanged).

### 3. Bank, retract, lifecycle
`recipe_bank`, `recipe_retract`; `app/recipes/lifecycle.py` hooked into
`run_lifecycle_pass` (same counter, same thresholds); corroboration by
normalised-procedure hash and intent embedding.
**Tests:** bank → active immediately; cross-conversation use → promoted on the
next pass; unused for `EXPIRY_AGE_THRESHOLD` ticks → archived, directory
intact; second harvest of a near-duplicate corroborates instead of creating;
retract denied across conversations (fails closed, as memory).

### 4. Harvest
`app/recipes/harvest.py` run from the extraction pass; candidate detection from
tool-call history; schema derivation from `describe_dataframe`; credential and
foreign-path drops.
**Tests:** fixture transcripts — (+) write-run-run script → candidate; (+)
`dataset_register`+`analyze`+reported numbers → query recipe with derived
`project:` schema that validates the real result rows; (+) ticket + condition
phrasing → detector with `attribution`; (−) script run once → none; (−) script
with a credential-shaped literal → dropped and logged; (−) nothing executed →
none. The pass must not call any model when there are no candidates.

### 5. `recipe_run`
Parameter validation against `inputs`; dispatch via the shell tool path with
the recipe dir as cwd; output validation via `output_contracts`; ledger
observation + claim with `code_ref`; `used` signal.
**Tests:** bad params rejected before anything runs; a blocked command inside
`procedure/` is refused by the same allowlist (no bypass through recipes);
output failing the schema fails the run and says which field; the recorded
claim's `code_ref` carries the current sha; editing `procedure/` then
`ledger_verify` reports the earlier claim drifted.

### 6. Bench kind + queue tab + promote/share
`CapabilityCard` kind=recipe, detail drawer, two verbs; recipes tab in the
proposal review surface; `shared_to` written on share.
**Tests:** hub-makes-no-fetch holds; kind=recipe never placed `always` by
default; share copies the directory verbatim (sha equal) and records the path;
promote-to-user makes the user copy shadowed by the project copy.

### 7. Nudge + CLI
Per-turn domain-match count in the prompt; `ziya recipes …`.
**Tests:** count present only when a domain matches; zero cost (no body)
when unused; CLI `run` records the same claim shape as the tool.

## 11. Open questions (not blocking slice 1)

- Whether `always`-placed recipes should inject the signature rather than the
  body; likely yes, revisit at slice 6.
- A `ziya:detection` builtin schema for detector output, so detectors across
  domains share one shape for the operational-impact dashboard.
- Harvest for Task Card runs: a `Repeat` that iterates a query is the strongest
  possible reuse signal and the transcript is in the run record, not a chat.
