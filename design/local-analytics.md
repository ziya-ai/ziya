# Local Analytics (Analytics Data Plane, Phase 2)

Status: **proposed** · Owner: maintainers · Extends `design/dataset-store.md`
(phase 1). Related: `design/research-harness.md` (build order: after M0,
before M1), `design/evidence-ledger.md` (analyze calls become attested
observations), `design/research-blocks.md` (`exec` derivations reuse this
engine), `design/query-broker.md` (broker results land here as datasets).

## Problem

Phase 1 moved rows out of the prompt, but any computation over them is
still done by the model: either in its head, in a Vega-Lite `transform`, or
in an ad-hoc `python3 -c` one-liner whose code is never kept. The
2026-10-02 network scan showed the failure modes that matter:

1. **Telemetry gaps read as loss.** A unit that reported nothing for an hour
   counted as an hour of outage, because nothing distinguished "no data"
   from "bad data".
2. **Counter resets read as huge negative or positive deltas.** Cumulative
   counters (`nss_dropped_packets_acc`) reset on reboot.
3. **Overlapping incidents double-counted.** Two alarms covering the same
   minute added two minutes of downtime.
4. **A fleet total stood alone.** "unit-seconds per day" hid that a handful of
   units carried most of it. (Standing reporting rule: a fleet aggregate is
   always reported with the per-entity distribution — n, mean, median,
   p10–p90, max, SD — per-incident size, incidents per entity per day, and a
   reconciliation back to the total.)
5. **Nothing was reproducible.** The numbers in the report could not be
   recomputed from what the conversation kept.

Each was caught by the user, not the harness. The fix is to make the correct
treatment the cheapest one: tested primitives the model calls by name,
instead of re-deriving them in SQL or prose each time.

## Design

One builtin tool, `analyze`, running read-only SQL in an in-process DuckDB
over registered datasets, project data files and prior results. Results
become datasets. Domain-correct structures ship as SQL table macros.

```
datasets (ds_…)  project files  prior results
        \             |             /
         └── views ── analyze(sql) ─┘   ← guard: sqlglot allowlist,
                         │                 DuckDB external access off,
                         │                 time / memory / row limits
                         ▼
          result → dataset ds_… (inline rows if small, else describe + ref)
                         │
                         └─ lineage: sql, input ids + sha256, engine version
```

### Tool

```
analyze(sql, inputs?: {alias: ds_id | path}, name?, max_inline_rows=200)
```

- `sql` is a single `SELECT` / `WITH … SELECT`. Registered datasets are
  addressable by id (`FROM ds_0123…`) or by the alias given in `inputs`.
- `path` inputs go through `fileio._resolve_and_validate` with the shared
  readable prefixes, exactly like `file_read` and `dataset_register`.
  Supported: Parquet, CSV/TSV, JSON/JSONL. A path input is registered as a
  dataset first, so it gets an id and a `describe`.
- **Result.** Always stored as a new dataset with lineage in `meta.json`.
  The tool returns the dataset's `describe`, plus the rows inline when the
  result has at most `max_inline_rows` rows and 32 KB. Larger results return
  only the describe and `vega_data: {url: "ziya://dataset/<id>"}`, so a chart
  can use them without the rows passing through the model.
- **Errors** return the DuckDB message plus, for a binder error, the
  columns of the tables the query referenced, so the model can retry once
  instead of calling `dataset_describe` first.

`dataset_*` (phase 1) is unchanged. `analyze` joins the `datasets` builtin
category, so it costs one tool description, not a new category.

### Engine and guard

DuckDB, in-process, one connection per call (no state across calls except
the dataset store). Layered, so no single check is load-bearing:

1. **Statement allowlist (sqlglot).** Parse with the DuckDB dialect; accept
   exactly one statement whose root is `SELECT`, `UNION`/`INTERSECT`/
   `EXCEPT` or `WITH` over those. Reject any function named `read_*`,
   `*_scan`, `glob`, `query_table`, `getenv`, or any `httpfs`/`s3` scheme
   string. The macros below are installed by the server, not called through
   this check.
2. **DuckDB configuration.** Inputs are registered as views by the server
   before the connection is locked; then `enable_external_access=false`,
   `autoload_known_extensions=false`, `autoinstall_known_extensions=false`,
   `lock_configuration=true`. `memory_limit` 2 GB, `threads` 4, temp
   directory inside the call's scratch dir.
3. **Limits.** Wall-clock 120 s (interrupted via `con.interrupt()`), result
   cap 500k rows (the phase-1 ingest cap), scratch directory removed after
   the call.

**To verify when building** (DuckDB-version dependent, so pinned in tests
rather than assumed): that a view over a Parquet file registered *before*
`enable_external_access=false` remains readable after it, and that
`query_table` honours the setting. If views over files do not survive,
inputs are loaded as Arrow/pandas relations instead (bounded by the 500k
row cap) and the spec is amended.

### Primitives (table macros)

Installed on every connection. Each has a reference implementation in SQL
templates in `app/utils/analytics/primitives.py` and its own tests on small
hand-checked fixtures. Names are deliberately long and descriptive; the
tool description lists them with one line each.

| Macro | Returns | Rule it enforces |
|---|---|---|
| `bin_with_coverage(t, entity, ts, bin, expected_every)` | entity, bin_start, n_obs, coverage ∈ [0,1] | A bin below the coverage threshold is *unknown*, not zero. Downstream metrics take `coverage` as an input. |
| `counter_deltas(t, entity, ts, value, max_gap)` | entity, ts, delta, reset (bool), gap (bool) | A decrease is a reset: the delta is the new value, not a negative. A gap over `max_gap` yields NULL. |
| `intervals_union(t, entity, start, end)` | entity, start, end | Overlapping or touching intervals merge (gaps-and-islands), so each instant counts once. |
| `intervals_intersect(a, b, entity)` / `intervals_subtract(a, b, entity)` | entity, start, end | Set operations per entity, e.g. "outage while in service". |
| `interval_duration(t, entity, start, end)` | entity, total_s, n_intervals, longest_s | Applied after `intervals_union`, so duration is never double-counted. |
| `runs(t, entity, ts, flag)` | entity, flag, start, end, n_samples | Run-length encoding over a boolean series: incidents from samples. |
| `entity_distribution(t, entity, metric)` | n_entities, total, mean, median, p10, p90, sd, max, top10_share, gini | The per-entity view a fleet total must travel with. Totals reconcile: `total = sum(metric)`. |

`entity_distribution` is also applied automatically: when a result has
exactly one entity-like key column (`*_id`, `entity*`, `device*`, `host*`) and
numeric columns, the tool result carries a `distribution` block per numeric
column next to the rows. The model does not have to remember to ask.

Statistical methods (tests, confidence intervals, bootstrap, regression,
difference-in-differences, change-point detection, natural controls) are
**not** primitives. They go in an analytics skill as recipes built on the
primitives above, using scipy (`ziya[data]`) and statsmodels
(`ziya[stats]`) through the job runner (M3) or, until it exists, through
the shell. Core holds only what has a single right answer.

### Storage format

- **Parquet is canonical when DuckDB is installed.** The server writes it
  with DuckDB's own `COPY … (FORMAT parquet)`; pyarrow is not needed for
  storage, only for fast pandas interchange.
- **Column dictionaries** (units, meaning, source; see
  `design/harness-small-items.md` item 2) are stored in Parquet key-value
  metadata. The JSON sidecar is still written for non-Parquet files.
- **Existing JSONL datasets migrate lazily** on first read by `analyze`:
  converted to `data.parquet`, `meta.json` updated, JSONL removed after the
  Parquet file is verified (row count and column set match).
- **Without `ziya[data]`** everything in phase 1 keeps working on JSONL.
- **Unchanged:** the frontend `/api/datasets/<id>/rows` API (the server
  converts to JSON), and append-only logs (job journals, the ledger), which
  stay JSONL. Arrow is in-memory interchange only.

### Dependencies and first use

New extras in `pyproject.toml`:

- `data = ["duckdb", "sqlglot", "pyarrow", "scipy"]`
- `stats = ["statsmodels"]`

`ziya-install-extras --data` / `--stats` install them into the running
environment (same mechanism as `--browser`). When `analyze` is called
without DuckDB, the tool result says so in one line and the frontend shows
an install button that runs the extras installer (fix, don't warn). No
fallback engine: pandas over 500k rows in the server process is the thing
this phase exists to avoid.

### Later milestones that build on this

- **M1 ledger:** each `analyze` call becomes an attested observation
  (`kind: derivation`, inputs by obs id and sha256, sql, result dataset id).
  Lineage in `meta.json` is shaped to be copied into it unchanged.
- **M4 broker:** broker query results are registered as datasets, so
  `analyze` runs on them directly.
- **M5 research blocks:** an `exec` derivation is an `analyze` call; the
  Verify block re-runs it from lineage and compares the result sha.

## Security

- No new file-read surface: path inputs use the `file_read` validator.
- No new write surface: results go to the dataset store, which tools cannot
  write except through the server.
- SQL cannot read, write or fetch anything the server did not register
  (allowlist + external access off + locked configuration).
- Resource bounds on time, memory, threads and result size; scratch cleaned
  per call.
- No code-bearing formats are deserialised.

## Implementation

| Step | Change | Files |
|---|---|---|
| 1 | Extras and installer flags | `pyproject.toml`, `app/utils/install_extras.py` |
| 2 | Guard: sqlglot allowlist | new `app/utils/analytics/guard.py` |
| 3 | Engine: connection setup, view registration, lock, limits | new `app/utils/analytics/engine.py` |
| 4 | Primitives as SQL templates + macro install | new `app/utils/analytics/primitives.py` |
| 5 | Parquet storage, lazy migration, lineage | `app/utils/dataset_store.py` |
| 6 | Tool + registration in `datasets` category | `app/mcp/tools/dataset_tools.py`, `app/mcp/builtin_tools.py` |
| 7 | Install prompt for a missing engine | frontend tool-result renderer |
| 8 | Analytics skill: method recipes over the primitives | new skill bundle |
| 9 | Docs | `Docs/Capabilities.md`, `Docs/FeatureInventory.md` |

## Tests

- **Guard.** Rejected: `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `PRAGMA`, `SET`,
  `CREATE`, two statements, `read_csv('/etc/passwd')`, `glob('*')`, an
  `https://` string in a `FROM`. Accepted: a `WITH` CTE over a dataset id
  calling a macro. Each refusal paired with an accepted near-miss.
- **Engine.** With the guard bypassed, `read_csv` of a file outside the
  registered inputs still fails (external access really is off), and a
  registered view still reads (positive control). A query over the time
  limit is interrupted and reported.
- **Primitives**, on hand-checked fixtures:
  - coverage: a unit silent for a whole bin gets coverage 0, not loss 1;
  - counter: `10, 20, 3, 8` gives deltas `10, 3, 5` with one reset;
  - union: `[0,10) ∪ [5,15) ∪ [20,25)` gives 20 s, not 25;
  - runs: `F T T F T` gives two incidents of 2 and 1 samples;
  - distribution: totals reconcile; one dominant entity raises gini and
    top10_share as expected.
- **Seam.** `analyze` through `execute_single_tool` with a registered
  dataset: the result is a new dataset whose lineage names the input id and
  its sha256, and whose rows match running the same SQL directly.
- **Auto distribution.** A `GROUP BY ct_id` result carries a
  `distribution` block; a result with no entity column does not.
- **Storage.** A phase-1 JSONL dataset is migrated on first `analyze`, the
  frontend rows endpoint returns identical JSON before and after, and
  migration does not remove the JSONL if verification fails.
- **No engine.** Without DuckDB importable, `analyze` returns the install
  message and every phase-1 tool still passes its suite.
- **Acceptance.** Per-unit figures recomputed with the primitives from raw
  CSVs match figures derived by hand.
  **Passed 2026-10-04** (`tests/test_analyze_ledger_acceptance.py`, on
  synthetic CSVs committed with the test): per-unit distributions (n, mean,
  median, max) and totals reproduce in SQL with `interval_duration` and
  `entity_distribution`, and each distribution's total reconciles to its
  fleet total. The first run of this acceptance used a real investigation's
  evidence (71 figures over 7 CSVs, kept outside the repository); that data
  was replaced by the synthetic set so the repository carries nothing from
  it. Scope: the synthetic CSVs are per-unit aggregates, so this exercises
  the engine, multi-input SQL and `entity_distribution`; the coverage and
  counter macros are covered by their fixture tests only, until raw
  per-second data is
  replayed.

## Decisions (2026-10-04)

1. **View vs relation registration** (probed on DuckDB 1.5.6, 2026-10-04):
   with `enable_external_access=false`, a view over a file and a Python-API
   relation both fail at query time, and only a materialised table survives.
   But `allowed_paths` (the exact input files) and `allowed_directories`
   (the call's scratch dir) keep those readable while every other read,
   `glob`, `COPY` outside scratch, `../` traversal, `https`, `INSTALL` and
   `ATTACH` are refused. Decision: views over the input files, listed in
   `allowed_paths`, no copy. Paths must be `realpath`ed first (`/var` is
   `/private/var` on macOS). Macros can be created after the lock;
   `con.interrupt()` stops a running query. `getenv` does not exist in this
   build. Engine tests pin all of the above.
2. **Coverage threshold:** default 0.8, overridable per call.
3. **Entity column for the automatic distribution block:** name patterns
   (`*_id`, `entity*`, `device*`, `host*`) by default; an explicit `entity=`
   argument overrides the heuristic.
4. **Macro conventions** (probed 2026-10-04): column arguments are matched
   exactly with `COLUMNS(c -> c = name)`, because `COLUMNS('id')` is a regex
   that also matches `xct` and takes the first match. A macro's own CTE
   names shadow the caller's tables inside it, so internal CTEs are
   prefixed `__z_`. Durations use a type-overloaded seconds helper; casting
   through `TIMESTAMPTZ` applied the session zone and lost an hour across
   DST. `bin_with_coverage` needs a timestamp column; gaps and durations are
   in seconds (`max_gap_s`, `total_s`). `top10_share` is the share held by
   the top 10% of entities. The guard requires a macro's table arguments to
   be bare registered or WITH names.
5. **Storage write path:** every dataset is written as JSONL first and, when
   DuckDB is installed, converted by the same verified `ensure_parquet` used
   for lazy migration, so new and migrated datasets take one code path.
   `register_dataframe` is the shared writer; derived results pass
   `lineage`. `meta.json` records `storage` and the data file's `sha256`.
