"""Tier 2 — golden comparison over the committed graphics corpus.

One test per (spec, theme), sharing the smoke tier's production dispatch.
Each render is compared with the machine-local golden under
.ziya/gfx-golden/ (see scripts/gfx_ledger.py, "goldens"):

  outcome     meaning                                            pytest
  ---------   ------------------------------------------------   --------
  error       did not render — Tier 1 territory, failed here too  FAIL
  baselined   no golden existed; captured one as PROVISIONAL      pass
  exact       pixel hash equals the golden                        pass
  noise       differs, but under tolerance (AA / hinting)         pass
  drift       differs beyond tolerance from a VALIDATED golden    pass*
  changed     differs beyond tolerance from a PROVISIONAL golden  pass*

  * recorded in .ziya/gfx-sweep/drift/<run>/ with the new render and a
    diff heatmap; FAIL with --render-golden-strict.

Why drift is not a failure by default: the judge that decides whether a
drift is a regression or an improvement is a model.  Wiring it into pytest
would make the suite need credentials, cost money, and be able to flip on
identical code.  The default run is offline and deterministic; the drift
report is the hand-off to the judge (a task card), and rebaselining is an
explicit ledger step so no chain of "equivalent" verdicts can ratchet a
baseline unnoticed.

A second run on the same commit is the determinism measurement for free:
the `exact` column per engine in the terminal summary is the hash-match
rate.  Engines that never match need `golden: false` in expectations (or
a seed plumbed through) rather than a wider tolerance.

Skipped unless ``--render`` is passed; see conftest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

import golden as G
from test_render_smoke import CHAT_TYPES, _render

pytestmark = [pytest.mark.render, pytest.mark.timeout(120)]

SWEEP_ROOT = Path(__file__).resolve().parents[2] / ".ziya" / "gfx-sweep"


def _renderer_label(renderer_fixture) -> str:
    _, r = renderer_fixture
    try:
        return f"chromium {r._browser.version}"  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return "unknown"


def test_corpus_spec_matches_golden(corpus_case, renderer, render_port, render_timeout_ms,
                                    chat_project_status, drift_report, request):
    engine, spec_id, spec, expectations, theme = corpus_case
    if expectations.get("golden") is False:
        pytest.skip(f"{spec_id}: marked non-deterministic (golden: false)")
    if str(spec.get("type", "")).strip().lower() in CHAT_TYPES and not chat_project_status[0]:
        pytest.skip(chat_project_status[1])

    tol = request.config.getoption("--render-golden-tolerance")
    tol = G.DEFAULT_TOLERANCE if tol is None else tol
    strict = request.config.getoption("--render-golden-strict")
    drift_report["tolerance"] = tol
    if drift_report["renderer"] is None:
        drift_report["renderer"] = _renderer_label(renderer)
    run = drift_report["run"]
    context = f"{engine}/{spec_id} theme={theme}"

    row: Dict[str, Any] = {"engine": engine, "spec_id": spec_id, "theme": theme}
    drift_report["outcomes"].append(row)

    try:
        png, _diag = _render(renderer, spec, theme, render_port, render_timeout_ms)
    except Exception as exc:  # noqa: BLE001
        row.update(outcome="error", detail=str(exc)[:500])
        pytest.fail(f"[error] {context}\n{exc}")

    current = G.ledger.golden_get(SWEEP_ROOT, engine, spec_id, theme)
    if current is None:
        rec = G.ledger.golden_capture(SWEEP_ROOT, engine, spec_id, theme, png,
                                      trust="provisional", run=run, source="runner")
        row.update(outcome="baselined", hash=rec["hash"], trust="provisional")
        return

    digest = G.pixel_hash(png)
    row["hash"] = digest
    row["golden_hash"] = current.get("hash")
    row["trust"] = current.get("trust")
    if digest == current.get("hash"):
        row["outcome"] = "exact"
        return

    golden_png = G.ledger.golden_paths(SWEEP_ROOT, engine, spec_id, theme)[0].read_bytes()
    cmp = G.compare(golden_png, png)
    row.update(fraction=round(cmp.fraction, 6), differing=cmp.differing,
               same_size=cmp.same_size, size=list(cmp.size_b), golden_size=list(cmp.size_a))
    if cmp.within(tol):
        row["outcome"] = "noise"
        return

    # Beyond tolerance: freeze the evidence the judge needs.
    outcome = "drift" if current.get("trust") == "validated" else "changed"
    row["outcome"] = outcome
    out_dir = Path(drift_report["dir"]) / engine
    out_dir.mkdir(parents=True, exist_ok=True)
    new_path = out_dir / f"{spec_id}.{theme}.png"
    new_path.write_bytes(png)
    row["new_png"] = str(new_path)
    if cmp.mask_png:
        heat = out_dir / f"{spec_id}.{theme}.heatmap.png"
        heat.write_bytes(G.heatmap(golden_png, cmp.mask_png))
        row["heatmap_png"] = str(heat)
    row["golden_png"] = str(G.ledger.golden_paths(SWEEP_ROOT, engine, spec_id, theme)[0])

    msg = (f"[{outcome}] {context}: {cmp.fraction:.3%} of pixels differ "
           f"(tolerance {tol:.2%}"
           + ("" if cmp.same_size else f"; size {cmp.size_a}->{cmp.size_b}")
           + f")\n  new:    {new_path}\n  golden: {row['golden_png']}")
    if strict:
        pytest.fail(msg)
