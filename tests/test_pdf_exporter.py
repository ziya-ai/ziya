"""
Unit tests for the headless conversation → PDF export service
(``app/services/pdf_exporter.py``).

Scope (Stage 2): request/response SHAPE, missing-conversation handling,
graceful degradation when Playwright is absent, and option pass-through.

NOT in scope here: PDF *fidelity* (colours, diagram images, page breaks) —
that is Stage 3's apparatus (a real headless render + PDF inspection).  These
tests deliberately never launch Chromium; the browser-driving methods are
mocked, mirroring the import-guard style of ``tests/test_diagram_renderer.py``.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import pdf_exporter


# ---------------------------------------------------------------------------
# Option normalization / pass-through
# ---------------------------------------------------------------------------

class TestNormalizeRenderOptions:
    def test_defaults_when_none(self):
        opts = pdf_exporter.normalize_render_options(None)
        assert opts == {
            "roundLimit": None,
            "includeHuman": True,
            "includeCollapsed": True,
            "includeFooter": True,
        }

    def test_roundlimit_none_is_meaningful(self):
        # None == "all rounds"; an explicit None must be honoured, not treated
        # as "unset and fall back to default" (default IS None here, but the
        # branch matters for callers who default differently).
        opts = pdf_exporter.normalize_render_options({"roundLimit": None})
        assert opts["roundLimit"] is None

    def test_roundlimit_value_passed_through(self):
        opts = pdf_exporter.normalize_render_options({"roundLimit": 3})
        assert opts["roundLimit"] == 3

    def test_booleans_pass_through(self):
        opts = pdf_exporter.normalize_render_options(
            {"includeHuman": False, "includeCollapsed": False, "includeFooter": False}
        )
        assert opts["includeHuman"] is False
        assert opts["includeCollapsed"] is False
        assert opts["includeFooter"] is False

    def test_unknown_keys_dropped(self):
        opts = pdf_exporter.normalize_render_options({"bogus": 1, "includeHuman": False})
        assert "bogus" not in opts
        assert opts["includeHuman"] is False


class TestBuildPrintPayload:
    def test_shape(self):
        msgs = [{"role": "human", "content": "hi"}]
        payload = pdf_exporter.build_print_payload(msgs, options={"roundLimit": 2})
        assert payload["title"] == "Ziya Session Transcript"
        assert payload["messages"] == msgs
        assert payload["options"]["roundLimit"] == 2

    def test_footer_included_when_requested_and_present(self):
        payload = pdf_exporter.build_print_payload(
            [], options={"includeFooter": True}, footer_html="<div>f</div>"
        )
        assert payload["footerHtml"] == "<div>f</div>"

    def test_footer_omitted_when_disabled(self):
        payload = pdf_exporter.build_print_payload(
            [], options={"includeFooter": False}, footer_html="<div>f</div>"
        )
        assert "footerHtml" not in payload

    def test_footer_omitted_when_no_html(self):
        payload = pdf_exporter.build_print_payload(
            [], options={"includeFooter": True}, footer_html=None
        )
        assert "footerHtml" not in payload


# ---------------------------------------------------------------------------
# Graceful degradation when Playwright is absent
# ---------------------------------------------------------------------------

class TestPlaywrightImportGuard:
    def test_check_playwright_returns_bool(self):
        pdf_exporter._playwright_available = None
        result = pdf_exporter._check_playwright()
        assert isinstance(result, bool)
        pdf_exporter._playwright_available = None

    @pytest.mark.asyncio
    async def test_create_raises_import_error_when_missing(self):
        pdf_exporter._playwright_available = False
        try:
            with pytest.raises(ImportError, match="Playwright is required"):
                await pdf_exporter.ConversationRenderSession.create()
        finally:
            pdf_exporter._playwright_available = None

    @pytest.mark.asyncio
    async def test_export_raises_import_error_when_missing(self):
        """The public entry point surfaces the missing-Playwright ImportError
        rather than silently succeeding."""
        pdf_exporter._playwright_available = False
        pdf_exporter._session_instance = None
        try:
            with pytest.raises(ImportError, match="Playwright is required"):
                await pdf_exporter.export_conversation_pdf(
                    messages=[{"role": "human", "content": "hi"}],
                )
        finally:
            pdf_exporter._playwright_available = None
            pdf_exporter._session_instance = None


# ---------------------------------------------------------------------------
# Conversation loading (missing-conversation handling)
# ---------------------------------------------------------------------------

class TestLoadConversationMessages:
    def test_missing_project_returns_none(self):
        with patch("app.storage.projects.ProjectStorage") as PS:
            PS.return_value.get.return_value = None
            assert pdf_exporter.load_conversation_messages("noproj", "chat") is None

    def test_missing_chat_returns_none(self):
        with patch("app.storage.projects.ProjectStorage") as PS, \
             patch("app.storage.chats.ChatStorage") as CS:
            PS.return_value.get.return_value = MagicMock()
            CS.return_value.get.return_value = None
            assert pdf_exporter.load_conversation_messages("proj", "nochat") is None

    def test_present_returns_message_dicts(self):
        msg = MagicMock()
        msg.model_dump.return_value = {"role": "human", "content": "hi"}
        chat = MagicMock()
        chat.messages = [msg]
        with patch("app.storage.projects.ProjectStorage") as PS, \
             patch("app.storage.chats.ChatStorage") as CS:
            PS.return_value.get.return_value = MagicMock()
            CS.return_value.get.return_value = chat
            out = pdf_exporter.load_conversation_messages("proj", "chat")
            assert out == [{"role": "human", "content": "hi"}]


# ---------------------------------------------------------------------------
# export_conversation_pdf orchestration (mocked session — no browser)
# ---------------------------------------------------------------------------

class TestExportConversationPdfOrchestration:
    @pytest.mark.asyncio
    async def test_requires_a_message_source(self):
        with pytest.raises(ValueError):
            await pdf_exporter.export_conversation_pdf()

    @pytest.mark.asyncio
    async def test_missing_conversation_raises_lookuperror(self):
        with patch.object(pdf_exporter, "load_conversation_messages", return_value=None):
            with pytest.raises(LookupError):
                await pdf_exporter.export_conversation_pdf(
                    project_id="p", conversation_id="missing",
                )

    @pytest.mark.asyncio
    async def test_returns_bytes_and_meta_with_messages(self):
        fake_session = MagicMock()
        fake_session.capture_pdf = AsyncMock(return_value=b"%PDF-1.4 fake")
        msgs = [{"role": "human", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        with patch.object(pdf_exporter, "get_render_session",
                          AsyncMock(return_value=fake_session)):
            pdf_bytes, meta = await pdf_exporter.export_conversation_pdf(
                messages=msgs, options={"roundLimit": 5, "includeFooter": False},
            )
        assert pdf_bytes == b"%PDF-1.4 fake"
        assert meta["message_count"] == 2
        assert meta["size"] == len(b"%PDF-1.4 fake")
        assert meta["options"]["roundLimit"] == 5
        # Option pass-through: the payload handed to the session carries opts.
        payload = fake_session.capture_pdf.call_args[0][0]
        assert payload["options"]["roundLimit"] == 5
        assert payload["options"]["includeFooter"] is False
        # Footer disabled -> no footerHtml injected.
        assert "footerHtml" not in payload

    @pytest.mark.asyncio
    async def test_footer_is_per_page_template_not_body_block(self):
        """includeFooter selects the PER-PAGE footer template (logo + tagline
        + metadata drawn in the bottom margin of every page) — the old
        end-of-document footerHtml block, which cost a mostly-empty final
        page, must NOT enter the /print payload on the PDF path."""
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
        assert "footerHtml" not in payload

    @pytest.mark.asyncio
    async def test_loads_by_id_when_no_messages(self):
        fake_session = MagicMock()
        fake_session.capture_pdf = AsyncMock(return_value=b"%PDF")
        with patch.object(pdf_exporter, "load_conversation_messages",
                          return_value=[{"role": "human", "content": "loaded"}]) as loader, \
             patch.object(pdf_exporter, "get_render_session",
                          AsyncMock(return_value=fake_session)):
            pdf_bytes, meta = await pdf_exporter.export_conversation_pdf(
                project_id="p", conversation_id="c",
            )
        loader.assert_called_once_with("p", "c")
        assert meta["message_count"] == 1
        assert meta["conversation_id"] == "c"


# ---------------------------------------------------------------------------
# POST /api/export/pdf endpoint (app/routes/export_routes.py)
#
# The endpoint is exercised by calling the handler directly with a built
# request model and mocking the service layer, mirroring the direct-call style
# of tests/test_export_rendered.py.  These assert request/response SHAPE,
# error->HTTP mapping, and option pass-through — NOT PDF fidelity.
# ---------------------------------------------------------------------------

def _patch_route_metadata():
    """Patch the metadata plumbing the endpoint reads (mirrors /rendered)."""
    return (
        patch("app.agents.models.ModelManager.get_model_alias", return_value="claude"),
        patch("app.utils.version_util.get_current_version", return_value="9.9"),
        patch("app.routes.export_routes.ziya_env", side_effect=lambda name, *a, **k: {
            "ZIYA_ENDPOINT": "bedrock", "ZIYA_PORT": "6969",
        }.get(name)),
    )


class TestExportPdfEndpoint:
    def _make_request(self, **kw):
        from app.routes.export_routes import PdfExportRequest
        return PdfExportRequest(**kw)

    def test_request_model_option_aliases(self):
        """The camelCase aliases the frontend sends map onto the snake_case
        fields (roundLimit/includeHuman/includeCollapsed/includeFooter)."""
        req = self._make_request(
            messages=[{"role": "human", "content": "hi"}],
            roundLimit=3, includeHuman=False,
            includeCollapsed=False, includeFooter=False,
        )
        assert req.round_limit == 3
        assert req.include_human is False
        assert req.include_collapsed is False
        assert req.include_footer is False

    def test_request_model_defaults(self):
        req = self._make_request(messages=[])
        assert req.round_limit is None
        assert req.include_human is True
        assert req.include_collapsed is True
        assert req.include_footer is True
        assert req.title == "Ziya Session Transcript"

    @pytest.mark.asyncio
    async def test_success_returns_pdf_response(self):
        from app.routes import export_routes
        m_alias, m_ver, m_env = _patch_route_metadata()
        with m_alias, m_ver, m_env, \
             patch("app.services.pdf_exporter.export_conversation_pdf",
                   AsyncMock(return_value=(b"%PDF-1.4 real", {"size": 13, "message_count": 2}))):
            resp = await export_routes.export_pdf(self._make_request(
                messages=[{"role": "human", "content": "hi"},
                          {"role": "assistant", "content": "yo"}],
                title="My Chat",
            ))
        # A FastAPI Response with the PDF bytes and correct headers.
        assert resp.status_code == 200
        assert resp.media_type == "application/pdf"
        assert resp.body == b"%PDF-1.4 real"
        assert 'attachment; filename="My_Chat.pdf"' in resp.headers["content-disposition"]
        assert resp.headers["x-ziya-message-count"] == "2"

    @pytest.mark.asyncio
    async def test_option_pass_through_to_service(self):
        from app.routes import export_routes
        captured = {}

        async def _fake_export(**kwargs):
            captured.update(kwargs)
            return b"%PDF", {"size": 4, "message_count": 1}

        m_alias, m_ver, m_env = _patch_route_metadata()
        with m_alias, m_ver, m_env, \
             patch("app.services.pdf_exporter.export_conversation_pdf",
                   side_effect=_fake_export):
            await export_routes.export_pdf(self._make_request(
                conversation_id="cid", project_id="pid",
                messages=[{"role": "human", "content": "hi"}],
                roundLimit=5, includeHuman=False,
                includeCollapsed=False, includeFooter=False,
            ))
        # Options forwarded with the frontend's camelCase semantics preserved.
        assert captured["options"] == {
            "roundLimit": 5, "includeHuman": False,
            "includeCollapsed": False, "includeFooter": False,
        }
        assert captured["conversation_id"] == "cid"
        assert captured["project_id"] == "pid"
        # Metadata plumbing forwarded (matches /rendered conventions).
        assert captured["version"] == "9.9"
        assert captured["model"] == "claude"
        assert captured["provider"] == "bedrock"
        assert captured["server_port"] == 6969

    @pytest.mark.asyncio
    async def test_missing_conversation_maps_to_404(self):
        from app.routes import export_routes
        m_alias, m_ver, m_env = _patch_route_metadata()
        with m_alias, m_ver, m_env, \
             patch("app.services.pdf_exporter.export_conversation_pdf",
                   AsyncMock(side_effect=LookupError("not found"))):
            resp = await export_routes.export_pdf(self._make_request(
                project_id="p", conversation_id="missing",
            ))
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_no_message_source_maps_to_400(self):
        from app.routes import export_routes
        m_alias, m_ver, m_env = _patch_route_metadata()
        with m_alias, m_ver, m_env, \
             patch("app.services.pdf_exporter.export_conversation_pdf",
                   AsyncMock(side_effect=ValueError("no source"))):
            resp = await export_routes.export_pdf(self._make_request(messages=None))
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_playwright_maps_to_501(self):
        """Absent Playwright surfaces as a real HTTP error, not silent success
        (mirrors the import-guard tests in tests/test_diagram_renderer.py)."""
        from app.routes import export_routes
        m_alias, m_ver, m_env = _patch_route_metadata()
        with m_alias, m_ver, m_env, \
             patch("app.services.pdf_exporter.export_conversation_pdf",
                   AsyncMock(side_effect=ImportError("Playwright is required"))):
            resp = await export_routes.export_pdf(self._make_request(
                messages=[{"role": "human", "content": "hi"}],
            ))
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_render_failure_maps_to_500(self):
        from app.routes import export_routes
        m_alias, m_ver, m_env = _patch_route_metadata()
        with m_alias, m_ver, m_env, \
             patch("app.services.pdf_exporter.export_conversation_pdf",
                   AsyncMock(side_effect=RuntimeError("render timed out"))):
            resp = await export_routes.export_pdf(self._make_request(
                messages=[{"role": "human", "content": "hi"}],
            ))
        assert resp.status_code == 500
