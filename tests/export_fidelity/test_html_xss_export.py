"""
End-to-end XSS-neutralization audit for the HTML export (Card II security).

Unlike the browser-free mutation proofs in ``test_html_checks_can_fail.py``,
this drives the REAL exporter (``render_html`` -> the production
``export_conversation_for_paste`` for the python mode) with the shared
``adversarial.xss_attempt()`` conversation, LOADS the exported HTML in headless
Chromium behind an XSS canary, and asserts the payloads are neutralized:

  * no injected ``<script>`` runs and no ``on*`` handler fires (canary stays
    false);
  * the exported HTML carries no executable construct;
  * the surrounding prose still survives (the escaped payload is visible text).

This guards the Stage-0 hardening (PenPal #51/#116) for the python export mode
and gives Stage 2 a ready-made gate to prove the route-driven mode is at least
as safe.  Playwright-gated (skips cleanly when no browser is installed).
"""
from __future__ import annotations

import pytest

from tests.export_fidelity import adversarial, checks as C
from tests.export_fidelity import render_harness as H


def _playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _playwright_available(), reason="Playwright not installed"
)


def test_python_html_export_neutralizes_xss():
    messages, survive = adversarial.xss_attempt()
    doc = H.render_html(messages, mode="python")

    res = C.check_xss_neutralized(doc)
    assert res.passed, res.failures
    assert res.measurements["xss_canary_fired"] is False
    assert res.measurements["executable_constructs"] == []

    # The exported HTML must show the payloads only in ESCAPED form.
    html = doc.meta["html"]
    assert "<script>" not in html.lower()
    assert "&lt;script&gt;" in html  # escaped, inert
    assert 'href="javascript:' not in html.lower()

    # Surrounding prose survives (the message was not dropped wholesale).
    for marker in survive:
        assert marker in doc.full_text


def test_xss_canary_actually_fires_on_a_vulnerable_document():
    """Prove the canary WORKS: a deliberately-vulnerable HTML (raw <script>
    that calls the canary) must make check_xss_neutralized FAIL — otherwise a
    passing result above would be meaningless."""
    vulnerable = (
        "<!DOCTYPE html><html><head></head><body>"
        "<p>hello</p>"
        "<script>window.__ziya_mark_xss && window.__ziya_mark_xss()</script>"
        "</body></html>"
    )
    doc = H.render_html([{"id": "x", "role": "assistant", "content": "ignored"}],
                        mode="python", html_override=vulnerable)
    res = C.check_xss_neutralized(doc)
    assert not res.passed
    # both signals should trip: the canary fired AND the static scan saw <script>
    assert res.measurements["xss_canary_fired"] is True
    assert res.measurements["executable_constructs"]
