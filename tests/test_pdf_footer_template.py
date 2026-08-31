"""Tests for the per-page PDF footer template and the pinned-model plumbing.

NOTE: these tests exercise ``build_pdf_footer_template`` /
``_provider_display_name`` in ``app/services/pdf_exporter.py`` and the
``model`` field on ``PdfExportRequest`` — they PASS only once the
corresponding diffs are applied (they will fail collection/assert before
that, which is the intended signal).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import pdf_exporter


# ---------------------------------------------------------------------------
# Footer template content
# ---------------------------------------------------------------------------

class TestBuildPdfFooterTemplate:
    def _build(self, **kw):
        defaults = dict(version="9.9", model="sonnet4.5", provider="bedrock")
        defaults.update(kw)
        return pdf_exporter.build_pdf_footer_template(**defaults)

    def test_contains_logo_tagline_metadata_and_page_numbers(self):
        ft = self._build()
        # Logo travels as an inlined data URI (footer templates cannot load
        # external resources).
        assert 'src="data:image/png;base64,' in ft
        # Line 1: the tagline, on its own line.
        assert "Exported from Ziya" in ft
        # Line 2: url · version · model (Provider).
        assert "github.com/ziya-ai/ziya" in ft
        assert "v9.9" in ft
        assert "sonnet4.5 (Bedrock)" in ft
        # Live page numbers via Chromium's magic class names.
        assert 'class="pageNumber"' in ft
        assert 'class="totalPages"' in ft
        # Chromium renders footer templates at font-size 0 by default; the
        # template must set its own.
        assert "font-size:6.5px" in ft

    def test_internal_url_override_shows_in_footer(self):
        with patch("app.utils.conversation_exporter.get_export_urls",
                   return_value=("https://w.internal.example/ziya",
                                 "https://w.internal.example/ziya")):
            ft = self._build()
        assert "w.internal.example/ziya" in ft
        assert "github.com" not in ft

    def test_dynamic_text_is_html_escaped(self):
        ft = self._build(model='ev<il>&"model')
        assert "ev<il>" not in ft
        assert "ev&lt;il&gt;" in ft

    def test_unknown_model_and_provider_omitted(self):
        ft = self._build(model="unknown", provider="unknown")
        assert "unknown" not in ft
        assert "v9.9" in ft  # version + url still present

    def test_model_without_provider_shown_bare(self):
        ft = self._build(provider="unknown")
        assert "sonnet4.5" in ft
        # No dangling "(...)" when the provider is unusable.
        assert "sonnet4.5 (" not in ft


class TestProviderDisplayName:
    @pytest.mark.parametrize("endpoint,expected", [
        ("bedrock", "Bedrock"),
        ("google", "Google"),
        ("openai", "OpenAI"),
        ("anthropic", "Anthropic"),
        ("zai", "z.ai"),
        ("meta", "Meta"),
        ("someday", "Someday"),   # unmapped -> capitalized fallback
        ("unknown", ""),
        ("", ""),
        (None, ""),
    ])
    def test_mapping(self, endpoint, expected):
        assert pdf_exporter._provider_display_name(endpoint) == expected


# ---------------------------------------------------------------------------
# Orchestration: per-page footer replaces the end-of-document block
# ---------------------------------------------------------------------------

class TestFooterTemplateOrchestration:
    @pytest.mark.asyncio
    async def test_footer_template_passed_and_body_block_gone(self):
        fake_session = MagicMock()
        fake_session.capture_pdf = AsyncMock(return_value=b"%PDF")
        with patch.object(pdf_exporter, "get_render_session",
                          AsyncMock(return_value=fake_session)):
            await pdf_exporter.export_conversation_pdf(
                messages=[{"role": "human", "content": "hi"}],
                options={"includeFooter": True},
                version="9.9", model="claude", provider="bedrock",
            )
        payload = fake_session.capture_pdf.call_args[0][0]
        kwargs = fake_session.capture_pdf.call_args.kwargs
        ft = kwargs.get("footer_template")
        assert ft and "claude (Bedrock)" in ft and "v9.9" in ft
        # The end-of-document footer block no longer enters the payload —
        # its final-page cost is what the per-page footer removes.
        assert "footerHtml" not in payload

    @pytest.mark.asyncio
    async def test_no_footer_template_when_disabled(self):
        fake_session = MagicMock()
        fake_session.capture_pdf = AsyncMock(return_value=b"%PDF")
        with patch.object(pdf_exporter, "get_render_session",
                          AsyncMock(return_value=fake_session)):
            await pdf_exporter.export_conversation_pdf(
                messages=[{"role": "human", "content": "hi"}],
                options={"includeFooter": False},
            )
        kwargs = fake_session.capture_pdf.call_args.kwargs
        assert not kwargs.get("footer_template")


# ---------------------------------------------------------------------------
# Route: conversation-pinned model overrides the global default
# ---------------------------------------------------------------------------

class TestExportPdfModelPin:
    async def _run(self, model_pin, validate_err=None, resolved_ep="bedrock"):
        from app.routes.export_routes import PdfExportRequest, export_pdf

        captured = {}

        async def fake_export(**kw):
            captured.update(kw)
            return b"%PDF", {"message_count": 1, "size": 4}

        with patch("app.agents.models.ModelManager.get_model_alias",
                   return_value="global-default"), \
             patch("app.utils.version_util.get_current_version",
                   return_value="9.9"), \
             patch("app.routes.export_routes.ziya_env",
                   side_effect=lambda name, *a, **k: {
                       "ZIYA_ENDPOINT": "bedrock", "ZIYA_PORT": "6969",
                   }.get(name)), \
             patch("app.utils.model_override.validate_model_override",
                   return_value=validate_err), \
             patch("app.utils.model_override.resolve_override_endpoint",
                   return_value=resolved_ep), \
             patch("app.services.pdf_exporter.export_conversation_pdf",
                   side_effect=fake_export):
            req = PdfExportRequest(
                messages=[{"role": "human", "content": "hi"}], model=model_pin,
            )
            await export_pdf(req)
        return captured

    @pytest.mark.asyncio
    async def test_valid_pin_overrides_model_and_endpoint(self):
        captured = await self._run("pinned-model", validate_err=None,
                                   resolved_ep="google")
        assert captured["model"] == "pinned-model"
        assert captured["provider"] == "google"

    @pytest.mark.asyncio
    async def test_invalid_pin_falls_back_to_global(self):
        captured = await self._run("bogus", validate_err="not permitted")
        assert captured["model"] == "global-default"
        assert captured["provider"] == "bedrock"

    @pytest.mark.asyncio
    async def test_no_pin_uses_global(self):
        captured = await self._run(None)
        assert captured["model"] == "global-default"
