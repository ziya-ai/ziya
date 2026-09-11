"""D-241 (group G-29d67c): a loop-derived trig argument at a high index
(``\\pgfmathsetmacro{0.09*cos(\\n*111)}`` with \\n up to 299) no longer
overflows / aborts -- the tikz structural recovery pass keeps it renderable.

Locked as an end-to-end render in both themes; theme-independent numeric-layer
defect, so success in either theme is only meaningful alongside the other.
"""
import json
import os

import pytest

from app.services.latex_renderer import latex_renderer

_SPEC = os.path.join(".ziya", "gfx-sweep", "specs", "tikz", "tikz-w2-14.json")


def test_w2_14_renders_both_themes():
    cap = latex_renderer.probe()
    if not cap.available:
        pytest.skip("no LaTeX toolchain available")
    with open(_SPEC) as fh:
        body = json.load(fh)["definition"]
    for theme in ("light", "dark"):
        res = latex_renderer.render("tikz", body, fmt="png", theme=theme,
                                    use_cache=False)
        assert res.ok, f"{theme}: {res.error_kind}: {res.error}"
        assert res.content
