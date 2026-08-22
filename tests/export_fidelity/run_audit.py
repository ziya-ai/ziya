#!/usr/bin/env python3
"""
Export-fidelity audit runner.

Renders every fixture variant, runs every check, writes the PDF and per-page
PNGs under ``.ziya/export-audit/pdf/``, prints a machine-readable JSON report
to stdout, and exits non-zero if ANY check fails.

Two rendering modes:

  * LIVE (default) — drive the REAL Stage-2 pipeline via
    ``render_harness.render_pdf`` against a running Ziya server whose built
    bundle includes the ``/print`` route.  This is the canonical path and the
    one CI should use once the Card-I diffs land + ``npm run build`` runs.

        python -m tests.export_fidelity.run_audit --server-port 6969

  * PDF-DIR — audit already-produced PDFs, one per variant, named
    ``<variant>.pdf`` (e.g. ``light.pdf``, ``dark.pdf``).  Lets the audit run
    with NO live server (useful in constrained environments and for auditing a
    captured artefact), and is backend-agnostic — any tool that emits a PDF for
    a variant can be graded.

        python -m tests.export_fidelity.run_audit --pdf-dir /path/to/pdfs

Exit code: 0 all checks pass; 1 any check fails; 2 a variant failed to render.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests.export_fidelity import fixture
from tests.export_fidelity import checks as checks_mod
from tests.export_fidelity import render_harness


DEFAULT_OUT_DIR = Path(".ziya/export-audit/pdf")


def _write_page_pngs(doc: render_harness.RenderedDocument, out_dir: Path, variant: str) -> List[str]:
    from PIL import Image
    paths = []
    for p in doc.pages:
        img = Image.fromarray(p.rgb)
        fp = out_dir / f"{variant}_page{p.index + 1}.png"
        img.save(fp)
        paths.append(str(fp))
    return paths


def _render_variant(
    variant: str,
    messages: List[Dict[str, Any]],
    *,
    mode: str,
    server_port: int,
    pdf_dir: Optional[Path],
    dpi: float,
) -> render_harness.RenderedDocument:
    if mode == "pdf-dir":
        pdf_path = pdf_dir / f"{variant}.pdf"
        if not pdf_path.exists():
            raise FileNotFoundError(f"expected pre-rendered PDF at {pdf_path}")
        return render_harness.load_pdf(pdf_path, dpi=dpi, meta={"variant": variant})
    # live
    return render_harness.render_pdf(
        messages, server_port=server_port, dpi=dpi,
        title=f"Ziya Fidelity Fixture ({variant})",
    )


def run_audit(
    *,
    mode: str = "live",
    server_port: int = 6969,
    pdf_dir: Optional[Path] = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    dpi: float = render_harness.DEFAULT_DPI,
    variants: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    variants = variants or fixture.all_variants()

    report: Dict[str, Any] = {
        "mode": mode,
        "dpi": dpi,
        "out_dir": str(out_dir),
        "variants": {},
        "summary": {"variants": 0, "checks_run": 0, "checks_failed": 0,
                    "render_errors": 0, "passed": True},
    }

    for variant, messages in variants.items():
        v_entry: Dict[str, Any] = {"checks": {}, "render_error": None}
        try:
            doc = _render_variant(
                variant, messages, mode=mode, server_port=server_port,
                pdf_dir=pdf_dir, dpi=dpi,
            )
        except Exception as exc:  # render failure is a hard, reported failure
            v_entry["render_error"] = f"{type(exc).__name__}: {exc}"
            report["summary"]["render_errors"] += 1
            report["summary"]["passed"] = False
            report["variants"][variant] = v_entry
            report["summary"]["variants"] += 1
            continue

        # persist artefacts
        if doc.raw_bytes:
            (out_dir / f"{variant}.pdf").write_bytes(doc.raw_bytes)
        try:
            v_entry["page_pngs"] = _write_page_pngs(doc, out_dir, variant)
        except Exception as exc:
            v_entry["page_pngs_error"] = f"{type(exc).__name__}: {exc}"

        v_entry["page_count"] = doc.page_count
        v_entry["pdf_size"] = len(doc.raw_bytes) if doc.raw_bytes else None

        for result in checks_mod.run_all_checks(doc):
            v_entry["checks"][result.name] = result.to_dict()
            report["summary"]["checks_run"] += 1
            if not result.passed:
                report["summary"]["checks_failed"] += 1
                report["summary"]["passed"] = False

        report["variants"][variant] = v_entry
        report["summary"]["variants"] += 1

    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Export-fidelity audit runner")
    ap.add_argument("--server-port", type=int, default=6969,
                    help="Ziya server port for LIVE mode (default 6969)")
    ap.add_argument("--pdf-dir", type=str, default=None,
                    help="Audit pre-rendered <variant>.pdf files in this dir "
                         "instead of driving a live server")
    ap.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR),
                    help=f"Where to write PDFs+PNGs (default {DEFAULT_OUT_DIR})")
    ap.add_argument("--dpi", type=float, default=render_harness.DEFAULT_DPI)
    ap.add_argument("--indent", type=int, default=2)
    ap.add_argument("--report-file", type=str, default=None,
                    help="Also write the JSON report to this path")
    args = ap.parse_args(argv)

    mode = "pdf-dir" if args.pdf_dir else "live"
    report = run_audit(
        mode=mode,
        server_port=args.server_port,
        pdf_dir=Path(args.pdf_dir) if args.pdf_dir else None,
        out_dir=Path(args.out_dir),
        dpi=args.dpi,
    )
    report_json = json.dumps(report, indent=args.indent, default=str)
    print(report_json)
    if args.report_file:
        Path(args.report_file).write_text(report_json)

    if report["summary"]["render_errors"]:
        return 2
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
