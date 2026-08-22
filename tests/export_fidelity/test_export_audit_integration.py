"""
INTEGRATION-TIER export-fidelity audit.

This is the slow tier.  It launches a real headless Chromium through the
SHIPPING pipeline (``app.services.pdf_exporter.export_conversation_pdf`` ->
``ConversationRenderSession`` -> the built ``/print`` route) and grades the
result with the shared checks.  It is the end-to-end complement to the fast
wiring guard in ``test_pipeline_wiring.py``.

Marking mirrors ``tests/test_diagram_renderer.py`` exactly (do not invent a new
convention):

  * ``@pytest.mark.integration`` — the repo's ``pytest.ini`` runs
    ``-m "not integration"`` by default, so this is skipped in the normal
    suite and opted into with ``pytest -m integration``.
  * ``@_skip_no_playwright`` — a ``skipif`` on Playwright/Chromium being
    importable, so the file still COLLECTS and the suite stays green on a
    machine without Playwright installed (graceful degradation must not break
    collection).

Requirements to actually RUN (not skip):
  1. ``pip install playwright && playwright install chromium``
  2. A running Ziya server on ``localhost:<ZIYA_TEST_PORT or 6969>`` whose
     built frontend bundle includes the ``/print`` route
     (``cd frontend && npx craco build`` first).

Run with::

    pytest tests/export_fidelity/test_export_audit_integration.py -m integration -v
"""
from __future__ import annotations

import os

import pytest


def _playwright_installed() -> bool:
    """Check if playwright (and, by extension, the headless renderer) is available."""
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


_skip_no_playwright = pytest.mark.skipif(
    not _playwright_installed(),
    reason="Playwright not installed (pip install playwright && playwright install chromium)",
)

# Server port the built /print route is served on. Overridable for CI.
_SERVER_PORT = int(os.environ.get("ZIYA_TEST_PORT", "6969"))


@pytest.mark.integration
class TestExportFidelityAudit:
    """End-to-end fidelity audit against a real headless render.

    These require a running Ziya server on localhost:<port> whose built bundle
    includes the /print route. Absent Playwright the whole class is skipped.
    """

    @_skip_no_playwright
    def test_canonical_variants_pass_all_checks(self):
        """Render every fixture variant through the real pipeline and assert
        every check passes — the canonical green audit (18 checks x variants)."""
        from tests.export_fidelity import fixture as fixture_mod
        from tests.export_fidelity import render_harness as harness_mod
        from tests.export_fidelity import checks as checks_mod

        variants = fixture_mod.all_variants()
        assert variants, "fixture produced no variants"

        any_rendered = False
        for name, messages in variants.items():
            doc = harness_mod.render_pdf(messages, server_port=_SERVER_PORT)
            assert doc.page_count >= 1, f"{name}: rendered zero pages"
            any_rendered = True
            results = checks_mod.run_all_checks(doc)
            failed = [r.name for r in results if not r.passed]
            assert not failed, (
                f"variant {name!r} failed checks {failed}; "
                f"measurements: {{r.name: r.measurements for r in results}}"
            )
        assert any_rendered

    @_skip_no_playwright
    def test_format_neutral_markers_survive_real_render(self):
        """Every unique fixture marker must survive to the extracted text of a
        real render (content_completeness is the format-neutral guard Cards
        II/III also rely on)."""
        from tests.export_fidelity import fixture as fixture_mod
        from tests.export_fidelity import render_harness as harness_mod
        from tests.export_fidelity import checks as checks_mod

        messages = fixture_mod.make_fidelity_conversation()
        doc = harness_mod.render_pdf(messages, server_port=_SERVER_PORT)
        result = checks_mod.check_content_completeness(doc.full_text)
        assert result.passed, (
            f"content_completeness failed on a real render: {result.failures}"
        )

    @_skip_no_playwright
    def test_body_links_become_clickable_annotations(self):
        """QUAL-03 coverage: an inline markdown BODY link — not just the footer
        link — must become a real clickable /Link annotation in the exported
        PDF, not dead blue text.

        The canonical fixture exercises only the footer URL, so this uses the
        dedicated body-link fixture (bare autolink + labelled + reference-style)
        and asserts EVERY body URL is backed by a Link annotation carrying its
        /URI (``n_unbacked_url_texts == 0``).  If a body link were dead this
        would fail and PROMOTE QUAL-03 from a coverage gap to a real defect.
        """
        from tests.export_fidelity import fixture as fixture_mod
        from tests.export_fidelity import render_harness as harness_mod
        from tests.export_fidelity import checks as checks_mod

        messages = fixture_mod.make_body_link_conversation()
        doc = harness_mod.render_pdf(messages, server_port=_SERVER_PORT)

        result = checks_mod.check_link_annotations(doc)
        assert result.passed, (
            f"link_annotations failed on the body-link fixture — a body link is "
            f"dead in the PDF: {result.failures}"
        )
        assert result.measurements["n_unbacked_url_texts"] == 0

        # Every distinct body URL must have a backing annotation /URI, proving
        # each of the three markdown link shapes is live (not merely the footer).
        annot_uris = result.measurements["annotation_uris"]
        for url in fixture_mod.BODY_LINK_URLS:
            assert any(a.startswith(url) or url.startswith(a) for a in annot_uris), (
                f"body URL {url!r} has no backing Link annotation; "
                f"annotation URIs were {annot_uris}"
            )
