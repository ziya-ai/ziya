# Data reuse made endemic: implementation spec (H1–H8)

Status: **pass 6** (2026-11-02). Pass 2 reworked pass 1 against review pass 1 (D-1…D-25, §12.1); pass 3 reworked
it against review pass 2 (D-26…D-41, §12.2); pass 4 reworked it against review pass 3 in
`.ziya/data-reuse/design-findings.json` (D-42…D-55, §12.3; contracts C25–C29 added); pass 5 reworks it against
review pass 4 (D-42 reopened, D-56…D-66, §12.4; contracts C30–C32 added); pass 6 reworks it against review
pass 5 (D-65 reopened, D-67…D-72, §12.5; contracts C33–C36 added). Every finding is resolved
in the text below or dispositioned in §12; the per-finding disposition is also recorded in the blackboard
(`.ziya/data-reuse/state.json`, `findings[].status` / `disposition`).
Inputs: `design/data-reuse-endemic.md` (H1–H8, acceptance criteria §5), `design/kuiper-telemetry-connector-guidance.md`
(S1–S6, sink protocol §3), `.ziya/data-reuse/compat.md` (contracts C1–C12, verified code conventions).
Supersedes: the slicing part of `design/query-broker.md` (`data_query_sliced`, never implemented; C12).
Related: `design/dataset-store.md`, `design/local-analytics.md`, `design/job-runner.md`, `design/evidence-ledger.md`.

Contract ids **C1–C12** refer to compat.md §1 and are normative except where §0.3 amends them; this spec adds
**C13–C36** (§0.2). Everything is additive unless marked **CHANGE**. No pyproject version bump; CHANGELOG lines go
under `## [Unreleased]`.

---

## 0. Conventions and cross-cutting contracts

### 0.1 Code conventions every item follows (verified in code, compat.md §0)

- Tools: `class XInput(BaseModel)` + `class XTool(BaseMCPTool)` (`name`, `description`, `InputSchema`,
  `async execute(**kwargs)`), `_get_project_root(kwargs)` first, CPU work through `asyncio.to_thread`, errors returned
  as `{"error": True, "message": ..., "code": ...}`; never raise out of `execute`.
- Registration (D-24, verified 2026-11-02): `get_dataset_tools()` (`app/mcp/builtin_tools.py` ~l.426) and
  `get_job_tools()` (~l.450) each have **one** class list on the `try` path and `return []` on `ImportError`. New
  tools go into that list **and** into the category dict description where tools are named literally: the `jobs`
  description (~l.167, "(job_start / job_wait / job_status / job_read / job_kill)") gains `cache_status`; the
  `datasets` description gains `dataset_pull / dataset_find / dataset_missing / dataset_append / entity_set_*`.
  (Pass-1 review said "two return lists"; the second path is the empty fallback, so there is one list to update.)
- Dataset ids stay `ds_[0-9a-f]{16}` from `new_dataset_id()`; storage stays user-global under `store_root()`,
  scoped by `meta.project_root` / `meta.conversation_id` (F7).
- Macros: CREATE MACRO text in `MACROS`, table-arg positions in `TABLE_ARGS`, one-liner in `DESCRIPTIONS`; column
  arguments are strings matched with `COLUMNS(c -> c = param)`; CTEs prefixed `__z_`; parameter names never equal an
  output column.
- Times: every time input (`t0`, `t1`, `window`) must carry an offset or `Z`; a naive value is error `naive_time`
  ("give an offset or Z, e.g. 2026-10-29T01:15:00-07:00") (D-20). Stored and rendered times are UTC.

### 0.2 Contracts added by this spec

| # | Contract | Decision |
|---|---|---|
| C13 | Harness-sliced connector invocation (amended pass 3, D-35) | Per slice the executor runs `slice_command` with `{SQL_FILE}` (rendered SQL) and `{OUT}` (`<job_dir>/slices/<i>-<attempt>.parquet`). Default `"{command} --sql-file {SQL_FILE} --parquet {OUT}"`. **Never through a shell**: argv = `shlex.split(slice_command)`; `{SQL_FILE}`/`{OUT}`/`{PARAMS_FILE}` are legal only as whole argv elements (else `bad_slice_command`) and are replaced by harness-generated paths; model-supplied values reach the connector only inside files (SQL file, `--params-file` JSON), never as argv text. The policy check runs on the fully rendered argv (§1.4.4). Output by what exists at `{OUT}`, else stdout sniff (JSONL / CSV). Exit `0` ok, `3` **permanent** (`_FATAL`, S6), other non-zero transient. Backoff 2, 8, 32, 60 s cap, ±20 % jitter. |
| C14 | Connector registry | `<project>/.ziya/connectors.json`: `{"<name>": {"command", "source", "slice_command"?, "settle_s"?, "entity_param"?}}`. Precedence for the horizon: explicit `settle=` > registry `settle_s` > `--capabilities` > undeclared (`None`). |
| C15 | Ledger attestation of one **slice** (replaced pass 3, D-33) | `record_observation(..., dataset_id=ds, slice={partition, t0, t1, entities, commit_seq})` → ref `{"kind": "dataset_slice", "id", "partition", "t0", "t1", "entities", "commit_seq"}`, sha = `slice_sha256` (§2.4.7) of that slice's rows as committed. A partitioned dataset **without** `slice` is attested as a whole snapshot (pass 5, D-62; amended pass 6, D-70): ref `{"kind": "dataset_snapshot", "id", "commit_seq", "partitions": {p: content_sha}}` taken from one `input_files(id)` snapshot (§2.3), restricted to `observation.partitions` when the caller names them; `content_sha` is C35 (row content, `__z_*` excluded), so a re-pull that returns identical data leaves it unchanged. Verify → `agrees` iff every partition in the ref still has the same `content_sha` (partitions added later are ignored), `drifted` with `changed_partitions` (and the newer `commit_seq`) otherwise, a referenced partition that no longer exists counts as changed; `not_testable` when the dataset is gone, and — the existing `verify.py` rule — when the observation was recorded `volatile=true` and its content changed. `meta.sha256` (file-level) is no longer used for verdicts. Nothing is refused (`slice_required` is withdrawn). Exposed on the HTTP ledger route and in `ziya_ledger_client.observe(..., dataset_id=, slice=, idempotency_key=)` (D-15, D-39). **CHANGE** `app/utils/evidence/verify.py` (kinds `dataset_slice`, `dataset_snapshot`), `app/utils/evidence/store.py` (ref kind validation, idempotency key) and **`app/mcp/tools/ledger_tools.py`** (`LedgerRecordInput.observation` description: "dataset_id (attested; a partitioned dataset is attested as its current snapshot, or one slice with slice={partition,t0,t1,entities,commit_seq})"; the tool passes an optional `observation.slice` through). |
| C16 | Entity-set scoping | `store_root()/entity_sets/<proj>/<name>/v<n>.json`, `<proj> = sha256(realpath(project_root))[:16]`. |
| C17 | **Slice ledger** (D-1, D-2, D-10; amended pass 3, D-26, D-27, D-29) | Coverage and skipping are derived from what was **queried**, not from row hulls. Every slice lies inside **one** partition (C21) and every slice record names exactly one `partition`. Records are committed atomically with the rows they describe (C22). A (slice window, entity) is *covered* iff `covered(record, threshold)` holds for some committed record that includes the entity (C23). |
| C18 | **Dataset identity** (D-5, D-8) | `identity = sha256(canonical_json({sql_template: ws-normalised, params: non-entity params resolved, entity_param, key, ts_col, entity_col, partition_by, source}))[:24]`; `canonical_json = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, ws-normalised = `" ".join(sql_template.split())`, params rendered per C3 (lists sorted are **not** reordered; the entity param is excluded). **One implementation** (pass 6, D-67): `ziya_data_sink.dataset_identity(...)` (§8.2, stdlib only); `data_pull.dataset_identity` is a re-export, so a producer and the harness compute byte-identical ids. Only the declared `entity_param` may differ between pulls into one dataset. `dataset_pull` without `dataset_id` reuses the project dataset with the same identity. Datasets — in the store, on the sink route and in `LocalParquetSink` — are keyed by identity; `name` is a label only (C33). |
| C19 | **Snapshot reads** (D-6, D-7) | Readers never glob. Under a shared lock they take the explicit file list `meta.partitions[*].file`; the engine accepts a list of parquet files per alias. Superseded files are retired, not unlinked, for a grace period. |
| C20 | **Duplicate keys** (D-4; amended pass 3, D-34) | `append` default `on_duplicate="error"` (nothing written, `duplicate_key` with `n_dup` and ≤ 5 example keys); opt-in `"last"` / `"first"` over an Arrow-side ordinal. Checked **per slice on arrival** in the executor (fail fast) and again per batch in `append_partition`. Cross-batch replacement is newest-**queried**-wins (C30, pass 5, D-56), not newest-committed. |
| C21 | **Slices never straddle a partition** (D-26) | `plan_slices` cuts at every `partition_by` boundary (UTC midnight) as well as at `step`: `[t0,t1)` 08:15Z…+2 d with `step=1h` yields `…, [23:15,00:00), [00:00,00:15), …`. A slice record carries `partition` (one string) and `t0/t1` inside it. The **store** (`append_partition`, harness-internal) refuses a slice record whose window is not inside its partition (`slice_outside_partition`). The **sink protocol** does not refuse a straddling slice: the sink splits it at partition boundaries before buffering (C26, amended pass 4, D-45), so a connector that slices only by step still works. |
| C22 | **One commit point per append** (D-27) | `meta.json`'s atomic rename is the only commit point. Meta carries a monotonic `commit_seq`; the commit's slice records are written first to `<ds>/slices/<commit_seq:08d>.jsonl` (tmp + fsync + rename) and stamped with that `commit_seq`; readers use only files with `seq <= meta.commit_seq`. Part files not referenced by `partitions`/`retired` and slice files with `seq > meta.commit_seq` are orphans, deleted by the next `LOCK_EX` holder. |
| C23 | **One settle threshold function** (D-28, D-29) | `effective_threshold(declared, explicit)`: declared horizon → `max(declared, explicit or 0)`; undeclared → `explicit` (may be `None` = nothing settles). `covered(record, threshold) = record.status == "ok" and threshold is not None and record.settle_s_used >= threshold and record.queried_at − record.t1 >= threshold + skew(record)`, `skew(record) = max(record.skew_margin_s, record.skew_s or 0, SKEW_MARGIN_S)` (C31, amended pass 6, D-65). Used identically by `plan_pull`, `missing`, `coverage[p].queried`, `dataset_find` and the sink route, always with `declared = declared_eff(meta)` (C25, pass 4, D-43) — never with the raw stored `meta.settle_s_declared`. `meta.settle_s_declared` comes only from registry/capabilities (raised, never lowered, on each pull commit); an explicit `settle=` never changes it. `queried_at` is always a harness-bounded reading (C24). Results report `threshold` and `settle_source`. |
| C24 | **Clock and `queried_at`** (D-28, D-38) | `queried_at` = harness wall clock taken immediately before spawning the attempt that produced the rows (per attempt, written into `<job_dir>/slices/<i>-<attempt>.json`); `settled_at_query` is computed by the harness from it, never taken from a producer. **On the sink route (amended pass 4, D-42) the bound is conservative — it may only err early, never late.** `queried_at` is **mandatory** on `append(slice=)` and `record_slice` in v1 and means "wall clock immediately before the query was submitted". Let `L` = the handle's *lower contact bound*: `handle_opened_at`, or — only for a handle opened with `concurrency=1` (the producer promises it submits each query after its previous request on the handle completed) — the time the route **finished answering** the handle's previous request. **Amended pass 5 (D-42 reopened): on the sink route the record's `queried_at` — the value used for `settled_at_query`, `covered` and the C30 merge order — is always `L`** (`clock_source="contact"`). A producer's value cannot be trusted to mean "before submit" (a connector that stamps at frame completion yields a value inside `[L, received_at]` that is still too late), so it is stored only as `producer_queried_at` for diagnostics, with flag `producer_clock_suspect=true` when it lies outside `[L − 5 s, received_at]`. Producers are told to open with `concurrency=1` so that `L` is the previous response time (tight) rather than `opened_at`. `received_at` is never used (it is an upper bound on when the query ran, the unsafe direction). Producer `settle_s_used` is ignored. Consequence: a delegated slice is settled only if the handle's previous contact was already past `t1 + threshold + skew`; a slow or late-stamping producer can only *delay* reuse. `clock_source ∈ {harness, contact, test}` is stored per record (`producer` is no longer produced; old records carrying it are read as-is). `ZIYA_TEST_NOW` is honoured only when `ZIYA_TESTING=1`, which **`tests/conftest.py` sets at import (CHANGE, D-55)**; it logs a warning and stamps `clock_source="test"`. |
| C25 | **Declared horizon read at use time** (pass 4, D-43) | `declared_now(meta) = resolve_settle(None, registry(project_root), caps_cached(meta.source.connector), meta.source.source).declared`, where `registry` is `.ziya/connectors.json` re-read when its mtime changes and `caps_cached` is the last `--capabilities` result stored at `<project>/.ziya/cache/caps/<sha256(command)[:16]>.json` (read paths never spawn the connector; `dataset_pull` refreshes the cache). `declared_eff(meta) = max` of the non-`None` values of `declared_now(meta)` and `meta.settle_s_declared` (`None` if both are). `plan_pull`, `missing`, `coverage`, `dataset_find`, the sink route and `dataset_missing` all compute `effective_threshold(declared_eff(meta), settle)`. The meta coverage cache (`coverage[p].queried`, `young`, `queried_at_*`) stores the `threshold` it was computed with and is used only when that equals the current threshold; otherwise readers recompute from slice records (same `covered`). So `dataset_missing(settle=s)` equals the to-query set of `dataset_pull(settle=s)` at the same instant even after a horizon is raised in the registry. A **lowered** registry value never lowers the threshold below `meta.settle_s_declared` (lowering is a data-owner decision applied by `dataset_pull(refresh=…)` into a new dataset, §11.1). |
| C26 | **Sink splits straddling slices** (pass 4, D-45) | On `append(partition, table, key, slice=…)` / `record_slice(partition, slice, …)` the sink (Ziya route and `LocalParquetSink`) computes the partitions overlapping `[slice.t0, slice.t1)`. One partition → as before. Several → the slice is split at each UTC midnight into sub-slices `[t0, 00:00)`, `[00:00, t1)`; rows (already checked to lie in `[slice.t0, slice.t1)`, `boundary_overlap` otherwise) are routed by `utc_day(ts_col)`; each sub-slice record (same `queried_at`, `status`, entities; `rows` = its own count; `split_from = [t0, t1]`) is buffered in **its own** partition's buffer and committed only with that partition's rows (C22). A crash after the first partition commits leaves the second sub-slice uncovered (D-26 preserved). The `partition` argument, when given with `slice=`, must be the partition of `slice.t0` (else `partition_mismatch`); `partition=None` is allowed and means "route by `ts_col`". Without `slice=`, rows outside `partition` are still an error. Delegation therefore needs no partition-aligned slicing from the connector; `--slices-file` stays advisory. |
| C27 | **Atomic identity index** (pass 4, D-48) | `store_root()/identities/<proj16>/<identity>` (one small file containing the `ds_` id), `<proj16>` as C16. `create_partitioned(identity=…)` writes the new dataset dir, then writes `<identity>.tmp.<pid>.<rand>` with the id, fsyncs it and **`os.link`s** it to `<identity>` (atomic; fails with `FileExistsError` if present). The loser removes its tmp file and its never-referenced dataset dir and returns the winner's meta with `reused: true`. `find_by_identity` reads only the index; an index entry whose dataset dir is gone is removed under `store_root()/identities/<proj16>/.lock` (`LOCK_EX`) and the lookup retried once. No dataset with an identity is created any other way. |
| C28 | **Threshold for handles not bound to a `dataset_pull` job** (pass 4, D-44) | `open` (route) / `open_sink` (local) take `source: str | None` and `settle_s: int | None` from the producer. Bound handle (token carries a `data_pull` job): `threshold = max(plan.threshold, settle_s or 0)` (a producer can raise, never lower). Unbound handle (shell tool, scripts, other jobs): `declared = declared_now` for `source` from the project registry/caps cache (C25), then `threshold = effective_threshold(declared, settle_s)`; when `declared is None` and `settle_s` is given, `threshold = settle_s` and `settle_source = "producer"`. `LocalParquetSink` has no registry: `declared = None`, so `threshold = settle_s` with `settle_source="producer"` — the same number the Ziya route reaches when the registry declares nothing or the same value. The handle's threshold, `settle_source` and `job_id | null` are fixed at open and stamped as `settle_s_used` on every record. Protocol `missing(t0, t1, entities, *, settle=None)` and `coverage(*, settle=None)` take `settle` with the dataset_pull meaning (C23). |
| C29 | **Canonical slice hash** (pass 4, D-51) | Each committed ok record stores `slice_columns` = the dataset's column names (excluding the hive column and `__z_*`) at commit time. `slice_sha256` hashes, for rows of the slice ordered by key, the concatenation over `slice_columns` (name order) of a per-type rendering: `NULL` → `\N`; FLOAT/DOUBLE → `printf('%.17g', c)`; DECIMAL → `c::VARCHAR`; TIMESTAMP* / DATE → `strftime(c, '%Y-%m-%dT%H:%M:%S.%fZ')` (TIMESTAMPTZ converted to UTC first); BOOLEAN → `true|false`; else `c::VARCHAR`; fields joined by `\x1f`, rows by `\n`. Columns added later are ignored; a recorded column that no longer exists → verdict `not_testable` ("column gone"). |
| C30 | **Newest-queried wins across batches** (pass 5, D-56) | Every row in a partitioned dataset carries `__z_qat TIMESTAMP` (UTC) = the `queried_at` of the slice record that produced it (harness sidecar time, or `L` on the sink route, C24); rows of a manual `dataset_append` / `dataset_register(key=…)` get the commit's wall time. `__z_qat` is stored in the part files, excluded from `columns`, `slice_columns` (C29) and from every reader view (§2.3). The cross-batch merge keeps per key the row with the **largest `__z_qat`**, ties broken by the newer batch (`__z_src`). Invariant: for any key in a slice covered by record `r`, the served row has `__z_qat ≥ r.queried_at ≥ r.t1 + threshold + skew`, so it was itself queried after the horizon — a buffered commit of an earlier, younger query can never overwrite rows of a later settled query, whatever the commit order (two executors, `flush_rows` splits, retries, `LocalParquetSink`). |
| C31 | **Clock skew: measured, with a floor** (pass 5, D-65; amended pass 6, D-65 reopened) | `SKEW_MARGIN_S = int(os.environ.get("ZIYA_SETTLE_SKEW_S", "300"))` (≥ 0) in `app/utils/data_pull.py` is a **floor**, not the estimate. The skew is **measured** against the source clock whenever one is available: `measure_skew(h0, source_now, h1) = max(0, ceil((h1 − source_now).total_seconds()))`, where `h0`/`h1` are harness wall-clock readings immediately before/after obtaining `source_now`. This is an **upper bound** on how far the harness clock runs ahead of the source (the source reading was taken at real time ≤ that of `h1`), so it can only over-estimate the skew, i.e. only delay settledness. Sources of `source_now`, in order: (a) `--capabilities` field `now` (ISO UTC from the source, e.g. ClickHouse `SELECT now64(3)`; S2 addition, §11.2), probed by `dataset_pull` at plan time and re-probed by the executor every 3600 s and after any attempt whose wall-clock gap from the previous one exceeds 600 s (sleep/resume); (b) on the sink route / `LocalParquetSink`, `open_sink(source_now=…)` or `append/record_slice(slice={…, source_now})` measured against the sink's own receipt time (`h1` = receipt, the conservative end); (c) none. `settled_at_query(t1, threshold, queried_at, skew_s) = threshold is not None and queried_at − t1 >= threshold + max(SKEW_MARGIN_S, skew_s or 0)`. Each record stores `skew_margin_s`, `skew_s` (measured, or `null`) and `skew_source ∈ {caps, producer, none}`; `covered` uses `max(record.skew_margin_s, record.skew_s or 0, SKEW_MARGIN_S)` (raising the margin invalidates older judgments, lowering never revalidates). With no source clock (`skew_source="none"`) only the floor protects; that residual risk (host skew > 300 s with an S2-unaware connector) is stated in the tool result as warning `skew_unmeasured` and in §11.6. Settle horizons are compared against the **harness** wall clock; this is documented in the tool description and in the protocol page. |
| C32 | **Delegated runs** (pass 5, D-58, D-59) | A delegated pull invokes the connector once per maximal run of contiguous to-pull slices with an identical entity subset (`plan_delegated_runs`, §1.4.7), passing that run's own `--t0/--t1` and subset; every child gets `ZIYA_DATA_SINK_REQUIRED=1`, under which the client must not fall back to a local sink; after each run the executor checks that every planned slice of the run has a committed record of this job (`sink_records_missing` otherwise). Runs are **coalesced** (C34, pass 6, D-72). |
| C33 | **Sink identity is mandatory** (pass 6, D-67) | Protocol v1 `open` requires `identity` (24 hex, as C18); `open_sink` without it raises `SinkError("permanent", code="identity_required")` in both sinks, and the route answers `identity_required` (non-retryable). Producers compute it with `ziya_data_sink.dataset_identity(sql_template=…, params=<non-entity params>, entity_param=…, key=…, ts_col=…, entity_col=…, partition_by="utc_day", source=…)`; `q_sliced` (S4) does so from its own template and params. The route resolves the handle's dataset by `find_by_identity` (C27) only, creating it on first open; `LocalParquetSink` stores under `<local_root>/<identity>/` with `name` in its `meta.json` as a label. An open whose `name` is already used by a dataset of a **different** identity opens/creates the dataset of its own identity and returns warning `name_reused` (with the other dataset's id); records are never shared across identities, and `coverage()`/`missing()` read only the handle's own dataset. A bound handle (token carries a `data_pull` job) must present the plan's identity (else `identity_mismatch`, permanent). |
| C34 | **Coalesced delegated runs** (pass 6, D-72) | `plan_delegated_runs(slices, *, max_extra=0.25, min_run=4, max_runs=32)`: start from the C32 maximal runs; repeatedly merge an adjacent pair (`a`, `b`, with the covered slices between them, if any, included in the merged span) when `extra(a∪b) ≤ max_extra × need(a∪b)` **or** either run is shorter than `min_run` slices and the gap between them is `≤ min_run` slices, where `need` = Σ to-pull entity-slices in the span and `extra` = `|union subset| × n_slices(span) − need`; then, while more than `max_runs` runs remain, merge the adjacent pair with the smallest `extra`. Merging is deterministic (left to right, smallest-extra ties to the left). Extra entity-slices are re-queried; their rows merge newest-queried-wins (C30) and their records are ordinary records of this job, so correctness is unchanged. The result reports `n_runs` and `extra_entity_slices`. |
| C35 | **Partition content hash** (pass 6, D-70) | `partitions[p].content_sha` = sha256 of the C29 rendering of **all** rows of the partition (ordered by key, columns = the dataset's `columns`, `__z_*` and the hive column excluded), computed in `append_partition` at commit from the rows it writes and stored in meta; unchanged when a re-query returns identical content even though `__z_qat` and the part file sha change. |
| C36 | **No sink for harness-sliced connector children** (pass 6, D-71) | The executor sets `ZIYA_DATA_SINK=off` (and removes `ZIYA_DATA_SINK_REQUIRED`) in the env of every **harness-sliced** connector child (`--sql-file/--parquet`); only delegated children keep the real value. `ziya_data_sink.discover()` maps `off` to "no sink": `open_sink` returns a `NullSink` whose `append`/`record_slice`/`flush`/`close` are no-ops returning `{"buffered": false, "sink": "off"}`, `coverage()` → `{}`, `missing()` → every requested `(partition, entity)`, `dataset_id` → `None` — it never falls back to `LocalParquetSink`. Defence in depth: the route refuses `open` (`sink_not_expected`, permanent) from a token bound to a `data_pull` job whose plan has `delegate=false`. The protocol page states that a producer started with `--sql-file/--parquet` must not open a sink. |

### 0.3 Amendments to compat.md contracts

- **C1** (settled): a *slice query* is settled iff `queried_at − slice.t1 >= settle_s_used` at the time it ran
  (`settled_at_query`), with `queried_at` per C24 and the threshold per C23. "Settled now" (`now − t1 >= settle_s`)
  only decides whether a **new** query will be `settled_at_query`; it never by itself makes a slice skippable (D-1).
- **C4** (env): `ZIYA_DATA_SINK=<versions>:<target>`, `<versions>` a comma list of decimal strings the harness
  serves (e.g. `1` or `1,2`); split on the first `:`. The literal value `off` (pass 6, C36) means "use no sink" (`NullSink`, never a local fallback). Endpoint paths are versioned. The `find_spec` fallback is
  **dropped** in v1: discovery is env-only (D-13, D-21).
- **C5** (append): adds `on_duplicate` (C20) and `n_dup_in` to the result.
- **C6** (coverage): the v1 protocol shape `{partition: {t_min, t_max, entities, n}}` is unchanged and remains a
  **descriptive row hull**; harness-only keys `queried`, `young`, `queried_at_min`, `queried_at_max` are added in
  meta, not in the protocol.
- **C5/§3 protocol** (pass 3, D-30): v1 adds `record_slice`, `flush`, `close`; sinks buffer per (handle, partition)
  (§8.1).
- **C7** (missing): computed from the slice ledger (C17), not from `t_max` (D-2).
- **C8** (observe): unchanged; the Ziya sink records through the HTTP ledger route with the scope token.

---

## 1. H1 — `dataset_pull`

### 1.1 Placement

| What | Where |
|---|---|
| Plan / render / settle / skip logic (pure) | **new** `app/utils/data_pull.py` |
| Executor (runs inside a managed job) | same module, `[sys.executable, "-m", "app.utils.data_pull", "run", "--plan", <path>]` |
| Internal-job start | **new** `JobManager.start_internal(argv, *, kind, label, project_root, conversation_id, max_runtime_s, job=None)` in `app/jobs/manager.py` |
| Job reservation (D-55) | **CHANGE** `app/jobs/manager.py`: the id/dir creation now inline in `start` (~l.287-289: `job_id = "job_" + secrets.token_hex(6)`, `job_dir = _jobs_root(project_root) / job_id`) is refactored into **new** `JobManager.reserve(*, project_root, kind) -> Reservation(job_id, job_dir)` (creates `job_dir` with a `reserved` marker) and **new** `JobManager.release(reservation)` (removes an unused dir). `start(...)` calls `reserve` itself (behaviour unchanged); `start_internal(..., job=reservation)` consumes a reservation made earlier. The marker is `job_dir/reserved`, JSON `{pid, pid_create_time, created_at, kind}` (pid start time from `psutil.Process(pid).create_time()` when psutil is importable, else `/proc/<pid>/stat` field 22 / `ps -o lstart=`, else `null`). **Spawn order (pass 6, D-68, CHANGE `start` ~l.287–330):** `start`/`start_internal` write `job.json` (with `wrapper_pid: null`, `wrapper_start_time: null`, `state_hint: "starting"`) **before** `subprocess.Popen`; a failed write aborts before any process exists (the dir keeps only `reserved` and is sweepable). After Popen a second `_write_json(job.json)` fills `wrapper_pid`/`wrapper_start_time`; then `reserved` is unlinked. So a dir always has at least one of them, and a dir in which a wrapper may run always has `job.json`. **CHANGE** `Job.state()`: when `meta.wrapper_pid` is null it uses `status.json.wrapper_pid` (written by `app.jobs.wrap.run` at spawn, `wrap.py` l.138) for the liveness check; with neither and `started_at` older than 60 s the job reads `lost` (never deleted). A crash between Popen and the second write therefore leaves a job that is reported running while the wrapper lives and lost afterwards, and is never swept. **Sweep rule (pass 5, D-57)**: `ziya cache prune` and `JobManager.scan` delete a job dir **only if** it contains `reserved`, contains **no `job.json`** (the file `JobManager.start` writes, `manager.py` `_write_json(job_dir / "job.json", meta)`; `scan()` reads it), the marker's `created_at` is older than 1 h, and the marker pid is not alive (or is alive with a different `pid_create_time`, i.e. reused). A dir without the marker is never deleted by the sweep; finished jobs' journals are governed only by the existing job retention. |
| Policy call (D-55) | `jobs.policy.check(shlex.join(argv_rendered), project_root=project_root, outputs=[], task_scope=<the calling lane's task scope from kwargs, else None>)` — the existing keyword-only signature (`policy.py` l.160); the returned `shell_env` feeds `child_env` exactly as for `job_start`. |
| Test clock gate (D-55) | **CHANGE** `tests/conftest.py`: `os.environ.setdefault("ZIYA_TESTING", "1")` at import (before any app import), plus a seam test asserting `ZIYA_TESTING == "1"` inside a test and that `now_utc()` honours `ZIYA_TEST_NOW` there, so D-38 tests cannot pass vacuously. |
| MCP tool | `DatasetPullTool` in `app/mcp/tools/dataset_tools.py`, category `datasets` |
| Rows written through | H2 `dataset_store.append_partition` (in the executor, under the dataset lock) |
| Ledger writes | `app/utils/ziya_ledger_client.py` over `ZIYA_LEDGER_URL` / `ZIYA_LEDGER_SCOPE` (D-15) |

### 1.2 Python API (`app/utils/data_pull.py`)

```python
STEP_RE = re.compile(r"^(\d+)\s*(m|min|h)$")            # C2
def parse_step(step: str | int) -> int: ...              # minutes; <1 or >1440 -> ValueError
def parse_time(value: str) -> datetime: ...              # aware ISO 8601 -> UTC; naive -> ValueError("naive_time") (D-20)
def render_params(params: Mapping[str, Any]) -> dict[str, str]: ...      # C3
def render_slice(template: str, t0: datetime, t1: datetime, params: Mapping[str, str]) -> str: ...
def plan_slices(t0: datetime, t1: datetime, step_min: int,
                partition_by: str = "utc_day") -> list[tuple[datetime, datetime, str]]: ...
    # C21: cut at step AND at every UTC-day boundary; -> (t0, t1, partition) with [t0,t1) inside partition
SKEW_MARGIN_S: int                                       # C31, default 300, env ZIYA_SETTLE_SKEW_S
def settled_at_query(slice_t1: datetime, settle_s: int | None, queried_at: datetime,
                     skew_s: int | None = None) -> bool: ...
    # C1/C31: None -> False; else (queried_at - slice_t1).total_seconds() >= settle_s + max(SKEW_MARGIN_S, skew_s or 0)
def measure_skew(h0: datetime, source_now: datetime, h1: datetime) -> int: ...   # C31: max(0, ceil(h1 - source_now))
def plan_delegated_runs(slices: list[SlicePlan], *, max_extra: float = 0.25, min_run: int = 4,
                        max_runs: int = 32) -> tuple[list[DelegatedRun], dict]: ...
    # C32 maximal runs, then C34 coalescing; DelegatedRun(t0, t1, entities, slice_indices, extra_entity_slices);
    # dict = {n_runs, n_runs_uncoalesced, extra_entity_slices}
def resolve_settle(settle, registry, caps, source) -> tuple[int | None, int | None, str]: ...
    # -> (declared, threshold=effective_threshold(declared, settle), settle_source) (D-22, C23)
def declared_now(meta: dict, *, project_root: str) -> int | None: ...    # C25: registry + caps cache, no spawn
def declared_eff(meta: dict, *, project_root: str) -> int | None: ...    # C25: max(declared_now, meta.settle_s_declared)
def effective_threshold(declared: int | None, explicit: int | None) -> int | None: ...   # C23
def covered(record: dict, threshold: int | None) -> bool: ...                             # C23, shared with H2/H3
def now_utc() -> tuple[datetime, str]: ...   # (time, clock_source); ZIYA_TEST_NOW only if ZIYA_TESTING=1 (C24)
def render_argv(slice_command: str, *, sql_file: str, out: str, params_file: str | None) -> list[str]: ...
    # C13: shlex.split, whole-element placeholders only, no shell (D-35)
def probe_capabilities(command: str, *, timeout_s: float = 20) -> dict | None: ...
dataset_identity = ziya_data_sink.dataset_identity   # C18/C33: single implementation, re-exported (D-67)
def plan_pull(ds_meta: dict, slice_ledger: list[dict], slices, entities: list[str] | None,
              threshold: int | None, refresh, max_age_s: int | None, now: datetime) -> list[SlicePlan]: ...
    # per slice: entities_to_query (subset),
    # reason in {"covered","young","missing","refresh","stale","failed_before"}
def build_plan(**inputs) -> PullPlan: ...
def run_plan(plan_path: str) -> int: ...                 # exit 0 ok / 1 required slice or sink failed
```

`PullPlan` fields: `dataset_id, identity, project_root, conversation_id, connector_name, connector_command,
slice_command, source, delegate, sql_template, params_raw, params_resolved, entity_param, entity_sets_used {name:
version}, t0, t1, step_min, settle_s_declared, threshold, settle_source, key, ts_col, ts_unit, entity_col,
partition_by, on_duplicate, retries, require, flush_rows, max_age_s, argv_rendered (the exact argv the policy
checked, D-35), params_file, job_started_at, clock_source, slices: [{i, t0, t1, partition, entities, reason}]`.

### 1.3 Tool schema

```python
class DatasetPullInput(BaseModel):
    connector: str = Field(..., description="Registered name in .ziya/connectors.json, or the connector command.")
    source: Optional[str] = Field(None, description="Settle-horizon key (e.g. 'clickhouse'). Default: registry entry.")
    sql_template: str = Field(..., description="SQL with {T0}/{T1} (UTC 'YYYY-MM-DD HH:MM:SS') and {param} slots.")
    t0: str = Field(..., description="Window start, ISO 8601 WITH offset or Z.")
    t1: str = Field(..., description="Window end (exclusive), ISO 8601 with offset or Z.")
    step: str = Field("15m", description="Slice length: 15m, 15min, 1h, 6h.")
    params: Optional[Dict[str, Union[str, int, float, bool, List[Union[str, int, float]]]]] = Field(
        None, description="Template params; '@name' or '@name@vN' expands to an entity set (H5).")
    entity_param: Optional[str] = Field(None, description="Param whose values are the entities (e.g. 'cts'). "
        "Default: the registry's entity_param, else the single '@name' param, else none.")
    key: List[str] = Field(..., min_length=1, description="Row key for dedupe (e.g. ['tiid','w']).")
    ts_col: str = Field(..., description="Timestamp column; routes rows to partitions and bounds each slice.")
    ts_unit: Optional[Literal["s", "ms", "us"]] = Field(None, description="Required when ts_col is numeric epoch.")
    entity_col: Optional[str] = Field(None, description="Entity column; default key[0].")
    partition_by: Literal["utc_day"] = "utc_day"
    settle: Optional[int] = Field(None, ge=0, description="Seconds after which data is immutable. Can raise, never "
        "lower, a declared horizon (C23); for an undeclared connector it is the horizon.")
    refresh: Union[bool, List[str]] = Field(False, description="True or ['YYYY-MM-DD', ...]: re-query even covered slices (backfill).")
    max_age_s: Optional[int] = Field(None, ge=0, description="Re-query covered slices whose newest qualifying query "
        "is older than this (late backfill, D-37).")
    boundary: Literal["error", "clip"] = Field("error", description="Rows with ts_col outside their slice's [T0,T1): "
        "error (boundary_overlap) or drop and count (D-34).")
    on_duplicate: Literal["error", "last", "first"] = Field("error", description="Rows sharing a key within one pull.")
    name: Optional[str] = None
    dataset_id: Optional[str] = Field(None, description="Target dataset. Default: the project dataset with the same identity, else a new one.")
    retries: int = Field(3, ge=0, le=10)
    require: Literal["all", "any"] = "all"
    wait_s: float = Field(0, ge=0, le=600)
```

### 1.4 Algorithm

1. **Resolve** connector (C14); parse times (reject naive, D-20); expand `@name` params (H5 `resolve_ref`, versions
   into `entity_sets_used`); `step_min`; `(declared, threshold, settle_source) = resolve_settle(...)` (C23; an explicit `settle` below a declared
horizon is ignored for settledness and reported as warning `settle_below_declared`); capabilities;
   `delegate = caps.sliced is True and "1" in caps.sink_protocol` (C10). `entity_param` per §1.3 default;
   `entities` = its expanded list, else `None` (template has no entity dimension; entity `"*"`).
2. **Dataset (C18, D-5, D-8)**: compute `identity`. If `dataset_id` is given its `meta.identity` must equal it
   (error `params_mismatch` naming the differing field; differing `entity_param` values are allowed). If not given,
   look up `find_by_identity(project_root, identity)` (C27 index); if found reuse it (`reused: true`), else
   `create_partitioned(identity=…)` before the job so the id is returned at once. `create_partitioned` is the
   atomic step (C27): of two concurrent pulls with the same identity (Parallel lanes, a conversation and its fork)
   exactly one creates the dataset; the other gets the winner's id with `reused: true` and plans against its slice
   ledger (D-48). Two executors on one dataset are safe: commits serialise on `LOCK_EX`; rows of a slice queried by both
   are resolved **newest-queried-wins** (C30, by `__z_qat`, not by commit order), so a slower executor's earlier,
   younger query committed last cannot replace a later settled query's rows; the second plan simply re-queries what
   the first had not yet committed.
   The threshold used from here on is `effective_threshold(declared_eff(meta), settle)` (C25): for an existing
   dataset `declared` is the larger of the registry/caps value now and the stored one; on commit
   `meta.settle_s_declared` is raised to it.
3. **Plan slices** `[t0,t1)` by `step_min` **and UTC-day boundaries** (C21), then `plan_pull` against the committed
   slice ledger (C17, C22) with `covered(record, threshold)` (C23):
   - `refresh` true, or the slice's partition is listed in `refresh` → query all entities (`reason="refresh"`).
   - else `entities_to_query = [e ∈ entities (or ["*"]) : not covered(slice, e)]`; empty → skip
     (`reason="covered"`). A covered entity is one with a qualifying ok record — including records whose query
     returned zero rows for it (D-2c). A failed slice record never covers (D-2a). An entity added to the roster
     later is uncovered for every slice (D-2b), so only it is queried: the entity param for that slice is rendered
     with the subset.
   - A slice that is not settled now is always queried (it will be recorded `settled_at_query=false`, so it stays
     uncovered and is re-queried by the first pull after the horizon — once — D-1).
   - Raising the horizon (registry/caps/explicit) invalidates records with `settle_s_used < threshold` (D-10).
   - `max_age_s`: a covered slice whose newest qualifying `queried_at` is older than `now − max_age_s` is re-queried
     (`reason="stale"`, D-37). Re-queries replace rows (newest-queried wins, C30) but never retract (§11.4).
   - Template check (D-34): if the template matches `(?i)between\s+\S*\{T0\}` or contains `<=\s*\S*\{T1\}`, or
     has no `<\s*\S*\{T1\}`, the result carries warning `template_not_half_open` ("use ts >= {T0} AND ts < {T1}").
4. **Policy** (D-35): the job id is reserved first (`JobManager.reserve(project_root=…, kind="data_pull") ->
   Reservation(job_id, job_dir)`, §1.1 CHANGE), so every path is final. The rendered SQL goes to `<job_dir>/sql/<i>.sql`, params to `<job_dir>/params.json`.
   `argv_rendered = render_argv(slice_command, sql_file=<job_dir>/sql/0.sql, out=<job_dir>/slices/0-1.parquet,
   params_file=<job_dir>/params.json)` (or the delegated `--sliced` argv with `--params-file`, never `--param k=v`)
   is checked with `jobs.policy.check(shlex.join(argv_rendered), project_root=…, outputs=[], task_scope=…)`
   (§1.1); a refusal means nothing is run and the reservation is released. The
   plan stores `argv_rendered`; the executor runs, per slice/attempt, that argv with only the harness-generated path
   elements substituted, via `subprocess.run(argv, shell=False, env=scrub_child_env(..., force=True))`. Only the
   **connector command** is policy-checked; the executor argv is harness-internal (D-3). An injection-shaped param
   value (`"1; curl … | sh"`) is data in `params.json`/the SQL file and never reaches a shell.
5. **Nothing to pull** → no job (the reservation is released); return `{dataset, reused, job_id: null, slices:
   {total, skipped, to_pull: 0}, threshold, settle_source, warnings}` (AC 1).
6. **Otherwise a managed internal job** (D-3): `JobManager.start_internal([sys.executable, "-m",
   "app.utils.data_pull", "run", "--plan", <job_dir>/pull-plan.json], kind="data_pull", label=f"dataset_pull
   {name}", max_runtime_s=...)`. The internal job's process keeps Ziya's interpreter environment (package root on
   `PYTHONPATH`, `meta.policy = {"internal": "data_pull", "inherit_env": true}`); the executor applies
   `scrub_child_env(os.environ, force=True)` (§6.1) to **every connector subprocess**, so the connector never sees
   the Ziya interpreter leak. The job receives `ZIYA_DATA_SINK`, `ZIYA_LEDGER_*` (token TTL = `max_runtime_s +
   3600`, D-14), `ZIYA_CACHE_DIR`, `ZIYA_CACHE_OWNER`. Returns `{dataset, reused, job_id, slices, plan_path,
   threshold, settle_source, warnings}` immediately; `wait_s > 0` adds the `job_wait` result like `JobStartTool`.
7. **Executor loop** (`run_plan`):
   - *delegated* (amended pass 5, D-58, D-59, C32; pass 6, D-72, C34): `runs, stats = plan_delegated_runs(to_pull)`
     (coalesced, ≤ 32 runs; `stats.n_runs` and `stats.extra_entity_slices` go to the journal and the result); for each run, in order,
     one connector invocation `--sliced --template F --t0 <run.t0> --t1 <run.t1> --step-min N --source S --key c1,c2
     --ts-col C --entity-col C --params-file <job_dir>/params-<r>.json [--slices-file <job_dir>/slices-<r>.json]`,
     where `params-<r>.json` renders the entity param with **the run's subset only** and `--t0/--t1` are the run's
     bounds, never the pull window. A connector that ignores `--slices-file` and slices by step from `--t0` (S4 as
     written) therefore queries only to-pull time and entities: a 7-day pull with 12 young tail slices invokes it
     once over those 3 h (AC 1/AC 2 hold for every S4 connector); `--slices-file` is an optimisation only. Covered
     slices between two runs are never re-queried. The child env carries `ZIYA_DATA_SINK_REQUIRED=1` (§8.1: a failed
     health check is then `SinkError(permanent)`, exit non-zero, never a local fallback). The connector writes
     through `ZIYA_DATA_SINK` with `record_slice` for **every** attempted slice, including zero-row and failed ones,
     and the sink route buffers and commits per partition exactly like the harness-sliced path below (§8.1, D-30);
     midnight-straddling slices are split (C26). The handle is opened with the plan's `threshold` and `job_id`; the
     route stamps every record and row with `queried_at = L` (C24, D-42) and computes `settled_at_query` itself.
     **Post-run check** (D-58): after the connector exits (any code), the executor reads the committed slice ledger
     and requires, for each planned slice of the run and each entity of the run's subset, that the union of `[t0,t1)`
     of records with `job_id == this job` (status ok **or** failed) contains the slice; otherwise the run fails with
     `sink_records_missing` (listing ≤ 10 windows) — exit 0 of the connector does not make it a success. A failed
     run is retried per C13; `require="all"` stops on a permanent failure.
   - *harness-sliced*: per planned slice in order: `queried_at, clock_source = now_utc()` written to
     `<job_dir>/slices/<i>-<attempt>.json` **immediately before** spawning the attempt (each retry takes its own,
     D-28), together with the current `skew_s`/`skew_source` (C31: from the plan-time `--capabilities` `now`, re-probed
     every 3600 s and after a > 600 s wall-clock gap); the connector child's env carries `ZIYA_DATA_SINK=off` (C36,
     D-71), so its adapter never writes a second copy; run `argv_rendered` with retries (C13); a permanent exit stops (`require="all"`) or records
     `status="failed"` and continues (`require="any"`).
   - **Arrival checks (D-34)**, on each successful slice output before it is buffered: every row's `ts_col` (with
     `ts_unit`) lies in the slice's `[t0,t1)`, else `boundary="error"` → permanent slice failure `boundary_overlap`
     ("rows at or after {T1}: use half-open predicates ts >= {T0} AND ts < {T1}") after the **first** such slice,
     `boundary="clip"` → rows dropped and counted `n_clipped`; within-slice duplicate keys with
     `on_duplicate="error"` → permanent failure `duplicate_key`. Because slices are disjoint and rows are bounded
     to their slice, two slices of one pull can never share a key, so a `flush_rows` split cannot turn a boundary
     duplicate into a silent newest-wins.
   - **Buffered commit (D-18)**: successful slice outputs stay in `<job_dir>/slices/`. A partition is committed
     with **one** `append_partition(ds, p, [paths…], on_duplicate=…)` when all its planned slices are done, or when
     its buffer exceeds `flush_rows` (default 2 000 000). So a full day of 96 slices rewrites the partition once.
   - The commit (C22) carries, in the same `append_partition` call, the slice records (`status="ok"`, one
     `partition` each, `queried_at` from the attempt sidecar, `settle_s_used = threshold`, `settled_at_query`,
     `clock_source`, `slice_sha256`) of exactly the slices whose rows it writes. Failed slices are committed as
     records only (`append_partition(ds, p, None, slices=[…failed…])`; never covers).
   - Within one commit, several `status="ok"` records for the same `(partition, t0, t1, entities)` (a delegated
     connector's retry after a `failed` record, D-53) collapse to the **last** one; the earlier ok records are
     dropped from the commit and counted `n_superseded_records` in the commit journal line. Failed records are
     kept (they never cover and are never observed). Every record gets `ordinal` = its 0-based position in the
     commit's slice file.
   - **After** the dataset lock is released (D-39): one ledger observation per committed **ok** slice record through
     `ziya_ledger_client.observe(source="sql", connector=…, query_text=<rendered slice SQL>, dataset_id=ds,
     slice={partition, t0, t1, entities, commit_seq}, as_of=queried_at, volatile=not settled_at_query,
     idempotency_key=sha256(ds|partition|t0|t1|",".join(sorted(entities))|commit_seq|ordinal))` (D-53: two entity
     subsets of one window in one commit get distinct keys and distinct observations), which carries the token's conversation/run provenance
     (D-15). The returned ids are appended to `<ds>/observations.jsonl` (`{idempotency_key, obs_id}`, short
     `LOCK_EX`). Records without an entry are re-observed by the next executor run on the dataset (idempotent).
   - A crash before a partition's commit loses only uncommitted slices; nothing claims them covered (C22), so a
     re-run re-queries exactly those (AC 2). A crash between the two commits of a midnight-straddling window leaves
     the second partition's slices uncovered (C21).
   - Journal line per slice: `slice 7/96 2026-10-29 01:45..02:00 entities=18 young rows=41210 attempt=1 1.8s`;
     per commit: `commit 2026-10-29 slices=96 n_in=… n_new=… n_replaced=… n_dup_in=0`.
   - Sink/ledger HTTP errors after start are transient (C13 retries), then permanent → exit 1. No mid-run fallback
     to a local sink (D-14).
   - End: `meta.pulls.append({job_id, t0, t1, step_min, params_resolved, entity_sets_used, threshold,
     settle_source, slices_ok, slices_failed, n_clipped, finished_at})` (D-16), itself a commit (C22).
8. Result and prompt say when `settle_source == "undeclared"` and no `settle=` was given: "connector declares no
   settle horizon; pass settle= or nothing is reused" (D-22).

---

## 2. H2 — partitioned, keyed datasets

### 2.1 On-disk layout

```
<store_root>/<ds_id>/
  meta.json                                    # the single commit point (C22): atomic rename, commit_seq
  slices/00000007.jsonl                        # C17/C22 slice records of commit 7 (immutable once committed)
  slices/00000008.jsonl                        # seq > meta.commit_seq => orphan, ignored, deleted by next writer
  observations.jsonl                           # {idempotency_key, obs_id} (D-39)
  parts/
    utc_day=2026-10-29/part-000004.parquet     # one live file per partition
    utc_day=2026-10-29/part-000003.parquet     # retired (listed in meta.retired), deleted after grace
    utc_day=2026-10-30/part-000002.parquet
  .lock                                        # fcntl: LOCK_EX for append/compact, LOCK_SH for snapshot reads
```

Hive naming `utc_day=YYYY-MM-DD` (C9); the partition column is not stored in the files. Each part file stores the
hidden per-row column `__z_qat TIMESTAMP` (C30), which no reader sees. Left to itself DuckDB
1.5.6 autocasts it to **DATE** (`hive_types_autocast`, verified 2026-11-02), so the engine passes
`hive_types = {'utc_day': 'VARCHAR'}` explicitly (verified: `typeof(utc_day) = VARCHAR`) and `utc_day` is always
`VARCHAR 'YYYY-MM-DD'` (D-50). The engine checks each file's parent dir matches `^utc_day=\d{4}-\d{2}-\d{2}$`. A
dataset whose own columns include the partition column name is refused at `create_partitioned` / first append /
`dataset_register(key=…)` with `partition_column_conflict` ("source has a column named utc_day; rename it in the
SQL"), so the hive column can never shadow or collide with data.

### 2.2 `meta.json` (partitioned) and `slices/<commit_seq>.jsonl`

```json
{
  "id": "ds_…", "name": "keyid 4s bins brewster", "storage": "partitioned", "partition_by": "utc_day",
  "identity": "9f2c…(24 hex)",
  "source": {"kind": "pull", "connector": "graf", "source": "clickhouse"},
  "key": ["tiid", "w"], "ts_col": "w", "entity_col": "tiid", "entity_param": "cts",
  "columns": [{"name": "tiid", "type": "VARCHAR", "vega_type": "nominal"}],
  "row_count": 10412337,
  "template": {"sql_template": "…{T0}…{T1}…{cts}…", "params": {"min_snr": 3}, "step_min": 15},
  "settle_s_declared": 10800, "settle_source": "registry", "commit_seq": 7,
  "coverage": {"2026-10-29": {"t_min": "2026-10-29T00:00:00Z", "t_max": "2026-10-29T23:59:56Z",
                              "entities": ["t1", "t2"], "n": 2160000,
                              "queried": {"t1": [["2026-10-29T00:00:00Z", "2026-10-30T00:00:00Z"]],
                                          "t2": [["2026-10-29T00:00:00Z", "2026-10-29T06:00:00Z"]]},
                              "young": false,
                              "queried_at_min": "2026-10-30T04:10:00Z", "queried_at_max": "2026-10-31T09:00:00Z"}},
  "young": ["2026-11-02"],
  "partitions": {"2026-10-29": {"file": "part-000004.parquet", "sha256": "…", "content_sha": "…", "n": 2160000}},
  "retired": [{"file": "utc_day=2026-10-29/part-000003.parquet", "retired_at": "…"}],
  "sha256": "<sha256 of sorted partition sha256 values>", "volatile": true,
  "pulls": [{"job_id": "…", "t0": "…", "t1": "…", "step_min": 15, "params_resolved": {"cts": "'t1','t2'"},
             "entity_sets_used": {"brewster_noncanary": 3}, "threshold": 10800, "settle_source": "registry",
             "slices_ok": 96, "slices_failed": 0, "finished_at": "…"}],
  "project_root": "…", "conversation_id": "…", "created_at": "…", "updated_at": "…"
}
```

`slices/<commit_seq>.jsonl`, one line per committed or failed slice query (all lines of a file share one
`commit_seq`; a slice record names exactly one partition, C21):

```json
{"t0": "2026-10-29T23:45:00Z", "t1": "2026-10-30T00:00:00Z", "partition": "2026-10-29", "identity": "9f2c…",
 "entities": ["t1", "t2"], "entity_sets_used": {"brewster_noncanary": 3},
 "queried_at": "2026-10-30T00:30:00Z", "clock_source": "harness", "settle_s_used": 10800,
 "settled_at_query": false, "status": "ok", "rows": 812, "slice_sha256": "…", "commit_seq": 7,
 "ordinal": 3, "slice_columns": ["loss", "rx_modem", "tiid", "w"], "split_from": null,
 "settle_source": "registry", "job_id": "…", "attempt": 1,
 "skew_margin_s": 300, "skew_s": 2, "skew_source": "caps"}
```

(`skew_margin_s`/`skew_s`/`skew_source`: C31, D-65; `content_sha` in `partitions`: C35, D-70; `ordinal`: position in the commit file, D-53; `slice_columns`: C29, D-51; `split_from`: original `[t0, t1]` when
the sink split a straddling slice, C26, D-45.) Meta additionally stores `coverage_threshold` — the threshold the
cached `coverage[*].queried/young/queried_at_*` were computed with (C25, D-43).

`entities` is `["*"]` when the pull had no entity param. Derived (cached in meta, recomputed on commit):
- `coverage[p].queried[e]` = union of `[t0,t1)` of committed records with `partition == p` and
  `covered(record, effective_threshold(declared_eff(meta), None))` (C23, C25) that include `e`; the threshold used
  is stored in `coverage_threshold`. For an undeclared dataset the stored `queried` is therefore empty. Readers
  whose current threshold (explicit `settle`, or a registry horizon raised since the commit) differs from
  `coverage_threshold` ignore the cache and recompute from the records (same function) (D-43). `queried_at_min/max` = oldest/newest `queried_at` of those records (D-37).
- `coverage[p].young` = some queried window in `p` has only non-qualifying records, or the partition end is not
  yet settled now.
- `t_min/t_max/entities/n` stay the descriptive row hull (protocol C6).

### 2.3 `app/utils/dataset_store.py` and engine changes

```python
def create_partitioned(*, name, key, ts_col, entity_col, entity_param=None, identity, partition_by="utc_day",
                       template=None, settle_s=None, settle_source="undeclared", source=None,
                       project_root=None, conversation_id=None) -> dict: ...
    # identity given -> C27 atomic index; returns meta with "reused": True when another creator won the race;
    # columns/key/ts_col/entity_col named like the partition column -> partition_column_conflict (D-50)
def find_by_identity(project_root: str, identity: str) -> dict | None: ...   # reads the C27 index only (D-48)
def append_partition(dataset_id: str, partition: str | None, table, *, on_duplicate="error",
                     slices: list[dict] | None = None, pull: dict | None = None) -> list[dict]:
    ...  # table: pyarrow.Table | path | list[path] | None (records-only commit);
         # every slice must have slice["partition"] == partition and lie inside it (C21);
         # returns [{partition, n_in, n_new, n_replaced, n_dup_in, commit_seq}]
def slice_records(dataset_id: str) -> list[dict]: ...         # committed only: seq <= meta.commit_seq (C22)
def coverage(dataset_id: str, *, settle: int | None = None) -> dict: ...   # C6 + queried + young (C23)
def missing(dataset_id: str, t0: str, t1: str, entities: list[str] | None, *, settle: int | None = None,
            step_min: int | None = None) -> dict: ...         # C7 amended; {missing, threshold, settle_source}
def slice_sha256(dataset_id: str, partition: str, t0: str, t1: str, entities: list[str]) -> str: ...  # §2.4.7
@dataclass(frozen=True)
class InputSnapshot: files: list[Path]; commit_seq: int | None; sha256: str | None
def input_files(dataset_id: str) -> InputSnapshot: ...
    # C19: files, meta.commit_seq and meta.sha256 read from the SAME meta under ONE LOCK_SH (D-52);
    # legacy -> InputSnapshot([ensure_parquet(id)], None, meta.sha256)
def is_partitioned(meta: dict) -> bool: ...
```

`missing(t0, t1, entities, settle=None)`: `threshold = effective_threshold(declared_eff(meta), settle)` (C25) — the
same function and the same inputs `dataset_pull` uses (including a horizon raised in the registry after the
dataset was created, D-43), so `dataset_missing` reports exactly what a `dataset_pull`
with the same `settle` would query (D-29). For each partition overlapping `[t0,t1)` and each requested entity
(`entities=None` → every entity ever queried, or `"*"` for entity-less datasets), the pair is missing iff
`[max(t0, p_start), min(t1, p_end))` is not contained in the union of `[t0,t1)` of committed records of that
partition with `covered(record, threshold)` that include the entity. Offline entities with settled
zero-row queries are covered (D-2c).

**CHANGE** (existing code, F2, D-7, D-25):
- `app/utils/analytics/engine.py:run_query`: `inputs: Mapping[str, Path | Sequence[Path]]`. A sequence must be
  non-empty, every element an existing `.parquet` file; all go into `allowed_paths`. `_view_sql(alias, paths)`
  emits `CREATE VIEW alias AS SELECT * EXCLUDE (__z_qat) FROM read_parquet([<paths>], hive_partitioning = true,
  hive_types = {'utc_day': 'VARCHAR'}, union_by_name = true)` (D-50; `EXCLUDE (__z_qat)`, C30). `read_rows` /
  `rows_as_csv` / `public_describe.columns` exclude it the same way; a source column named `__z_qat` (or any `__z_*`)
  is refused like the partition column (`reserved_column_conflict`). Single `Path` unchanged.
  **Also CHANGE** (D-49, defence in depth): `run_query` rejects a result whose column names are not unique
  (`duplicate_output_column`, naming them: "rename before chaining; see interval_join chaining") before it is
  registered as a dataset, so `register_dataframe` never receives duplicate names.
- `analyze._resolve_inputs` (`analyze.py:81`): partitioned → `snap = input_files(id)`; `snap.files` go to the
  engine; **CHANGE** `analyze.py:83` (`entry["sha256"] = ds.load_meta(entry["id"]).get("sha256")`, a second,
  unlocked read) → `entry["sha256"] = snap.sha256` and `entry["commit_seq"] = snap.commit_seq`, so the lineage
  names exactly the snapshot the query read (D-52). Legacy unchanged (`commit_seq` absent). (The pass-1
  claim about `analyze.py:210` is withdrawn: that path reads the analyze result dataset, which is a legacy file.)
- `ensure_parquet(id)`: partitioned → returns `None` without conversion; callers use `input_files`.
- `data_file(id)`: raises `DatasetError("partitioned; use input_files")` for partitioned.
- `read_rows` / `rows_as_csv` (preview, `ziya://dataset` fetches): partitioned → DuckDB over `input_files` with
  projection and `LIMIT/OFFSET` pushed down. `load_dataframe` on partitioned with `row_count > MAX_SERVE_ROWS` raises
  `DatasetError("too large; use analyze")` (D-25).
- `public_describe(meta)`: adds `storage, identity, coverage (without queried unless asked), young, template,
  settle_s_declared, settle_source, commit_seq, key, entity_param, pulls[-3:]`.
- `list_datasets`: item adds `storage` (default `"file"`).

### 2.4 Append, dedupe and retirement

Inside `append_partition`, under `LOCK_EX`:

0. (Start of every append under `LOCK_EX`): delete orphan part files (not in `partitions`/`retired`) and slice files
   with `seq > meta.commit_seq`.
1. Read the batch with pyarrow (concatenate a list of paths, `promote_options="permissive"`), add ordinal
   `__z_ord = pa.array(range(n))` **on the Arrow side** (D-11) and, when absent, `__z_qat` (C30): per input path the
   `queried_at` of the slice record that produced it (the executor/sink passes `qat_by_path`, or one `queried_at`
   for a single table), else the commit's wall time for manual appends. Validate: key and `ts_col` columns present, no NULL
   key (`null_key`), every row's `utc_day(ts_col) == partition` (`partition_mismatch`). Errors write nothing.
2. Duplicates within the batch: `n_dup_in = n − count(DISTINCT key)`. `on_duplicate="error"` and `n_dup_in > 0` →
   error `duplicate_key` with `n_dup_in` and up to 5 example keys, nothing written (C20, D-4). `"last"`/`"first"`
   keep the max/min `__z_ord` per key.
3. Merge with the live partition file: `UNION ALL BY NAME` with source rank `__z_src` (old = 0, batch = 1);
   `QUALIFY row_number() OVER (PARTITION BY <key> ORDER BY __z_qat DESC, __z_src DESC) = 1` → **newest-queried wins**
   across batches (C30, pass 5, D-56; C5 amended), ties to the newer batch. A batch row whose live row has a later
   `__z_qat` is dropped and counted `n_stale_in`. Schema union by name; a type
   conflict is `schema_conflict`.
4. Counts: `n_in`, `n_new = |keys(batch) − keys(old)|`, `n_replaced` = shared keys where the batch row won,
   `n_stale_in` = shared keys where the live row won (`n_replaced + n_stale_in = |keys(batch) ∩ keys(old)|`).
5. **Commit (C22, D-27).** `s = meta.commit_seq + 1`. (a) Write `part-<n+1:06d>.parquet.tmp`, fsync, rename
   (skipped for a records-only commit). (b) Validate every slice record: `partition` equals the call's partition and
   `[t0,t1)` lies inside it (`slice_outside_partition`); stamp `commit_seq = s` and `slice_sha256` (step 7); write
   `slices/<s:08d>.jsonl.tmp`, fsync, rename. (c) Build the new meta: `commit_seq = s`, `partitions[p]` → new file,
   previous file appended to `retired` with `retired_at`, `pulls` (if given), recomputed `coverage`, `row_count`,
   `sha256`, `columns`, `updated_at`; write `meta.json.tmp`, fsync, **rename (the commit point)**, fsync the
   directory. A failure anywhere before the rename leaves the old meta in force: the new part file and slice file
   are orphans (not referenced; `seq > commit_seq`), invisible to every reader and to `plan_pull`, so the slices
   are re-queried. Orphans are deleted at step 0 of the next `LOCK_EX` holder.
6. Retirement (C19, D-6): files in `retired` older than `RETIRE_GRACE_S = max(600, 2 × analyze DEFAULT_TIMEOUT_S)`
   are unlinked at the start of the next append (or by `ziya cache prune`). Readers snapshot `partitions[*].file`
   under `LOCK_SH`, so a concurrent read sees either the old or the new file per partition, never both.
7. `slice_sha256` (D-33, amended pass 4 D-51): over the **post-merge** partition rows with `ts_col ∈ [t0,t1)` and
   entity ∈ `entities` (all for `"*"`), ordered by key, with the canonical per-type rendering of C29 over the
   record's `slice_columns` only; computed in DuckDB as `sha256(string_agg(<rendered row>, '\n' ORDER BY <key>))`;
   empty slice → sha256 of `""`. Verification recomputes the same expression over the current dataset with the
   record's `slice_columns` (§8.3, `verify.py`): a column added by a later append (schema union) does not change
   the hash, a DuckDB release that changes default DOUBLE→text rendering does not either (fixed `%.17g`), and a
   recorded column that has disappeared yields `not_testable`, not `drifted`. Pinned by a golden-hash test.

Overlapping re-pull (queried later): `n_new = 0`, `n_replaced = n_in`, `n_stale_in = 0`, row count unchanged; a delayed commit of an *earlier* query: `n_replaced = 0`, `n_stale_in = n_in`. Rows absent from a re-pull are kept
(append never deletes; F9, §11.4).

### 2.5 New tools (`dataset_tools.py`, category `datasets`)

```python
class DatasetAppendInput(BaseModel):
    id: str
    path: Optional[str] = Field(None, description="Parquet/CSV/JSONL file to append.")
    records: Optional[List[Dict[str, Any]]] = None
    partition: Optional[str] = Field(None, description="YYYY-MM-DD; omit to route rows by ts_col.")
    on_duplicate: Literal["error", "last", "first"] = "error"
class DatasetMissingInput(BaseModel):
    id: str
    t0: str
    t1: str
    entities: Optional[Union[List[str], str]] = Field(None, description="Ids or '@set'.")
    settle: Optional[int] = Field(None, ge=0, description="Same meaning as dataset_pull.settle (C23).")
```

`dataset_append` → `{id, results: [{partition, n_in, n_new, n_replaced, n_dup_in}], row_count}`. A manual append
writes no slice records (manual rows are never "covered"; only pulls are). `dataset_missing` → `{id, missing:
[[partition, entity], …], n, young: [...], threshold, settle_source, unsettled_records: n}` (`unsettled_records` =
committed ok records in the window that do not qualify under `threshold`, so an undeclared dataset says "pulled but
not settled" instead of looking empty). `dataset_register` gains optional `key`, `ts_col`, `entity_col`,
`on_duplicate`: with `key` and `ts_col` the file becomes a partitioned dataset.

---

## 3. H3 — `dataset_find`

Placement: `find_datasets(...)` in `dataset_store.py`; `DatasetFindTool` in `dataset_tools.py`, category `datasets`.

```python
class DatasetFindInput(BaseModel):
    table: Optional[str] = Field(None, description="Source table, matched against FROM/JOIN of the producing SQL.")
    window: Optional[List[str]] = Field(None, min_length=2, max_length=2, description="[t0, t1] ISO with offset.")
    entities: Optional[Union[List[str], str]] = Field(None, description="Ids or '@set'. Strongly recommended.")
    columns: Optional[List[str]] = None
    text: Optional[str] = None
    all_conversations: bool = True
    limit: int = Field(10, ge=1, le=50)
```

Candidates and filters as pass 1 (project filter, table regex `(?i)\b(?:from|join)\s+([`"\w.]+)` over
`template.sql_template`, `columns` ⊆). **Ranking (D-9)** — joint, from the slice ledger:

- With entities: `score = Σ_e |queried_e ∩ window| / (|window| × |entities|)` (fraction of requested
  (entity × time) actually covered), using `coverage[p].queried` across partitions.
- Without entities: `score` over the dataset's own entity universe; result carries `entities_unconstrained: true`
  and `n_entities`; rank by score then `n_entities` desc.
- Two scores (D-31), both with the formula above: `score_settled` over records with `covered(record,
  effective_threshold(declared_eff(meta), None))` (C23, C25 — a horizon raised in the registry after the pull lowers
  `score_settled` immediately, D-43), `score_any` over all committed `status="ok"` records
  (young or undeclared included). A dataset is kept iff `score_any > 0` (when a window/entities were given); rank by
  `score_settled` desc, then `score_any` desc, then fewer young partitions in the window, then `updated_at` desc.
  `unsettled: true` when `score_settled < score_any`, with `settle_source` and `max_settle_s_used`, and the hint
  "reuse needs dataset_pull(settle=…) or a re-pull after the horizon". Legacy datasets: `score: null`, listed after,
  only on `table`/`text`/`columns` match.

Item = `list_datasets` shape + `vega_data` + `{storage, identity, coverage_overlap: {score_settled, score_any,
unsettled, n_entities, entities_unconstrained}, missing: {n_pairs, first: [≤10 pairs], threshold}, template:
{sql_template[:400], params}, settle_s_declared, settle_source, queried_at_min, queried_at_max}` (the last two over
the window, D-37), and (pass 5, D-61) **`pull_args`** = `{dataset_id, connector, source, sql_template (full, the
stored text), params (non-entity params as stored; the entity param as `@name` when the latest pull resolved it from
an entity set, else the literal list), entity_param, key, ts_col, ts_unit, entity_col, step}` — exactly the
`DatasetPullInput` fields that determine identity (C18), so `dataset_pull(**pull_args, t0=…, t1=…)` passes the
identity check by construction and queries only `dataset_missing`. `pinned_entities` (`@name@vN` of the latest pull)
is reported beside it.

Prompt (**CHANGE** `session_context_prompt._format_data_section`, gated on `datasets`): "Before any pull, call
`dataset_find(table=…, window=[t0,t1], entities=…)`. To extend a found dataset call
`dataset_pull(**pull_args, t0=…, t1=…)` with the item's `pull_args` — do not rewrite its SQL (a different SQL text is
a different dataset); it then queries only what `dataset_missing` reports; give times with an offset; an undeclared connector needs `settle=` for any reuse.
Cite entity sets as `@name@vN`."

---

## 4. H4 — `interval_join` and `asof_join` macros

Placement: `_INTERVAL_JOIN`, `_ASOF_JOIN` in `app/utils/analytics/primitives.py` appended to `MACROS`;
`TABLE_ARGS[...] = (0, 1)`; `DESCRIPTIONS`; the literal macro list in `_format_data_section` gains both.

### 4.1 `interval_join`

```
interval_join(points_tbl, intervals_tbl, entity_col, ts_col, start_col, end_col,
              priority_col := NULL, tiebreak_col := NULL, closed := 'left')
  -> every column of points_tbl, iv STRUCT(<intervals_tbl columns>), n_matches BIGINT, ambiguous BOOLEAN
```

The endemic call `interval_join(points, intervals, 'entity','ts','start','end', priority := 'col')` maps to
`priority_col :=` (a parameter named `priority` would collide with column identifiers; primitives convention).

- **Output-name guard (pass 4, D-49).** If `points_tbl` already has a column named `iv`, `n_matches` or
  `ambiguous`, the macro raises `error('interval_join: points already have column iv/n_matches/ambiguous; rename
  them before chaining')`. Implemented as a further guard CTE referenced like `__z_ij_g`:
  `CASE WHEN regexp_matches(__z_top(<ty>), '(^|, )"?(iv|n_matches|ambiguous)"? ') THEN error(…) END` with
  `<ty> = (SELECT typeof(any_value(__z_q)) FROM query_table(points_tbl::VARCHAR) __z_q)` — `typeof` of the
  row-struct gives the column list even for an empty table. **Top-level names only (pass 5, D-63)**: internal
  helper macro `__z_top(ty)` strips the leading `STRUCT(` and then removes innermost parenthesised groups eight times
  (`regexp_replace(x, '\([^()]*\)', '', 'g')` nested 8×), so nested STRUCT fields (`leg STRUCT(s INTEGER,
  ambiguous BOOLEAN)`) can no longer match; nesting deeper than 8 levels is documented as unsupported by the guard
  (the engine's `duplicate_output_column` check still applies). Verified DuckDB 1.5.6 (2026-11-02, 4 passes):
  `STRUCT(tiid INTEGER, w INTEGER, leg STRUCT(s INTEGER, ambiguous BOOLEAN), deep STRUCT(a STRUCT(n_matches
  INTEGER)))` → no match; top-level `ambiguous` → match; the same table empty → match. `asof_join` guards `st`, `n_ties`, `ambiguous` the same way. Chaining is
  documented (DESCRIPTIONS and the analyze tool description) as:
  ```sql
  WITH legs AS (SELECT * EXCLUDE (iv, n_matches, ambiguous), iv AS leg, n_matches AS leg_n, ambiguous AS leg_ambiguous
                FROM interval_join(bins, contact_legs, 'tiid', 'w', 's', 'e', priority_col := 'leg_rank'))
  SELECT * EXCLUDE (st, n_ties, ambiguous), st AS sw, ambiguous AS sw_ambiguous
  FROM asof_join(legs, sw_versions, 'tiid', 'w', 'since')
  ```
  and the engine refuses any result with duplicate column names (§2.3), so a hand-written chain cannot lose the flag.
- Candidates: same `entity_col` value and `start <= ts < end` (`closed='left'`, default) or `start <= ts <= end`
  (`closed='both'`, for inclusive leg ends) (D-12). Other values of `closed` → `error()`.
- Intervals with NULL entity/start/end or `end < start` (or `end <= start` with `closed='left'`) are ignored. Points
  with NULL entity or ts are kept unmatched.
- Every point kept (left join); unmatched `iv = NULL`, `n_matches = 0`, `ambiguous = false`.
- Choice when `n_matches > 1`: smallest `priority_col` (NULLs last) → latest start → earliest end → smallest
  `tiebreak_col` (if given). `ambiguous = true` when the top two candidates are equal on every applied key; the pick
  among them is then arbitrary (D-11). Input order is **not** a tie rule and output order is not guaranteed: callers
  `ORDER BY` (DuckDB's `row_number() OVER ()` is used only as a unique join-back id, never for ordering).
- Type guard (D-12, made reachable pass 3 D-32): compares **type families** via helper macro `__z_tfam(typeof(x))`
  ∈ {`integer` (TINYINT…UHUGEINT), `floating` (FLOAT, DOUBLE, DECIMAL*), `timestamp` (TIMESTAMP, TIMESTAMP_S/MS/NS,
  DATE), `timestamptz`}; different families → `error('interval_join: ts_col and start_col type families differ;
  cast explicitly')`. BIGINT points vs INTEGER legs pass. The guard CTE is referenced by the output
  (`CROSS JOIN __z_ij_g g … WHERE g.__z_ok IS NULL`) so the optimizer cannot prune it. Verified on DuckDB 1.5.6
  (2026-11-02): DOUBLE vs INTEGER raises, BIGINT vs INTEGER returns rows, `closed := 'right'` raises. Unit guard for numeric columns: if `max(abs(ts)) / nullif(max(abs(end)), 0)` is > 100
  or < 0.01 → `error('interval_join: ts and interval bounds differ by >100x; ms vs s?')`. Doc example: points
  `w` in epoch-ms vs legs `s/e` in epoch-s → wrap `w // 1000` or `epoch_ms(w)` in a WITH.

```sql
CREATE MACRO __z_tfam(t) AS CASE
  WHEN t IN ('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT') THEN 'integer'
  WHEN t IN ('FLOAT','DOUBLE') OR t LIKE 'DECIMAL%' THEN 'floating'
  WHEN t IN ('TIMESTAMP','TIMESTAMP_S','TIMESTAMP_MS','TIMESTAMP_NS','DATE') THEN 'timestamp'
  WHEN t = 'TIMESTAMP WITH TIME ZONE' THEN 'timestamptz' ELSE t END;

CREATE MACRO interval_join(points_tbl, intervals_tbl, entity_col, ts_col, start_col, end_col,
                           priority_col := NULL, tiebreak_col := NULL, closed := 'left') AS TABLE
WITH __z_ij_p AS MATERIALIZED (SELECT *, row_number() OVER () AS __z_pid FROM query_table(points_tbl::VARCHAR)),
__z_ij_pk AS (SELECT __z_pid, COLUMNS(c -> c = entity_col) AS __z_e, COLUMNS(c -> c = ts_col) AS __z_t FROM __z_ij_p),
__z_ij_r AS (SELECT __z_ij_r0 AS __z_iv, *, NULL::INTEGER AS __z_none FROM query_table(intervals_tbl::VARCHAR) __z_ij_r0),
__z_ij_ik AS (SELECT __z_iv, COLUMNS(c -> c = entity_col) AS __z_e, COLUMNS(c -> c = start_col) AS __z_s,
                     COLUMNS(c -> c = end_col) AS __z_en,
                     COLUMNS(c -> c = coalesce(priority_col, '__z_none')) AS __z_pr,
                     COLUMNS(c -> c = coalesce(tiebreak_col, '__z_none')) AS __z_tb FROM __z_ij_r),
__z_ij_g AS MATERIALIZED (SELECT CASE WHEN closed NOT IN ('left','both') THEN error('interval_join: closed must be left|both')
                         WHEN __z_tfam((SELECT any_value(typeof(__z_t)) FROM __z_ij_pk))
                           <> __z_tfam((SELECT any_value(typeof(__z_s)) FROM __z_ij_ik))
                         THEN error('interval_join: ts_col and start_col type families differ; cast explicitly') END AS __z_ok),
__z_ij_m AS (SELECT p.__z_pid, i.__z_iv, i.__z_pr, i.__z_s, i.__z_en, i.__z_tb,
                    count(*) OVER (PARTITION BY p.__z_pid) AS __z_n,
                    row_number() OVER (PARTITION BY p.__z_pid
                      ORDER BY i.__z_pr ASC NULLS LAST, i.__z_s DESC, i.__z_en ASC, i.__z_tb ASC NULLS LAST) AS __z_rk
             FROM __z_ij_pk p CROSS JOIN __z_ij_g JOIN __z_ij_ik i
               ON p.__z_e = i.__z_e AND i.__z_s <= p.__z_t
              AND (p.__z_t < i.__z_en OR (closed = 'both' AND p.__z_t = i.__z_en)))
SELECT p.* EXCLUDE (__z_pid), m1.__z_iv AS iv, coalesce(m1.__z_n, 0) AS n_matches,
       coalesce(m2.__z_pid IS NOT NULL AND m2.__z_pr IS NOT DISTINCT FROM m1.__z_pr AND m2.__z_s = m1.__z_s
                AND m2.__z_en = m1.__z_en AND m2.__z_tb IS NOT DISTINCT FROM m1.__z_tb, false) AS ambiguous
FROM __z_ij_p p CROSS JOIN __z_ij_g g
LEFT JOIN (SELECT * FROM __z_ij_m WHERE __z_rk = 1) m1 USING (__z_pid)
LEFT JOIN (SELECT * FROM __z_ij_m WHERE __z_rk = 2) m2 USING (__z_pid)
WHERE g.__z_ok IS NULL
```

(Normative structure; the numeric unit guard is a further `__z_ij_u` CTE in the same style and is referenced the
same way (`WHERE … AND u.__z_ok IS NULL`); exact text pinned by tests that assert each error message fires. The
inner `CROSS JOIN __z_ij_g` in `__z_ij_m` is dropped as redundant. `__z_none` keeps `COLUMNS` from matching an
empty set, F8. `AS MATERIALIZED` (DuckDB ≥ 0.9; the analytics extra already pins `duckdb>=1.1`) guarantees the
row-numbered CTE is evaluated once, so the join-back id is stable on any supported version (D-36). `__z_tfam` and `__z_top` are
registered in `MACROS` before both joins and listed in no `DESCRIPTIONS` (internal).)

### 4.2 `asof_join`

```
asof_join(points_tbl, states_tbl, entity_col, ts_col, state_ts_col, tiebreak_col := NULL)
  -> every column of points_tbl, st STRUCT(<states_tbl columns>), n_ties BIGINT, ambiguous BOOLEAN
```

- Latest state with the same entity and `state_ts <= ts` (a state stamped exactly at `ts` applies).
- NULLs (D-23): state rows with NULL entity or NULL state_ts are ignored; a point with NULL entity or ts gets
  `st = NULL, n_ties = 0`; implemented by filtering before the `ASOF LEFT JOIN`, so DuckDB-version NULL-key
  behaviour never matters.
- No prior state: `st = NULL`, `n_ties = 0`.
- Several states with the same (entity, state_ts): `n_ties` = their count; pick smallest `tiebreak_col` if given;
  `ambiguous = n_ties > 1 and (tiebreak_col is NULL or tied on it)` (D-11). Same family guard as 4.1, referenced
  by the output the same way (D-32); the point CTE is `AS MATERIALIZED` (D-36).

### 4.3 `DESCRIPTIONS`

- `interval_join(points, intervals, 'entity', 'ts', 'start', 'end', priority_col := 'col', closed := 'left')` ->
  point columns, `iv` (containing interval, same entity; left: start <= ts < end, both: <= end), `n_matches`,
  `ambiguous`. Ties: smallest priority, latest start, earliest end, tiebreak_col. ts and bounds must share type and
  unit (ms vs s is an error). Output unordered: add ORDER BY. Points must not already have iv/n_matches/ambiguous:
  rename them when chaining.
- `asof_join(points, states, 'entity', 'ts', 'state_ts')` -> point columns, `st` (latest state at or before ts),
  `n_ties`, `ambiguous`. NULL keys never match. Points must not already have st/n_ties/ambiguous.

---

## 5. H5 — named entity sets

Placement: **new** `app/utils/entity_sets.py`; tools `EntitySetSaveTool`, `EntitySetGetTool` in
`dataset_tools.py`, category `datasets`. Storage (C16):

```json
{"name": "brewster_noncanary", "version": 3, "kind": "tiid", "ids": ["t1", "t2"], "n": 2,
 "sha256": "<sha256 of ids joined by \\n>", "note": "…", "supersedes": 2,
 "project_root": "…", "conversation_id": "…", "created_at": "…"}
```

```python
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
KIND_RE = re.compile(r"^[a-z_]{1,32}$")
REF_RE  = re.compile(r"^@([a-z][a-z0-9_]{0,63})(?:@v(\d+))?$")
def save(project_root, name, ids, *, kind="id", note="", conversation_id=None) -> dict: ...
    # sorted unique str; identical to latest -> {"unchanged": true}; else v<latest+1>, O_EXCL create (immutable)
def get(project_root, name, version=None) -> dict: ...
def list_sets(project_root) -> list[dict]: ...
def resolve_ref(project_root, value) -> tuple[Any, dict]: ...
    # "@name" -> (ids of latest, {"name": n, "version": v, "sha256": s}); "@name@v3" pinned; non-ref -> (value, {})
```

Versions are immutable files; a save never overwrites (D-16 first half was about *meta* overwriting, resolved
below). No delete tool in v1.

`@name` resolution sites and what is recorded (D-16):
- `dataset_pull.params`: per pull in `meta.pulls[i].entity_sets_used` and per slice in the committed slice records (`slices/<commit_seq>.jsonl`)
  (`entity_sets_used`) — never a single overwritten field.
- `dataset_find.entities`, `dataset_missing.entities`.
- `analyze.inputs` `{"alias": "@name"}` → one-column table `id VARCHAR`; the analyze lineage stores
  `{alias: {"entity_set": name, "version": v, "sha256": s}}` and the input is rendered as `@name@vN` in the stored
  lineage, so a later `ledger_verify` uses the same population.
- Drift warning (D-40): when `analyze` resolves `@name` (unpinned or pinned) to version `v` and some partitioned
  input dataset's `meta.pulls[*].entity_sets_used[name]` does not contain `v`, the result gains
  `warnings: [{"code": "entity_set_drift", "entity_set": name, "version": v, "pulled_versions": [...],
  "not_pulled": [<ids in v absent from every pulled version, ≤ 20>], "fix": "dataset_missing(id, t0, t1,
  entities='@name@vN')"}]`.
- Prompt: "cite entity sets as `@name@vN` in reports".

Tools:
```python
class EntitySetSaveInput(BaseModel):
    name: str
    ids: Optional[List[Union[str, int]]] = None
    from_dataset: Optional[str] = Field(None, description="ds_ id; take distinct values of `column`.")
    column: Optional[str] = None
    kind: str = "id"
    note: str = ""
class EntitySetGetInput(BaseModel):
    name: str = Field(..., description="'name' or '@name@vN'.")
    version: Optional[int] = None
```
`dataset_list` (**CHANGE**, additive): top-level `entity_sets: list_sets(project_root)`; `datasets` unchanged.

---

## 6. H6 — clean job environment and cache hygiene

### 6.1 Environment (F1)

**CHANGE** `app/jobs/manager.py:start` (before the `PYTHONPATH` prepend at ~l.305):

```python
env["ZIYA_JOB_USER_PYTHONPATH"] = env.get("PYTHONPATH", "")
env["ZIYA_JOB_ZIYA_PREFIX"] = ziya_private_prefix() or ""     # D-46: "" when Ziya runs in the user's own interpreter
env["ZIYA_JOB_PKG_ROOT"] = pkg_root
if inherit_env: env["ZIYA_JOB_INHERIT_ENV"] = "1"
```
and `meta["policy"]["inherit_env"] = inherit_env`. **New** `start_internal(argv, …)` shares the code path, spawns
`app.jobs.wrap` with `--argv-json` instead of a shell command, sets `ZIYA_JOB_INHERIT_ENV=1` **and
`ZIYA_JOB_INTERNAL=<kind>`** for the internal process, and records `meta.policy.internal = kind` (D-3).

**New** `ziya_private_prefix() -> str | None` in `app/jobs/policy.py` (pass 4, D-46). Ziya's interpreter prefix is
treated as *Ziya-private* only when Ziya runs in a dedicated environment: `sys.prefix != sys.base_prefix` (any
venv, including toolbox/pipx/uv-tool installs, which are venvs), or `realpath(sys.prefix)` contains one of
`/.toolbox/tools/`, `/pipx/venvs/`, `/uv/tools/`. Otherwise (Ziya installed into the user's own primary
interpreter, e.g. this host: `/Users/dcohn/.pyenv/versions/3.12.8`, `sys.prefix == sys.base_prefix`, verified
2026-11-02) it returns `None` and the scrub leaves `PATH`, `VIRTUAL_ENV`, `PYTHONHOME` alone — moving the user's own
`python3` behind a shim or Homebrew would break previously working jobs. In both cases Ziya's **package root**
(`ZIYA_JOB_PKG_ROOT`) is stripped from `PYTHONPATH`; that is the leak that makes `import app` / a wrong `site`
resolve.

**New** pure `scrub_child_env(env, *, force=False) -> dict` in `app/jobs/policy.py`, applied by `wrap.run` before
`sh -c` (skipped when `ZIYA_JOB_INHERIT_ENV=1` and not `force`); the `data_pull` executor calls it with `force=True`
for every connector subprocess. Key handling, `ZIYA_JOB_*` lifetime (pass 4, D-47):

- `wrap.run` for a **user** job (`ZIYA_JOB_INTERNAL` unset): applies the scrub unless inherit, then always removes
  the internal keys (last table row) from the command's env.
- `wrap.run` for an **internal** job (`ZIYA_JOB_INTERNAL` set): passes the env through unchanged, **including** the
  `ZIYA_JOB_*` keys, so the executor process has them.
- `scrub_child_env(env, force=True)` **uses** `ZIYA_JOB_ZIYA_PREFIX` / `ZIYA_JOB_PKG_ROOT` /
  `ZIYA_JOB_USER_PYTHONPATH` and then **removes** them (and `ZIYA_JOB_INHERIT_ENV`, `ZIYA_JOB_INTERNAL`) from the
  returned child env. If they are absent (executor launched by hand or by a test), it falls back to
  `ziya_private_prefix()` and the package root of the calling process (`Path(app.__file__).parents[1]`) and treats
  the user PYTHONPATH as the current `PYTHONPATH` minus that root. So a connector spawned by the executor never sees
  Ziya's venv first on `PATH`, never sees `VIRTUAL_ENV` pointing at it, never sees the package root, and never sees
  `ZIYA_JOB_*`. `subprocess.run(argv, env=child)` resolves `argv[0]` against the **child's** `PATH`
  (`os.get_exec_path(env)`), so the reordering takes effect for a bare `python3` connector command.

| Key | Default (`inherit_env=False`); P = `ZIYA_JOB_ZIYA_PREFIX` (may be empty), R = `ZIYA_JOB_PKG_ROOT` |
|---|---|
| `PYTHONPATH` | `ZIYA_JOB_USER_PYTHONPATH` **minus** entries whose realpath is under R, or under P when P is non-empty (D-17); removed if empty |
| `VIRTUAL_ENV`, `PYTHONHOME` | removed iff P non-empty and realpath under P; untouched when P is empty (D-46) |
| `__PYVENV_LAUNCHER__` | removed iff P non-empty |
| `PATH` | iff P non-empty: entries whose realpath is `<P>/bin` moved to the end; untouched when P is empty (D-46) |
| `ZIYA_JOB_USER_PYTHONPATH`, `ZIYA_JOB_ZIYA_PREFIX`, `ZIYA_JOB_PKG_ROOT`, `ZIYA_JOB_INHERIT_ENV`, `ZIYA_JOB_INTERNAL` | removed from every user command's env (including `inherit_env=True`) and from every connector child of the executor; kept only in the internal executor process itself (D-47) |

Opt-in: `JobStartInput.inherit_env: Optional[bool] = Field(False, description="Keep Ziya's interpreter environment
(PYTHONPATH, VIRTUAL_ENV, PATH order). Default: clean.")`. Hint (D-17, amended pass 3 D-41): `job_wait`/
`job_status`/`job_read` on a job that exited non-zero (any runtime) with `inherit_env` false and
`ModuleNotFoundError|ImportError: |No module named` in the **first 200 journal lines** add `"hint": "the job ran with
a clean environment; inherit_env=True keeps Ziya's interpreter"`. The `job_start` tool description states the
clean-environment default, and the CHANGELOG entry under `## [Unreleased]` is filed as a **behaviour change**
("jobs no longer inherit Ziya's interpreter environment; pass inherit_env=True").
Shell-tool leak: out of scope (F6).

### 6.2 Cache ownership

`policy.child_env` and the shell envelope set `ZIYA_CACHE_DIR=<project>/.ziya/cache/<owner_path>` and
`ZIYA_CACHE_OWNER` ∈ `conv:<id>` → `conv/<id>`, `run:<run_id>` → `run/<run_id>`, else `adhoc` → `adhoc`. Created
`0700` lazily. The harness never sets `ATLAS_METRICS_CACHE` (C11). Connector precedence:
`ATLAS_METRICS_ADHOC_CACHE` > `$ZIYA_CACHE_DIR/atlas-metrics` > `/tmp`.

### 6.3 `cache_status` tool (category `jobs`, `app/mcp/tools/job_tools.py`)

```python
class CacheStatusInput(BaseModel):
    owner: Optional[str] = Field(None, description="'conv:<id>', 'run:<id>', 'adhoc', or a glob like 'conv:*'.")
    include_connectors: bool = True
```
Result `{owners: [{owner, path, bytes, files, oldest, newest, purgeable}], datasets: {bytes, n, retired_bytes},
total_bytes, connectors: [{name, ok, status | error}]}`; `purgeable` = `conv:*` or `adhoc`. Implementation
`app/utils/cache_hygiene.py: scan / prune(project_root, *, older_than_s=7*86400, max_bytes=20*2**30,
owner_glob="conv:*", dry_run=False)`; prune also unlinks dataset files in `retired` past grace and sweeps stale
job reservations **only** by the §1.1 rule (`reserved` marker present, no `job.json`, > 1 h, marker pid dead); it
never deletes a job dir that has `job.json`. CLI `ziya cache
status|prune` (`cmd_cache` next to `cmd_ledger` in `app/cli.py`). Prompt line gated on `jobs` (D-24).

---

## 7. H7 — advisory lint

Placement: **new** `app/utils/script_lint.py`.

```python
@dataclass(frozen=True)
class Rule: id: str; pattern: re.Pattern; message: str
RULES: tuple[Rule, ...]
SCAN_CAP_BYTES = 512 * 1024
MAX_HINTS = 5
CONTEXT_LINES = 12
PRAGMA_RE = re.compile(r"#\s*ziya:\s*lint-ok\s+([\w,]+)")
def lint_python(text: str, *, region: tuple[int, int] | None = None) -> list[dict]: ...
    # [{id, line, message}]; region = (first_line, last_line) of changed text; ±CONTEXT_LINES
def should_lint(path: str) -> bool: ...   # *.py, not under tests/, not app/utils/script_lint.py (D-19)
```

| id | Regex | Example that fires | Hint |
|---|---|---|---|
| `nocache_loop` | prefilter `\bnocache\s*=\s*True`; then **AST** (pass 4, D-54): a `Call` with keyword `nocache=Constant(True)` whose ancestor chain (within the enclosing function, not crossing a `def`/`lambda`/`class`) contains a `For`/`AsyncFor`/`While` body. On `SyntaxError` (partial file) fallback: regex `^([ \t]*)(?:for\|while)\b[^\n]*:\s*\n((?:[^\n]*\n){0,11}?)([ \t]*)[^\n]*\bnocache\s*=\s*True` (`re.M`) accepted only if `len(group 3) > len(group 1)` **and** no non-blank line in group 2 has indentation `<= len(group 1)` | `for t in slices:\n    rows = q(sql.format(t), nocache=True)` | "nocache=True inside a time-slice loop: use dataset_pull(..., settle=)." |
| `config_from_source` | `open\(\s*[^)]*\.py['"]\s*\)\s*\.read\(\)\s*\.split\(` | `LEB = open("anand_loss7d.py").read().split("LEB = [")[1]` | "Config parsed from another script: entity_set_save once, pass '@name'." |
| `retry_loop` | `for\s+\w+\s+in\s+range\(\s*\d+\s*\)\s*:\s*\n\s*try\s*:\s*\n[^\n]*(?<![\w.])q\(` | `for i in range(3):\n    try:\n        r = q(sql)` | "Hand-written retry around q(): dataset_pull retries and stops on permanent errors." |
| `filter_after_load` | `read_parquet\([^)]*\)[\s\S]{0,400}?\.isin\(` | `df = pd.read_parquet(f)\ndf = df[df.tiid.isin(cts)]` | "Filter in analyze (WHERE … IN), not after loading." |
| `groupby_apply_lambda` | `\.groupby\([^)]*\)\s*(?:\[[^\]]*\]\s*)?\.apply\(\s*lambda` | `df.groupby("tiid").apply(lambda g: g.loss.quantile(.9))` | "analyze does this in SQL without loading into pandas." |
| `syspath_scrub` | `sys\.path\[:\]\s*=\s*\[\s*\w+\s+for\s+\w+\s+in\s+sys\.path\s+if` | `sys.path[:] = [p for p in sys.path if ".toolbox/tools/ziya" not in p]` | "Not needed under job_start: jobs get a clean environment (H6)." |

Near-miss for `nocache_loop` (D-54, must **not** fire): `for k in cfg:\n    pass\n\ndef latest():\n    return
q("SELECT max(ts) FROM t", nocache=True)\n` (the call is in a different function after a dedent). **Bounded AST work (pass 5, D-64)**: the AST
never parses the whole file. For each prefilter hit line, the enclosing **top-level block** is located by
indentation (from the nearest preceding line at column 0 that starts a statement — `def`, `async def`, `class`,
`for`, `while`, `with`, `if`, `try`, or any other — to the line before the next column-0 statement line; comment and
blank lines do not end a block). **String state (pass 6, D-69):** column-0 candidates come from one linear
`string_state_scan(text) -> bytearray` (per line: 1 if the line *starts* inside a triple-quoted string), a
character loop tracking `'''`/`"""` openers with any `[rRbBuUfF]{0,2}` prefix, backslash escapes, single-quoted
strings (closed at end of line) and `#` comments outside strings; lines flagged 1 are never block starts or block
ends. The scan is O(n) and inside the 20 ms negative budget only when the prefilter hits (it runs only then). Each
distinct block is `ast.parse`d alone (top-level, so no dedent is needed), blocks > 64 KB use the regex fallback,
and results are cached per `(path, sha256(block))` in a bounded LRU (256 entries) on the module. **On a
`SyntaxError` of a block** (an unusual string form the scan mis-tracked, or a genuinely partial file) the
fallback order is: whole-file `ast.parse` when `len(text) ≤ WHOLE_PARSE_MAX = 256 KB` (≈ 115 ms on this host,
inside the 250 ms worst case); else the regex fallback, whose indentation check **skips lines flagged in-string**.
Must-fire case: `def main():\n    for t in slices:\n        sql = f"""\nSELECT * FROM t\nWHERE ts >= '{t}'\n"""\n        rows = q(sql, nocache=True)\n`. In patch mode only hits whose line is in the region ±12 are considered.
Rules (D-19): `(?<![\w.])q\(` only matches a bare `q(` call; a line or the file header carrying
`# ziya: lint-ok <rule>[,<rule>]` suppresses that rule; each `(conversation_id, path, rule)` is reported at most once
per conversation (in-memory set on the tool module, keyed by conversation id); first match per rule; ≤ 5 hints;
cost budget (asserted): **< 20 ms** for a 512 KB prefilter-negative file (full and patch mode), **< 250 ms**
worst case for a 512 KB file with hits (measured basis: `ast.parse` of a whole 500 KB file costs ~226 ms on this
host, so per-block parsing keeps the worst case below that).

Surfacing (never blocks, additive key):
- `FileWriteTool.execute` (`app/mcp/tools/fileio.py`): successful write with `should_lint(path)` → `"lint_hints"`
  (omitted when empty). Full write lints the whole text; **patch mode lints only the replaced/inserted region ±12
  lines** of the post-patch text.
- `JobStartTool.execute`: `python3? [-u] <file>.py …` under the project with `should_lint` → `"lint_hints"` in the
  start result (and in the `wait_s` result).
- Format `"[nocache_loop] L42: <message>"`.

---

## 8. H8 — data-sink protocol v1 (one page) and `ziya_data_sink.py`

§8.1–8.3 are normative; the H8 implementation copies them into `design/data-sink-protocol.md`.

### 8.1 Protocol v1

**Discovery (env only).** `ZIYA_DATA_SINK=<versions>:<target>`, split on the first `:`. `<versions>` = comma list of
decimal strings the harness serves (`"1"`, later `"1,2"`). `target` starting `http(s)://` is an endpoint base;
otherwise a dotted module path exporting `open_sink`. The client picks the **highest version both sides support**;
none in common, or unset → its own local sink (one stderr line). There is no `find_spec` discovery (D-13, D-21).
The harness keeps serving v1 for at least one release after v2 ships.

**Opening.** `open_sink(name, *, identity, ts_col, entity_col, key, partition_by="utc_day", source=None,
settle_s=None, concurrency=None, source_now=None) -> Sink`. **`identity` is mandatory** (C33, pass 6, D-67):
compute it with `dataset_identity(...)` from the producing SQL template and non-entity params; without it →
`SinkError("permanent")`, code `identity_required`. `source_now` (optional, ISO UTC from the source clock) lets the
sink measure skew (C31). `ZIYA_DATA_SINK=off` → `NullSink`, no local fallback (C36); a producer started with
`--sql-file/--parquet` must not open a sink. `source`/`settle_s` fix the handle's threshold per C28 (bound handle:
plan threshold, raised by `settle_s`; unbound: registry/caps for `source`, raised by `settle_s`, or `settle_s` alone
with `settle_source="producer"`). **A producer that passes `source` MUST also pass `settle_s = settle_seconds(source)` (S2)** (pass 5, D-66): the
local sink has no registry, so only then do the local and Ziya sinks reach the same threshold. `source` without
`settle_s` is accepted but both sinks attach warning `settle_s_missing` to the open result (and the client prints it
to stderr once); parity (AC 6) is guaranteed only when `settle_s` is given. `concurrency=1` is the producer's
promise that it submits each slice query only after its previous request on this handle returned; it makes `L`
(C24) the previous response time and is recommended for every producer. Endpoint form: `GET <target>/v<N>/health`
first; failure → local sink and the client prints `data-sink: local at <path>` to stderr — **unless
`ZIYA_DATA_SINK_REQUIRED=1`** (set by the harness for delegated `dataset_pull` children, C32), in which case a
failed health check (or no common version, or an unparsable value) raises `SinkError("permanent")` and the producer
must exit non-zero (pass 5, D-58). This is the **only** fallback point; after opening, errors are raised (`SinkError(transient|permanent)`),
never silently redirected (D-14). **Same `identity` → same dataset; `name` is a label.** A `name` already used by
a dataset of another identity → the handle's own identity's dataset plus warning `name_reused` (records never
shared, `missing()`/`coverage()` see only the handle's dataset). A bound handle whose identity differs from its
`data_pull` plan → `identity_mismatch`.

**Sink methods.**

| Method | Returns | Rules |
|---|---|---|
| `append(partition, table, key, *, on_duplicate="error", slice=None)` | `{partitions: [{partition, n_in, n_new, n_replaced, n_dup_in}], buffered: true}` | `table` = `pyarrow.Table` or parquet path; within-batch duplicates per C20; cross-batch newest wins; `slice` = `{t0, t1, entities, queried_at?}` (`queried_at` optional and **diagnostic only**: stored as `producer_queried_at`; the record's `queried_at` is the handle's `L`, C24 pass 5) ≡ `append` + `record_slice(…, "ok", n)`. Rows get `__z_qat = L` (C30). With `slice`: rows outside `[slice.t0, slice.t1)` → `boundary_overlap`; a slice spanning midnight is **split** and its rows routed by `ts_col` (C26); `partition` is `None` or the partition of `slice.t0`. Without `slice`: rows outside `partition` → error. NULL key → error. Errors buffer nothing. |
| `record_slice(partition, slice, status, rows)` | `None` | **Required for every attempted slice** (v1, D-30), including zero-row (`status="ok", rows=0`) and failed (`status="failed"`); `slice.queried_at` optional, diagnostic. A window spanning midnight is split into one record per partition (C26), `rows` apportioned by the rows actually routed. The record's `queried_at` is always `L` (C24, D-42 pass 5); the producer value is kept as `producer_queried_at` with `producer_clock_suspect` when outside `[L − 5 s, received_at]`; `settle_s_used`, if sent, is ignored (the handle's threshold is used, C28). |
| `flush(partition=None)` / `close()` | `[{partition, commit_seq, …counts}]` | Sinks **buffer** appends and slice records per (handle, partition) and commit them in one `append_partition` (C22) on `flush`, on `close`, when a partition's buffer exceeds `flush_rows` (default 2 000 000), or when the producer moves to a later partition. Unflushed buffers at a crash are lost and **not** covered. |
| `coverage(*, settle=None)` | `{partition: {t_min, t_max, entities, n}}` | Descriptive row hull; unknown extra keys ignored by clients (C6). `settle` accepted for symmetry (no effect on the hull). |
| `missing(t0, t1, entities, *, settle=None)` | sorted `[[partition, entity], …]` | From recorded slices (C7 amended) with `threshold = effective_threshold(<handle declared>, settle)` raised by the handle's threshold (C28); `entities=None` → every entity recorded, `"*"` for entity-less. Same answer from `LocalParquetSink` and the Ziya route when `settle_s` is given (MUST accompany `source`, D-66); without it both return warning `settle_s_missing` and parity is not guaranteed (AC 6). |
| `observe(query_text, result_hash, *, connector=None, as_of=None, partition=None)` | `obs_id` or `None` | Optional; local sinks return `None`. Ziya records `source="sql"` attested by the partition (C15). |
| `dataset_id` | `str` or `None` | |

**Endpoint form (Ziya).** `POST <target>/v1/<op>`, `op` ∈ `open, append, record_slice, flush, close, coverage,
missing, observe`; `open` refuses a token bound to a harness-sliced `data_pull` job (`sink_not_expected`, C36) and binds the handle to the token's job when the token carries a `data_pull` job (threshold,
`job_id`), and otherwise computes the threshold per C28; it records `opened_at`, `source`, `settle_source`,
`concurrency`, and the route keeps `last_response_at` per handle (C24 bound); `open` sends `{name, identity, key, ts_col, entity_col, partition_by, source, settle_s, concurrency, source_now}`;
`append` sends
`{handle, partition, key, path, on_duplicate, slice}` with `path` a parquet file under `$ZIYA_CACHE_DIR/sink-out/`.
Header `X-Ziya-Ledger-Scope: $ZIYA_LEDGER_SCOPE`. Responses `{"ok": true, …}` or `{"ok": false, "error", "message",
"retryable": bool}`; `401` (expired token) is permanent. Tokens for jobs are issued with TTL = `max_runtime_s +
3600` (D-14); the route can only touch datasets in the token's project.

**Capabilities.** `<connector> --capabilities` → `{"settle_s": {source: int}, "typed", "sliced", "sink_protocol":
["1"], "cache_status"?}`; undeclared sources omitted (never settled). Delegate iff `sliced` and `"1"` ∈
`sink_protocol`.

**Stability.** Additive within v1; anything else is v2 at `/v2/` and advertised in `<versions>`.

### 8.2 Vendorable module `app/utils/ziya_data_sink.py`

Stdlib + optional `pyarrow`, no `app.*` imports (AST-checked), like `ziya_ledger_client.py`.

```python
PROTOCOL_VERSIONS = ("1",)
PROVIDER = "ziya"
ENV = "ZIYA_DATA_SINK"
class SinkError(Exception): kind: str   # "transient" | "permanent"
def parse_env(value: str | None) -> tuple[tuple[str, ...], str] | None: ...   # (("1","2"), "http://…") / None
def choose_version(served: Sequence[str], supported: Sequence[str] = PROTOCOL_VERSIONS) -> str | None: ...
def discover(env=None) -> tuple[str, str, str] | None: ...                    # (version, "endpoint"|"module", target)
REQUIRED_ENV = "ZIYA_DATA_SINK_REQUIRED"   # "1" -> no local fallback at open (C32, D-58)
def dataset_identity(*, sql_template: str, params: Mapping[str, Any], entity_param: str | None,
                     key: Sequence[str], ts_col: str, entity_col: str | None,
                     partition_by: str = "utc_day", source: str | None) -> str: ...   # C18/C33, 24 hex
def open_sink(name, *, identity, ts_col, entity_col, key, partition_by="utc_day",
              source=None, settle_s=None, concurrency=None, source_now=None,
              local_root=None, flush_rows=2_000_000) -> "Sink": ...   # C28 threshold, C24 concurrency;
              # identity missing/not 24 hex -> SinkError("permanent", code="identity_required") (D-67)
              # ZIYA_DATA_SINK=off -> NullSink (C36, D-71)
              # source without settle_s -> warning settle_s_missing (D-66); REQUIRED_ENV=1 + failed health -> SinkError("permanent")
def split_slice(t0: datetime, t1: datetime, partition_by="utc_day") -> list[tuple[datetime, datetime, str]]: ...  # C26
# Sink: append, record_slice, flush, close, coverage, missing, observe, dataset_id; context manager (close on exit)
class Sink(Protocol): ...
class NullSink: ...       # C36: no-op sink for ZIYA_DATA_SINK=off; missing() returns every requested pair
class LocalParquetSink:   # <local_root or $ZIYA_CACHE_DIR/data-sink or /tmp/ziya-data-sink-$USER>/<identity>/  (C33; name in meta.json)
                          #   utc_day=…/part-*.parquet + meta.json (commit_seq) + slices/<seq>.jsonl
class HttpSink:           # urllib; /v<N>/<op>; health at open
```

`LocalParquetSink` implements the same append/dedupe/slice-ledger, buffering, `record_slice`, C21 and C22 commit
rules (pyarrow; without pyarrow only JSONL records and no parquet-path input), including the C26 split, stamping
`queried_at = L` from its own clock (`opened_at`, or under `concurrency=1` the time its previous call returned;
`clock_source="contact"`, producer value kept as `producer_queried_at`, C24 pass 5), the same `__z_qat`
newest-queried merge (C30), skew floor and measured skew from `source_now` (C31), identity keying and
`name_reused` (C33), and the threshold `settle_s` given to `open_sink`
(`settle_source="producer"`, C28; `source` without `settle_s` → warning `settle_s_missing`, D-66). Counts and `missing()` identical to the Ziya sink for the same `source`/`settle_s` (AC 6). Server side: `POST
/api/data-sink/v1/<op>` in `app/routes/`, backed by H2, token verified with `evidence.scope.verify`.

### 8.3 Where `ZIYA_DATA_SINK` is set

Value: `1:http://127.0.0.1:<port>/api/data-sink` (same host/port as `ZIYA_LEDGER_URL`).

| Spawn path | Change |
|---|---|
| `evidence.scope.build_envelope(project_root, conversation_id, *, ttl_s=DEFAULT_TTL_S)` | **CHANGE**: `ttl_s` parameter; returns additionally `data_sink`, `cache_dir`, `cache_owner` |
| Shell tool: `shell_server._ledger_env()` (envelope attached in `app/mcp/manager.py` ~l.2639) | emits `ZIYA_DATA_SINK`, `ZIYA_CACHE_DIR`, `ZIYA_CACHE_OWNER` |
| Managed jobs: `policy.child_env` | same keys; envelope built with `ttl_s = max_runtime_s + 3600` |
| Task-card lanes | no separate spawn path: shell dispatch and `JobManager.start` with run/block provenance; asserted by a run-scoped envelope test |
| Internal `data_pull` jobs | `JobManager.start_internal` uses the same `child_env`; **delegated** connector children get it through `scrub_child_env(force=True)` (which never removes `ZIYA_*` data/ledger/cache keys) and additionally `ZIYA_DATA_SINK_REQUIRED=1` (C32, D-58); **harness-sliced** connector children get `ZIYA_DATA_SINK=off` and no `ZIYA_DATA_SINK_REQUIRED` (C36, pass 6, D-71). No other spawn path sets it. |

**Verification of slice observations (D-33).** **CHANGE** `app/utils/evidence/verify.py`: kind `dataset_slice` →
recompute `slice_sha256` over the dataset's current committed rows → `agrees`, or `drifted` when a later commit
replaced them (expected for `volatile=true` young slices, reported with the newer `commit_seq`), or `not_testable`
when the dataset is gone or one of the record's `slice_columns` no longer exists (C29, D-51); columns added after
the commit are ignored. Kind `dataset_snapshot` (C15, D-62, amended pass 6, D-70) → `agrees` iff every partition in
`ref.partitions` has the same current `content_sha` (C35), else `drifted` with `changed_partitions` and the current
`commit_seq`; a changed observation recorded `volatile=true` → `not_testable` (existing rule); dataset gone →
`not_testable`. **CHANGE**
`app/utils/evidence/store.py`: accepts both ref kinds and an `idempotency_key` (same key → same `obs_id`, no new
row). **CHANGE** `app/mcp/tools/ledger_tools.py`: description and `observation.slice` pass-through (C15).

No envelope → key unset → clients use the local sink.

---

## 9. Compatibility with connector guidance

| S-item | Connector provides | Consumed by | How |
|---|---|---|---|
| S1 no cache write on `nocache` | `if not nocache: cache_put(...)` | H6 `cache_status`, AC 3 | Stub implements; test asserts `$ZIYA_CACHE_DIR/atlas-metrics` unchanged. |
| S2 settle horizon, `until=` | `settle_seconds(source)`, `settle_s` and (pass 6) `now` in `--capabilities`; `source_now` on `open_sink` | H1 `resolve_settle` → `settle_s_declared`, `effective_threshold` (C23) | Precedence C14; explicit `settle=` can raise, never lower, a declared horizon; result reports `threshold` + `settle_source`; raising a horizon invalidates older judgments, and read paths see the raise at once (C25, D-43). The same `settle_seconds(source)` value passed to `open_sink(source=, settle_s=)` gives identical coverage in the local and Ziya sinks (C28, D-44); passing `source` without it is a protocol violation that both sinks report as `settle_s_missing` (D-66). Horizons are compared against the harness clock plus `max(SKEW_MARGIN_S, measured skew)`; the skew is measured from the source clock in `--capabilities.now` or `open_sink(source_now=)` (C31, D-65 pass 6). |
| S3 typed / columnar | `q_frame`, `--parquet` | H1 C13 output, H2 `append_partition`, H8 `append(table)` | Arrow or parquet path; union by name; type conflict error. |
| S4 `q_sliced` | Python + `--sliced` (C10) + `--params-file` + optional `--slices-file` | H1 delegated mode, H8 `append(slice=)` / `record_slice` / `flush` | Connector may slice by step only (S4 as written); the sink splits midnight-straddling slices (C26, D-45). It is invoked once per contiguous to-pull run with that run's `--t0/--t1` and entity subset, so step-only slicing never re-queries covered time (C32, D-59), and under `ZIYA_DATA_SINK_REQUIRED=1` must not fall back to a local sink (D-58). It computes `identity` with `ziya_data_sink.dataset_identity` from its template and non-entity params and passes it to `open_sink` (mandatory, C33, D-67); run boundaries are coalesced by the harness (C34, D-72); when started with `--sql-file/--parquet` it opens no sink (`ZIYA_DATA_SINK=off`, C36, D-71). It calls `record_slice` for every attempted slice incl. zero-row/failed (D-30), keeps rows inside `[T0,T1)`; the sink buffers and stamps settledness and merge order with the handle's lower contact bound `L` (C24, C30, D-42). Its own `queried_at` is diagnostic. |
| S5 cache placement / status / prune | `ATLAS_METRICS_ADHOC_CACHE`, `--cache-status`, `--prune` | H6 `ZIYA_CACHE_DIR`, `cache_status` | C11 precedence. |
| S6 retry contract | retried vs `_FATAL` | H1 executor | Exit 3 permanent, other non-zero transient (C13). |
| §3 soft detection / sink | `LocalParquetSink`, env adapter | H8 | Env-only discovery, version list, health-at-open fallback; AC 6. |

---

## 10. Test plan

Fixture `tests/fixtures/stub_connector/graf_q_stub.py` (stdlib + pyarrow): `--capabilities`, `--sql-file/--parquet`,
`--sliced [--slices-file]`, `--cache-status`; deterministic table (`tiid`, `w` 4-s bins, `rx_modem`, `loss`);
switches `STUB_CAPS=sliced|plain|none`, `STUB_LATE_ROWS=<min>` (rows in the last N minutes before `now` are withheld,
so a young query is partial), `STUB_FAIL=transient:2|permanent|at:<slice>`, `STUB_DUP_MODEM=1` (two rows per
(tiid,w)), `STUB_OFFLINE=<tiid>@<iso>`, `STUB_INCLUSIVE_T1=1` (returns the bin at `T1` too, D-34),
`STUB_CRASH_AFTER=<n>` (kills the executor after n partition commits, D-26), `STUB_CLOCK_SKEW_S=<s>` (stub's
`--capabilities.now` and `source_now` are `s` seconds behind the injected harness clock, D-65), `STUB_ADAPTER=1`
(the stub's layer-1 adapter opens a sink whenever `ZIYA_DATA_SINK` is not `off`, D-71), `STUB_TEMPLATE=<ul|dl>`
(two different SQL templates with the same name and key, D-67), a per-slice invocation counter, S1
cache behaviour. Clock injected through `ZIYA_TEST_NOW`, honoured only with `ZIYA_TESTING=1` (set in
`tests/conftest.py`), read by `data_pull` and the stub (C24, D-38). Store fault injection: `dataset_store` reads
`ZIYA_TEST_FAIL_AT ∈ {after_part, after_slices, before_meta_rename}` (also gated by `ZIYA_TESTING=1`) (D-27). Shared C3 vector `tests/fixtures/data_pull_render.json`.

### 10.1 Seam tests per item (tool registered AND callable AND prompt mentions it)

| Item | File | Tests |
|---|---|---|
| H1 | `tests/test_data_pull.py` | parsing (steps, naive time rejected), render vector, slice plan; `resolve_settle` precedence + `settle_source`; identity reuse without `dataset_id` (`reused: true`), `params_mismatch`; **D-1**: pull while young → clock past horizon → re-pull re-queries the young slices exactly once, third pull queries none, late rows present; **D-10** refresh and raised horizon re-query; executor under the default clean env succeeds and the stub's `sys.path` has no Ziya root (**D-3**); buffered commit: 96 slices → 1 partition rewrite (**D-18**); crash before commit re-queries only uncommitted slices; ledger observation per slice carries the conversation id (**D-15**); token TTL ≥ job runtime (**D-14**); **pass 3**: unaligned t0 (01:15-07:00) + `STUB_CRASH_AFTER=1` → `dataset_missing` reports the second partition's 00:00–00:15 window (**D-26**); `plan_slices` never yields a slice spanning midnight; `queried_at` equals the pre-spawn sidecar of the producing attempt, a delayed commit does not make a young slice settled, retry uses its own time (**D-28**); `settle=0` on a declared connector → warning `settle_below_declared`, records `settle_s_used = declared`; `dataset_missing` and the next `dataset_pull` agree slice-for-slice for each `settle` value (**D-29**); `STUB_INCLUSIVE_T1=1` fails on the first slice with `boundary_overlap` (stub counter = 1), `boundary="clip"` succeeds with `n_clipped > 0`, plan warning `template_not_half_open` (**D-34**); param `"1; touch /tmp/pwn"` arrives verbatim in `params.json`, `/tmp/pwn` absent, executor never calls a shell, policy checked the stored `argv_rendered` (**D-35**); `max_age_s` re-queries old covered slices (`reason="stale"`), coverage shows `queried_at_min/max` (**D-37**); `ZIYA_TEST_NOW` without `ZIYA_TESTING` is ignored with a warning test (**D-38**); observe failure does not hold the dataset lock (concurrent analyze completes) and is replayed idempotently on the next run (**D-39**); tool in `get_dataset_tools()` and `datasets` description; prompt mentions `dataset_pull`. |
| H2 | `tests/test_dataset_partitioned.py` | layout/meta/`slices/<seq>.jsonl`; `on_duplicate` error default with examples, `last`/`first` (**D-4**, `STUB_DUP_MODEM`); cross-batch newest-wins; null key / partition mismatch; overlap `n_new=0`; **D-2** (a) failed slice reported missing, (b) late roster entity missing only for itself, (c) offline entity with settled zero rows covered; engine accepts a file list, hive column present (**D-7**); concurrent analyze during appends: `count(*) == count(DISTINCT key)` and no missing-file error (**D-6**); retired files deleted after grace; preview of a large partitioned dataset uses LIMIT pushdown (RSS/row-count bound) and `load_dataframe` refuses (**D-25**); **pass 3**: `ZIYA_TEST_FAIL_AT` at each of the three points → meta unchanged, `slice_records` excludes the orphan file, next `plan_pull` re-queries those slices, next append deletes the orphans (**D-27**); slice record with a window outside its partition → `slice_outside_partition` (**D-26**); `slice_sha256` stable across re-reads and changes when a later commit replaces the slice's rows (**D-33**); `dataset_append`/`dataset_missing` registered; legacy tests green. |
| H3 | `tests/test_dataset_find.py` | project filter, table regex; **D-9** A-day1/B-day2 dataset scores 0.5 for [A,B]×2 days; no-entities result flags `entities_unconstrained` and ranks 18-CT above 1-CT; **D-31** a dataset pulled from an undeclared connector without `settle=` is returned with `score_settled == 0`, `score_any > 0`, `unsettled: true`, ranked below a settled one; legacy after; registered; prompt contains "Before any pull, call `dataset_find`". |
| H4 | `tests/test_analytics_interval_join.py` | half-open and `closed='both'` boundary; unmatched kept; `n_matches`; each tie rule; `ambiguous` on full tie; type-family mismatch (DOUBLE vs INTEGER) and ms/s errors fire with their messages, BIGINT vs INTEGER passes, `closed := 'right'` errors (**D-12**, **D-32**); macro text contains `AS MATERIALIZED` for the point CTE (**D-36**); results identical with `threads=1` and `threads=8` on a large tied fixture except rows flagged `ambiguous` (**D-11**); `asof_join` at-or-before, NULL rules (**D-23**), `n_ties`; both in `TABLE_ARGS`, `DESCRIPTIONS`, prompt list. |
| H5 | `tests/test_entity_sets.py` | validation, versioning, unchanged save, immutability, per-project isolation; `@name`/`@name@vN` in pull/find/analyze; per-pull and per-slice versions survive a v4 save; analyze lineage records `@name@vN` + sha (**D-16**); analyze with `@name` at v4 over a dataset pulled with v3 returns `entity_set_drift` naming the new id (**D-40**); `dataset_list.entity_sets`; tools registered. |
| H6 | `tests/test_job_runner_env.py` | `scrub_child_env` table row by row incl. server `PYTHONPATH` containing a fake `.toolbox/tools/ziya` path under the prefix (**D-17**); real job shows no Ziya root; `inherit_env=True` keeps it; `start_internal` keeps it for the executor and `force=True` scrub for its children; ModuleNotFoundError hint fires for a job that fails at import after 6 s (sleep then import) and not for a job whose journal lacks the pattern (**D-41**); `job_start` description mentions the clean default; cache env keys; `cache_status` in `get_job_tools()` and the `jobs` description (**D-24**); prompt line gated on `jobs`; prune dry-run. |
| H7 | `tests/test_script_lint.py`, `tests/test_fileio_tools.py` | each rule fires on its example, not on a near-miss (`df.q(`, `myq(`); pragma; `script_lint.py` excluded; patch mode lints only the region; once per (conversation, path, rule) (**D-19**); cost bounds; `file_write` / `job_start` carry `lint_hints`, writes never blocked. |
| H8 | `tests/test_ziya_data_sink.py` | `parse_env` version lists, `choose_version` (v1 client vs `1,2` server works — **D-13**); no `find_spec` path (**D-21**); health-at-open fallback prints local path, no mid-run fallback, 401 permanent (**D-14**); `LocalParquetSink` append/slice/missing parity; `record_slice` zero-row covers, failed does not; buffered: 96 appends → 1 partition rewrite per sink (**D-30**); `append(slice=)` straddling midnight → split into two records with `split_from` (C26; the store-level `append_partition` still refuses a single out-of-partition record with `slice_outside_partition`); producer `queried_at` outside the handle window → `queried_at = L`, `clock_source="contact"`, producer `settle_s_used` ignored (**D-28**, **D-42**); `dataset_slice` observation verifies `agrees`, then `drifted` after a re-pull (**D-33**); no `app.` import; `child_env`, shell `_ledger_env`, run-scoped envelope set `ZIYA_DATA_SINK=1:http://127.0.0.1:<port>/api/data-sink`; route rejects bad token. |

**Pass-4 additions (D-42…D-55)** — each row names the file it extends:

| Finding | File | Test |
|---|---|---|
| D-42 | `tests/test_ziya_data_sink.py` | delegated stub (`STUB_APPEND_DELAY_S`, new switch) submits slice `[t1−15m, t1)` at `t1 + threshold − 120 s` and delivers the append 180 s later: (a) with its pre-submit `queried_at` → `settled_at_query=false`, slice stays in `missing()`; (b) with `queried_at` omitted → the record uses `L` (`clock_source="contact"`, response carries warning `queried_at_missing`), still unsettled; (c) with `queried_at` later than `received_at` → replaced by `L`, still unsettled; (d) `concurrency=1` with sequential appends → `L` = previous response time, so a correctly stamped slice queried after the horizon *is* settled. |
| D-43 | `tests/test_data_pull.py` | pull with registry `settle_s=10800` at a time when all slices are settled at 10800 but not at 21600; edit `.ziya/connectors.json` to 21600; `dataset_missing` == the next `dataset_pull`'s to-query set (non-empty), `dataset_find` `score_settled < 1`; stale `coverage_threshold` cache ignored. |
| D-44 | `tests/test_ziya_data_sink.py` | `open_sink(source="clickhouse", settle_s=10800)` from a shell-tool-scoped token (no job) under Ziya and with the env unset: `missing()` equal, records `settle_s_used=10800`, `settle_source="producer"` when the registry is empty and `"registry"` when it declares 10800; a producer `settle_s` below a registry value does not lower it. |
| D-45 | `tests/test_ziya_data_sink.py`, AC 6 | stub with `STUB_ALIGN=step` (slices from t0 by step only, `--slices-file` ignored) over 08:15Z…+2 d, 1 h: pull succeeds; the 23:15–00:15 slice yields two records with `split_from`, rows routed to both partitions; crash after the first partition's commit leaves the 00:00–00:15 sub-slice in `missing()`. |
| D-46 | `tests/test_job_runner_env.py` | `scrub_child_env` with a fake env where `ziya_private_prefix()` is `None` (prefix == base_prefix) leaves `PATH`/`VIRTUAL_ENV` byte-identical and only removes the package root from `PYTHONPATH`; a venv-prefix case moves `<P>/bin` to the end. |
| D-47 | `tests/test_data_pull.py` | stub run by the executor reports `sys.executable` not under a private Ziya prefix (when one exists), no `VIRTUAL_ENV` under it, no package root on `sys.path`, no `ZIYA_JOB_*` keys; executor run by hand (keys absent) gives the same via fallback. |
| D-48 | `tests/test_dataset_partitioned.py`, `tests/test_data_pull.py` | two threads (and two processes) call `create_partitioned(identity=X)` / `dataset_pull` concurrently: one dataset id, the other result `reused: true`, the losing dataset dir does not exist; stale index entry is repaired. |
| D-49 | `tests/test_analytics_interval_join.py` | points with an `n_matches` (or `ambiguous`) column → the guard error; empty such table → still errors; the documented chaining WITH works and keeps both flags; engine refuses a hand-written query returning two `ambiguous` columns (`duplicate_output_column`). |
| D-50 | `tests/test_dataset_partitioned.py` | `typeof(utc_day) = 'VARCHAR'` through `analyze`; a source with its own `utc_day` column → `partition_column_conflict`. |
| D-51 | `tests/test_dataset_partitioned.py`, `tests/test_ledger_*` | golden `slice_sha256` for a fixed fixture; appending a batch with a new column leaves earlier slices `agrees`; dropping a recorded column (rewrite fixture) → `not_testable`. |
| D-52 | `tests/test_analyze_tool_seam.py` | a commit injected between snapshot and lineage write (hook) → lineage `commit_seq`/`sha256` equal the snapshot's, not the new meta's. |
| D-53 | `tests/test_data_pull.py` | one commit with two entity subsets of one window → two observations with distinct keys; failed-then-ok for one window → one observation, `n_superseded_records` 0 / ok-then-ok → 1. |
| D-54 | `tests/test_script_lint.py` | the dedent near-miss does not fire; a nested-loop call fires; a partial file (SyntaxError) uses the indent-checked fallback. |
| D-55 | `tests/test_job_runner*.py`, `tests/test_data_pull.py` | `ZIYA_TESTING == "1"` under pytest; `JobManager.reserve`/`release` round-trip and `start` unchanged; policy refusal releases the reservation (no job dir left). |

**Pass-5 additions (D-42 reopened, D-56…D-66)**:

| Finding | File | Test |
|---|---|---|
| D-42 | `tests/test_ziya_data_sink.py` | replaces the pass-4 D-42 cases: a stub that stamps `queried_at` at frame completion (inside `[L, received_at]`) for a slice submitted at `t1 + threshold − 120 s` → record `queried_at == L`, `clock_source="contact"`, `producer_queried_at` kept, `settled_at_query=false`, slice in `missing()`; `concurrency=1` sequential appends → `L` = previous response time; same in `LocalParquetSink`. |
| D-56 | `tests/test_dataset_partitioned.py`, `tests/test_data_pull.py` | executor A queries a slice at 12:00 (young, `STUB_LATE_ROWS`) and commits at 12:40; executor B queries it at 12:10 (settled) and commits at 12:15 → final rows equal B's, `n_stale_in = n_in` for A's commit, B's `slice_sha256` verifies `agrees`; same interleaving through `LocalParquetSink`; `__z_qat` absent from `analyze` `SELECT *`, `dataset_describe.columns` and `slice_columns`. |
| D-57 | `tests/test_job_runner*.py`, `tests/test_cache_hygiene.py` | prune leaves a > 1 h old finished job (has `job.json`) and a running job intact, removes a > 1 h stale `reserved`-only dir with a dead pid, keeps a `reserved`-only dir whose pid is alive; `start` leaves no `reserved` marker after writing `job.json`. |
| D-58 | `tests/test_data_pull.py`, `tests/test_ziya_data_sink.py` | stub health endpoint returns 503: with `ZIYA_DATA_SINK_REQUIRED=1` `open_sink` raises `SinkError("permanent")`; delegated pull fails (no exit-0 success), nothing written under `/tmp/ziya-data-sink-$USER`; a stub that exits 0 without `record_slice` → `sink_records_missing`. |
| D-59 | `tests/test_data_pull.py` | `plan_delegated_runs` unit cases (a gap of > `min_run` covered slices between two runs of ≥ `min_run` slices → two runs; a subset change whose union adds > 25 % → two runs; C34, pass 6); AC 2 variant above. |
| D-60 | `tests/test_data_reuse_acceptance.py` | AC 4 probe; with a fake unrelated `app` package on `sys.path` the probe passes; with `R` injected into `PYTHONPATH` and `inherit_env=True` it fails. |
| D-61 | `tests/test_dataset_find.py`, AC 1 variant | items carry `pull_args`; `DatasetPullInput(**pull_args, t0=…, t1=…)` validates; prompt contains "pull_args". |
| D-62 | `tests/test_ledger_*` | `ledger_record(observation={source: "sql", dataset_id: <partitioned>})` succeeds with ref kind `dataset_snapshot`, verifies `agrees`, `drifted` after another commit; with `slice=` → `dataset_slice`; tool description mentions partitioned attestation. |
| D-63 | `tests/test_analytics_interval_join.py` | must-not-fire: intervals table that is itself an `interval_join` output (struct field `ambiguous`) chained into a second join succeeds; top-level `ambiguous` still errors, empty table still errors. |
| D-64 | `tests/test_script_lint.py` | 512 KB prefilter-negative < 20 ms; 512 KB with hits in two functions < 250 ms; only the enclosing blocks are parsed (spy on `ast.parse` input length); cache hit on unchanged block. |
| D-65 | `tests/test_data_pull.py` | slice with `queried_at − t1 = threshold + 120 s` → not settled (margin 300); `ZIYA_SETTLE_SKEW_S=0` → settled; records store `skew_margin_s`; raising the env invalidates a previously covering record. |
| D-66 | `tests/test_ziya_data_sink.py` | `open_sink(source="clickhouse")` without `settle_s` → `settle_s_missing` warning from both sinks. |

**Pass-6 additions (D-65 reopened, D-67…D-72)**:

| Finding | File | Test |
|---|---|---|
| D-65 | `tests/test_data_pull.py`, `tests/test_ziya_data_sink.py` | `STUB_CLOCK_SKEW_S=480` (harness 8 min fast): slice with `queried_at − t1 = threshold + 420 s` → `skew_s ≥ 480`, `skew_source="caps"`, not settled; at `threshold + 500 s` settled; `measure_skew` unit cases (upper bound, never negative); `open_sink(source_now=…)` 480 s behind → same judgment in both sinks; no source clock → `skew_source="none"`, warning `skew_unmeasured`, floor 300 s applied. |
| D-67 | `tests/test_ziya_data_sink.py`, AC 6 | `STUB_TEMPLATE=ul` then `STUB_TEMPLATE=dl` under one `name` and key, in both sinks: B's `missing()` returns every pair, B queries all slices, A's rows and `slice_sha256` unchanged, B's open carries `name_reused`; `open_sink` without `identity` → `SinkError` code `identity_required`; `data_pull.dataset_identity is ziya_data_sink.dataset_identity`; golden identity vector shared with `tests/fixtures/data_pull_render.json`; bound handle with a different identity → `identity_mismatch`. |
| D-68 | `tests/test_job_runner*.py`, `tests/test_cache_hygiene.py` | hook `ZIYA_TEST_FAIL_AT=after_popen` (gated by `ZIYA_TESTING`) aborts `start` between Popen and the second `job.json` write: the dir has `job.json` with `wrapper_pid: null`, state reads `running` (from `status.json`) while the wrapper lives; `prune` with the clock 2 h later does not delete it; after the wrapper exits it reads `exited`/`lost`; a failed first `job.json` write leaves no process. |
| D-69 | `tests/test_script_lint.py` | the triple-quoted SQL-in-loop case fires `nocache_loop`; `string_state_scan` unit cases (prefixes, escaped quotes, `#` inside strings); a 300 KB file whose block fails to parse falls back to the regex with in-string lines skipped and still fires; budgets of D-64 still hold. |
| D-70 | `tests/test_ledger_*` | snapshot claim restricted to partition 10/29; re-pull of the young 11/02 tail with identical content → `agrees`; with changed 11/02 content and claim over all partitions → `drifted` with `changed_partitions=["2026-11-02"]`; a `volatile=true` observation whose content changed → `not_testable`; `content_sha` unchanged when only `__z_qat` changes. |
| D-71 | `tests/test_data_pull.py` | `STUB_ADAPTER=1`, harness-sliced pull: stub child env has `ZIYA_DATA_SINK=off`, no sink records or datasets other than the pull's, `/tmp/ziya-data-sink-$USER` untouched; `discover()` maps `off` to `NullSink`; route `open` with the job's token → `sink_not_expected`; delegated pull still writes through the sink. |
| D-72 | `tests/test_data_pull.py` | checkerboard coverage over 672 slices × 18 entities → `n_runs ≤ 32`, `extra_entity_slices` reported and ≤ the bound implied by C34, every to-pull (slice, entity) in some run; uniform gaps of > 4 covered slices between runs of ≥ 4 slices with equal subsets → runs kept separate (D-59 case); deterministic output. |

Each new test is run first against the unmodified tree where feasible and must fail (recorded per item in the
blackboard `lanes`).

### 10.2 Acceptance criteria as end-to-end tests (`tests/test_data_reuse_acceptance.py`, stub connector)

| AC | Test |
|---|---|
| 1 | Conversation A pulls 10/29 with `settle=10800` and `ZIYA_TEST_NOW` = 10/31 (all settled at query). Conversation B calls `dataset_find(table, window, entities=@roster)` → A's dataset, `score == 1.0`; then `dataset_pull` for the same window **without `dataset_id`** → `reused: true`, `job_id: null`, `to_pull: 0`; stub counter unchanged. Variant: A pulled at 10/30 00:30 (tail young) → B at 10/30 09:00 re-queries exactly the young slices (D-1), and a third pull queries none. Variant (pass 5, D-61): B calls `dataset_pull` with its **own** differently formatted SQL (other alias, a comment) → a new dataset (documented, asserted `reused: false`); B calls `dataset_pull(**item.pull_args, t0, t1)` → `reused: true`, `to_pull: 0`, stub counter unchanged. |
| 2 | Pull with `STUB_FAIL=at:40` crashing the job; re-run queries only uncommitted or not-settled-at-query slices (per-slice counter); late rows arrive; `count(*) == count(DISTINCT key)`. Variants (pass 3): unaligned `t0` with `STUB_CRASH_AFTER=1` (D-26) and `ZIYA_TEST_FAIL_AT=before_meta_rename` (D-27) — in both the re-run re-queries the uncommitted window and the final dataset equals a clean single run row-for-row. Variant (pass 5, D-59): delegated stub with `STUB_ALIGN=step` (ignores `--slices-file`) over 7 days where days 1–6 are covered and the last 3 h young → one connector invocation with `--t0/--t1` = those 3 h, stub per-slice counter = 12. |
| 3 | `nocache` stub query leaves `$ZIYA_CACHE_DIR/atlas-metrics` unchanged; `cache_status` lists `conv:<id>` with bytes/files. |
| 4 | `job_start("python3 tests/fixtures/env_probe.py")` exits 0 with no scrub in the script. The probe (pass 5, D-60) asserts that `R` — the server's `ZIYA_JOB_PKG_ROOT` (Ziya's package root, computed in the test process and passed to the probe as `argv[1]`, never read from the job's own env) — is **not on `sys.path`**, and that **if** `import app` succeeds its `app.__file__` is not under `R` (an unrelated installed `app` package — e.g. this host's `site-packages/app` from an installed ziya dist — is allowed; the bare "`import app` fails" clause is dropped because it neither holds on such hosts nor discriminates a leak); only when `ziya_private_prefix()` is not `None` (Ziya in a dedicated venv) does it also assert `sys.prefix` ≠ that prefix and no `VIRTUAL_ENV` under it. On a base-interpreter install (this host) the prefix inequality is not asserted, because the user's interpreter *is* Ziya's and nothing leaked (D-46). |
| 5 | `interval_join` once in `MACROS`; no `searchsorted` bin→leg join under `app/` or `tests/fixtures/`; two callers with different `priority_col` get different, rule-consistent `iv` on an ambiguous fixture. |
| 6 | Stub `--sliced` suite twice, including `STUB_OFFLINE` (D-30) and `STUB_ALIGN=step` (D-45), both runs opening the sink with the same `identity` (from `dataset_identity`, C33), `source="clickhouse", settle_s=10800` and the test registry declaring the same source identically (D-44): `ZIYA_DATA_SINK` unset (local sink) and set to the test server, the latter **outside** a `dataset_pull` job (shell-scoped token); rows equal row-for-row, `missing()` equal (offline entity covered in both); a third pair of runs opening with `source` but **without** `settle_s` asserts both sinks return warning `settle_s_missing` (D-66) and is excluded from the parity assertion; under Ziya the dataset is found by `dataset_find` and each slice has a ledger observation with the conversation provenance. |

---

## 11. Open decisions (people, not code)

1. Real settle horizons per source (data owners). Until then registry override or `settle=`; undeclared = never
   settled, which the tool now says explicitly (`settle_source`).
2. Skill authors' acceptance of v1 additions: `open_sink(identity=, source=, settle_s=, concurrency=)`,
   `append(on_duplicate=, slice=)` (producer `queried_at` optional, diagnostic), `record_slice`, `--sliced
   --slices-file`, exit code 3, version-list env value, **`settle_s` required with `source`** (D-66),
   **`ZIYA_DATA_SINK_REQUIRED`** honoured at open (D-58), `concurrency=1` recommended, **`identity` mandatory on
   open, computed with the vendored `dataset_identity`** (D-67), **`ZIYA_DATA_SINK=off` honoured** (D-71), and the
   S2 addition **`now` in `--capabilities` / `source_now` on open** (D-65). Partition-aligned slicing
   and `--slices-file` are **not** required of the connector (C26, C32). Settledness on the sink route uses the
   conservative `L` (C24), which only delays reuse, never marks partial data settled.
3. Shell-tool `PYTHONPATH`/venv leak (F6) — out of scope.
4. Append never deletes rows absent from a re-pull (F9); `refresh` and `max_age_s` re-query but do not retract. A
   retracting source needs a new dataset unless a data owner reports retractions. Backfills beyond the horizon are
   visible through `queried_at_min/max` and handled by `max_age_s` (D-37); whether any source backfills is a data-
   owner question.
5. `RETIRE_GRACE_S` and `flush_rows` defaults to be confirmed by the benchmark tests.
6. Residual skew risk (D-65): with a connector that reports no source clock, only the 300 s floor applies; a host
   whose clock runs > 300 s ahead (sleep/resume before NTP sync) can mark a slice settled up to `skew − 300 s`
   early. The result carries `skew_unmeasured`; the fix is the S2 `now` field, requested of the skill authors (2).
7. C34 coalescing constants (`max_extra=0.25`, `min_run=4`, `max_runs=32`) to be tuned against measured connector
   start cost.

---

## 12. Disposition of design reviews

### 12.1 Pass 1 (D-1…D-25)

| id | sev | disposition | where |
|---|---|---|---|
| D-1 | high | resolved: settledness per query (`settled_at_query`), slice ledger C17; young slice re-queried once after the horizon | §0.3 C1, §1.4.3, §2.2, §10 AC1 variant |
| D-2 | high | resolved: coverage = queried windows per (partition, entity); failed never covers; zero-row settled covers | C17, §2.2, §2.3 `missing`, §10 H2 |
| D-3 | high | resolved: `start_internal` with `sys.executable`, executor inherits, connector children scrubbed `force=True`; only connector command policy-checked | §1.1, §1.4.4/6, §6.1 |
| D-4 | high | resolved: `on_duplicate="error"` default, `n_dup_in` always | C20, §2.4.2 |
| D-5 | high | resolved: identity hash over template + non-entity params + key/cols; `params_mismatch`; params per pull | C18, §1.4.2, §2.2 `pulls` |
| D-6 | high | resolved: snapshot file list under `LOCK_SH`, no globbing, retired files with grace | C19, §2.4.5–6 |
| D-7 | medium | resolved: engine accepts file lists (CHANGE), `analyze.py:210` claim withdrawn, `input_files` | §2.3 |
| D-8 | medium | resolved: reuse by identity, `reused: true`; AC1 omits `dataset_id` | §1.4.2, §10.2 |
| D-9 | medium | resolved: joint (entity × time) score; `entities_unconstrained` + `n_entities` | §3 |
| D-10 | medium | resolved: `refresh` input; `settle_s_used >= current` | §1.3, §1.4.3 |
| D-11 | medium | resolved: Arrow-side ordinal for append; macros drop input-order ties, add `tiebreak_col` + `ambiguous`, output unordered | §2.4.1, §4.1–4.2 |
| D-12 | medium | resolved: `closed := 'left'\|'both'`, type and ms/s guards | §4.1 |
| D-13 | medium | resolved: version list in env, versioned paths, highest common version | §0.3 C4, §8.1 |
| D-14 | medium | resolved: health-at-open is the only fallback; errors after open raise; TTL = runtime + 1 h | §1.4.6–7, §8.1, §8.3 |
| D-15 | medium | resolved: executor observes through `ziya_ledger_client` with the scope token | §1.1, §1.4.7, C15 |
| D-16 | medium | resolved: per-pull and per-slice versions; analyze lineage `@name@vN` + sha | §5 |
| D-17 | medium | resolved: Ziya-prefix/pkg-root entries dropped from restored `PYTHONPATH`; ModuleNotFoundError hint | §6.1 |
| D-18 | medium | resolved: buffered per-partition commit (`flush_rows`); 96 slices → 1 rewrite | §1.4.7 |
| D-19 | low | resolved: region-only lint in patch mode, once per conversation, pragma, self-exclusion, bare `q(` | §7 |
| D-20 | low | resolved: naive times rejected (`naive_time`) | §0.1, §1.2 |
| D-21 | low | resolved: `find_spec` discovery dropped; env only; `PROVIDER` attribute | §0.3 C4, §8.1–8.2 |
| D-22 | low | resolved: `settle_source` in result and prompt; AC tests set `settle` explicitly | §1.4.8, §3, §10.2 |
| D-23 | low | resolved: NULL rules for `asof_join` (and `interval_join`) | §4.1–4.2 |
| D-24 | medium | resolved with correction: each `get_*_tools` has one list (+ `return []` fallback); descriptions updated; prompt gating per category | §0.1, §6.3, §10 H1/H6 |
| D-25 | low | resolved: LIMIT/projection pushdown for partitioned previews; `load_dataframe` refuses large | §2.3 |

### 12.2 Pass 2 (D-26…D-41)

| id | sev | disposition | where |
|---|---|---|---|
| D-26 | high | resolved: slices are cut at partition boundaries; a record names one partition and is committed only with that partition; `slice_outside_partition` in store and protocol (protocol part superseded in pass 4 by the C26 split, D-45); crash test | C17, C21, §1.2, §1.4.3, §1.4.7, §2.2, §2.4.5, §8.1, §10 H1/H2, AC 2 variant |
| D-27 | high | resolved: `meta.json` rename is the single commit point; slice records in `slices/<commit_seq>.jsonl` written before and ignored unless `seq <= meta.commit_seq`; orphans removed by next writer; fault-injection tests at three points | C22, §2.1, §2.2, §2.4.0/5, §10 H2, AC 2 variant |
| D-28 | medium | resolved: `queried_at` = harness clock before spawning the producing attempt (sidecar, per retry); settledness computed by the harness; sink route bounds producer time and ignores producer `settle_s_used`; `clock_source` recorded | C24, §1.4.7, §8.1, §10 H1/H8 |
| D-29 | medium | resolved: one `effective_threshold` + `covered` used by plan, missing, coverage, find; `settle_s_declared` only from registry/caps; explicit can raise, not lower; `dataset_missing(settle=)`; threshold reported | C23, §1.3, §1.4.1, §2.2, §2.3, §2.5, §10 H1 |
| D-30 | medium | resolved: protocol v1 `record_slice` required for every attempted slice; sinks buffer per (handle, partition) and commit on flush/close/flush_rows; AC 6 with `STUB_OFFLINE` | §0.3, §1.4.7, §8.1–8.2, §9, §10 H8, AC 6 |
| D-31 | medium | resolved: `score_settled` + `score_any`; keep iff `score_any > 0`; `unsettled` flag with hint | §3, §10 H3 |
| D-32 | medium | resolved: guard CTE referenced in the output `WHERE g.__z_ok IS NULL`; type families via `__z_tfam`; verified on DuckDB 1.5.6; error-message tests | §4.1–4.2, §10 H4 |
| D-33 | medium | resolved: observations attest an immutable slice (`dataset_slice` ref, `slice_sha256` over the committed slice rows); `verify.py`/`store.py` marked CHANGE; drift after re-pull is the expected verdict | C15, §2.4.7, §8.3, §10 H2/H8 |
| D-34 | medium | resolved: rows bounded to their slice on arrival (`boundary_overlap`, fail fast, or `boundary="clip"`); within-slice duplicate check on arrival; plan-time `template_not_half_open` warning; disjoint bounded slices make cross-flush duplicates impossible | C20, §1.3, §1.4.3, §1.4.7, §8.1, §10 H1 |
| D-35 | medium | resolved: no shell; whole-element placeholders; params via `--params-file`; job dir reserved first so the policy check sees the final argv, which the plan stores; injection test | C13, §1.2, §1.4.4, §1.4.7, §10 H1 |
| D-36 | low | resolved: `AS MATERIALIZED` on the row-numbered CTEs; `duckdb>=1.1` already pinned | §4.1–4.2, §10 H4 |
| D-37 | low | resolved: `queried_at_min/max` in coverage, describe and find; `dataset_pull(max_age_s=)`; retraction stays open decision 4 | §1.3, §1.4.3, §2.2, §3, §11 |
| D-38 | low | resolved: `ZIYA_TEST_NOW` only with `ZIYA_TESTING=1`, warning, `clock_source="test"` | C24, §10 |
| D-39 | low | resolved: observations made after the lock is released, idempotency key per (slice, commit_seq), replay from `observations.jsonl` | §1.4.7, §2.1, §8.3, §10 H1 |
| D-40 | low | resolved: `entity_set_drift` warning in analyze naming un-pulled ids and the `dataset_missing` fix | §5, §10 H5 |
| D-41 | low | resolved: hint from journal content (first 200 lines) regardless of runtime; `job_start` description and CHANGELOG behaviour-change entry | §6.1, §10 H6 |

### 12.3 Pass 3 (D-42…D-55)

All fourteen are resolved by spec changes; none is dispositioned won't-fix.

| id | sev | disposition | where |
|---|---|---|---|
| D-42 | medium | resolved: `queried_at` mandatory (pre-submit) on `append(slice=)`/`record_slice`; accepted only in `[L − 5 s, received_at]` with `L` = `opened_at` (or previous response time under `concurrency=1`); absent/out of range → `L` (`clock_source="contact"`); `received_at` never used. Errors can only delay reuse | C24, §8.1, §1.4.7, §9 S4, §11.2, §10 pass-4 D-42 |
| D-43 | medium | resolved: `declared_now`/`declared_eff` read registry + caps cache at use time; every reader and `plan_pull` use `effective_threshold(declared_eff, settle)`; coverage cache valid only for its stored `coverage_threshold`; `settle_s_declared` raised on commit | C23, C25, §1.2, §1.4.2, §2.2, §2.3, §3, §9 S2 |
| D-44 | medium | resolved: C28 threshold for unbound handles (registry/caps for `source`, raised by producer `settle_s`, or `settle_s` alone with `settle_source="producer"`); protocol `missing`/`coverage` take `settle=`; AC 6 runs outside a job with the same declared source | C28, §8.1, §8.2, §10.2 AC 6 |
| D-45 | medium | resolved (option A): the sink splits midnight-straddling slices into per-partition sub-slices, each committed only with its partition's rows; `slice_outside_partition` remains a store-internal guard; partition-aligned slicing not required of connectors | C21, C26, §1.4.7, §8.1, §9 S4, §11.2 |
| D-46 | medium | resolved: `ziya_private_prefix()` — prefix is Ziya-private only for a venv/toolbox/pipx/uv-tool install; otherwise only the package root is stripped and `PATH`/`VIRTUAL_ENV` untouched; AC 4 asserts no package root, prefix inequality only when private | §6.1, §10.2 AC 4, §10 pass-4 D-46 |
| D-47 | medium | resolved: `wrap.run` keeps `ZIYA_JOB_*` for internal jobs (`ZIYA_JOB_INTERNAL`), removes them for user jobs; `scrub_child_env(force=True)` uses then removes them, with a fallback to the calling process's prefix/package root; `PATH` lookup via child env | §6.1, §10 pass-4 D-47 |
| D-48 | medium | resolved: atomic identity index (`os.link` of a tmp file, O_EXCL semantics); the race loser reuses the winner; stale entries repaired under a lock | C27, §1.4.2, §2.3, §10 pass-4 D-48 |
| D-49 | medium | resolved: macros raise when points already have `iv/n_matches/ambiguous` (`st/n_ties/ambiguous`), via a `typeof(row-struct)` guard verified on DuckDB 1.5.6 incl. empty tables; documented chaining with renames; engine refuses duplicate output column names | §4.1–4.3, §2.3, §10 pass-4 D-49 |
| D-50 | low | resolved: `hive_types={'utc_day':'VARCHAR'}` explicit (verified: DATE without it, VARCHAR with it); `partition_column_conflict` | §2.1, §2.3 |
| D-51 | low | resolved: `slice_columns` per record, canonical per-type rendering (`%.17g` for floats), new columns ignored, missing column → `not_testable` | C29, §2.2, §2.4.7, §8.3 |
| D-52 | low | resolved: `input_files` returns `InputSnapshot(files, commit_seq, sha256)` from one `LOCK_SH`; `analyze.py:83` CHANGE records them in lineage | §2.3 |
| D-53 | low | resolved: idempotency key adds sorted entities and record ordinal; duplicate ok records for one window in one commit collapse to the last; only ok records observed | §1.4.7, §2.2 |
| D-54 | low | resolved: AST containment test (call inside a For/While body of the same function), indentation-checked regex fallback on SyntaxError; dedent near-miss test | §7 |
| D-55 | low | resolved: `tests/conftest.py` CHANGE sets `ZIYA_TESTING=1`; `JobManager.reserve`/`release` refactored out of `start` (CHANGE); `policy.check` called with its real keyword arguments | §1.1, §1.4.4, C24, §10 pass-4 D-55 |

### 12.4 Pass 4 (D-42 reopened, D-56…D-66)

All twelve are resolved by spec changes; none is dispositioned won't-fix.

| id | sev | disposition | where |
|---|---|---|---|
| D-42 | medium | resolved (reopened): on the sink route the record's `queried_at` (settledness and merge order) is always the lower contact bound `L`; the producer value is diagnostic only (`producer_queried_at`, `producer_clock_suspect`); `concurrency=1` recommended so `L` is the previous response time. A late-stamping producer can only delay reuse | C24, C30, §8.1, §8.2, §9 S4, §11.2, §10 pass-5 D-42 |
| D-56 | high | resolved: per-row `__z_qat` (slice `queried_at`), merge `ORDER BY __z_qat DESC, __z_src DESC` — newest-queried wins regardless of commit order; `n_stale_in`; invariant that a covered slice's served rows were queried after the horizon; hidden from readers | C20, C30, §1.4.2, §1.4.3, §2.1, §2.3, §2.4.1/3/4, §8.1, §8.2, §10 pass-5 D-56 |
| D-57 | high | resolved: sweep only dirs with the `reserved` marker AND no `job.json`, older than 1 h, marker pid dead (pid + create time recorded); `start` writes `job.json` before unlinking the marker | §1.1, §6.3, §10 pass-5 D-57 |
| D-58 | medium | resolved: `ZIYA_DATA_SINK_REQUIRED=1` for delegated children — failed health is `SinkError(permanent)`, no local fallback; executor post-run check `sink_records_missing` | C32, §1.4.7, §8.1, §8.2, §8.3, §10 pass-5 D-58 |
| D-59 | medium | resolved: one connector invocation per maximal contiguous to-pull run with identical entity subset, run bounds as `--t0/--t1`; `--slices-file` optimisation only | C32, §1.2, §1.4.7, §9 S4, §10.2 AC 2 variant |
| D-60 | medium | resolved: AC 4 asserts `ZIYA_JOB_PKG_ROOT` not on `sys.path` and, if `app` imports, `app.__file__` not under it; bare "import app fails" dropped | §10.2 AC 4, §10 pass-5 D-60 |
| D-61 | medium | resolved: `dataset_find` items carry `pull_args` (identity fields incl. full stored SQL, entity param as `@name`); prompt says to extend via `dataset_pull(**pull_args, t0, t1)`; AC 1 variant | §3, §10.2 AC 1 variant |
| D-62 | medium | resolved: `dataset_snapshot` ref kind for partitioned datasets without `slice` (verify by `meta.sha256`/`commit_seq`); `slice_required` withdrawn; `ledger_tools.py` marked CHANGE | C15, §8.3, §10 pass-5 D-62 |
| D-63 | low | resolved: guard matches top-level names only via `__z_top` (8× innermost-group strip), verified on DuckDB 1.5.6; must-not-fire test | §4.1, §10 pass-5 D-63 |
| D-64 | low | resolved: AST parses only enclosing top-level blocks of prefilter hits, LRU per (path, block sha); budget restated < 20 ms negative / < 250 ms worst case and asserted | §7, §10 pass-5 D-64 |
| D-65 | low | resolved: `SKEW_MARGIN_S` (default 300 s, env `ZIYA_SETTLE_SKEW_S`) added to `settled_at_query`/`covered`, stored per record; horizons compared against the harness clock (documented) | C23, C31, §1.2, §9 S2, §10 pass-5 D-65 |
| D-66 | low | resolved: protocol v1 — `settle_s` MUST accompany `source`; both sinks warn `settle_s_missing`; parity stated as guaranteed only with `settle_s` | §8.1, §8.2, §9 S2, §10.2 AC 6, §11.2 |

### 12.5 Pass 5 (D-65 reopened, D-67…D-72)

All seven are resolved by spec changes; none is dispositioned won't-fix.

| id | sev | disposition | where |
|---|---|---|---|
| D-65 | low | resolved (reopened): skew is **measured** (`measure_skew`, an upper bound of harness-ahead skew) from `--capabilities.now` (re-probed hourly and after > 600 s gaps) or `open_sink(source_now=)`; threshold uses `max(SKEW_MARGIN_S, skew_s)`; per-record `skew_s`/`skew_source`; unmeasured → floor + `skew_unmeasured`, residual risk in §11.6. The 8-min scenario is judged with `skew_s ≥ 480` and stays unsettled | C23, C31, §1.2, §1.4.7, §2.2, §8.1, §9 S2, §11.2/11.6, §10 pass-6 D-65 |
| D-67 | high | resolved: identity mandatory on open (`identity_required`), single vendored `dataset_identity` (C18 exactly, re-exported by `data_pull`), datasets keyed by identity in route and `LocalParquetSink` (`<local_root>/<identity>/`), same name + different identity → separate dataset + `name_reused`, bound handle `identity_mismatch`; two-template test in both sinks | C18, C33, §1.2, §8.1, §8.2, §9 S4, §10 pass-6 D-67, AC 6 |
| D-68 | low | resolved: `job.json` written before Popen (wrapper fields null), second write after; `Job.state` uses `status.json.wrapper_pid` when meta has none; sweep never touches a dir with `job.json`; kill-between-spawn-and-record test | §1.1, §10 pass-6 D-68 |
| D-69 | low | resolved: linear `string_state_scan` so in-string lines are never block boundaries; on block SyntaxError whole-file parse ≤ 256 KB, else regex with in-string lines skipped; must-fire test | §7, §10 pass-6 D-69 |
| D-70 | low | resolved: snapshot refs carry per-partition `content_sha` over row content excluding `__z_*` (C35), optional partition restriction; `drifted` lists `changed_partitions`; volatile changed → `not_testable` like `verify.py` | C15, C35, §2.2, §8.3, §10 pass-6 D-70 |
| D-71 | low | resolved: harness-sliced connector children get `ZIYA_DATA_SINK=off` → `NullSink` (no fallback); route refuses `open` from a harness-sliced job token (`sink_not_expected`); protocol forbids sinks under `--sql-file/--parquet` | C36, §1.4.7, §8.1–8.3, §9 S4, §10 pass-6 D-71 |
| D-72 | low | resolved: C34 coalescing (≤ 25 % extra entity-slices or short runs, cap 32 invocations by smallest-extra merges); `n_runs`, `extra_entity_slices` reported; checkerboard test | C32, C34, §1.2, §1.4.7, §11.7, §10 pass-6 D-72 |
