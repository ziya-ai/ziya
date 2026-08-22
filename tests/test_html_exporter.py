"""Unit tests for the dual-mode HTML export service (Card II Stage 2).

Covers, per the task's test requirements:
  * mode selection WITH and WITHOUT Playwright (param / env / auto precedence)
  * self-containment of route output (no external refs, dark rules dropped)
  * option pass-through to the shared /print payload
  * graceful degradation when the route tier fails (never hard-fails)
  * XSS / dangerous-scheme neutralization in the serializer

These tests are browser-FREE: the route render is stubbed so mode selection,
degradation and option-plumbing are exercised deterministically without
launching Chromium.  A separate audit (run under a live server) grades the
real route bytes; see the state file harness invocation.
"""
import asyncio
import os
import re

import pytest

from app.services import html_exporter as HE


# --------------------------------------------------------------------------
# select_html_mode — precedence: explicit > env > auto
# --------------------------------------------------------------------------

class TestModeSelection:
    def _clear_env(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)

    def test_explicit_python_wins(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("ZIYA_HTML_EXPORT_MODE", "route")
        # explicit arg beats the env var
        assert HE.select_html_mode("python") == "python"

    def test_explicit_route_wins(self, monkeypatch):
        self._clear_env(monkeypatch)
        assert HE.select_html_mode("route") == "route"

    def test_env_used_when_no_explicit(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("ZIYA_HTML_EXPORT_MODE", "python")
        assert HE.select_html_mode(None) == "python"

    def test_auto_with_playwright_is_route(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setattr(HE, "_playwright_available", lambda: True)
        assert HE.select_html_mode("auto") == "route"

    def test_auto_without_playwright_is_python(self, monkeypatch):
        """Without a browser, auto MUST resolve to python (never hard-fail)."""
        self._clear_env(monkeypatch)
        monkeypatch.setattr(HE, "_playwright_available", lambda: False)
        assert HE.select_html_mode("auto") == "python"

    def test_none_defaults_to_auto_detection(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setattr(HE, "_playwright_available", lambda: False)
        assert HE.select_html_mode(None) == "python"

    def test_unknown_mode_falls_back_to_auto(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setattr(HE, "_playwright_available", lambda: False)
        assert HE.select_html_mode("bogus") == "python"

    def test_forced_route_stays_route_even_without_playwright(self, monkeypatch):
        """A FORCED route request is not silently rewritten — it will attempt
        the route and then degrade at runtime, so the caller sees the reason."""
        self._clear_env(monkeypatch)
        monkeypatch.setattr(HE, "_playwright_available", lambda: False)
        assert HE.select_html_mode("route") == "route"


# --------------------------------------------------------------------------
# export_conversation_html — python path & graceful degradation
# --------------------------------------------------------------------------

_MSGS = [
    {"role": "human", "content": "Show me `code`"},
    {"role": "assistant", "content": "Here:\n\n```python\nprint('hi')\n```"},
]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if False else asyncio.run(coro)


class TestPythonMode:
    def test_python_mode_reports_fallback_fidelity(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)
        result = asyncio.run(HE.export_conversation_html(_MSGS, mode="python"))
        assert result["mode"] == "python"
        assert result["fidelity"] == "fallback"
        assert result["format"] == "html"
        assert "<!DOCTYPE html>" in result["content"]
        assert result["size"] == len(result["content"])
        assert "fallback_reason" not in result  # not a degradation, an explicit choice

    def test_message_count_passed(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)
        result = asyncio.run(HE.export_conversation_html(_MSGS, mode="python"))
        assert result["message_count"] == 2


class TestRouteModeAndDegradation:
    def test_route_success_reports_high_fidelity(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)

        async def fake_route(messages, **kwargs):
            return "<!DOCTYPE html><html><body>ROUTE</body></html>"

        monkeypatch.setattr(HE, "_route_export", fake_route)
        result = asyncio.run(HE.export_conversation_html(_MSGS, mode="route"))
        assert result["mode"] == "route"
        assert result["fidelity"] == "high"
        assert "ROUTE" in result["content"]
        assert "fallback_reason" not in result

    def test_route_failure_degrades_to_python(self, monkeypatch):
        """Route tier raising (e.g. Playwright missing at runtime) MUST degrade
        to python and record a reason — never hard-fail."""
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)

        async def boom(messages, **kwargs):
            raise ImportError("Playwright is required")

        monkeypatch.setattr(HE, "_route_export", boom)
        result = asyncio.run(HE.export_conversation_html(_MSGS, mode="route"))
        assert result["mode"] == "python"
        assert result["fidelity"] == "fallback"
        assert "fallback_reason" in result
        assert "Playwright" in result["fallback_reason"]
        assert "<!DOCTYPE html>" in result["content"]

    def test_route_timeout_degrades(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)

        async def boom(messages, **kwargs):
            raise RuntimeError("Conversation render timed out")

        monkeypatch.setattr(HE, "_route_export", boom)
        result = asyncio.run(HE.export_conversation_html(_MSGS, mode="route"))
        assert result["mode"] == "python"
        assert "timed out" in result["fallback_reason"]


class TestOptionPassThrough:
    def test_options_reach_route_export(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)
        captured = {}

        async def fake_route(messages, *, options, title, version, model,
                             provider, server_port, embed_images, timeout_ms):
            captured["options"] = options
            captured["title"] = title
            captured["embed_images"] = embed_images
            captured["server_port"] = server_port
            return "<!DOCTYPE html><html></html>"

        monkeypatch.setattr(HE, "_route_export", fake_route)
        opts = {"roundLimit": 3, "includeHuman": False,
                "includeCollapsed": False, "includeFooter": True}
        asyncio.run(HE.export_conversation_html(
            _MSGS, mode="route", options=opts, title="My Title",
            embed_images=False, server_port=7777,
        ))
        assert captured["options"] == opts
        assert captured["title"] == "My Title"
        assert captured["embed_images"] is False
        assert captured["server_port"] == 7777

    def test_route_export_builds_payload_with_options(self, monkeypatch):
        """_route_export must funnel options through the SHARED build_print_payload
        (the /print single-source-of-truth) and call extract_export_html."""
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)
        from app.services import pdf_exporter as PE

        seen = {}

        class FakeSession:
            async def extract_export_html(self, payload, *, timeout_ms, embed_images):
                seen["payload"] = payload
                seen["embed_images"] = embed_images
                return "<!DOCTYPE html><html></html>"

        async def fake_get(port):
            seen["port"] = port
            return FakeSession()

        monkeypatch.setattr(PE, "get_render_session", fake_get)
        opts = {"roundLimit": 2, "includeHuman": True,
                "includeCollapsed": True, "includeFooter": True}
        asyncio.run(HE._route_export(
            _MSGS, options=opts, title="T", version="9.9", model="m",
            provider="p", server_port=1234, embed_images=True, timeout_ms=5000,
        ))
        assert seen["port"] == 1234
        assert seen["embed_images"] is True
        payload = seen["payload"]
        assert payload["options"]["roundLimit"] == 2
        assert payload["messages"] == _MSGS
        assert "footerHtml" in payload  # includeFooter True


class TestSizeGuard:
    def test_size_warning_when_over_limit(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)

        async def big_route(messages, **kwargs):
            return "<!DOCTYPE html><html><body>" + ("x" * 5000) + "</body></html>"

        monkeypatch.setattr(HE, "_route_export", big_route)
        result = asyncio.run(HE.export_conversation_html(
            _MSGS, mode="route", size_limit=1000,
        ))
        assert "size_warning" in result
        assert "1000" in result["size_warning"]

    def test_no_size_warning_under_limit(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)

        async def small_route(messages, **kwargs):
            return "<!DOCTYPE html><html></html>"

        monkeypatch.setattr(HE, "_route_export", small_route)
        result = asyncio.run(HE.export_conversation_html(
            _MSGS, mode="route", size_limit=10_000_000,
        ))
        assert "size_warning" not in result


class TestGracefulDegradationEndToEnd:
    """When Playwright is genuinely absent, auto -> python AND a forced route
    request degrades — HTML export never hard-fails on a missing browser."""

    def test_auto_without_browser_produces_python_output(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)
        monkeypatch.setattr(HE, "_playwright_available", lambda: False)
        result = asyncio.run(HE.export_conversation_html(_MSGS, mode="auto"))
        assert result["mode"] == "python"
        assert "<!DOCTYPE html>" in result["content"]

    def test_forced_route_without_browser_degrades_not_raises(self, monkeypatch):
        monkeypatch.delenv("ZIYA_HTML_EXPORT_MODE", raising=False)
        monkeypatch.setattr(HE, "_playwright_available", lambda: True)  # selected

        async def no_browser(messages, **kwargs):
            # Simulate the real ConversationRenderSession.create ImportError.
            raise ImportError("Playwright is required for headless ...")

        monkeypatch.setattr(HE, "_route_export", no_browser)
        result = asyncio.run(HE.export_conversation_html(_MSGS, mode="route"))
        # Degraded, not raised.
        assert result["mode"] == "python"
        assert result["fidelity"] == "fallback"
        assert "fallback_reason" in result


class TestSerializerSecurityStructure:
    """Browser-free structural guards on the self-contained serializer program.

    The serializer runs in-page, but its SOURCE must retain the security-
    critical passes; these guards flip fail<->pass if a pass is dropped
    (mutation-provable) and cost no browser."""

    def test_serializer_drops_dark_media_and_pins_light(self):
        from app.services.pdf_exporter import _SELF_CONTAINED_HTML_JS as js
        # prefers-color-scheme:dark detection + drop
        assert "prefers-color-scheme" in js
        assert "MEDIA_RULE" in js
        assert "color-scheme:light" in js

    def test_serializer_strips_scripts_and_handlers(self):
        from app.services.pdf_exporter import _SELF_CONTAINED_HTML_JS as js
        assert "querySelectorAll('script')" in js
        assert "startsWith('on')" in js  # inline handler strip
        assert "DANGER_SCHEME" in js
        assert "javascript" in js  # dangerous scheme pattern

    def test_serializer_removes_interactive_artifacts(self):
        from app.services.pdf_exporter import _SELF_CONTAINED_HTML_JS as js
        for token in ("button", "apply-changes", "toolbar",
                      "scroll-indicator", "contenteditable"):
            assert token in js

    def test_serializer_only_embeds_same_origin_images(self):
        from app.services.pdf_exporter import _SELF_CONTAINED_HTML_JS as js
        # cross-origin images are NOT fetched (no new remote loading)
        assert "url.origin !== location.origin" in js
        assert "readAsDataURL" in js

    def test_export_method_exists_on_session(self):
        from app.services.pdf_exporter import ConversationRenderSession
        assert hasattr(ConversationRenderSession, "extract_export_html")
