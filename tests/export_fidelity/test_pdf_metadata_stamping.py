"""Guard for QUAL-02: the PDF driver stamps conversation-specific metadata.

These tests exercise the driver's metadata helpers directly
(``_build_document_metadata`` + ``_apply_document_metadata``) WITHOUT a browser,
so they run in the fast (browser-free) tier alongside test_pipeline_wiring.
They prove:

  * a conversation title becomes /Title (never the app-shell '<title>'),
  * model/provider drive /Author, with sensible fallbacks,
  * /Subject and /Creator are set to export-identifying strings, and
  * ``add_metadata`` MERGES, so Chromium's parseable /CreationDate survives.

The end-to-end fail->pass (Chromium page.pdf() leaves /Title as the app shell,
/Creator 'Chromium', no /Author or /Subject until this lands) is proven in the
integration audit; check_document_metadata's own pass/fail is covered by
test_checks_can_fail.test_document_metadata_can_pass_and_fail.
"""
from __future__ import annotations

import io

import pytest

from app.services import pdf_exporter as PE

pypdf = pytest.importorskip("pypdf")
from pypdf import PdfReader, PdfWriter  # noqa: E402


def _pdf_with_chromium_defaults() -> bytes:
    """A 1-page PDF carrying the exact Info dict Chromium's page.pdf() emits."""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_metadata({
        "/Title": "Ziya - Code Assistant",
        "/Creator": "Chromium",
        "/CreationDate": "D:20260101120000+00'00'",
    })
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_build_metadata_uses_conversation_title():
    md = PE._build_document_metadata(
        title="My Debugging Session", model="claude-3", provider="bedrock",
    )
    assert md["/Title"] == "My Debugging Session"
    assert md["/Author"] == "claude-3 (bedrock)"
    assert md["/Subject"] == PE._METADATA_SUBJECT
    assert md["/Creator"] == PE._METADATA_CREATOR


def test_build_metadata_rejects_app_shell_and_generic_titles():
    # An incoming app-shell / generic / empty title must NOT be trusted; the
    # fallback the driver emits must still SATISFY the audit check (whose own
    # default-title set is the authority for "looks like a default").
    from tests.export_fidelity.checks import _CHROMIUM_DEFAULT_TITLES as CHECK_DEFAULTS
    for bad in ("Ziya - Code Assistant", "", None, "  ", "Ziya Session Transcript"):
        md = PE._build_document_metadata(title=bad, model="m", provider="p")
        # driver never echoes the incoming app-shell title verbatim
        assert md["/Title"].lower() != "ziya - code assistant"
        # and the emitted fallback passes the audit check's default-title gate
        assert md["/Title"].lower() not in CHECK_DEFAULTS


def test_build_metadata_author_fallbacks():
    # both generic -> 'Ziya'
    assert PE._build_document_metadata(
        title="X", model="unknown", provider="unknown")["/Author"] == "Ziya"
    assert PE._build_document_metadata(
        title="X", model="test-model", provider="test-provider")["/Author"] == "Ziya"
    # only provider meaningful
    assert PE._build_document_metadata(
        title="X", model="unknown", provider="anthropic")["/Author"] == "anthropic"
    # only model meaningful
    assert PE._build_document_metadata(
        title="X", model="gpt-4", provider="unknown")["/Author"] == "gpt-4"


def test_apply_metadata_overrides_chromium_defaults_and_keeps_creation_date():
    before = _pdf_with_chromium_defaults()
    r0 = PdfReader(io.BytesIO(before))
    m0 = {str(k): str(v) for k, v in dict(r0.metadata).items()}
    assert m0["/Title"] == "Ziya - Code Assistant"
    assert m0["/Creator"] == "Chromium"

    md = PE._build_document_metadata(
        title="My Chat", model="claude-3", provider="bedrock")
    after = PE._apply_document_metadata(before, md)
    r1 = PdfReader(io.BytesIO(after))
    m1 = {str(k): str(v) for k, v in dict(r1.metadata).items()}
    assert m1["/Title"] == "My Chat"
    assert m1["/Creator"] == PE._METADATA_CREATOR
    assert m1["/Author"] == "claude-3 (bedrock)"
    assert m1["/Subject"] == PE._METADATA_SUBJECT
    # MERGE, not replace: Chromium's parseable CreationDate must survive.
    assert m1["/CreationDate"] == m0["/CreationDate"]


def test_apply_metadata_empty_is_noop():
    before = _pdf_with_chromium_defaults()
    assert PE._apply_document_metadata(before, {}) == before


def test_apply_metadata_never_raises_on_garbage_bytes():
    # Best-effort: malformed input returns the bytes unchanged, never raises.
    garbage = b"not a pdf"
    md = PE._build_document_metadata(title="X", model="m", provider="p")
    assert PE._apply_document_metadata(garbage, md) == garbage
