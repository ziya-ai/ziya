"""PDF post-processing must not run on the event loop (stall dumps, 2026-11-18).

Two watchdog dumps showed the loop itself, with every other thread idle,
inside ``export_document_pdf``: once in pypdf's ``PdfWriter(clone_from=...)``
(``_synthesize_outline``) and once in pdfplumber's ``extract_words``
(``_pdf_page_words``).  Both are pure-Python parses of the whole captured
PDF and take seconds on a long document; a document export runs several of
them (probe mapping, outline, metadata, then word counts per recapture).

The helpers are synchronous functions over bytes.  The contract pinned here:
inside any ``async def`` of app/services/pdf_exporter.py they are never
called directly, only handed to ``asyncio.to_thread``.  Checked on the AST
by identifier, so formatting and line positions do not matter.
"""
import ast
import asyncio
import threading
from pathlib import Path

import pytest

import app.services.pdf_exporter as pe

SRC = Path(pe.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)

# Every helper that parses a whole PDF with pypdf or pdfplumber.
PARSERS = {"_map_sentinels_to_pages", "_synthesize_outline", "_apply_document_metadata",
           "_pdf_page_words", "_pdf_figure_gaps"}


def _is_to_thread(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "to_thread"
            and isinstance(f.value, ast.Name) and f.value.id == "asyncio")


def _async_calls():
    """(enclosing async function, Call node) for every call in async code."""
    for fn in ast.walk(TREE):
        if isinstance(fn, ast.AsyncFunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    yield fn.name, node


def test_the_parsers_exist_and_are_synchronous():
    defined = {n.name for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}
    assert PARSERS <= defined, PARSERS - defined
    for name in PARSERS:
        assert not asyncio.iscoroutinefunction(getattr(pe, name))


def test_no_async_function_calls_a_pdf_parser_directly():
    direct = sorted({(fn, c.func.id) for fn, c in _async_calls()
                     if isinstance(c.func, ast.Name) and c.func.id in PARSERS})
    assert direct == [], f"called on the event loop: {direct}"


def test_every_parser_is_handed_to_a_worker_thread():
    """Positive half: the calls were moved, not deleted."""
    threaded = {c.args[0].id for _fn, c in _async_calls()
                if _is_to_thread(c) and c.args and isinstance(c.args[0], ast.Name)}
    assert PARSERS <= threaded, f"never run via asyncio.to_thread: {PARSERS - threaded}"


@pytest.mark.asyncio
async def test_a_parser_run_through_to_thread_leaves_the_loop_free():
    """The mechanism itself, on the real helper: a parse of unreadable bytes
    degrades (returns None) on a thread that is not the loop's."""
    seen = []
    real = pe._pdf_page_words

    def spy(pdf_bytes, margin):
        seen.append(threading.get_ident())
        return real(pdf_bytes, margin)

    out = await asyncio.to_thread(spy, b"not a pdf", {"top": "1in", "bottom": "1in"})
    assert out is None
    assert seen and seen[0] != threading.get_ident()
