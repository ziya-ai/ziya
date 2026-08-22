"""HTML-04 regression: a downloaded .html export must open LIGHT regardless of
the reader's OS/browser color scheme.

The defect (fixed in _export_as_html): the embedded <style> carried an
``@media (prefers-color-scheme: dark)`` block that restyled body/.message/pre/
code/.visualization for dark, so a saved .html opened DARK on a dark-mode host.
prefers-color-scheme is a non-reactive host vector, so a light-themed transcript
must not follow it.

These are browser-free static assertions on the exported HTML source; the
end-to-end pixel proof (forced-dark render stays light) lives in the shared
fidelity apparatus check ``dark_mode_independence`` (tests/export_fidelity).
"""
from __future__ import annotations

import re


def _export_html() -> str:
    from app.utils.conversation_exporter import export_conversation_for_paste

    messages = [
        {"role": "human", "content": "Hello there"},
        {"role": "assistant", "content": "Hi! Here is some `code` and a **bold** word."},
    ]
    result = export_conversation_for_paste(
        messages,
        format_type="html",
        target="public",
        version="9.9.9",
        model="test-model",
        provider="test-provider",
    )
    return result["content"]


class TestHtmlDarkModeIndependence:
    def test_no_prefers_color_scheme_dark_media_block(self):
        """The exported CSS must NOT contain a prefers-color-scheme: dark block
        that would repaint the document dark on a dark-mode host."""
        html = _export_html()
        assert "prefers-color-scheme" not in html, (
            "exported HTML still contains a prefers-color-scheme media query — "
            "a downloaded .html would follow the reader's host theme (HTML-04)"
        )
        # Belt-and-suspenders: the specific dark bg the block used must be gone.
        assert "#0d1117" not in html, (
            "exported HTML still references the dark background #0d1117"
        )

    def test_pins_light_color_scheme(self):
        """The document must explicitly opt into the light color scheme so the
        browser does not apply dark UA styling on a dark-mode host."""
        html = _export_html()
        # color-scheme: light declared on :root and/or body.
        assert re.search(r"color-scheme\s*:\s*light", html), (
            "exported HTML does not declare color-scheme: light"
        )

    def test_body_background_is_white_light(self):
        """Body background stays the light #ffffff (unconditional, not inside a
        media query)."""
        html = _export_html()
        assert "background: #ffffff" in html or "background:#ffffff" in html
