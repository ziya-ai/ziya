# Graphics render regression corpus

Specs that must keep rendering, one directory per engine, with
`expectations.json` naming the invariants and where each spec came from.

**Do not hand-edit.** This directory is written by
`scripts/gfx_ledger.py promote`, automatically whenever `reconcile` flips a
defect to `verified` in the GFX sweep. A fix that lands through the GFX task
cards is covered here without anyone writing a test. Spec bytes are frozen on
first promotion; a later sweep cannot silently rewrite what a spec tests.

Provenance per spec (`origins[]`):
- `regression_set` — passed both themes in the Stage 1 sweep; never broken.
  If one of these stops rendering, a fix broke something unrelated.
- `D-NNN` + signature — the spec behind a verified defect; this is what
  "used to be broken" looked like.

Run:

```
python3 -m pytest tests/gfx_render --render                    # smoke: regression sets, ~13 min
python3 -m pytest tests/gfx_render --render --render-scope=full
python3 -m pytest tests/gfx_render --render --render-engine=mermaid
```

Needs a running Ziya server (`--render-port`, default `$ZIYA_PORT` or 6969).
chat-message cases additionally need the server's at-rest key material
(`ZIYA_ENCRYPTION_KEY`) in the test process; without it they skip with the
reason, they do not fail.

Invariants the smoke tier checks: `renders`, `no_console_errors`, `ink_present`.
`visual:*` tags are recorded for a future measured tier and ignored here.

## Golden tier (`test_render_golden.py`)

The smoke tier asks "does it still render?"; the golden tier asks "does it
still render *the same*?". Each (spec, theme) is compared with a golden PNG
under `.ziya/gfx-golden/<engine>/<spec>.<theme>.png` — **machine-local and
gitignored**, because the pixels depend on the Chromium build. What IS
committed here is `golden.<theme>.hash` in `expectations.json`: the pixel
hash of a render a judge approved, so another machine can recognise it.

Comparison is cheapest-first: pixel hash (exact) → per-pixel diff at capture
resolution with a 0.5 % tolerance (`noise`) → `drift`. Outcomes per row:

| outcome     | meaning                                                   | pytest |
|-------------|-----------------------------------------------------------|--------|
| `exact`     | hash equals the golden                                    | pass   |
| `noise`     | differs under tolerance (anti-aliasing, hinting)          | pass   |
| `baselined` | no golden existed; captured one as **provisional**        | pass   |
| `drift`     | beyond tolerance vs a **validated** golden                | pass\* |
| `changed`   | beyond tolerance vs a **provisional** golden              | pass\* |
| `error`     | did not render                                            | FAIL   |

\* recorded to `.ziya/gfx-sweep/drift/<run>/` (new render + `.heatmap.png`)
and printed in the terminal summary; `--render-golden-strict` makes them fail.
Drift is a finding for a judge, not a verdict: the "GFX drift judge" task
card reads the report, looks at golden / new / heatmap with `view_image`, and
records regressions through the ledger so Stage 2 queues them.

### Trust, and why a fresh clone shows everything as `baselined`

A golden carries a trust level in its sidecar, and only `gfx_ledger.py`
changes it:

- **provisional** — the runner captured it because none existed. It asserts
  nothing about correctness, only that later renders can be compared.
- **validated** — a judge recorded `ok` on *these bytes*
  (`record --light-png/--dark-png`, which the GFX Stage 1/2 cards now do),
  or the pixels hash-match a validated hash committed here, or a human ran
  `golden rebaseline --reason`.

On a new machine there are no goldens, so the first `--render` run
baselines everything as provisional. Then either:

```
python3 scripts/gfx_ledger.py golden trust-from-hash   # accept renders identical to committed hashes
python3 scripts/gfx_ledger.py golden status            # what is validated / provisional / missing
```

or run a GFX sweep (Stage 1, or Stage 2's verify step after a fix), which
validates every judged-ok render. Hashes that do not match on your machine
are listed by `trust-from-hash` as `mismatch` — a different Chromium build,
usually — and stay provisional until judged.

### Rebaselining is a human act

A validated golden moves only by a fresh judged `record`, or by

```
python3 scripts/gfx_ledger.py golden rebaseline --engine mermaid --spec mermaid-w1-01 \
    --theme dark --png .ziya/gfx-sweep/drift/<run>/mermaid/mermaid-w1-01.dark.png \
    --reason "labels re-hinted after font upgrade; layout unchanged"
```

The previous golden is archived under `.ziya/gfx-golden/history/`. The judge
card prints these commands; it never runs them — a chain of "equivalent"
verdicts must not be able to ratchet a baseline unnoticed.

### Non-deterministic engines

An engine whose layout differs run to run without a code change (unseeded
force layouts) shows as `changed` every run. Mark the spec `"golden": false`
in `expectations.json` to keep it smoke-tier only, rather than widening the
tolerance for everyone.
