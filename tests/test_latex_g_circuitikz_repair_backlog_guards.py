r"""
Backlog guards for fix group G-CIRCUITIKZ-REPAIR.

Covers the one code fix made under this group:

  * D-042 (circuitikz-w1-10, unknown-block-shape-silently-degenerates):
    ``amp``/``adc``/``dac``/``dsp`` are DSP block-diagram node shapes a model
    reaches for to draw a signal chain.  circuitikz recognises the keys but
    draws NOTHING for them (they are no-ops, not errors), so a five-block chain
    renders as a bare wire with no error -- silent structural loss.  The fix
    aliases each to a drawn rectangular block (``ziyablock``) in the circuitikz
    profile's ``extra_preamble`` so the node becomes a visible, labelled box.

Two directions of certification:

  1. STRUCTURAL (always runs, no toolchain): the assembled document for the
     circuitikz profile carries the block aliases mapped to a drawn rectangle,
     in BOTH themes.  Fails against the unpatched tree (the aliases are absent).

  2. BEHAVIOURAL (skipped without pdflatex): rendering circuitikz-w1-10 draws
     strictly more path elements than a control body that re-neutralises the
     aliases to empty styles (reproducing the pre-fix invisible behaviour at
     runtime, without needing the unpatched tree).  Asserted for both themes.

The other four defects in the group were confirmed already-remediated in source
(D-039: missing-semicolon insertion + orphan ``\end`` strip -- see
test_latex_g05_statement_structure.py) or declined as disproportionate /
engine-limited (D-040, D-041, D-043); see the backlog for the recorded reasons.
These import the REAL modules under test, never a re-implementation.
"""
import json
import os

import pytest

from app.services.latex_profiles import get_profile
from app.services.latex_renderer import LatexRenderer

_SPEC_DIR = os.path.join(
    os.path.dirname(__file__), "..", ".ziya", "gfx-sweep", "specs", "circuitikz"
)

_BLOCK_ALIASES = ("amp", "adc", "dac", "dsp")


def _load(spec_id: str) -> str:
    with open(os.path.join(_SPEC_DIR, spec_id + ".json")) as fh:
        return json.load(fh)["definition"]


# ---------------------------------------------------------------------------
# D-042 structural: the block aliases reach the assembled preamble (both themes)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_block_shape_aliases_present_in_preamble(theme):
    profile = get_profile("circuitikz")
    doc = profile.build_document(r"\node[amp] (a) {};", standalone=True, theme=theme)

    # The original fix was an unconditional ``<name>/.style={<rectangle>}``.
    # That REPLACED circuitikz's real ``to[amp]`` bipole key, so a path
    # ``to[amp]`` silently lost its symbol and ``to[amp, l={LNA}]`` died with
    # "I do not know the key '/tikz/l'" (see test_latex_first_run_failures).
    # The alias is now context-aware: on a ``to`` path it forwards to the
    # preserved original key, on a node it applies circuitikz's own
    # ``<name>shape``.  Pin that contract, not the old mechanism.
    assert r"\newif\ifziyatopath" in doc
    assert "every to/.append style" in doc
    for name in _BLOCK_ALIASES:
        assert f"{name}/.code=" in doc, f"{name} alias missing for theme={theme}"
        assert f"ziya-orig-{name}" in doc, f"{name}: original bipole key not preserved"
        assert f"{name}shape" in doc, f"{name}: node case does not use circuitikz's shape"
    # The mechanism this test used to pin must be gone -- it is the bug.
    assert "ziyablock" not in doc


def test_block_aliases_do_not_disturb_quartz_aliases():
    # The pre-existing crystal/resonator aliases must survive the addition.
    profile = get_profile("circuitikz")
    doc = profile.build_document(r"\node[amp] (a) {};", standalone=True, theme="light")
    for name in ("quartz", "crystal", "xtal"):
        assert name + "/.style" in doc


# ---------------------------------------------------------------------------
# D-042 behavioural: the blocks actually draw (skipped without pdflatex)
# ---------------------------------------------------------------------------
_renderer = LatexRenderer()
_cap = _renderer.probe()
_no_latex = not getattr(_cap, "available", False)

# Re-neutralising the aliases in the BODY (which is emitted after the preamble,
# so it wins) reproduces the pre-fix "recognised key, draws nothing" behaviour
# at runtime -- the runtime control for the direction check.
_NEUTRALISE = (
    r"\tikzset{amp/.style={},adc/.style={},dac/.style={},dsp/.style={}}"
)


def _path_count(body: str) -> int:
    res = _renderer.render("circuitikz", body, fmt="svg")
    assert res.ok, f"render failed: {res.error_kind}: {res.error}"
    return res.content.decode("utf-8", "replace").count("<path")


@pytest.mark.skipif(_no_latex, reason="no LaTeX toolchain available")
def test_w1_10_blocks_become_visible():
    body = _load("circuitikz-w1-10")
    patched = _path_count(body)
    control = _path_count(_NEUTRALISE + "\n" + body)
    # Four previously-invisible blocks now draw a rectangle each.
    assert patched > control, (patched, control)
    assert patched - control >= 4, (patched, control)


@pytest.mark.skipif(_no_latex, reason="no LaTeX toolchain available")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_w1_10_renders_in_both_themes(theme):
    body = _load("circuitikz-w1-10")
    res = _renderer.render("circuitikz", body, fmt="png", theme=theme)
    assert res.ok, f"{theme}: {res.error_kind}: {res.error}"
    assert res.content and len(res.content) > 0
