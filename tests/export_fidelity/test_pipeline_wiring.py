"""
FAST-TIER wiring guard for the export-fidelity pipeline.

This is the cheap tier that runs on EVERY test invocation (no ``npm run build``,
no headless browser, no live server).  It asserts that the shared apparatus is
correctly wired together — the render backends, the check registries, the
production service seams, and the /print route contract — so that a breakage in
the plumbing is caught immediately rather than only surfacing in the slow
integration audit.

It deliberately does NOT render anything.  The slow, browser-driven end-to-end
verification lives in ``test_export_audit_integration.py`` (marked
``integration`` + skipped without Playwright), mirroring the two-tier split in
``tests/test_diagram_renderer.py``.

Collection MUST be safe whether or not Playwright is installed: the production
service (``app.services.pdf_exporter``) imports Playwright lazily, and the
harness modules have no top-level browser imports, so importing them here can
never fail on a Playwright-less machine.  A regression that adds an eager
``import playwright`` at module scope would break this test's own import and be
caught.
"""
from __future__ import annotations

import inspect

from tests.export_fidelity import checks as checks_mod
from tests.export_fidelity import render_harness as harness_mod
from tests.export_fidelity import fixture as fixture_mod


# ---------------------------------------------------------------------------
# Render backend registry
# ---------------------------------------------------------------------------
def test_render_backend_registry_has_pdf():
    """The PDF backend must be registered and dispatchable by name."""
    assert "pdf" in harness_mod.RENDER_BACKENDS
    assert callable(harness_mod.RENDER_BACKENDS["pdf"])


def test_render_backend_registry_has_html():
    """Card II's HTML backend must be registered alongside pdf (added without
    forking the harness) and dispatchable by name."""
    assert "html" in harness_mod.RENDER_BACKENDS
    assert callable(harness_mod.RENDER_BACKENDS["html"])


def test_html_backend_drives_real_production_export_paths():
    """The HTML backend must call SHIPPING code, not a private reimplementation:
    the python mode calls export_conversation_for_paste; the route mode drives
    the shared ConversationRenderSession.extract_html seam."""
    src = inspect.getsource(harness_mod.render_html)
    assert "export_conversation_for_paste" in src, (
        "python mode must invoke the production HTML exporter"
    )
    route_src = inspect.getsource(harness_mod._render_route_html)
    assert "extract_html" in route_src and "get_render_session" in route_src, (
        "route mode must drive the shared ConversationRenderSession seam"
    )


def test_html_checks_registry_populated():
    """The HTML-specific check registry must carry every Card-II analyzer."""
    assert set(checks_mod.HTML_CHECKS) == {
        "self_containment",
        "dark_mode_independence",
        "diff_coloring",
        "syntax_highlighting",
        "math_rendering",
        "table_rendering",
        "highlight_preservation",
        "structural_validity",
        "xss_neutralized",
    }
    for fn in checks_mod.HTML_CHECKS.values():
        assert callable(fn)


def test_run_all_checks_applies_html_checks_only_to_html_docs():
    """HTML checks must run for an html-source doc and NOT for a text-only doc
    (so a PDF/markdown doc is not graded by HTML-only analyzers)."""
    import numpy as np
    html_page = harness_mod.RenderedPage(
        0, np.full((4, 4, 3), 255, dtype=np.uint8), " ", 4, 4, []
    )
    html_doc = harness_mod.RenderedDocument(
        pages=[html_page], full_text=" ", source_format="html",
        meta={"probe": {}, "resource_refs": {}, "parse_errors": [],
              "dark_probe": {}, "dark_rgb": np.full((4, 4, 3), 255, dtype=np.uint8)},
    )
    html_names = {r.name for r in checks_mod.run_all_checks(html_doc)}
    assert set(checks_mod.HTML_CHECKS) <= html_names

    text_doc = harness_mod.RenderedDocument(pages=[], full_text="hi",
                                            source_format="pdf")
    text_names = {r.name for r in checks_mod.run_all_checks(text_doc)}
    assert not (set(checks_mod.HTML_CHECKS) & text_names)


def test_render_dispatch_rejects_unknown_backend():
    """render() must fail loudly on an unknown backend (so Card II's future
    'html' backend registration is a deliberate, discoverable act)."""
    try:
        harness_mod.render("does-not-exist", [])
    except ValueError as exc:
        assert "unknown render backend" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("render() accepted an unknown backend")


def test_pdf_backend_drives_the_real_production_service():
    """The PDF backend must call the SHIPPING pipeline, not a private copy.

    We assert the harness references ``export_conversation_pdf`` from
    ``app.services.pdf_exporter`` so the audit can never silently drift from
    what production actually renders.
    """
    src = inspect.getsource(harness_mod.render_pdf)
    assert "export_conversation_pdf" in src, (
        "render_pdf must invoke app.services.pdf_exporter.export_conversation_pdf"
    )


# ---------------------------------------------------------------------------
# Check registries
# ---------------------------------------------------------------------------
def test_raster_and_neutral_check_registries_populated():
    assert set(checks_mod.RASTER_CHECKS) >= {
        "colorfulness",
        "background_whiteness",
        "dark_theme_leak",
        "image_presence",
        "expected_color_presence",
        "whitespace_waste",
        "page_break_sanity",
    }
    assert set(checks_mod.FORMAT_NEUTRAL_CHECKS) == {
        "text_extractability",
        "content_completeness",
    }


def test_format_neutral_checks_operate_on_plain_text():
    """The two format-neutral checks are what Cards II/III reuse on their own
    extracted text/markdown.  They must accept a plain string and return a
    CheckResult flagged ``format_neutral=True`` (no raster dependency)."""
    for name, fn in checks_mod.FORMAT_NEUTRAL_CHECKS.items():
        result = fn("some text with no markers")
        assert result.name == name
        assert result.format_neutral is True, (
            f"{name} must be format-neutral so non-PDF backends can run it"
        )


def test_run_all_checks_runs_neutral_pair_without_raster():
    """A text-only RenderedDocument (no raster pages) must still get both
    format-neutral checks — proving the neutral seam Cards II/III depend on
    works with zero raster involvement."""
    doc = harness_mod.RenderedDocument(pages=[], full_text="hello world")
    results = checks_mod.run_all_checks(doc)
    names = {r.name for r in results}
    assert names == {"text_extractability", "content_completeness"}


# ---------------------------------------------------------------------------
# Production service seams (import-only; Playwright imported lazily downstream)
# ---------------------------------------------------------------------------
def test_pdf_exporter_public_seams_exist():
    """The reusable seams Cards II/III drive must be importable without a
    browser present."""
    from app.services import pdf_exporter as pe

    for name in (
        "ConversationRenderSession",
        "get_render_session",
        "shutdown_render_session",
        "build_print_payload",
        "load_conversation_messages",
        "normalize_render_options",
        "export_conversation_pdf",
    ):
        assert hasattr(pe, name), f"pdf_exporter is missing seam {name!r}"


def test_render_session_exposes_both_capture_backends():
    """The SHARED session must expose capture_pdf (PDF) AND extract_html (the
    seam Card II reuses for its HTML export)."""
    from app.services.pdf_exporter import ConversationRenderSession

    assert hasattr(ConversationRenderSession, "capture_pdf")
    assert hasattr(ConversationRenderSession, "extract_html")


# ---------------------------------------------------------------------------
# Fixture contract (shared stressor for all three cards)
# ---------------------------------------------------------------------------
def test_fixture_variants_and_markers_present():
    variants = fixture_mod.all_variants()
    assert len(variants) >= 2  # light + dark at minimum
    assert len(fixture_mod.UNIQUE_TEXT_MARKERS) == 15
    # exactly-once contract is validated in test_fixture_and_checks.py; here we
    # only assert the marker list is non-empty and wired for reuse.
    assert fixture_mod.PRESENCE_MARKERS
