"""
Guard for the EXPLICIT client-side PDF fallback selection (retirement Stage 7).

The server-side PDF path (`POST /api/export/pdf`) is primary, but it cannot
satisfy the one case where the server has no Playwright/Chromium — there it
returns HTTP 501. Rather than delete the client-side renderer, it is KEPT as an
explicit, documented fallback: `ExportConversationModal.handlePdfExport` detects
the 501 and calls `exportConversationAsPdf` (browser print engine over the live
DOM) so the user still gets a PDF.

This is a browser-free STRUCTURAL guard (same style as the PDF-01/05/06/09
guards): it reads the shipped TypeScript sources and asserts the selection is
wired deliberately — the fallback util still exists, the modal imports it, and
handlePdfExport branches on the 501 status. It is mutation-provable: removing
the `response.status === 501` branch or the import flips these tests to failing.

Why structural rather than a rendered check: the fallback ends in the browser's
native print dialog (`window.print()` via an iframe), which is unobservable to
any headless harness — the very reason the server path was built. So the
observable contract we CAN lock down is the selection logic itself.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODAL = REPO / "frontend" / "src" / "components" / "ExportConversationModal.tsx"
PDF_UTIL = REPO / "frontend" / "src" / "utils" / "pdfExport.ts"


def _strip_line_comments(text: str) -> str:
    """Remove // line comments and /* */ block comments so we assert on real
    code, not on the explanatory comments that also mention 501/fallback."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)//.*$", "", text)
    return text


def test_client_fallback_util_still_exists():
    """The fallback renderer is retained (not deleted) and still exports its
    entry point."""
    assert PDF_UTIL.is_file(), f"missing client fallback util at {PDF_UTIL}"
    assert "export async function exportConversationAsPdf" in PDF_UTIL.read_text(), (
        "pdfExport.ts must still export exportConversationAsPdf as the fallback"
    )


def test_util_documents_its_fallback_role():
    """The retention must be documented, not incidental — the header comment
    must state it is the Playwright-absent fallback."""
    head = PDF_UTIL.read_text()[:1500].lower()
    assert "fallback" in head, "pdfExport.ts must document its fallback role"
    assert "playwright" in head or "501" in head, (
        "pdfExport.ts must explain WHEN it is used (Playwright absent / HTTP 501)"
    )


def test_modal_imports_the_fallback():
    code = _strip_line_comments(MODAL.read_text())
    assert re.search(
        r"import\s*\{[^}]*\bexportConversationAsPdf\b[^}]*\}\s*from\s*['\"][^'\"]*pdfExport['\"]",
        code,
    ), "ExportConversationModal must import exportConversationAsPdf for the fallback"


def test_modal_selects_fallback_on_501():
    """handlePdfExport must branch on the 501 status and call the client
    fallback — the explicit selection logic."""
    code = _strip_line_comments(MODAL.read_text())
    assert re.search(r"response\.status\s*===\s*501", code), (
        "modal must detect HTTP 501 (Playwright absent) to select the fallback"
    )
    # the 501 branch must actually invoke the fallback renderer
    assert "exportConversationAsPdf(" in code, (
        "the 501 branch must call exportConversationAsPdf(...)"
    )


def test_primary_path_is_still_server_side():
    """Guard against accidentally reverting the primary path back to the client
    renderer: the modal must still POST to /api/export/pdf."""
    code = _strip_line_comments(MODAL.read_text())
    assert "/api/export/pdf" in code, (
        "modal must still POST to the server-side /api/export/pdf as the primary path"
    )
