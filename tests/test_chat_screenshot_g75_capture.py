"""G-75 / D-026 regression: oversize chat-message capture must be unclipped.

The chat-message renderer captures the live ``.message`` node with a bare
Playwright element screenshot (``app/utils/chat_screenshot.py`` Stage 5).
Playwright captures *painted* pixels, so a message taller than its fixed-height
``overflow`` scroll container was truncated to the visible band (the ~1500px
ceiling) and a horizontally offset one was shaved on its leading edge, with the
remainder of the canvas padded blank -- silent data loss with no overflow
affordance.

The fix adds a capture-prep step that unclips the ancestor chain (dropping
``overflow`` clipping and any height cap) and grows the viewport to contain the
full element before the screenshot.

Direction: every assertion below exercises ``build_capture_prep_js`` /
``MAX_CAPTURE_*``, which do NOT exist in the pre-fix module -- the import at the
top fails against unpatched code, so this whole module fails without the change
and passes with it.  The behavioural assertions further pin that the emitted JS
actually neutralises the clip (rather than merely existing).
"""
import re

import pytest

from app.utils import chat_screenshot as cs


def test_capture_prep_helper_exists_and_is_pure():
    # Present only after the fix; absent -> AttributeError -> test fails on
    # unpatched code (the certifies-the-fix direction).
    assert hasattr(cs, "build_capture_prep_js")
    js = cs.build_capture_prep_js(["Alpha", "Beta"], "assistant")
    assert isinstance(js, str) and js.strip().startswith("() =>")


def test_capture_prep_neutralises_overflow_clipping_on_ancestors():
    js = cs.build_capture_prep_js(["Alpha"], "assistant")
    # Walks the ancestor chain (not just the element itself)...
    assert "parentElement" in js
    assert "document.documentElement" in js
    # ...and drops both overflow clipping and any height cap, which is what
    # makes the previously-clipped pixels paint and thus be captured.
    assert "setProperty('overflow', 'visible', 'important')" in js
    assert "setProperty('max-height', 'none', 'important')" in js


def test_capture_prep_reports_full_unclipped_extent():
    js = cs.build_capture_prep_js(["Alpha"], "assistant")
    # The caller needs the far edges (from the page origin) to size the
    # viewport so the whole element is contained -- both dimensions, not one.
    for key in ("width:", "height:", "right:", "bottom:", "left:", "top:"):
        assert key in js, key
    # It anchors the element at the origin so the capture starts at its
    # true top-left (the left-edge-shave half of the defect).
    assert "inline: 'start'" in js
    assert "block: 'start'" in js


def test_capture_prep_embeds_the_locator_so_it_targets_our_message():
    js = cs.build_capture_prep_js(["Sentinelword"], "assistant")
    # The locator words are baked in (same mechanism as the other builders),
    # so prep operates on OUR seeded message, not arbitrary chrome.
    assert "Sentinelword" in js
    assert "querySelectorAll('.message')" in js


def test_capture_prep_returns_null_when_element_absent():
    js = cs.build_capture_prep_js(["Alpha"], "assistant")
    # Must degrade to null (caller then skips the resize) rather than throwing.
    assert "if (!el) return null;" in js


def test_capture_caps_are_bounded_and_sane():
    # Ceilings exist so a pathological document cannot drive an unbounded
    # off-screen surface; they must still be large enough to clear the old
    # ~1500px vertical ceiling that caused the truncation.
    assert isinstance(cs.MAX_CAPTURE_WIDTH, int)
    assert isinstance(cs.MAX_CAPTURE_HEIGHT, int)
    assert cs.MAX_CAPTURE_HEIGHT >= 4000 > 1500
    assert 1400 <= cs.MAX_CAPTURE_WIDTH <= 8000


def test_render_flow_wires_prep_before_screenshot():
    """The prep step is actually invoked in the render path (not dead code).

    Static check on the source: the capture-prep evaluate and the viewport
    resize must appear BEFORE the element screenshot in ``render_chat_message``,
    otherwise the unclip would happen too late to affect the capture.
    """
    import inspect

    src = inspect.getsource(cs.render_chat_message)
    prep_at = src.find("build_capture_prep_js(")
    resize_at = src.find("set_viewport_size(")
    shot_at = src.find("element.screenshot()")
    assert prep_at != -1, "prep not wired into render flow"
    assert resize_at != -1, "viewport not grown to fit the element"
    assert shot_at != -1
    assert prep_at < shot_at, "prep must run before the screenshot"
    assert resize_at < shot_at, "viewport resize must run before the screenshot"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
