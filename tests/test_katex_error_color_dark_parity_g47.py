"""G-47 / D-052 — the dark KaTeX error colour must stay in sync across the split.

The live browser render resolves KaTeX's ``errorColor`` from the active theme
(``katexRenderOptions`` in ``frontend/src/utils/mathSanitizer.js``): #cc0000 on
light, a lighter red on dark because #cc0000 is only ~2.8:1 on the dark chat
surfaces.  The screenshot harness in ``app/utils/chat_screenshot.py`` greps the
rendered DOM for the error colour to count failed math tokens, so it must know
the DARK colour too or it under-reports failures in dark mode.

Python cannot import JS, so the value is necessarily duplicated.  This test
parses the JS and compares — exactly like ``test_error_colour_matches_the_js_constant``
does for the light constant — so the two cannot drift.

It FAILS on the unpatched tree: ``KATEX_ERROR_COLOR_DARK`` does not yet exist in
``mathSanitizer.js`` there, so no match is found.  It passes once the shared
sanitizer gains the dark constant and the factory that selects it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def _sanitizer_source() -> str:
    from app.utils import conversation_exporter as ce

    found = ce._find_math_sanitizer()
    if not found:
        pytest.skip("frontend/src/utils/mathSanitizer.js absent (not shipped in a wheel)")
    return Path(found).read_text(encoding="utf-8")


def test_dark_error_colour_matches_the_js_constant():
    from app.utils.chat_screenshot import KATEX_ERROR_COLOR_DARK

    source = _sanitizer_source()
    match = re.search(
        r"const\s+KATEX_ERROR_COLOR_DARK\s*=\s*['\"](#[0-9a-fA-F]{6})['\"]", source
    )
    assert match, "KATEX_ERROR_COLOR_DARK not found in mathSanitizer.js"
    assert KATEX_ERROR_COLOR_DARK.lower() == match.group(1).lower(), (
        f"Python has {KATEX_ERROR_COLOR_DARK}, JS has {match.group(1)}"
    )


def test_js_defines_a_theme_resolving_render_options_factory():
    """The dark colour is only reachable through a theme-resolving factory;
    pin its presence so a future edit cannot quietly revert to one constant."""
    source = _sanitizer_source()
    assert re.search(r"function\s+katexRenderOptions\s*\(", source), (
        "katexRenderOptions(isDarkMode) factory missing from mathSanitizer.js"
    )
