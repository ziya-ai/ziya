# Datasets by Reference (Analytics Data Plane, Phase 1)

## Problem

Today the model *is* the data pipeline for any analytic question. A CSV or
xlsx is extracted to text and dropped into context; the model reads it,
counts/sums in its head, and hand-types the numbers into a Vega-Lite or
Plotly spec. Failure modes: transcription errors, silently dropped rows,
wrong field types (`quantitative` vs `nominal` guessed), and files of a few
thousand rows consuming the whole context window.

## Design

A session-level **dataset store** that holds tabular data *outside* the
prompt, hands the model a small `describe` (schema, types, ranges, nulls,
head), and lets renderers pull the rows by reference.

```
CSV / xlsx / JSON / tool output
        │  dataset_register
        ▼
~/.ziya/datasets/<ds_id>/{meta.json, data.jsonl}
        │                         │
        │ describe (≈40 lines)    │ GET /api/datasets/<id>/rows
        ▼                         ▼
      model  ──── spec with  data:{url:"ziya://dataset/<id>"}  ──▶ vegaLitePlugin
                                                    (resolved to inline values
                                                     at preprocess time)
```

### Storage

* Location: `get_ziya_home()/datasets/<id>/` — user-level, not
  project-level. Frontend renderers fetch without an `X-Project-Root`
  header (see `latexPlugin`), so a project-scoped store would resolve to
  the wrong `.ziya/` in multi-project sessions. The id is globally unique
  so no scoping is needed for lookup; `meta.json` records `project_root`
  and `conversation_id` for listing/hygiene.
* Id: `ds_` + 16 hex chars (`secrets.token_hex(8)`). Unguessable; the
  serving endpoint validates against `^ds_[0-9a-f]{16}$` and reads only
  from the store directory — never an arbitrary path.
* Format: `data.jsonl` (pandas `orient=records, lines=True,
  date_format=iso`) plus a dtype map in `meta.json` used to restore
  datetime columns on load. No pickle (untrusted-deserialisation risk),
  no parquet (pyarrow is not a dependency).
* Directory mode `0o700`, matching `pdf_rag`.

### Ingest sources (`dataset_register`)

| source | handled by |
|---|---|
| `.csv` `.tsv` `.txt` | `pandas.read_csv` (sep sniffed for tsv) |
| `.xlsx` `.xls` | `pandas.read_excel(sheet_name=…)`; sheet list returned on ambiguity |
| `.json` `.jsonl` | `read_json` (records / lines) |
| `records` (list of dicts) | `DataFrame(records)` — for tool output, pcap summaries |
| `csv_text` | `read_csv(StringIO)` — for pasted / model-produced tables |

Paths go through the same `_resolve_and_validate` + readable-prefix
allowlist used by `file_read` and `pdf_*`.

Row cap on ingest: 500k rows (refuse above with a clear message). Rows
served per request: default 50k, max 100k, `truncated` flag in the
response so the frontend can warn.

### `describe` shape

```json
{
  "id": "ds_…", "name": "sales.xlsx:Q3", "row_count": 12840,
  "columns": [
    {"name": "region", "dtype": "object", "vega_type": "nominal",
     "nulls": 0, "distinct": 4, "sample": ["EU","NA","APAC","LATAM"]},
    {"name": "revenue", "dtype": "float64", "vega_type": "quantitative",
     "nulls": 12, "min": 0.0, "max": 98213.5},
    {"name": "day", "dtype": "datetime64[ns]", "vega_type": "temporal",
     "min": "2025-07-01", "max": "2025-09-30"}
  ],
  "head": [ …5 rows… ],
  "vega_data": {"url": "ziya://dataset/ds_…"}
}
```

`vega_type` inference: datetime → temporal; numeric → quantitative, except
integer columns with ≤ 12 distinct values that look like ranks/levels →
ordinal hint; bool/object → nominal. The model is told to trust this over
its own guess.

### Tools (builtin category `datasets`, enabled by default)

* `dataset_register(path | records | csv_text, name?, sheet?)` → describe
* `dataset_describe(id)` → describe
* `dataset_preview(id, limit≤200, offset, columns?)` → rows (bounded; the
  description discourages using it to page the whole file into context)
* `dataset_list()` → ids, names, row counts, source, conversation

Four tools, compact descriptions — the tool list is the first prefix
segment before every cache breakpoint, so this is paid on every turn.

### Frontend

`frontend/src/plugins/d3/datasetRefs.ts` (pure, fetch injectable):

* `DATASET_URL_RE = /^ziya:\/\/dataset\/(ds_[0-9a-f]{16})$/`
* `resolveDatasetRefs(spec, fetcher)` walks the spec at every nesting
  level (`layer`, `*concat`, `facet.spec`, `repeat.spec`), replaces a
  `data:{url:"ziya://dataset/…"}` object with `data:{values:[…]}`, dedupes
  fetches per id, and returns `{spec, resolved, failed}`.
* Hooked into `vegaLitePlugin` immediately after the spec is parsed and
  **before** `sanitizeSpec` / `stripExternalDataUrls`, so the existing SSRF
  posture is unchanged: the restricted loader still refuses every URL, and
  the stripper still removes anything that was not resolved. Only the
  `ziya://dataset/` scheme is ever fetched, only from `/api/datasets/`,
  which serves only store contents by validated id.
* A failed resolution leaves the url in place → stripped → chart renders
  empty with a console warning, exactly as an unknown URL does today.

### Out of scope for this phase (tracked)

* `compute` / SQL tools over dataset handles (phase 2).
* Plotly column references (`"x": "ziya://dataset/<id>/<col>"`).
* PDF table extraction into datasets (pdfplumber table → records).
* Server-side aggregation endpoint; for now the model uses Vega-Lite
  `transform` (`aggregate`, `bin`, `window`, …) against the full rows.
* `datatable` renderer over a handle.

## Security notes

* Serving endpoint is read-only, id-validated, store-only.
* Ingest path validation reuses `fileio._resolve_and_validate` with the
  shared readable prefixes — no new file-access surface.
* `records`/`csv_text` ingest is bounded (rows and bytes) to prevent a
  prompt-injected tool call from filling the disk.
* No deserialisation of code-bearing formats.
