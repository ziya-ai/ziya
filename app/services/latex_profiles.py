"""
LaTeX rendering profiles — the extension point for server-side LaTeX diagrams.

Adding a new LaTeX-family diagram type (chemfig, tikz-cd, musixtex, ...) should
cost one entry in PROFILES and nothing else.  A profile declares:

  * which LaTeX packages to \\usepackage (and with what options)
  * which *TeX Live distribution* packages provide them, so a missing
    installation can be reported with an actionable ``tlmgr install`` line
  * which environment (if any) the body should be wrapped in

The renderer in ``latex_renderer.py`` is profile-agnostic; it only assembles a
document from a profile and hands it to the toolchain.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatexPackage:
    """A single ``\\usepackage[options]{name}`` line."""
    name: str
    options: str = ""

    def render(self) -> str:
        if self.options:
            return f"\\usepackage[{self.options}]{{{self.name}}}"
        return f"\\usepackage{{{self.name}}}"

    def render_optional(self) -> str:
        """Load the package only when installed, else expand to nothing.

        Needed because an unconditional ``\\usepackage`` of an absent package
        is a *fatal* error, so a package that merely adds a feature cannot be
        loaded that way: its absence would break every diagram of the type,
        including ones not using the feature.

        Only sound for packages whose own dependencies are certain to be
        present.  ``chemmacros`` is the counter-example that motivated this
        note: guarding ``chemmacros.sty`` alone lets it load on a system
        missing its dependency closure (58 packages deep), and it then dies
        fatally -- taking plain ``\\chemfig`` rendering down with it.  Prefer a
        feature bundled inside the profile's own package (see chemfig's Lewis
        module) over guarding a package with unbundled dependencies.
        """
        return f"\\IfFileExists{{{self.name}.sty}}{{{self.render()}}}{{}}"


@dataclass(frozen=True)
class LatexProfile:
    """Everything needed to turn a diagram body into a compilable document.

    Attributes:
        key:          the diagram ``type`` this profile serves.
        packages:     LaTeX packages for the preamble, in order.
        optional_packages: packages loaded only when present (see
                      ``LatexPackage.render_optional``).  Absence degrades a
                      feature; it never fails the render.
        libraries:    TikZ libraries to \\usetikzlibrary (empty for non-TikZ).
        wrap_env:     environment to wrap the body in, or None to use the body
                      verbatim.  Bodies that already open the environment are
                      detected and not double-wrapped.
        env_options:  optional bracket options for the wrapping environment.
        tl_packages:  TeX Live *distribution* package names required.  Used to
                      build the install instructions shown when absent; these
                      differ from ``packages`` (e.g. TikZ ships in ``pgf``).
        optional_tl_packages: distribution packages that unlock optional
                      features.  Included in install instructions but never
                      treated as missing, so they cannot block a render.
        probe_files:  files whose presence proves the profile is installed,
                      checked with ``kpsewhich``.  Cheaper and more reliable
                      than parsing ``tlmgr list``.
        extra_preamble: raw lines appended after the packages.
    """
    key: str
    packages: tuple[LatexPackage, ...] = ()
    optional_packages: tuple[LatexPackage, ...] = ()
    libraries: tuple[str, ...] = ()
    wrap_env: Optional[str] = None
    env_options: str = ""
    tl_packages: tuple[str, ...] = ()
    optional_tl_packages: tuple[str, ...] = ()
    probe_files: tuple[str, ...] = ()
    extra_preamble: tuple[str, ...] = ()

    def build_document(self, body: str, *, standalone: bool, fmt: str = "png") -> str:
        """Assemble a full LaTeX document around ``body``.

        ``standalone`` selects the document class.  The standalone class crops
        to the drawing's bounding box; the article fallback does not, which is
        why the SVG path (dvisvgm --exact-bbox) is preferred when available.

        ``fmt`` selects the PGF output driver, and it is load-bearing rather
        than cosmetic.  Under plain ``latex`` PGF defaults to
        ``pgfsys-dvips.def``, which emits the drawing as raw PostScript
        ``ps::`` specials that dvisvgm cannot interpret -- the graphics are
        silently dropped and only the text labels survive (a 4x3 circuit came
        out as a 12pt SVG).  Forcing ``pgfsys-dvisvgm.def`` makes PGF emit
        native SVG specials instead.

        The override must NOT be applied to the PDF/PNG path: pdflatex with
        that driver produces a PDF whose ink collapses to the text alone
        (verified: 71500 -> 513 ink pixels).  Hence per-format selection.
        """
        lines: list[str] = []
        if standalone:
            lines.append("\\documentclass[border=2pt]{standalone}")
        else:
            lines.append("\\documentclass{article}")

        if fmt == "svg":
            lines.append("\\def\\pgfsysdriver{pgfsys-dvisvgm.def}")

        for pkg in self.packages:
            lines.append(pkg.render())
        for pkg in self.optional_packages:
            lines.append(pkg.render_optional())
        if self.libraries:
            lines.append("\\usetikzlibrary{" + ",".join(self.libraries) + "}")
        lines.extend(self.extra_preamble)

        if not standalone:
            # No standalone class: suppress page furniture so the alpha-crop
            # heuristic is not thrown off by a page number far below the art.
            lines.append("\\pagestyle{empty}")

        lines.append("\\begin{document}")
        lines.append(self._wrap(body))
        lines.append("\\end{document}")
        return "\n".join(lines) + "\n"

    def _wrap(self, body: str) -> str:
        if not self.wrap_env:
            return body
        # A model frequently emits the environment itself.  Wrapping again
        # produces a confusing "\begin{tikzpicture} ended by \end{document}",
        # so detect and pass through.
        if f"\\begin{{{self.wrap_env}}}" in body:
            return body
        opts = f"[{self.env_options}]" if self.env_options else ""
        return (
            f"\\begin{{{self.wrap_env}}}{opts}\n"
            f"{body}\n"
            f"\\end{{{self.wrap_env}}}"
        )


# ---------------------------------------------------------------------------
# The registry.  Add new LaTeX-family diagram types here.
# ---------------------------------------------------------------------------
PROFILES: dict[str, LatexProfile] = {
    "tikz": LatexProfile(
        key="tikz",
        packages=(LatexPackage("tikz"),),
        libraries=("arrows.meta", "positioning", "calc", "patterns"),
        wrap_env="tikzpicture",
        tl_packages=("pgf",),
        probe_files=("tikz.sty",),
    ),
    "circuitikz": LatexProfile(
        key="circuitikz",
        # siunitx is loaded because real-world circuit labels lean on \SI{}{};
        # it is declared in tl_packages so a missing install is reported.
        packages=(
            LatexPackage("siunitx"),
            LatexPackage("circuitikz", "american"),
        ),
        wrap_env="circuitikz",
        tl_packages=("circuitikz", "siunitx"),
        probe_files=("circuitikz.sty",),
        # LLMs frequently guess plausible-but-nonexistent component keys for
        # the crystal/resonator bipole, which circuitikz names
        # ``piezoelectric`` (there is no quartz/crystal/xtal key).  Alias the
        # common guesses as real styles so ``to[quartz=$X_1$]`` and the bare
        # ``to[quartz]`` both resolve instead of dying in pgfkeys.
        extra_preamble=(
            r"\tikzset{quartz/.style={piezoelectric=#1},quartz/.default=,"
            r"crystal/.style={piezoelectric=#1},crystal/.default=,"
            r"xtal/.style={piezoelectric=#1},xtal/.default=}",
        ),
    ),
    "chemfig": LatexProfile(
        key="chemfig",
        # xcolor is loaded FIRST, with the extended name sets, because chemfig
        # loads xcolor itself (no options) as a dependency -- and xcolor is a
        # once-only package, so whoever loads it first wins.  Without this line
        # only xcolor's ~19 base colours exist, and a model authoring a
        # structure naturally reaches for a CSS/SVG colour name (Crimson, Navy,
        # DarkGreen, Teal, Orange, ...) inside a \color{}/\textcolor{} label or
        # a bond's 5th colour field.  Every such name is then a FATAL
        # "Undefined color" -> no output at all, for a diagram that is
        # otherwise perfectly valid.  Verified against a live chemfig install:
        # \color{Crimson}{...} aborts without this option and renders with it,
        # and pre-loading with options produces no clash when chemfig later
        # requests xcolor unoptioned.  svgnames + dvipsnames together cover the
        # ~340 names an LLM is likely to emit.  (Note this cannot rescue an
        # invalid spelling such as lowercase `navy`, which is not a name in any
        # set -- that remains a genuine input error.)
        packages=(
            LatexPackage("xcolor", "svgnames,dvipsnames"),
            LatexPackage("chemfig"),
        ),
        # mhchem supplies \ce{} and \pu{}, which chemfig cannot typeset --
        # chemfig draws structures, not equations.  Optional because mhchem
        # does not ship with BasicTeX, and a hard dependency would break
        # structure rendering on a stock install.  version=4 is pinned
        # deliberately: mhchem's default version differs across releases and
        # v3 parses \ce{} arrows incompatibly, so an unpinned load would
        # silently change how equations render.
        optional_packages=(LatexPackage("mhchem", "version=4"),),
        tl_packages=("chemfig",),
        optional_tl_packages=("mhchem",),
        probe_files=("chemfig.sty",),
        # Lewis structures come from chemfig's OWN bundled module, which it
        # ships but does not auto-load -- NOT from chemmacros.  Verified: this
        # enables \lewis and \Lewis with no additional distribution package,
        # whereas chemmacros needs a 58-package dependency closure and fails
        # fatally when any of it is absent (which was reproduced: an apparently
        # successful `tlmgr install chemmacros` still broke all chemfig
        # rendering).  Guarded so a future chemfig without the module degrades
        # instead of failing.
        #
        # The \input target is a fixed literal chosen here, never
        # user-controlled: diagram bodies are prescanned and \input inside a
        # body is rejected, so this does not widen the file-access surface.
        extra_preamble=(
            r"\IfFileExists{chemfig-lewis.tex}{\input{chemfig-lewis.tex}}{}",
        ),
    ),
    "tikz-cd": LatexProfile(
        key="tikz-cd",
        packages=(LatexPackage("tikz-cd"),),
        wrap_env="tikzcd",
        tl_packages=("tikz-cd",),
        probe_files=("tikz-cd.sty",),
    ),
}

#: Constructs that resolve coordinates recorded in the .aux file on a previous
#: run (pgf "position marks"): chemfig's electron-pushing arrows, and TikZ's
#: remember-picture overlays.  Two consequences, both verified empirically:
#:
#:  1. They need TWO compilation passes.  On pass 1 the marks do not exist yet,
#:     so the arrow is drawn from a default position (measured: 10 red pixels
#:     on pass 1 vs 18 on pass 2, converging at 2).
#:  2. They cannot be rendered via DVI/dvisvgm at all.  ``pgfsys-dvisvgm.def``
#:     writes a bogus y-coordinate into the .aux (verified: 50586364 vs the
#:     correct 229375 from pdflatex), placing the arrow far off-canvas.  This is
#:     a driver limitation, not chemfig-specific -- a plain TikZ
#:     ``remember picture`` overlay reproduces it identically.
_POSITION_MARK_PATTERNS: tuple[str, ...] = (r"\chemmove", "remember picture")


def requires_position_marks(body: str) -> bool:
    """True when ``body`` uses .aux-recorded coordinates.

    Callers must give such bodies a second compilation pass and must not route
    them through the DVI/SVG path.
    """
    return any(pat in body for pat in _POSITION_MARK_PATTERNS)


#: A ``\color`` / ``\textcolor`` appearing inside a chemfig ``\charge`` /
#: ``\Charge`` argument.  Verified empirically against a live chemfig install:
#: such a body compiles cleanly through pdflatex -> PDF -> PNG, but under the
#: DVI/dvisvgm path it sends dvisvgm's colour routine
#: (``\pgfsys@svg@set@color@orig``) into an unbounded expansion and aborts with
#: "TeX capacity exceeded" -- no image at all.  A plain ``\color`` in an
#: ordinary substituent label is fine on the SVG path, and a colourless
#: ``\charge`` is fine; only the COMBINATION diverges, because \charge typesets
#: its argument inside a pgfpicture whose colour push/pop the dvisvgm driver
#: mishandles.  This mirrors the position-mark case: a construct that survives
#: PDF but not DVI, so it must be forced to the PNG path.
#:
#: The ``[^{}]*`` before the colour macro stays inside the charge's own brace
#: group (angle/offset/tikz key text never contains ``{``), so a ``\color`` in
#: a sibling label after the ``\charge`` does not match.
_CHARGE_COLOR_RE = None  # compiled lazily below to keep re import local


def charge_color_breaks_dvisvgm(body: str) -> bool:
    """True when ``body`` colours a ``\\charge`` argument.

    Such bodies must not be routed through the DVI/dvisvgm (SVG) path: they
    trigger a "TeX capacity exceeded" runaway in the dvisvgm colour driver even
    though they compile fine to PDF/PNG.  Callers should force PNG.

    Advisory heuristic: any internal fault degrades to ``False`` (attempt the
    normal path) rather than raising, so a regex defect can never block a
    render that would otherwise succeed.
    """
    global _CHARGE_COLOR_RE
    try:
        import re
        if _CHARGE_COLOR_RE is None:
            _CHARGE_COLOR_RE = re.compile(
                r"\\(?:charge|Charge)\s*\{[^{}]*\\(?:color|textcolor)\b"
            )
        return bool(_CHARGE_COLOR_RE.search(body))
    except Exception:                      # pragma: no cover - defensive
        return False

#: Diagram types this module can render.  ``diagram_render.py`` consults this
#: so LaTeX types stop being rejected as unsupported.
LATEX_DIAGRAM_TYPES: frozenset = frozenset(PROFILES)

#: Distribution packages needed by the toolchain itself, independent of any
#: profile.  ``standalone`` gives correct cropping; ``dvisvgm`` gives SVG.
TOOLCHAIN_TL_PACKAGES: tuple[str, ...] = ("standalone", "dvisvgm")


def get_profile(diagram_type: str) -> Optional[LatexProfile]:
    """Look up a profile by diagram type, case/whitespace insensitively."""
    if not diagram_type:
        return None
    return PROFILES.get(diagram_type.strip().lower())


def install_command(tl_packages: tuple[str, ...] | list[str]) -> str:
    """Build the ``tlmgr install`` line shown in the not-installed notice."""
    ordered = sorted(set(tl_packages))
    return "tlmgr install " + " ".join(ordered) if ordered else ""
