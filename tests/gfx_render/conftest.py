"""Render-in-the-loop regression suite for the graphics engines.

Off by default: every test here drives headless Chromium against a RUNNING
Ziya server, which takes minutes rather than milliseconds.  Enable with::

    python3 -m pytest tests/gfx_render --render                  # smoke: regression sets
    python3 -m pytest tests/gfx_render --render --render-scope=full
    python3 -m pytest tests/gfx_render --render --render-engine=mermaid
    python3 -m pytest tests/gfx_render --render --render-port=6969

Scope
  smoke  the Stage 1 regression sets (specs that always passed both themes;
         if one stops rendering, a fix broke something unrelated) — ~400
         renders.
  full   every spec in tests/gfx_corpus/, including the specs behind each
         verified defect — the "this used to be broken" set.

The corpus is written by ``scripts/gfx_ledger.py promote`` (automatically
when reconcile flips a defect to verified), so a defect fixed through the
GFX cards is covered here without anyone hand-authoring a test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

CORPUS = Path(__file__).resolve().parent.parent / "gfx_corpus"


def pytest_addoption(parser):
    g = parser.getgroup("gfx-render")
    g.addoption("--render", action="store_true", default=False,
                help="run the render-in-the-loop graphics suite (needs a running server)")
    g.addoption("--render-scope", default="smoke", choices=("smoke", "full"),
                help="smoke = Stage 1 regression sets; full = whole corpus")
    g.addoption("--render-engine", default=None,
                help="restrict to one engine directory of the corpus")
    g.addoption("--render-port", type=int,
                default=int(os.environ.get("ZIYA_PORT", "6969")),
                help="port of the running Ziya server whose /render page is used")
    g.addoption("--render-timeout-ms", type=int, default=30_000)
    g.addoption("--render-golden-tolerance", type=float, default=None,
                help="golden tier: share of differing pixels still counted as the same "
                     "render (default golden.DEFAULT_TOLERANCE = 0.5%%)")
    g.addoption("--render-golden-strict", action="store_true", default=False,
                help="golden tier: fail on DRIFT/CHANGED instead of only recording it")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "render: drives headless Chromium against a live server; "
                   "selected only with --render")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--render"):
        return
    skip = pytest.mark.skip(reason="render suite is opt-in: pass --render")
    for item in items:
        if "render" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolate_ziya_home():
    """Override the root conftest's sandbox — deliberately a no-op here.

    Every other test wants ZIYA_HOME redirected to a tmp dir.  This suite
    is the exception by construction: it drives the RUNNING server, and the
    chat-message renderer seeds its throwaway conversation into the same
    project store that server reads (``chat_screenshot.resolve_project_id``).
    Under the sandbox it finds no project and every chat-message case fails
    with "Could not resolve a Ziya project" before a single pixel renders.
    Nothing here writes to the real home except that one seeded conversation,
    which the renderer removes itself.
    """
    yield


# ── corpus enumeration ───────────────────────────────────────────────────

def load_corpus(scope: str = "full", engine: str | None = None
                ) -> List[Tuple[str, str, Dict[str, Any], Dict[str, Any]]]:
    """(engine, spec_id, spec, expectations) for every selected corpus entry."""
    out = []
    if not CORPUS.exists():
        return out
    for eng_dir in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        if engine and eng_dir.name != engine:
            continue
        exp_path = eng_dir / "expectations.json"
        if not exp_path.exists():
            continue
        exp = json.loads(exp_path.read_text())
        for sid, rec in sorted(exp.get("specs", {}).items()):
            if scope == "smoke" and not any(
                    o.get("origin") == "regression_set" for o in rec.get("origins", [])):
                continue
            spec_path = eng_dir / f"{sid}.json"
            if not spec_path.exists():
                continue
            out.append((eng_dir.name, sid, json.loads(spec_path.read_text()), rec))
    return out


def pytest_generate_tests(metafunc):
    if "corpus_case" not in metafunc.fixturenames:
        return
    cfg = metafunc.config
    cases = load_corpus(cfg.getoption("--render-scope"), cfg.getoption("--render-engine"))
    params, ids = [], []
    for engine, sid, spec, rec in cases:
        for theme in rec.get("themes", ["light", "dark"]):
            params.append((engine, sid, spec, rec, theme))
            ids.append(f"{sid}[{theme}]")
    metafunc.parametrize("corpus_case", params, ids=ids)


# ── renderer ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def render_port(request) -> int:
    return request.config.getoption("--render-port")


@pytest.fixture(scope="session")
def render_timeout_ms(request) -> int:
    return request.config.getoption("--render-timeout-ms")


@pytest.fixture(scope="session")
def server_alive(render_port) -> None:
    """Fail the whole session up front, with the reason, if there is no server."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://localhost:{render_port}/render", timeout=5) as r:
            if r.status != 200:
                pytest.exit(f"/render returned {r.status} on port {render_port}", returncode=3)
    except Exception as exc:  # noqa: BLE001
        pytest.exit(
            f"No Ziya server on port {render_port} ({exc}). The render suite "
            f"screenshots the live /render page; start the server (`ziya`) or "
            f"pass --render-port.", returncode=3,
        )


@pytest.fixture(scope="session")
def chat_project_status(server_alive) -> Tuple[bool, str]:
    """Can THIS process resolve the project the chat-message renderer seeds into?

    Probed once per session.  The plugin and LaTeX engines need only the
    server; chat-message additionally reads ~/.ziya project records, which
    are encrypted at rest.  A test process without the server's key material
    cannot read them, and that is a precondition of the environment, not a
    rendering defect — so chat-message cases are SKIPPED with the remedy
    rather than failed 2x49 times with a decryption traceback.
    """
    try:
        from app.utils.chat_screenshot import resolve_project_id
        resolve_project_id(None)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "encrypt" in msg.lower() or "key material" in msg.lower():
            return False, ("chat-message cases need the server's at-rest key: set "
                           "ZIYA_ENCRYPTION_KEY (or reach the KEK provider) for the "
                           "test process, then re-run")
        return False, f"chat-message renderer cannot resolve a project: {msg[:200]}"


@pytest.fixture(scope="session")
def drift_report(request) -> Iterator[Dict[str, Any]]:
    """Accumulates golden-tier outcomes; written once at session end.

    .ziya/gfx-sweep/drift/<run>.json is what the drift-judge card reads and
    what `gfx_ledger.py golden rebaseline --png` points at.  Kept out of
    pytest's pass/fail so an offline run stays deterministic: DRIFT is a
    finding to judge, not a verdict.
    """
    from datetime import datetime, timezone
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(__file__).resolve().parents[2] / ".ziya" / "gfx-sweep"
    report: Dict[str, Any] = {
        "run": run, "root": str(root), "dir": str(root / "drift" / run),
        "tolerance": None, "renderer": None,
        "outcomes": [],   # one row per (engine, spec, theme)
    }
    yield report
    if not report["outcomes"]:
        return
    summary: Dict[str, Dict[str, int]] = {}
    for o in report["outcomes"]:
        e = summary.setdefault(o["engine"], {})
        e[o["outcome"]] = e.get(o["outcome"], 0) + 1
    report["summary"] = summary
    dest = Path(report["dir"]) / "report.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=1) + "\n")
    request.config._gfx_drift_report = (dest, summary)  # type: ignore[attr-defined]


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    rep = getattr(config, "_gfx_drift_report", None)
    if not rep:
        return
    dest, summary = rep
    tr = terminalreporter
    tr.section("gfx golden tier")
    keys = ("exact", "noise", "baselined", "drift", "changed", "error")
    tr.write_line(f"{'engine':<16}" + "".join(f"{k:>10}" for k in keys))
    for engine in sorted(summary):
        row = summary[engine]
        tr.write_line(f"{engine:<16}" + "".join(f"{row.get(k, 0):>10}" for k in keys))
    tr.write_line(f"report: {dest}")
    if any(r.get("drift") or r.get("changed") for r in summary.values()):
        tr.write_line("DRIFT/CHANGED are recorded, not failed (pass --render-golden-strict "
                      "to fail). Judge them, then `gfx_ledger.py golden rebaseline`.")


@pytest.fixture(scope="session")
def renderer(server_alive, render_port) -> Iterator[Any]:
    """One warm Chromium for the whole session; ~1s startup, reused by every case.

    Built directly rather than via the module singleton so the test process
    owns and closes it, and so a stale singleton from another test cannot
    leak in.
    """
    import asyncio
    from app.services.diagram_renderer import DiagramRenderer

    loop = asyncio.new_event_loop()
    r = loop.run_until_complete(DiagramRenderer.create(render_port))
    try:
        yield (loop, r)
    finally:
        try:
            loop.run_until_complete(r.close())
        finally:
            loop.close()
