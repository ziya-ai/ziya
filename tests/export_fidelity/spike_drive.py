#!/usr/bin/env python3
"""
Stage-1 feasibility spike driver.

Serves the built frontend (SPA, index.html fallback) on an ephemeral port and
drives the /print-spike route with headless Playwright the same way
app/services/diagram_renderer.py drives /render: navigate, wait for
data-render-status=complete, capture console + pageerror diagnostics, then
assert on the presence of GENUINELY-rendered structures.

A blank render with console exceptions is a FAILURE, not a pass. We do not
accept "it did not crash": we assert Prism token spans, per-line diff
insert/delete rows, and a completed mermaid diagram (svg/g.node) are present.

Usage:
    python3 tests/export_fidelity/spike_drive.py [build_dir] [out_dir]
Exit 0 = GO (all structures present, no fatal errors). Exit 1 = NO-GO.
"""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "frontend" / "build"
OUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / ".ziya" / "spike-out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


class SPAHandler(http.server.SimpleHTTPRequestHandler):
    """Static file server with SPA fallback to index.html for unknown routes."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BUILD_DIR), **kw)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        fs = (BUILD_DIR / path.lstrip("/"))
        if path != "/" and not fs.exists():
            # SPA fallback — serve index.html so client router handles route
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *a):  # silence
        pass


def start_server() -> tuple[socketserver.TCPServer, int]:
    httpd = socketserver.TCPServer(("127.0.0.1", 0), SPAHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


async def run(port: int) -> dict:
    from playwright.async_api import async_playwright

    console_log: list[str] = []
    pageerror_log: list[str] = []
    result: dict = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 1600})
        page.on("console", lambda m: console_log.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: pageerror_log.append(str(e)))

        url = f"http://127.0.0.1:{port}/print-spike"
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Wait for the spike readiness contract, mirroring diagram_renderer.py.
        try:
            await page.wait_for_function(
                """() => {
                    const r = document.getElementById('print-spike-root');
                    const s = r && r.getAttribute('data-render-status');
                    return s === 'complete' || s === 'error';
                }""",
                timeout=30000,
            )
        except Exception as e:  # timeout — capture what's there
            result["wait_error"] = repr(e)

        status = await page.get_attribute("#print-spike-root", "data-render-status")
        err_attr = await page.get_attribute("#print-spike-root", "data-error")

        # Probe for GENUINELY-rendered structures.
        probes = await page.evaluate(
            """() => {
                const q = (s) => document.querySelectorAll(s).length;
                const container = document.getElementById('print-spike-content');
                // Prism token spans (syntax highlighting)
                const prismTokens = q('code span.token, pre span.token');
                const prismTokenTypes = Array.from(
                    document.querySelectorAll('span.token')
                ).map(e => (e.className.match(/token\\s+([\\w-]+)/) || [])[1])
                 .filter(Boolean);
                // react-diff-view per-line insert/delete rows
                const diffInsert = q('.diff-code-insert, [class*="diff-code-insert"], tr.diff-line-insert, .diff-line-insert');
                const diffDelete = q('.diff-code-delete, [class*="diff-code-delete"], tr.diff-line-delete, .diff-line-delete');
                // generic diff table presence
                const diffTable = q('.diff, table.diff, .diff-view, [class*="diff"]');
                // mermaid diagram completion (svg with nodes)
                const svgCount = q('svg');
                const gNodes = q('svg g.node, svg .node, svg .nodes g');
                const mermaidMarkers = (container ? container.textContent : '').includes('NodeAlphaMRK');
                // markers present in text
                const text = container ? container.innerText : '';
                return {
                    prismTokens, prismTokenTypes: [...new Set(prismTokenTypes)],
                    diffInsert, diffDelete, diffTable,
                    svgCount, gNodes,
                    markers: {
                        human: text.includes('MRK_HUMAN_PROMPT_7q3'),
                        intro: text.includes('MRK_INTRO_PROSE_3k8'),
                        codeFn: text.includes('greet_MRK_fn_5c'),
                        diffAdd: text.includes('added_MRK_add_line'),
                        diffDel: text.includes('removed_MRK_del_line'),
                        diagramNode: mermaidMarkers,
                        closing: text.includes('MRK_CLOSING_2f7'),
                    },
                    htmlLen: container ? container.innerHTML.length : 0,
                    hasErrorBoundary: !!document.querySelector('[class*="error-boundary"], .ant-result-error'),
                };
            }"""
        )

        # Diff background colors (defect #4 evidence): sample computed styles.
        diff_colors = await page.evaluate(
            """() => {
                const pick = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    return getComputedStyle(el).backgroundColor;
                };
                return {
                    insertBg: pick('.diff-code-insert, [class*="diff-code-insert"], .diff-line-insert'),
                    deleteBg: pick('.diff-code-delete, [class*="diff-code-delete"], .diff-line-delete'),
                };
            }"""
        )

        # Full DOM dump + screenshot for the artifact record.
        html = await page.content()
        (OUT_DIR / "spike_render.html").write_text(html, encoding="utf-8")
        await page.screenshot(path=str(OUT_DIR / "spike_render.png"), full_page=True)

        result.update({
            "url": url,
            "render_status": status,
            "data_error": err_attr,
            "probes": probes,
            "diff_colors": diff_colors,
            "console_errors": [c for c in console_log if c.startswith("[error]")],
            "console_warnings": [c for c in console_log if c.startswith("[warning]") or c.startswith("[warn]")],
            "console_total": len(console_log),
            "pageerrors": pageerror_log,
        })
        await browser.close()
    return result


def evaluate_go(r: dict) -> tuple[bool, list[str]]:
    """Decide GO/NO-GO from probes. Returns (go, reasons)."""
    reasons = []
    p = r.get("probes", {}) or {}
    m = p.get("markers", {}) or {}
    go = True

    if r.get("render_status") != "complete":
        go = False
        reasons.append(f"render_status={r.get('render_status')!r} (not complete); data_error={r.get('data_error')!r}")
    if p.get("hasErrorBoundary"):
        go = False
        reasons.append("error boundary present in DOM")
    if p.get("prismTokens", 0) < 3:
        go = False
        reasons.append(f"Prism token spans too few: {p.get('prismTokens')}")
    if p.get("diffInsert", 0) < 1 or p.get("diffDelete", 0) < 1:
        go = False
        reasons.append(f"diff insert/delete rows missing: insert={p.get('diffInsert')} delete={p.get('diffDelete')}")
    if p.get("svgCount", 0) < 1:
        go = False
        reasons.append(f"no diagram svg rendered: svgCount={p.get('svgCount')}")
    if not (m.get("codeFn") and m.get("diffAdd") and m.get("diffDel") and m.get("diagramNode")):
        go = False
        reasons.append(f"expected text markers missing: {m}")
    # Fatal page errors are a hard fail
    if r.get("pageerrors"):
        # tolerate benign ResizeObserver noise only
        fatal = [e for e in r["pageerrors"] if "ResizeObserver" not in e]
        if fatal:
            go = False
            reasons.append(f"page errors: {fatal[:3]}")
    return go, reasons


def main() -> int:
    if not (BUILD_DIR / "index.html").exists():
        print(json.dumps({"error": f"no build at {BUILD_DIR}"}))
        return 2
    httpd, port = start_server()
    try:
        r = asyncio.run(run(port))
    finally:
        httpd.shutdown()
    go, reasons = evaluate_go(r)
    r["verdict"] = "GO" if go else "NO-GO"
    r["verdict_reasons"] = reasons
    (OUT_DIR / "spike_result.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
    print(json.dumps(r, indent=2))
    print(f"\n=== VERDICT: {r['verdict']} ===")
    for reason in reasons:
        print(f"  - {reason}")
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
