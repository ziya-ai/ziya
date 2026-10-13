"""
Repo-wide guard: no HTTP handler under app/api or app/routes may be a
zero-await ``async def``.

THE DEFECT CLASS.  A FastAPI handler declared ``async def`` that contains no
``await`` runs its entire body ON the asyncio event loop.  Nothing in it ever
yields, so for the handler's whole duration no other request in the process
is served.  In isolation that costs milliseconds.  Under startup load — two
background folder scans and two AST indexers, all CPU-bound Python holding
the GIL in bursts — even a tiny disk read inside such a coroutine parks the
loop for seconds, because the loop thread cannot get the GIL back to finish
the read.  The size of the I/O does not matter: a 252 KB skill-store read
stalled the loop as badly as a 483 MB run-record read.

The log shape is "nothing, then everything at once": a stretch with no
requests completing, followed by a burst of them all finishing within a few
hundred milliseconds as soon as the parked coroutine returns.  On
2026-10-09 the stall watchdog's dumps attributed six such stalls to
specific handlers (chats, task_runs, bench, task_cards, folder_routes,
ast_routes) plus the embedding backfill, and a sweep then found ~147 more
handlers of the same shape across ~28 files.  They were converted together.

THE REMEDY.  Make the handler plain ``def`` — Starlette dispatches it to its
AnyIO worker threadpool, off the loop — or, when the handler genuinely needs
the running loop, keep ``async def`` and move the blocking call into
``await asyncio.to_thread(...)``.

This test exists so that a 148th instance cannot land unnoticed.  The
per-module guards (tests/test_*_not_event_loop_blocking.py) pin the modules
that were fixed; this one covers every module, including ones that do not
exist yet.
"""

from pathlib import Path

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

REPO = Path(__file__).resolve().parent.parent
SCAN_DIRS = [REPO / "app" / "api", REPO / "app" / "routes"]

MIN_FILES = 20
MIN_HANDLERS = 150


def _scan():
    """Return (files_scanned, handlers, offenders).

    handlers is ``[(relpath, name, lineno, is_async, n_awaits)]``; offenders
    is the subset that are ``async def`` with zero awaits.  Files that do not
    parse are skipped (they would fail their own import tests anyway).
    """
    files = 0
    handlers = []
    for d in SCAN_DIRS:
        for path in sorted(d.rglob("*.py")):
            try:
                found = _route_handlers(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            files += 1
            rel = path.relative_to(REPO).as_posix()
            handlers.extend((rel, *h) for h in found)
    offenders = [h for h in handlers if h[3] and h[4] == 0]
    return files, handlers, offenders


def test_no_zero_await_async_handlers_anywhere():
    files, handlers, offenders = _scan()
    assert files >= MIN_FILES, (
        f"only scanned {files} files under app/api and app/routes; the "
        f"directory layout has probably changed and this guard is blind"
    )
    assert len(handlers) >= MIN_HANDLERS, (
        f"only found {len(handlers)} route handlers in {files} files; the "
        f"decorator detection has probably broken and this guard is vacuous"
    )
    assert offenders == [], (
        "These HTTP handlers are 'async def' but await nothing, so their "
        "body runs ON the event loop and blocks every other request while "
        "it runs (seconds, under startup GIL contention):\n"
        + "\n".join(f"  {p}:{ln} {name}" for p, name, ln, _a, _n in offenders)
        + "\n\nRemedy: make the handler plain 'def' (Starlette runs it in its "
        "threadpool), or, if it truly needs the running loop, keep 'async "
        "def' and wrap the blocking call in 'await asyncio.to_thread(...)'."
    )


def test_detector_flags_a_synthetic_zero_await_handler():
    """Guard the guard: the detector must flag the exact pattern."""
    src = (
        "router = None\n"
        "@router.get('/x')\n"
        "async def blocking_handler():\n"
        "    return open('f').read()\n"
    )
    assert _route_handlers(src) == [("blocking_handler", 3, True, 0)]


def test_detector_passes_plain_def_and_awaiting_coroutine():
    src = (
        "import asyncio\n"
        "router = None\n"
        "@router.get('/a')\n"
        "def fine():\n"
        "    return open('f').read()\n"
        "@router.post('/b')\n"
        "async def offloaded():\n"
        "    return await asyncio.to_thread(open, 'f')\n"
    )
    got = {n: (a, w) for n, _ln, a, w in _route_handlers(src)}
    assert got == {"fine": (False, 0), "offloaded": (True, 1)}
