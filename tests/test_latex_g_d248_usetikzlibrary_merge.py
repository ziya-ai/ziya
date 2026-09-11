"""D-248 (group G-29d67c): a body-level ``\\usetikzlibrary{...}`` request is
merged into the profile preamble so a shape it provides is defined.

``_sanitize_input`` strips preamble ``\\usetikzlibrary`` lines, and the profile
emits only its OWN libraries -- so ``\\node[diamond]`` after a stripped
``\\usetikzlibrary{shapes.geometric}`` was an undefined key and aborted the
compile (tikz-w4-10).  ``_extract_requested_libraries`` collects the request
and render() merges it into the TikZ-family preamble.  Locked here in both
themes.
"""
import json
import os

import pytest

from app.services.latex_renderer import LatexRenderer, latex_renderer

_SPEC = os.path.join(".ziya", "gfx-sweep", "specs", "tikz", "tikz-w4-10.json")


def test_requested_library_extracted():
    body = r"\usetikzlibrary{shapes.geometric}" "\n" r"\node[diamond] {x};"
    assert "shapes.geometric" in LatexRenderer._extract_requested_libraries(body)


def test_w4_10_renders_both_themes():
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
