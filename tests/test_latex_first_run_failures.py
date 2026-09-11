"""
Regression tests for three LaTeX failures a first-run user hit within their
first few "show me a sample graphic" turns.  Each was reproduced against a live
TeX Live 2026basic install before the fix was written; the live tests below
re-run the exact bodies and skip (not pass) when no TeX is installed.

1. circuitikz -- Ziya's preamble aliased ``amp``/``adc``/``dac``/``dsp`` to a
   plain rectangle NODE style (for D-042: ``\\node[amp]`` draws nothing in
   stock circuitikz).  But circuitikz defines those same names as PATH-style
   bipole keys, so the alias clobbered them: ``to[amp, l={LNA}]`` died with
   ``! Package pgfkeys Error: I do not know the key '/tikz/l'`` (the ``l``
   label key is only valid inside a bipole), and a bare ``to[amp]`` compiled
   but silently drew a plain wire with the amplifier symbol missing.
   Measured:  to[amp] with alias -> 1 <path>;  without -> 2 <path>.
   Fix: dispatch on context.  ``every to`` sets a flag before a ``to[...]``'s
   own options run, so the key routes to circuitikz's original bipole on a
   path and to the ``<name>shape`` node shape in a node.

2. pgfplots -- ``shader=interp`` is refused outright by the dvisvgm driver
   (``surface shading (shader=interp) is NOT available for the selected
   driver``), and ``-halt-on-error`` turns that into "No pages of output".
   It compiles fine through pdflatex -> PNG.  Same class as position marks and
   coloured \\charge: a construct that survives PDF but not DVI, so force PNG;
   when PNG is not possible, downgrade to ``shader=faceted`` with a warning
   rather than fail.

3. chemfig -- ``\\lewis`` failed with ``Undefined control sequence
   \\CF_expafter``.  UPSTREAM REGRESSION, verified against CTAN: chemfig 1.81
   (2026/09/01) removed ``\\CF_expafter`` / ``\\CF_swapunbrace`` from
   ``chemfig.tex`` but shipped an unchanged ``chemfig-lewis.tex`` that still
   calls ``\\CF_expafter`` four times.  Every ``\\lewis`` on chemfig >= 1.81
   therefore aborts, on any install (the user's ``kpsewhich -all`` showed one
   file, dated 2026/09/01).  1.71 (what an older TL ships) is unaffected.
   The macro is two lines; Ziya's chemfig preamble defines it when -- and only
   when -- chemfig has not.  Reproduced by stripping the two defs from a 1.71
   ``chemfig.tex`` on TEXINPUTS (same ``\\iterate ... \\CF_expafter`` frame as
   the user's log); the shim compiles it, and is a no-op on real 1.71.  When
   the shim is somehow absent the log parser still names the cause instead of
   "check for a typo".
"""
from __future__ import annotations

import re
import os
import shutil
import subprocess

import pytest

import app.services.latex_profiles as P
from app.services.latex_profiles import (
    downgrade_interp_shading,
    requires_interp_shading,
)
from app.services.latex_renderer import LatexRenderer


_HAS_TEX = bool(shutil.which("latex") and shutil.which("dvisvgm"))
_HAS_PDF = bool(shutil.which("pdflatex") and (shutil.which("gs") or shutil.which("ghostscript")))
live = pytest.mark.skipif(not _HAS_TEX, reason="no latex/dvisvgm on PATH")


def _svg_paths(res) -> int:
    content = res.content or b""
    if isinstance(content, bytes):
        content = content.decode("utf8", "ignore")
    return len(re.findall(r"<path", content))


def _first_bang(res) -> str:
    m = re.search(r"^!.*$", res.error or "", re.M)
    return m.group(0) if m else (res.error or "")


# --------------------------------------------------------------------------
# 1. circuitikz: amp/adc/dac/dsp alias must not clobber the bipole path keys
# --------------------------------------------------------------------------

class TestCircuitikzBlockAliasIsContextAware:

    def test_preamble_no_longer_overwrites_the_tikz_amp_key_with_a_node_style(self):
        pre = "\n".join(P.get_profile("circuitikz").extra_preamble)
        # The old alias assigned /tikz/amp a plain node style, which is what
        # replaced circuitikz's own path-style key.
        assert "amp/.style={ziyablock}" not in pre, (
            "REGRESSION: amp/.style={ziyablock} redefines circuitikz's "
            "/tikz/amp bipole key; to[amp, l=...] dies with '/tikz/l' unknown"
        )
        # The replacement must keep the original reachable and dispatch on a
        # to-path flag.
        assert "every to" in pre
        for k in ("amp", "adc", "dac", "dsp"):
            assert f"ziya-orig-{k}" in pre, f"original circuitikz '{k}' key not preserved"
            assert f"{k}shape" in pre, f"node context of '{k}' does not map to {k}shape"

    @live
    def test_to_amp_with_label_compiles(self):
        # The exact construct from the user's failing superheterodyne front end.
        res = LatexRenderer().render(
            "circuitikz", r"\draw (0,0) to[amp,l={LNA}] (2,0);", fmt="svg", use_cache=False)
        assert res.ok, _first_bang(res)

    @live
    def test_to_amp_draws_the_amplifier_not_just_a_wire(self):
        res = LatexRenderer().render(
            "circuitikz", r"\draw (0,0) to[amp] (2,0);", fmt="svg", use_cache=False)
        assert res.ok, _first_bang(res)
        # Wire alone = 1 path (measured with the old alias).  Wire + symbol = 2.
        assert _svg_paths(res) >= 2, "amplifier symbol silently dropped from to[amp]"

    @live
    def test_node_amp_still_draws_something(self):
        # D-042's original complaint: stock circuitikz draws NOTHING for
        # \node[amp] (measured: 0 paths).  The context dispatch must keep the
        # node case visible.
        res = LatexRenderer().render(
            "circuitikz", r"\node[amp] (a) at (0,0) {};", fmt="svg", use_cache=False)
        assert res.ok, _first_bang(res)
        assert _svg_paths(res) >= 1, "\\node[amp] regressed to drawing nothing"

    @live
    def test_flag_reverts_between_path_and_node(self):
        # If the every-to flag leaked out of the path's TeX group, the
        # following \node[amp] would route to the bipole and draw nothing.
        res = LatexRenderer().render(
            "circuitikz",
            r"\draw (0,0) to[amp] (2,0); \node[amp] (a) at (4,0) {};",
            fmt="svg", use_cache=False)
        assert res.ok, _first_bang(res)
        assert _svg_paths(res) >= 3

    @live
    def test_users_full_receiver_front_end_compiles(self):
        body = r"""\ctikzset{RF/scale=0.8}
\tikzset{every node/.append style={font=\scriptsize}}
\node[antenna,anchor=south] (ant) at (0,0) {};
\draw (ant.south) -- (1,0);
\draw (1,0) to[bandpass,l={preselect}] (3,0) to[amp,l={LNA}] (5,0);
\node[mixer] (m) at (6.3,0) {};
\draw (5,0) -- (m.1);
\node[oscillator] (lo) at (6.3,-2) {};
\node[below=3pt] at (lo.south) {LO};
\draw[-{Latex[length=2mm]}] (lo.4) -- (m.2);
\draw (m.3) -- (7.6,0);
\draw (7.6,0) to[bandpass,l={IF BPF}] (9.6,0) to[amp,l={IF amp}] (11.6,0)
      to[adc,l={ADC}] (13.6,0);
\draw (13.6,0) -- (14.4,0) node[right] {DSP};"""
        res = LatexRenderer().render("circuitikz", body, fmt="svg", use_cache=False)
        assert res.ok, _first_bang(res)


# --------------------------------------------------------------------------
# 2. pgfplots: shader=interp cannot go through dvisvgm
# --------------------------------------------------------------------------

class TestPgfplotsInterpShading:

    @pytest.mark.parametrize("body,expected", [
        (r"\addplot3[surf, shader=interp] {x*y};", True),
        (r"\addplot3[surf,shader = interp,samples=20] {x};", True),
        (r"\addplot3[surf, shader=faceted interp] {x*y};", True),
        (r"\addplot3[surf, shader=flat] {x*y};", False),
        (r"\addplot3[surf, shader=faceted] {x*y};", False),
        (r"\addplot[interpolate] {x};", False),   # unrelated key containing the word
    ])
    def test_requires_interp_shading(self, body, expected):
        assert requires_interp_shading(body) is expected

    def test_downgrade_rewrites_only_the_shader_value(self):
        body = r"\addplot3[surf, shader=interp, samples=40] {x};\addplot3[shader=faceted interp]{y};"
        out, note = downgrade_interp_shading(body)
        assert "shader=interp" not in out and "faceted interp" not in out
        assert out.count("shader=faceted") == 2
        assert "samples=40" in out            # neighbours untouched
        assert note and "dvisvgm" in note     # the user is told why

    def test_downgrade_is_a_noop_when_not_needed(self):
        body = r"\addplot3[surf, shader=flat] {x};"
        out, note = downgrade_interp_shading(body)
        assert out == body and note is None

    @live
    @pytest.mark.skipif(not _HAS_PDF, reason="no pdflatex/gs for the PNG path")
    def test_auto_format_render_of_interp_surface_succeeds(self):
        # This body was a hard "No pages of output" failure on fmt=auto.
        body = (r"\begin{axis}[view={40}{30}]\addplot3[surf, shader=interp, "
                r"domain=-2:2, domain y=-2:2, samples=12]{x*y};\end{axis}")
        res = LatexRenderer().render("pgfplots", body, fmt="auto", use_cache=False)
        assert res.ok, _first_bang(res)
        assert res.fmt == "png", "interp shading must be routed to the PNG path"

    @live
    def test_forced_svg_downgrades_instead_of_failing(self):
        # When the caller pins SVG there is no PNG escape; the body must still
        # render, at reduced shading quality, and say so.
        body = (r"\begin{axis}[view={40}{30}]\addplot3[surf, shader=interp, "
                r"domain=-2:2, domain y=-2:2, samples=12]{x*y};\end{axis}")
        res = LatexRenderer().render("pgfplots", body, fmt="svg", use_cache=False)
        assert res.ok, _first_bang(res)
        assert any("faceted" in w for w in res.warnings)


# --------------------------------------------------------------------------
# 3. chemfig 1.81 dropped \CF_expafter but chemfig-lewis.tex still uses it
# --------------------------------------------------------------------------

_CHEMFIG_LEWIS_BODY = (
    r"\ce{CH4 + 2O2 -> CO2 + 2H2O}\hspace{1.5cm}"
    r"\chemfig{\lewis{2:6:,O}=C=\lewis{2:6:,O}}"
)


class TestChemfig181LewisShim:

    def test_preamble_defines_cf_expafter_only_when_absent(self):
        pre = "\n".join(P.get_profile("chemfig").extra_preamble)
        assert r"\CF_expafter" in pre, "the 1.81 shim is missing from the preamble"
        # Conditional: must not clobber the real definition on chemfig <= 1.71.
        assert re.search(r"\\ifdefined\\CF_expafter\\else", pre), (
            "shim must be guarded so it is a no-op where chemfig defines the macro"
        )
        # chemfig-lewis.tex is where the call sites are, so the shim must be
        # in place BEFORE that file is \input.
        assert pre.index(r"\CF_expafter") < pre.index("chemfig-lewis.tex"), (
            "shim is defined after chemfig-lewis.tex is loaded -- too late for "
            "any code that file runs at load time"
        )

    @live
    def test_lewis_compiles_on_installed_chemfig(self):
        # Whatever chemfig version is installed (1.71 no-op, 1.81 repaired),
        # a Lewis structure must compile.
        r = LatexRenderer().render("chemfig", _CHEMFIG_LEWIS_BODY, use_cache=False)
        assert r.ok, r.error

    @live
    def test_lewis_compiles_against_a_chemfig_tex_without_cf_expafter(self, tmp_path, monkeypatch):
        """Simulate 1.81 by stripping the two defs from the installed
        chemfig.tex and placing that copy first on TEXINPUTS.  This reproduces
        the user's exact log frame without the shim and must compile with it."""
        src = subprocess.run(["kpsewhich", "chemfig.tex"], capture_output=True,
                             text=True).stdout.strip()
        if not src:
            pytest.skip("chemfig.tex not on this TeX install")
        text = open(src, errors="ignore").read()
        stripped, n = re.subn(
            r"^\\def\\CF_swapunbrace.*\n^\\def\\CF_expafter.*\n", "", text, flags=re.M)
        if n == 0:
            pytest.skip("installed chemfig.tex already lacks \\CF_expafter (>= 1.81); "
                        "covered by test_lewis_compiles_on_installed_chemfig")
        (tmp_path / "chemfig.tex").write_text(stripped)
        monkeypatch.setenv("TEXINPUTS", f"{tmp_path}{os.pathsep}")
        r = LatexRenderer().render("chemfig", _CHEMFIG_LEWIS_BODY, use_cache=False)
        assert r.ok, (
            "REGRESSION: \\lewis on a chemfig.tex without \\CF_expafter "
            f"(chemfig 1.81 shape) failed: {r.error}"
        )


class TestChemfigInternalUndefinedIsNamed:
    """Belt-and-braces: if the shim is ever absent, the parser must still not
    blame the user's body."""

    # Verbatim shape of the user's log (TeX puts the macro at the END of the
    # continuation line).
    _LOG = (
        "! Undefined control sequence.\n"
        "\\iterate ...*\\CF_lewiscurrentoffset }\\CF_expafter\n"
        "                                                  {\\draw [fill,black,}{\\CF_l...\n"
        "l.31 \\chemfig{\\lewis{2:6:,O}=C=\\lewis{2:6:,O}}\n"
    )

    def test_cf_internal_undefined_names_the_chemfig_version_bug(self):
        msg = LatexRenderer._extract_error(self._LOG)
        assert "typo" not in msg.lower(), (
            "REGRESSION: a chemfig-internal macro (\\CF_...) being undefined is "
            "a chemfig problem, not a mistake in the user's body"
        )
        assert r"\CF_expafter" in msg
        assert "1.81" in msg

    def test_a_users_own_typo_is_still_reported_as_one(self):
        # TeX breaks the line AT the undefined macro, so it ends the l.N line.
        log = ("! Undefined control sequence.\n"
               "l.5 \\chemfig{\\lewsi\n"
               "                    {2,O}}\n")
        msg = LatexRenderer._extract_error(log)
        assert "typo" in msg.lower()
        assert "version" not in msg.lower()
