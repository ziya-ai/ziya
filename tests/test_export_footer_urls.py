"""Regression tests for export footer URL resolution and the provider
shadowing defect (app/utils/conversation_exporter.py).

DEFECT PINNED HERE: ``_create_footer`` used to resolve the deployment URLs
inline with ``for provider in config_providers:``, SHADOWING the ``provider``
PARAMETER with a config-provider object.  With any active config provider the
HTML footer then rendered the object's repr (``<...ConfigProvider object at
0x...>``), which the browser parsed as an unknown tag and displayed as
nothing — the user-visible "Provider: is empty" defect.  URL resolution now
lives in ``get_export_urls()``, which cannot touch the caller's scope.
"""
from unittest.mock import MagicMock, patch

from app.utils.conversation_exporter import _create_footer, get_export_urls


class TestGetExportUrls:
    def test_defaults_are_public_github(self):
        with patch("app.plugins.get_active_config_providers", return_value=[]):
            ziya_url, repo_url = get_export_urls()
        assert ziya_url == "https://github.com/ziya-ai/ziya"
        assert repo_url == "https://github.com/ziya-ai/ziya"

    def test_config_provider_overrides_urls(self):
        cp = MagicMock()
        cp.provider_id = "enterprise"
        cp.get_defaults.return_value = {
            "urls": {"ziya_url": "https://w.internal.example/ziya"}
        }
        with patch("app.plugins.get_active_config_providers", return_value=[cp]):
            ziya_url, repo_url = get_export_urls()
        assert ziya_url == "https://w.internal.example/ziya"
        # repo_url not supplied -> keeps the public default.
        assert repo_url == "https://github.com/ziya-ai/ziya"

    def test_provider_without_urls_is_skipped(self):
        cp = MagicMock()
        cp.get_defaults.return_value = {}
        with patch("app.plugins.get_active_config_providers", return_value=[cp]):
            ziya_url, _ = get_export_urls()
        assert ziya_url == "https://github.com/ziya-ai/ziya"

    def test_provider_exception_never_breaks_export(self):
        cp = MagicMock()
        cp.get_defaults.side_effect = RuntimeError("boom")
        with patch("app.plugins.get_active_config_providers", return_value=[cp]):
            ziya_url, _ = get_export_urls()
        assert ziya_url == "https://github.com/ziya-ai/ziya"


class TestCreateFooterProviderNotShadowed:
    def _footer_with_providers(self, providers):
        with patch("app.plugins.get_active_config_providers",
                   return_value=providers):
            return _create_footer("public", "9.9", "sonnet4.5", "bedrock", "html")

    def test_provider_renders_with_no_config_providers(self):
        html = self._footer_with_providers([])
        assert "Provider: <code>bedrock</code>" in html

    def test_provider_renders_with_active_config_providers(self):
        """THE shadowing regression: an active config provider (with or
        without urls) must not replace the provider string in the footer."""
        cp_no_urls = MagicMock()
        cp_no_urls.get_defaults.return_value = {}
        cp_urls = MagicMock()
        cp_urls.provider_id = "enterprise"
        cp_urls.get_defaults.return_value = {
            "urls": {"ziya_url": "https://w.internal.example/ziya"}
        }
        html = self._footer_with_providers([cp_no_urls, cp_urls])
        assert "Provider: <code>bedrock</code>" in html
        # No object repr may leak into the document.
        assert "MagicMock" not in html
        assert " object at 0x" not in html
        # And the internal URL override took effect in the same render.
        assert "https://w.internal.example/ziya" in html

    def test_markdown_footer_provider_intact(self):
        cp = MagicMock()
        cp.get_defaults.return_value = {}
        with patch("app.plugins.get_active_config_providers", return_value=[cp]):
            md = _create_footer("public", "9.9", "sonnet4.5", "bedrock", "markdown")
        assert "**Provider:** `bedrock`" in md
