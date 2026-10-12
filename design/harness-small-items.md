# Research Harness: Small Items

Status: **proposed** · Owner: maintainers · Related: `design/research-harness.md`
(integration), `design/evidence-ledger.md`, `design/data-source-connectors.md`,
`design/typed-findings.md`, `design/time-budgeted-cards.md`.

Four follow-ups from the reference investigation run that do not need a spec of
their own. Each is independently buildable.

| # | Item | Size | Depends on |
|---|---|---|---|
| 1 | `show_document`: show a file to the user without the model retyping it | small | none |
| 2 | Column dictionaries for evidence files | small | ledger (claim checks only) |
| 3 | Log-scale bug in chart preprocessing, and visible chart rewrites | small, bug fix first | none |
| 4 | Human-action requests (Ask with `ask_kind: "action"`) | medium | none; budgets interplay with item 7 |

---

## 1. `show_document`

### Problem

Asked to "render the report inline", the only way to put a file in front of
the user was for the model to retype it as response text. The parroting
detector (`app/hallucination/shingle_index.py`, wired in
`app/text_delta_processor.py`) saw assistant text reproducing a prior
`file_read` result and interrupted twice with *"Model appears to be
reproducing prior `file_read` output rather than calling the tool —
retrying…"*. The detector was right: that pattern is what fabricated tool
output looks like. What is missing is a way to display a file in which the
model never produces its text. Retyping also costs output tokens (about 10k
for a 40 KB report) and can drift from the file.

### Design

A builtin tool:

```
show_document(path, heading?, start_line?, end_line?, render: "auto" | "markdown" | "code")
```

- **Server reads the file** with the same path validation and readable
  prefixes as `file_read`. Cap 512 KB per call; larger files must be shown by
  `heading` or line range.
- **The user sees the content.** The tool emits a `document_display` stream
  event attached to the current assistant message. The frontend renders it
  through the normal markdown renderer (diagrams, tables and math included)
  as an expanded block labelled with the path, line range, size and SHA-256.
- **The model does not.** The tool result returned to the model contains only
  `{path, lines, bytes, sha256, headings[]}`. With no content in its context,
  the model has nothing to parrot and cannot alter the text it shows.
- **Persisted** with the message, so reloading the conversation and exporting
  it (Markdown, PDF) include the displayed content.
- **`heading`** selects one section (that heading through the next heading of
  the same or higher level), so the model can show one part of a long report.
- **Not for PDFs or images.** A PDF path returns a link and page count;
  images use the existing image rendering.

The tool description tells the model to use it whenever the user asks to see
or render a file, and not to retype file content.

The parroting detector is unchanged. Retyping a file after `file_read` still
triggers it, which is the behaviour we want.

### Security

- Same read rules as `file_read`; no new file access.
- Displayed content goes through the existing markdown sanitisation and
  diagram loaders.
- The `document_display` event can only be produced by the tool, never by
  model text.

### Implementation

| Step | Change | Files |
|---|---|---|
| 1 | Tool | new `app/mcp/tools/show_document.py`; `app/mcp/builtin_tools.py` |
| 2 | Stream event and persistence on the message | `app/streaming_tool_executor.py`, chat storage |
| 3 | Frontend message part that renders the document block | frontend message renderer (location to confirm when building) |
| 4 | Included in chat export | chat export path |

### Tests

- **Seam.** A turn where the model calls `show_document` on a 300-line
  markdown file: the persisted assistant message contains the full file
  text in its `document_display` part, and the tool result sent back to the
  model does **not** contain any line of the file (assert on the tool-result
  string).
- **Detector unaffected.** The same file read with `file_read` and then
  retyped still triggers the parroting detector (positive control).
- **Section.** `heading: "3. Targets"` displays exactly that section.
- **Cap.** A 600 KB file without `heading` or a range is refused with the
  heading list.

---

## 2. Column dictionaries for evidence files

### Problem

#5's per-incident statistic was first computed from `dur` in
`evidence/rca2/dead_episodes_h17.csv`, which is the whole path's length, not
the dead episode's (`L`). The CSV carried no description of its columns; the
meaning lived only in `rca2/persec.py`. The typed-findings spec requires a
column dictionary for per-entity tables attached to a finding, but most
evidence files are intermediate tables that never become findings.

### Design

**Sidecar file.** `<file>.columns.json` next to an evidence table:

```json
{"columns": [
   {"name": "dur", "type": "int", "unit": "s", "description": "length of the whole route"},
   {"name": "L", "type": "int", "unit": "s", "description": "length of this dead episode"}],
 "rows": 732,
 "produced_by": {"obs_id": "obs_…"}}
```

**Written automatically where the harness writes the file:**

- `ziya data query --out` and `data_query_sliced(out=…)`: column names and
  types from the connector; descriptions from the connector's `describe`
  and matching pitfall notes; computed columns without a description are
  listed as `description: null`.
- `dataset_register` gains an optional `columns` argument, and
  `dataset_describe` returns the descriptions.

**From scripts.** The stdlib helper (`ziya_data`) gains
`write_table(rows | dataframe, path, columns={name: description, …})`. It
refuses to write when any column lacks a description, and writes the CSV and
the sidecar together through the server.

**Used by the ledger.** When a claim's `derivation.column` or `exec` names a
column of a file input, the ledger copies that column's description into the
claim. A `dur`-based claim then reads "derived from `dur` (length of the
whole route)", so the slip shows up wherever the claim is cited. A file
input without a sidecar marks the claim `columns_undocumented`, and the
Evidence appendix lists it.

**Lint.** `ziya evidence lint <dir>` lists tables without a sidecar or with
empty descriptions. The Report block runs it on every file referenced by a
cited claim and records the result in its decisions; it does not block
export.

Arbitrary script writes (`df.to_csv`) cannot be intercepted, so this is not
enforced for them. The helper and the lint are the incentives.

### Tests

- `write_table` with an undescribed column raises and writes nothing.
- `ziya data query --out x.csv` writes `x.csv.columns.json` with connector
  types.
- A claim deriving from `dur` carries the description "length of the whole
  route"; a claim on a file with no sidecar is `columns_undocumented`.
- Lint flags a CSV without a sidecar and passes one with a complete sidecar.

---

## 3. Chart preprocessing: log-scale bug, then visible rewrites

### Finding

Reproduced on 2026-10-02 with `render_diagram`:

| Spec | Result |
|---|---|
| Single rule mark, `x: lo` (values 5 and 20) with `scale.type: "log"`, `x2: hi` (up to 25,000) | **linear axis** |
| Two layers at top level, log declared on the rule layer only | **linear axis** |
| Two layers inside a `vconcat` child, log declared on both layers | log axis |

**Cause.** Fix 3.7 in `frontend/src/plugins/d3/vegaLitePlugin.ts`
(`fixLogScales`, around line 2318) deletes an authored `scale.type: "log"`
when the ratio of max to min of the channel's `field` is below 100. It reads
only `channelSpec.field`. It ignores `x2`/`y2` and every other layer that
shares the scale, so a rule from 5 to 25,000 is judged by its lower ends
alone (range 4×). It visits the top-level encoding, `hconcat`/`vconcat`
children and top-level `layer` children, but not layers nested inside a
concat child, which is why the third spec kept its log scale. The overnight
report's "native log scale did not apply across layers" was this pass, not
Vega-Lite.

**A related heuristic.** Fix 3.8 (around line 1502) rewrites the field and
domain of an axis fed by a `calculate` containing `log(` with a small
numeric domain, converting it back to a log scale on the original field.
The pre-logged workaround used in the scan has exactly that shape. It did not
fire there only because the `x` encoding was inside layers. If it does fire,
it silently undoes an author's deliberate choice.

### Fix (bug)

1. **Never remove an authored scale type because of the data range.** An
   author who wrote `log` asked for it. Delete the "range < 100" branch.
2. **Keep the log → symlog conversion** when any value on the scale is ≤ 0,
   because log is undefined there. Compute this over every field bound to
   the scale: `field`, `x2`/`y2` in the same view, and the same channel in
   sibling layers (Vega-Lite shares positional scales across layers by
   default).
3. **Visit layers inside concat, facet and repeat children** consistently.
4. **Fix 3.8:** only rewrite when the axis has no explicit `axis.labelExpr`
   (a labelled pre-logged axis is deliberate).

Tests (frontend, jest), each paired with the case that must still change:

- the repro rule spec keeps `scale.type: "log"`;
- the same spec with a 0 in `hi` becomes `symlog`;
- a two-layer spec with log on one layer and a ≤ 0 value only in the other
  layer becomes `symlog`;
- a pre-logged spec with `labelExpr` is not rewritten by Fix 3.8; one
  without `labelExpr` still is.

### Visible rewrites (the "chart lint" item, reframed)

The preprocessing passes already describe each rewrite in a `console.log`
(e.g. `🔧 VEGA-PREPROCESS: Removed x log scale (range too small: 4.0x)`). The
model never sees those messages, so after a render it can only guess why the
chart differs from its spec.

- The preprocessing entry point collects each pass's message into a
  `rewrites[]` list alongside the rewritten spec.
- `render_diagram` includes `rewrites[]` in its text result, so the model
  reads "Converted x log scale to symlog (has zero values)" instead of
  re-rendering blind.
- The chart's existing debug view lists them for the user.

This changes no rendering behaviour. Rewrites stay; they stop being
invisible.

---

## 4. Human-action requests

### Problem

Device journals can only be downloaded through the file-transfer console,
which has no agent-accessible API. The phase-2 brief could only say "write
the exact downlink request to your output for a human". Nothing then waited
for the files: the lane carried on without them or failed, and the human had
to notice the request in a report.

### Design

The existing Ask block (`_execute_ask`, backed by `ConsentLedger`) gains a
second kind:

```json
{
  "block_type": "ask",
  "name": "Pull device journals",
  "ask_kind": "action",
  "ask_question": "{{previous.outputs.action_request.text}}",
  "ask_expect": {"files": [".ziya/audit/journals/dev014_*.journal"], "min_bytes": 1024},
  "ask_timeout_s": 28800,
  "ask_on_timeout": "continue",
  "ask_variable": "journal_paths"
}
```

| Field | Meaning |
|---|---|
| `ask_kind` | `question` (today's behaviour, default) or `action`. |
| `ask_question` | The instructions. Usually produced by a preceding Task as an `action_request` part. |
| `ask_expect` | Completion condition: `files` (globs, each must match at least one file, optional `min_bytes`), or `manual: true` (the human clicks Done). |
| `ask_timeout_s` / `ask_on_timeout` | How long to wait; then `fail` (failed artifact) or `continue` (empty answer, recorded as timed out). |
| `ask_variable` | Receives the matched paths as a JSON list. |

**Mechanics.**

- The block opens a ledger request of kind `action` with the instructions
  and the expected files, exactly as a question is opened today.
- A watcher in the server checks the globs every 10 s. When they are
  satisfied, it settles the request with the matched paths, recorded as
  answered by "file watcher". `await_answer` is unchanged.
- The human can also answer from the Ask UI: **Done** (with an optional
  note) or **Cannot do** (a rejection, which produces a failed artifact, as
  a rejected question does today).

**Only its own branch waits.** An Ask holds the sequence it sits in. Inside a
Parallel, the other children are separate coroutines and keep running. Two
code changes follow from that:

- **Run status.** Today an open Ask moves the run to awaiting input. With
  other branches active, the run should stay `running` with
  `pending_actions: n`, and show awaiting input only when nothing else is
  running.
- **More than one open request.** The run record has a single
  `pending_ask`. Two Asks open in parallel branches need a list. To confirm
  in `TaskRunConsentStore` when building.

**Budgets (item 7).** Waiting counts toward the deadline. In finalize mode,
an Ask whose block is not a finalizer is closed with reason `budget`, and the
report's Not completed section names the action and its instructions, so the
human can still do it and extend the run.

### Security

- `ask_expect.files` globs are restricted to project paths that are both
  readable and writable under the effective policy. The model cannot point
  the watcher elsewhere.
- The watcher only checks existence and size; it does not read file
  contents.
- Instructions may be model-generated and are shown to the human as
  untrusted text.

### Tests

- **Branch-local seam.** A Parallel with child A an action Ask expecting
  `x/*.journal` and child B a Task. B finishes while A waits; creating
  `x/a.journal` settles A within one watcher interval, and `ask_variable`
  holds `["x/a.journal"]`. The run status was `running` while B ran.
- **Timeout.** With `ask_on_timeout: "continue"` the block passes with an
  empty answer recorded as timed out; with `fail` it fails.
- **Manual.** `manual: true` settles on Done; Cannot do produces a failed
  artifact.
- **Glob scope.** A glob outside the project is refused at card validation.
- **Budget.** In finalize mode an open action Ask is closed with reason
  `budget` and appears in the budget summary.
