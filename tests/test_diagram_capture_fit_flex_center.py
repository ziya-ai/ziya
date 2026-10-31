"""The capture-fit unclip must also undo flex centring on the ancestor chain.

Incident 2026-10-25 (gap 7 of the "Round" harp-score music work): a six-system
music score (~1700px tall) captured at the right size but with its first two
systems and its left margin missing, and an equal band of empty canvas at the
bottom.  The harness root (``#diagram-render-root``: 100vh, overflow:hidden,
flex align-items:center) centres a child taller than itself by pushing it to
negative y.  _CAPTURE_FIT_JS removed the overflow clip but left the centring,
and an element screenshot cannot reach above y=0, so the top was lost.

The JS runs in a browser, so this pins the contract on the real string: the
same ancestor walk that removes the clip must anchor alignment to the start.
It fails against the pre-fix module (no alignItems/justifyContent anywhere).
"""
from __future__ import annotations

import re

from app.services.diagram_renderer import _CAPTURE_FIT_JS


def _ancestor_walk(js: str) -> str:
    m = re.search(r"while \(el && el !== document\.body\) \{(.*?)\n  \}", js, re.S)
    assert m, "harness: the ancestor unclip loop was not found"
    return m.group(1)


def test_unclip_loop_still_removes_the_clip():
    body = _ancestor_walk(_CAPTURE_FIT_JS)
    assert "el.style.overflow = 'visible'" in body


def test_unclip_loop_anchors_flex_alignment_to_the_start():
    body = _ancestor_walk(_CAPTURE_FIT_JS)
    assert "el.style.alignItems = 'flex-start'" in body
    assert "el.style.justifyContent = 'flex-start'" in body
