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
import re
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


#: Math packages loaded for EVERY LaTeX profile.
#:
#: No profile declared these, so \dfrac in an axis label or legend entry,
#: \text in a TikZ node, or \boldsymbol in a chemfig label died with
#: "Undefined control sequence" -- a FATAL abort producing no image at all,
#: for an otherwise valid body.  Reproduced identically under `pgfplots` and
#: `tikz`, which is why this is shared machinery rather than a per-profile
#: package list: the gap belongs to every profile that typesets a label.
#:
#: REQUIRED rather than \IfFileExists-guarded because both ship in
#: scheme-basic -- verified present in the 2026basic install this renderer
#: targets (amsmath/amsmath.sty, amsfonts/amssymb.sty) -- so neither needs a
#: tl_packages entry.  Emitted BEFORE the profile's own packages: siunitx
#: documents that amsmath should precede it, and circuitikz declares siunitx
#: itself, so an "after" placement would invert that order there.  Safe ahead
#: of a profile's optioned xcolor because neither package requests xcolor
#: (verified: they pull only amsbsy, amsfonts, amsopn and amstext), and the
#: D-004 contract is about beating the UNOPTIONED load the profile's own
#: package performs.
_BASE_MATH_PACKAGES: tuple[LatexPackage, ...] = (
    LatexPackage("amsmath"),
    LatexPackage("amssymb"),
)

#: Packages loaded for every profile ONLY IF INSTALLED.
#:
#: siunitx supplies \si / \SI / \qty -- the canonical way to put a unit on an
#: axis label or a node.  Only the circuitikz profile declared it, so
#: \si{\watt} in a pgfplots xlabel aborted fatally: the same class of defect
#: as the amsmath gap above, found the same way.
#:
#: Guarded rather than required because siunitx does not ship in every minimal
#: install, and an unconditional load would take down EVERY diagram of the
#: type, including ones using no units -- the chemmacros failure recorded in
#: LatexPackage.render_optional.  Guarding is sound here because its own
#: dependency closure is just expl3, which modern LaTeX preloads into the
#: format.
_BASE_OPTIONAL_PACKAGES: tuple[LatexPackage, ...] = (
    LatexPackage("siunitx"),
)

#: TeX Live distribution package names for the globally guarded packages
#: above.  Kept beside them deliberately: a guarded load without a matching
#: install hint is exactly the defect this pair fixes -- siunitx was loaded
#: for every profile but named in only ONE profile's `tlmgr install` line, so
#: on a machine without it \si{} silently produced no unit and nothing said
#: which package to install.  Surfaced via
#: LatexProfile.effective_optional_tl_packages.
_BASE_OPTIONAL_TL_PACKAGES: tuple[str, ...] = (
    "siunitx",
)


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
                      treated as missing, so they cannot block a render.  Read
                      through ``effective_optional_tl_packages``, which merges
                      the globally guarded packages in.
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
    #: ``standalone`` crop margin.  Default 2pt is right for line art whose ink
    #: stays inside the computed bounding box.  A profile whose engine draws
    #: satellite ink OUTSIDE that box (chemfig ``\charge`` / ``\lewis`` place a
    #: charge glyph or lone-pair dots beyond the atom box, which the standalone
    #: crop then slices at the canvas edge -- D-042) widens it so the crop keeps
    #: that ink.  A per-profile knob, so a larger margin never changes the tight
    #: crop of the engines that do not need it.
    border: str = "2pt"

    @property
    def effective_optional_tl_packages(self) -> tuple[str, ...]:
        """Optional TL package names to name in the ``tlmgr install`` hint.

        The profile's own declarations plus the globally guarded packages.
        Deliberately a derived view rather than a mutation of the
        ``optional_tl_packages`` FIELD, so a profile's declaration stays
        exactly what its author wrote (tests assert on that) while the install
        hint reflects what the preamble actually loads.

        A name already in ``tl_packages`` is not filtered out -- circuitikz
        genuinely requires siunitx, so it appears in both -- because both
        downstream consumers de-duplicate (``_not_installed`` via
        ``dict.fromkeys``, ``install_command`` via ``sorted(set(...))``).
        """
        merged = list(self.optional_tl_packages)
        merged.extend(_BASE_OPTIONAL_TL_PACKAGES)
        return tuple(dict.fromkeys(n for n in merged if n))

    def build_document(self, body: str, *, standalone: bool, fmt: str = "png",
                       theme: str = "light") -> str:
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
            lines.append("\\documentclass[border=" + self.border + "]{standalone}")
        else:
            lines.append("\\documentclass{article}")

        if fmt == "svg":
            lines.append("\\def\\pgfsysdriver{pgfsys-dvisvgm.def}")

        # Shared packages the profile did not declare itself.  Deduped because
        # a profile-level load with options (amsmath[intlimits], or
        # circuitikz's required siunitx) must win: emitting an unoptioned load
        # as well is a fatal "Option clash".
        declared = {p.name for p in self.packages}
        declared |= {p.name for p in self.optional_packages}
        for pkg in _BASE_MATH_PACKAGES:
            if pkg.name not in declared:
                lines.append(pkg.render())
        for pkg in self.packages:
            lines.append(pkg.render())
        for pkg in self.optional_packages:
            lines.append(pkg.render_optional())
        for pkg in _BASE_OPTIONAL_PACKAGES:
            if pkg.name not in declared:
                lines.append(pkg.render_optional())
        if self.libraries:
            lines.append("\\usetikzlibrary{" + ",".join(self.libraries) + "}")
        lines.extend(self.extra_preamble)

        if not standalone:
            # No standalone class: suppress page furniture so the alpha-crop
            # heuristic is not thrown off by a page number far below the art.
            lines.append("\\pagestyle{empty}")

        lines.append("\\begin{document}")

        # Theme-aware opaque background + default ink for the RASTER (PNG) path.
        #
        # The SVG path is deliberately left transparent: the browser's
        # enhanceSVGVisibility recolours it live per theme, so baking a surface
        # in would fight that.  The PNG path has no such recolouring -- gs
        # -sDEVICE=pngalpha composites the default black TeX ink onto a
        # transparent background, and the viewer then shows it on whatever theme
        # surface is active (black ink on the ~#1F1F1F dark panel measures
        # ~1.27:1 -- effectively invisible; the byte-identical PNG was served
        # for both themes).
        #
        # The colours are RESOLVED FROM ``theme`` rather than a single constant
        # being swapped, which is what keeps light correct while fixing dark:
        #   dark : light ink on a dark page  -> #EDEDED on #1F1F1F = 14.08:1
        #   light: dark  ink on a white page -> #000000 on #FFFFFF = 21.00:1
        # (xcolor is always present here: chemfig loads it explicitly and every
        # TikZ-family profile pulls it in via pgf.)
        if fmt != "svg":
            if theme == "dark":
                lines.append("\\pagecolor[HTML]{1F1F1F}")
                lines.append("\\color[HTML]{EDEDED}")
            else:
                lines.append("\\pagecolor[HTML]{FFFFFF}")
                lines.append("\\color[HTML]{000000}")

        lines.append(self._wrap(body))
        lines.append("\\end{document}")
        return "\n".join(lines) + "\n"

    def _wrap(self, body: str) -> str:
        if not self.wrap_env:
            return body
        # A model frequently emits the environment itself.  Wrapping again
        # produces a confusing "\begin{tikzpicture} ended by \end{document}",
        # so detect and pass through.
        #
        # Generalised beyond a literal ``\begin{<wrap_env>}`` substring: a body
        # may already open its OWN drawing environment that differs from the
        # profile's default (e.g. a ``tikzpicture`` supplied under the
        # ``tikz-cd`` profile, or a hyphen/CD variant), and double-wrapping any
        # of those yields a mismatched ``\begin{X} ended by \end{Y}``.  Match
        # the environment allowing optional trailing chars (``*``, ``-``) so the
        # common variants are recognised, and also pass through when the body
        # already carries any known drawing environment.
        if re.search(r"\\begin\s*\{" + re.escape(self.wrap_env) + r"[*-]?\}", body):
            return body
        for env in _DRAWING_ENVS:
            if re.search(r"\\begin\s*\{" + re.escape(env) + r"\}", body):
                return body
        opts = f"[{self.env_options}]" if self.env_options else ""
        return (
            f"\\begin{{{self.wrap_env}}}{opts}\n"
            f"{body}\n"
            f"\\end{{{self.wrap_env}}}"
        )


#: Drawing/matrix environments a model may open itself.  When a body already
#: contains a ``\begin{<one of these>}`` the profile must NOT wrap it again --
#: the outer ``\begin{<wrap_env>}`` would be closed by the inner ``\end`` and
#: the compile aborts with a mismatched-environment error.  Kept small and
#: specific (the picture-level environments only) so an incidental
#: ``\begin{scope}`` inside a body that DOES still need wrapping is not mistaken
#: for a self-supplied top-level environment.
_DRAWING_ENVS: tuple[str, ...] = ("tikzpicture", "circuitikz", "tikzcd", "chemfig")


# ---------------------------------------------------------------------------
# The registry.  Add new LaTeX-family diagram types here.
# ---------------------------------------------------------------------------
PROFILES: dict[str, LatexProfile] = {
    "tikz": LatexProfile(
        key="tikz",
        # xcolor[svgnames,dvipsnames] FIRST, ahead of tikz (which pulls xcolor
        # in unoptioned via pgf).  xcolor is a once-only package, so loading it
        # first with the extended name sets wins -- exactly the chemfig
        # precedent.  Without it a body reaching for a CSS/SVG colour name
        # (CornflowerBlue, SteelBlue, ...) is a FATAL "Undefined color" for an
        # otherwise-valid diagram (D-004).  The lowercase spelling a model
        # habitually emits is normalised to this CamelCase form by
        # latex_color.normalize_colors before the compile.
        packages=(LatexPackage("xcolor", "svgnames,dvipsnames"), LatexPackage("tikz")),
        libraries=("arrows.meta", "positioning", "calc", "patterns"),
        wrap_env="tikzpicture",
        tl_packages=("pgf",),
        probe_files=("tikz.sty",),
    ),
    "circuitikz": LatexProfile(
        key="circuitikz",
        # siunitx is loaded because real-world circuit labels lean on \SI{}{};
        # it is declared in tl_packages so a missing install is reported.
        # xcolor[svgnames,dvipsnames] FIRST for the same reason as the tikz
        # profile: circuitikz pulls xcolor in unoptioned (via tikz/pgf), so a
        # CSS/SVG colour name in a wire/label colour is otherwise a FATAL
        # "Undefined color" (D-004).  Pre-loading with the name sets wins the
        # once-only package and adds no clash when circuitikz later requests it.
        packages=(
            LatexPackage("xcolor", "svgnames,dvipsnames"),
            LatexPackage("siunitx"),
            LatexPackage("circuitikz", "american"),
        ),
        # positioning provides the ``<key>=<dist> of <node>`` chained-placement
        # syntax (right/left/above/below ... of ...).  circuitikz builds on
        # TikZ but does NOT load positioning itself, so without this line a
        # body such as ``\node[adc, right=2.5cm of A]`` parses ``right=`` as a
        # bare PGF-math key and dies with "Unknown operator `of'" -- a fatal
        # abort with no image, for a construct a model very commonly emits to
        # lay out a block chain.  circuitikz ships in pgf/tikz, so the library
        # needs no extra distribution package.
        #
        # fit provides the ``fit=(A)(B)`` key used by ``\node[draw, dashed,
        # fit=...]`` to draw a bounding box around a set of named subcircuit
        # nodes/coordinates -- a plausible thing a model emits to annotate a
        # stage of a schematic.  Like positioning, circuitikz does NOT load it,
        # so without this line ``fit=(A)(B)`` dies with "I do not know the key
        # '/tikz/fit'" -- a fatal abort with no image.  Also ships in pgf, no
        # extra distribution package.  Structurally identical to the
        # positioning gap above.
        libraries=("positioning", "fit"),
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
        # Wider crop than the 2pt default: chemfig's \charge and \lewis place a
        # charge glyph / lone-pair dots OUTSIDE the atom's bounding box, which
        # the standalone class does not account for, so a tight 2pt crop slices
        # that satellite ink at the canvas edge (D-042).  6pt keeps the common
        # +/-45-degree charge/lone-pair placement inside the crop.  Scoped to
        # chemfig so the tighter crop of the other engines is unchanged.
        border="6pt",
    ),
    "tikz-cd": LatexProfile(
        key="tikz-cd",
        # xcolor[svgnames,dvipsnames] FIRST (see the tikz profile) so a CSS/SVG
        # colour name in a diagram label resolves instead of aborting (D-004).
        packages=(LatexPackage("xcolor", "svgnames,dvipsnames"), LatexPackage("tikz-cd")),
        wrap_env="tikzcd",
        tl_packages=("tikz-cd",),
        probe_files=("tikz-cd.sty",),
    ),
    "pgfplots": LatexProfile(
        key="pgfplots",
        # xcolor[svgnames,dvipsnames] FIRST (see the tikz profile) so a CSS/SVG
        # colour name in a plot, legend or axis style resolves instead of
        # aborting (D-004).  pgfplots pulls xcolor in unoptioned via tikz/pgf.
        packages=(LatexPackage("xcolor", "svgnames,dvipsnames"), LatexPackage("pgfplots")),
        # A model usually emits a bare \begin{axis}...\end{axis} (or the
        # semilog/loglog/polar variants).  All of these live INSIDE a
        # tikzpicture, so that is the wrap target.  ``axis`` is deliberately
        # NOT added to _DRAWING_ENVS: a body opening an axis still needs the
        # tikzpicture wrap, and a body that opens tikzpicture itself is
        # already passed through by the existing detection in _wrap.
        wrap_env="tikzpicture",
        # pgfplots is its own TeX Live package -- it does NOT ship in pgf.
        tl_packages=("pgfplots",),
        probe_files=("pgfplots.sty",),
        extra_preamble=(
            # Without a compat level pgfplots keeps pre-1.3 defaults (axis
            # labels placed against the outer box, old legend spacing) and
            # warns on every compile.  ``newest`` rather than a pinned number
            # so an older installed pgfplots never rejects the preamble for
            # naming a version it does not know.
            r"\pgfplotsset{compat=newest}",
            # Libraries a model commonly reaches for: fillbetween
            # (\addplot fill between), statistics (boxplots), polar
            # (polaraxis), dateplot (date coordinates), groupplots (small
            # multiples), smithchart (RF impedance loci).  All ship inside the
            # pgfplots distribution package, so probing pgfplots.sty proves
            # they are present too -- verified for smithchart specifically in
            # the 2026basic install this renderer targets
            # (pgfplots/libs/tikzlibrarypgfplots.smithchart.code.tex).
            r"\usepgfplotslibrary{fillbetween,statistics,polar,dateplot,groupplots,smithchart}",
        ),
    ),
    # Labelled trees: constituency/syntax trees, taxonomies, decision and game
    # trees, phylogenies.  graphviz draws *a* tree but not in the notation
    # these fields read (triangles over elided constituents, aligned leaves,
    # movement arrows), which is what forest exists for.
    "forest": LatexProfile(
        key="forest",
        # xcolor[svgnames,dvipsnames] FIRST (see the tikz profile): forest
        # pulls xcolor in unoptioned via pgf, and xcolor is once-only, so
        # without the pre-load a CSS/SVG colour name on a node is a FATAL
        # "Undefined color" for an otherwise valid tree (D-004).
        packages=(LatexPackage("xcolor", "svgnames,dvipsnames"), LatexPackage("forest")),
        # arrows.meta is load-bearing, not decoration.  forest permits plain
        # TikZ after the bracket (the tree's named nodes are in scope), which is
        # how movement/co-index arrows are drawn -- and a named tip is the
        # common way to write one.  Without this library ``\draw[-Stealth]``
        # dies with "Unknown arrow tip kind 'Stealth'": a fatal abort, no image.
        # positioning/calc accompany it for the same reason they do in the tikz
        # profile; all three ship in pgf, which is already declared below.
        libraries=("arrows.meta", "positioning", "calc"),
        wrap_env="forest",
        # forest is its own distribution package AND is built on pgf/tikz;
        # neither ships inside the other, so both are named so the install
        # hint is complete on a bare distribution.
        tl_packages=("forest", "pgf"),
        probe_files=("forest.sty",),
        # ``roof`` -- the triangle over an elided constituent -- is the single
        # strongest reason to prefer forest over graphviz for a syntax tree, and
        # it lives in forest-lib-linguistics, which forest does NOT auto-load.
        # Without this line ``[NP, roof [...]]`` dies in pgfkeys with "I do not
        # know the key '/tikz/roof'".  ``edges`` supplies forked/folder edges,
        # the other notation-specific idiom (verified not to alter the default
        # rendering of a plain tree).  Both libraries ship inside the forest
        # distribution package, so probing forest.sty proves they are present.
        # \useforestlibrary is preamble-only and requires forest already loaded,
        # which is why it is here rather than a package option.
        extra_preamble=(r"\useforestlibrary{linguistics,edges}",),
    ),
    # Proof trees: natural deduction, sequent calculus, typing rules.
    # bussproofs rather than ebproof because \AxiomC/\UnaryInfC is the syntax
    # LLMs actually emit, and because it is pure LaTeX box-building with no
    # pgf dependency -- so it compiles on a near-bare distribution.
    "bussproofs": LatexProfile(
        key="bussproofs",
        # xcolor for coloured inference labels and side conditions.  bussproofs
        # does not pull xcolor in itself, but the extended name sets are loaded
        # for the same D-004 reason as every other profile: a colour name in a
        # label should never be the thing that aborts a compile.
        packages=(
            LatexPackage("xcolor", "svgnames,dvipsnames"),
            LatexPackage("bussproofs"),
        ),
        wrap_env="prooftree",
        tl_packages=("bussproofs",),
        probe_files=("bussproofs.sty",),
        extra_preamble=(
            # WITHOUT THIS LINE NO BUSSPROOFS BODY COMPILES AT ALL.  bussproofs
            # defines prooftree as \begin{center}...\DisplayProof...\end{center},
            # and the standalone class typesets its body in an \hbox (LR mode),
            # where a center environment is illegal: every input whatsoever
            # aborts with "! LaTeX Error: Not allowed in LR mode."  Dropping the
            # center wrapper and keeping \DisplayProof -- which is bussproofs'
            # own box-producing form -- is legal in LR mode and additionally
            # crops tight: measured 281x46px here against 363x94px for the
            # \parbox/minipage remedy on identical ink, because those must be
            # given a fixed width that the crop then keeps.  standalone's
            # ``varwidth`` option would also work but requires varwidth.sty,
            # which is absent from a basic install, so it would trade this
            # failure for a missing-package one.
            #
            # Consequence worth knowing: a body that supplies its own
            # \DisplayProof now gets a second one appended and fails with
            # "Proof tree badly specified" (the stack is empty the second time).
            # The paired skill tells the model not to write it.
            r"\renewenvironment{prooftree}{}{\DisplayProof}",
            # bussproofs' sequent (no-C) commands -- \Axiom$...$, \UnaryInf$...$
            # -- align the premises on \fCenter, which the package DEFAULTS to
            # \relax (bussproofs.sty:335).  That default is worse than an error:
            # the antecedent and succedent abut with nothing between them, so
            # the proof renders, aligns correctly, and shows no turnstile.  A
            # body cannot repair it, because the renderer's prescan rejects
            # \def/\newcommand/\renewcommand outright, so the definition has to
            # come from here.  \mathrel gives it relational spacing, since these
            # commands place it inside math mode.
            r"\renewcommand{\fCenter}{\mathrel{\vdash}}",
        ),
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
