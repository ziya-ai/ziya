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
                       theme: str = "light",
                       extra_libraries: tuple[str, ...] = ()) -> str:
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
        else:
            # D-472: TeX Live 2026 pdfTeX writes the page dict -- and its
            # /MediaBox -- inside a compressed cross-reference object stream
            # (/ObjStm), so the renderer's plaintext /MediaBox scan finds
            # nothing, cannot learn the natural page size, and silently ignores
            # an explicit width/height request (a 4000x3000 ask rasterises at
            # the natural ~150dpi 224x175).  Disable object-stream compression
            # (\pdfobjcompresslevel=0) so the page dict stays an uncompressed
            # top-level object and /MediaBox is readable again; stream/content
            # compression (\pdfcompresslevel) is untouched, so the PDF is barely
            # larger.  Only for the PDF (raster) path -- the SVG path goes
            # through the DVI driver and never reads a MediaBox.
            lines.append("\\pdfobjcompresslevel=0\\relax")

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
        # siunitx ``\micro`` routes through the TS1 / text-companion glyph
        # ``\textmu`` (via siunitx's internal ``\SIUnitSymbolMicro``), whose
        # Type1 font ``tcrm*`` is ABSENT from a minimal TeX Live tree -- so
        # ``\SI{10}{\micro\farad}`` (microfarads: the single most common analog
        # unit, and the reason the circuitikz profile loads siunitx at all)
        # aborts the whole compile with "Font tcrm1000 at 600 not found" and
        # produces no image, for an otherwise valid schematic (D-056,
        # circuitikz-w1-03).  Redefine the one macro siunitx uses for the micro
        # sign so it renders from the MATHS fonts (cmmi's \mu, which every
        # install ships) instead of TS1 -- exactly the sidestep
        # latex_unicode.transliterate already uses for a raw U+00B5 / \textmu.
        #
        # Emitted after the siunitx load and guarded by ``\ifdefined`` so it is
        # a harmless no-op on a profile that never loads siunitx (or a host
        # without it).  ``\RenewDocumentCommand`` is a LaTeX-kernel primitive
        # (always present) and covers siunitx v2 AND v3: v3 removed the
        # ``math-micro``/``text-micro`` options but kept ``\SIUnitSymbolMicro``.
        # Structural, theme-independent fix (the failure never reaches the
        # rasteriser), so identical on the light and dark paths.
        lines.append(
            r"\ifdefined\SIUnitSymbolMicro"
            r"\RenewDocumentCommand{\SIUnitSymbolMicro}{}{\ensuremath{\mu}}\fi")
        # D-348: the \SIUnitSymbolMicro renew above covers siunitx v2, but
        # siunitx v3's \micro (and \ohm, \celsius, \degree) no longer route
        # through those symbol macros -- they emit the TS1 text-companion
        # glyphs \textmu / \textohm / \textcelsius / \textdegree directly,
        # whose Type1 font ``tcrm*`` is absent from the TeX Live basic tree and
        # CANNOT be generated on demand inside the render sandbox ("Font
        # tcrm1000 at 600 not found -> no output PDF"), fataling every schematic
        # with a microfarad / kilo-ohm value (circuitikz-w1-03).  Redefine the
        # offending UNITS themselves to their maths-font equivalents (cmmi/cmr
        # \mu, \Omega, \circ -- shipped by every install), so the TS1 companion
        # font is never touched regardless of the siunitx major version.
        # Guarded by \ifdefined\DeclareSIUnit so it is a no-op when siunitx is
        # absent, and emitted after the load so it wins over the defaults.
        lines.append(
            r"\ifdefined\DeclareSIUnit"
            r"\DeclareSIUnit\micro{\ensuremath{\mu}}"
            r"\DeclareSIUnit\ohm{\ensuremath{\Omega}}"
            r"\DeclareSIUnit\celsius{\ensuremath{{}^\circ}C}"
            r"\DeclareSIUnit\degree{\ensuremath{{}^\circ}}\fi")
        # Profile libraries plus any the body requested via a (now-stripped)
        # body-level \usetikzlibrary (D-005).  De-duplicated, profile order
        # first.  Emitted for a TikZ-family profile even when it declares no
        # libraries of its own (tikz-cd) so a body-requested library is not
        # lost.
        merged_libraries = list(self.libraries)
        for lib in extra_libraries:
            if lib and lib not in merged_libraries:
                merged_libraries.append(lib)
        if merged_libraries:
            lines.append("\\usetikzlibrary{" + ",".join(merged_libraries) + "}")
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
                # Per-engine dark remaps.  Some library-internal masks/fills
                # default to white (or black) and do NOT inherit the document
                # \color, so on the baked #1F1F1F page they render at the wrong
                # end of the surface.  Each override resolves FROM the theme
                # (the page #1F1F1F / ink #EDEDED just baked above) rather than
                # substituting an unrelated constant, and is emitted only on the
                # dark path so the light render is byte-identical.  Named
                # colours so the library keys can reference them; xcolor is
                # always loaded here (every TikZ-family profile and chemfig
                # pull it in).
                lines.append("\\definecolor{ziyathemepage}{HTML}{1F1F1F}")
                lines.append("\\definecolor{ziyathemeink}{HTML}{EDEDED}")
                if self.key == "tikz-cd":
                    # D-465: tikz-cd's ``background color`` (the double/equal
                    # arrow gap, the crossing-over preaction mask, and
                    # description-label fills) defaults WHITE, so #FFFFFF on the
                    # dark page = 1.17:1 -- equal signs collapse to one slab, a
                    # white bar erases a passing-behind crossing, and label text
                    # vanishes on a white patch.  Point it at the page colour so
                    # the gap/mask/fill match #1F1F1F.  #EDEDED ink on #1F1F1F =
                    # 14.08:1 dark; light is untouched (white gap on a white page
                    # is the correct invisible default).
                    lines.append("\\tikzcdset{background color=ziyathemepage}")
                elif self.key == "circuitikz":
                    # D-350: circuitikz open-terminal poles (``ocirc``) fill
                    # WHITE by default, so an open contact renders as a solid
                    # light dot indistinguishable from a filled ``*`` junction
                    # on the dark page -- open-terminal semantics lost.  Fill
                    # them with the page colour so the open ring reads as open
                    # again (the ring stroke is #EDEDED = 14.08:1 dark).  Light
                    # is untouched (white fill on a white page is the correct
                    # open look).
                    lines.append("\\ctikzset{open poles fill=ziyathemepage}")
                elif self.key == "chemfig":
                    # D-330: chemfig's Lewis lone-pair dots do NOT inherit the
                    # document \color -- they render #000000 (1.27:1 on #1F1F1F,
                    # invisible) while the bonds correctly pick up #EDEDED.  Set
                    # the tikzpicture default colour (chemfig draws structures in
                    # a tikzpicture) to the theme ink so the dots pick it up:
                    # #EDEDED on #1F1F1F = 14.08:1 dark.  An explicit body-level
                    # \color still wins, and light is untouched (black dots on a
                    # white page = 21:1).
                    lines.append(
                        "\\tikzset{every picture/.append style={color=ziyathemeink}}")
                # D-494: a pgf ``patterns`` fill tile is drawn in the pattern's
                # OWN default colour (black) -- the document \color does not
                # reach it -- so on the baked #1F1F1F page the pattern ink is
                # black at 1.27:1 (effectively invisible; only the auto-lifted
                # swatch borders survive).  Default the pattern colour to the
                # theme ink so the tiles read as #EDEDED on #1F1F1F = 14.08:1.
                # ``every path`` runs before a path's own options, so a body's
                # explicit ``pattern color=`` still wins, and the key is a
                # harmless no-op on a path that draws no pattern.  Emitted only
                # when the ``patterns`` library is actually loaded (profile
                # default or a body-level \usetikzlibrary), so a profile without
                # it never references an undefined key; dark-only, so the light
                # render (black tiles on white = 21:1) is byte-identical.
                if "patterns" in merged_libraries:
                    lines.append(
                        "\\tikzset{every path/.append style={pattern color=ziyathemeink}}")
            else:
                # D-357: a model frequently emits a self-contained "card" whose
                # background is an author-drawn DARK plate (``\fill[plate] ...
                # rectangle``) with light ink on top -- but the plate does not
                # cover the full drawing bbox, so leads/grounds spilling past it
                # land on the WHITE page still in that light plate-ink and
                # vanish (circuitikz-w4-*: #5FD4E4 on #FFFFFF = 1.75:1).  When a
                # SOLE dark plate is detected, match the light page to it so the
                # whole cropped canvas is the plate surface and the off-plate
                # ink stays legible (#F2F6FA -> 12.17:1, #5FD4E4 -> 7.57:1,
                # default ink #EDEDED -> 11.29:1 on #16324A); the plate rectangle
                # is then redundant but harmless.  The dark render is untouched
                # (it already passes: light ink on the #1F1F1F page, on or off
                # the plate), and a body WITHOUT such a plate keeps the plain
                # white page, so every other light render is byte-identical.
                plate_rgb = None
                try:
                    from app.utils.latex_color import detect_dark_plate
                    plate_rgb = detect_dark_plate(body)
                except Exception:          # pragma: no cover - defensive
                    plate_rgb = None
                if plate_rgb is not None:
                    r, g, b = plate_rgb
                    lines.append("\\pagecolor[RGB]{%d,%d,%d}" % (r, g, b))
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
        # D-466: the passthrough above matched a drawing-environment ``\begin``
        # ANYWHERE in the body, which misfires when the body's OUTER structure
        # is not itself a picture but a matrix row that NESTS one in a cell
        # (``L_{3} \arrow[r] & \begin{tikzcd}...``, tikz-cd-w2-06).  Treating
        # that as already-wrapped emits the outer row bare, so it lands in
        # horizontal text mode and aborts pre-raster with "Missing $ inserted".
        # If a drawing ``\begin`` is preceded, at brace/bracket depth 0, by
        # matrix-cell syntax (an unescaped ``&``, a ``\\`` row break, or a
        # ``\arrow``/``\ar`` command) then that ``\begin`` is nested and the
        # body still needs its own outer wrap -- so skip the passthrough.
        if not _drawing_env_is_nested(body, self.wrap_env):
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


def _drawing_env_is_nested(body: str, wrap_env: str) -> bool:
    """True when the first drawing ``\\begin`` sits behind matrix-cell content.

    The ``_wrap`` passthrough must fire only when the body's OUTER structure is
    itself a picture/matrix environment.  A body whose top level is a matrix row
    that nests a picture in a cell -- e.g. ``L_{3} \\arrow[r] & \\begin{tikzcd}
    ...`` (tikz-cd-w2-06) -- contains a drawing ``\\begin`` but is NOT already
    wrapped; passing it through drops it into horizontal text mode.  Detect that
    case by scanning the text before the first drawing ``\\begin`` and reporting
    any matrix-cell token (an unescaped ``&``, a ``\\\\`` row break, or a
    ``\\arrow``/``\\ar`` command) seen at brace/bracket depth 0 -- all of which
    are only legal INSIDE such an environment, so their presence before the
    ``\\begin`` proves it is nested.
    """
    envs = [wrap_env] + [e for e in _DRAWING_ENVS if e != wrap_env]
    m = re.search(r"\\begin\s*\{(" + "|".join(re.escape(e) for e in envs) + r")[*-]?\}", body)
    if not m:
        return False
    prefix = body[:m.start()]
    depth = 0
    i = 0
    n = len(prefix)
    while i < n:
        c = prefix[i]
        if c == "\\":
            if i + 1 < n and prefix[i + 1] == "\\":  # ``\\`` row break
                if depth == 0:
                    return True
                i += 2
                continue
            word = re.match(r"\\[A-Za-z@]+", prefix[i:])
            if word:
                if depth == 0 and word.group(0) in (r"\arrow", r"\ar"):
                    return True
                i += len(word.group(0))
                continue
            i += 2  # escaped single char (e.g. ``\&``, ``\%``) -- skip both
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth = max(0, depth - 1)
        elif c == "&" and depth == 0:
            return True
        i += 1
    return False


def _circuitikz_block_alias(name: str) -> str:
    """Make ``\\node[<name>]`` visible WITHOUT clobbering ``to[<name>]``.

    circuitikz defines ``amp``/``adc``/``dac``/``dsp`` as PATH-style bipole
    keys (``to[amp]``) and ships the node shapes under ``<name>shape``.  A bare
    ``\\node[amp]`` therefore runs the bipole setup outside a path and draws
    NOTHING (measured: 0 <path> elements; D-042).  The previous fix -- an
    unconditional ``amp/.style={<rectangle>}`` -- cured the node case by
    REPLACING the bipole key, so on a path ``to[amp]`` silently lost the
    amplifier symbol (1 <path>, the wire, instead of 2) and ``to[amp, l={LNA}]``
    died with ``I do not know the key '/tikz/l'`` because ``l`` is only valid
    inside a bipole.  Both measured against a live circuitikz.

    TikZ runs ``every to`` before a ``to[...]``'s own options, inside the
    path's TeX group, so a flag raised there is visible to the key on a path
    and has reverted by the time a later ``\\node[amp]`` runs (verified:
    ``to[amp] ... \\node[amp]`` draws both).  The original key body is copied
    aside under ``ziya-orig-<name>`` before being redefined; ``{#1}`` forwards
    a ``to[amp=label]`` value and ``\\pgfkeysnovalue`` when there is none.
    """
    return (
        rf"\pgfkeysgetvalue{{/tikz/{name}/.@cmd}}{{\ziyaorig}}"
        rf"\pgfkeyslet{{/tikz/ziya-orig-{name}/.@cmd}}{{\ziyaorig}}"
        rf"\tikzset{{{name}/.code={{\ifziyatopath"
        rf"\pgfkeysalso{{/tikz/ziya-orig-{name}={{#1}}}}"
        rf"\else\pgfkeysalso{{{name}shape}}\fi}}}}"
    )


_CIRCUITIKZ_BLOCK_ALIASES: tuple[str, ...] = (
    r"\newif\ifziyatopath"
    r"\tikzset{every to/.append style={/utils/exec=\ziyatopathtrue}}",
) + tuple(_circuitikz_block_alias(n) for n in ("amp", "adc", "dac", "dsp"))


#: The ``\chord`` macro behind the ``fretboard`` profile (guitar / ukulele /
#: bass chord-diagram boxes).
#:
#: Hand-rolled in TikZ rather than built on a chord package (guitarchordschemes,
#: gchords, songs, leadsheets) because NONE of those ship in the BasicTeX tree
#: this renderer targets, and every profile so far has been careful to need
#: nothing beyond pgf where it can help it.  ~60 lines of TikZ cost no
#: ``tlmgr install`` and render wherever the ``tikz`` profile does.
#:
#: ``\chord[opts]{Name}{positions}`` -- positions are a comma list low string to
#: high (``x`` muted, ``0`` open, ``n`` fret); the string COUNT is inferred, so
#: four entries draw a ukulele box and six a guitar.  The compact ``x02210``
#: form everybody actually writes is rewritten to the comma list beforehand by
#: ``app.utils.fretboard_lint`` (pgffor's ``\foreach`` only splits on commas).
#: Options: ``fret=<n>`` base fret (auto: 1 when the shape fits frets 1-4,
#: else the lowest fretted note, printed as "5fr"), ``barre=<fret>`` a bar
#: across every string stopped at that fret, ``fingers={...}`` digits printed
#: under the strings (0 = none), ``frets=<n>`` rows shown (auto), ``scale``.
#:
#: ``\sharp``/``\flat`` are re-bound inside the macro to ``\ensuremath`` forms
#: so a chord NAME can carry them in text mode (``F{\sharp}m``, ``B{\flat}``)
#: without a "Missing $" abort; the lint rewrites ``#``/``♯``/``♭`` to them.
#: Each box is its own tikzpicture followed by a small ``\hspace``, so several
#: ``\chord`` calls in one body line up as a chord row.  Verified by compiling
#: through the real renderer on both the DVI/SVG and PDF/PNG paths, light and
#: dark (tests/test_latex_fretboard.py pins the seams).
_FRETBOARD_PREAMBLE: tuple[str, ...] = (
    r"\pgfkeys{/ziyafret/.is family,/ziyafret,"
    r"fingers/.store in=\ziyafretfingers,fingers=,"
    r"fret/.store in=\ziyafretbase,fret=0,"
    r"barre/.store in=\ziyafretbarre,barre=0,"
    r"frets/.store in=\ziyafretcount,frets=0,"
    r"scale/.store in=\ziyafretscale,scale=1}",
    r"\newcommand{\chord}[3][]{%",
    r"\begingroup",
    r"\pgfkeys{/ziyafret,#1}%",
    r"\def\ziyaX{x}%",
    r"\let\ziyaSharp\sharp\let\ziyaFlat\flat",
    r"\def\sharp{\ensuremath{\ziyaSharp}}\def\flat{\ensuremath{\ziyaFlat}}%",
    # Pass 1 over the positions: string count and the lowest/highest fretted
    # note, which drive the automatic base fret and row count below.
    r"\xdef\ziyaN{0}\xdef\ziyaMin{99}\xdef\ziyaMax{0}%",
    r"\foreach \p [count=\i] in {#3}{\xdef\ziyaN{\i}%",
    r"  \ifx\p\ziyaX\else\ifnum\p>0",
    r"    \ifnum\p<\ziyaMin\relax\xdef\ziyaMin{\p}\fi",
    r"    \ifnum\p>\ziyaMax\relax\xdef\ziyaMax{\p}\fi",
    r"  \fi\fi}%",
    r"\ifnum\ziyafretbase<1",
    r"  \ifnum\ziyaMax>4 \ifnum\ziyaMin>1 \xdef\ziyafretbase{\ziyaMin}\else\xdef\ziyafretbase{1}\fi",
    r"  \else\xdef\ziyafretbase{1}\fi",
    r"\fi",
    r"\ifnum\ziyafretcount<1",
    r"  \pgfmathtruncatemacro{\ziyafretcount}{max(4,\ziyaMax-\ziyafretbase+1)}%",
    r"\fi",
    r"\pgfmathsetmacro{\ziyaW}{(\ziyaN-1)*0.5}%",
    r"\pgfmathsetmacro{\ziyaH}{\ziyafretcount*0.6}%",
    r"\begin{tikzpicture}[scale=\ziyafretscale,line cap=round,line join=round,"
    r"baseline=(current bounding box.north)]",
    r"  \node[anchor=south,font=\large\bfseries,inner sep=1pt] at (\ziyaW/2,0.62) {#2};",
    # Thick nut when the box starts at fret 1; otherwise a plain line and a
    # "Nfr" label to its right, as published chord charts print it.
    r"  \ifnum\ziyafretbase=1",
    r"    \draw[line width=2.2pt] (0,0) -- (\ziyaW,0);",
    r"  \else",
    r"    \draw[line width=0.6pt] (0,0) -- (\ziyaW,0);",
    r"    \node[anchor=west,font=\small,inner sep=2pt] at (\ziyaW,-0.3) {\ziyafretbase fr};",
    r"  \fi",
    r"  \foreach \k in {1,...,\ziyafretcount}{\draw[line width=0.6pt] (0,-\k*0.6) -- (\ziyaW,-\k*0.6);}",
    r"  \pgfmathtruncatemacro{\ziyaNm}{\ziyaN-1}%",
    r"  \foreach \s in {0,...,\ziyaNm}{\draw[line width=0.6pt] (\s*0.5,0) -- (\s*0.5,-\ziyaH);}",
    # Barre: a thick bar from the lowest to the highest string stopped at the
    # barre fret.  Drawn before the dots so the dots sit on top of it.
    r"  \ifnum\ziyafretbarre>0",
    r"    \xdef\ziyaBmin{99}\xdef\ziyaBmax{-1}%",
    r"    \foreach \p [count=\i from 0] in {#3}{%",
    r"      \ifx\p\ziyaX\else\ifnum\p=\ziyafretbarre\relax",
    r"        \ifnum\i<\ziyaBmin\relax\xdef\ziyaBmin{\i}\fi",
    r"        \ifnum\i>\ziyaBmax\relax\xdef\ziyaBmax{\i}\fi",
    r"      \fi\fi}%",
    r"    \ifnum\ziyaBmax>\ziyaBmin",
    r"      \pgfmathsetmacro{\ziyaBy}{-(\ziyafretbarre-\ziyafretbase+0.5)*0.6}%",
    r"      \draw[line width=5.5pt] (\ziyaBmin*0.5,\ziyaBy) -- (\ziyaBmax*0.5,\ziyaBy);",
    r"    \fi",
    r"  \fi",
    r"  \foreach \p [count=\i from 0] in {#3}{%",
    r"    \ifx\p\ziyaX",
    r"      \node[font=\small,inner sep=0pt] at (\i*0.5,0.28) {$\times$};",
    r"    \else\ifnum\p=0",
    r"      \draw[line width=0.6pt] (\i*0.5,0.28) circle (0.09);",
    r"    \else",
    r"      \pgfmathsetmacro{\ziyaY}{-(\p-\ziyafretbase+0.5)*0.6}%",
    r"      \fill (\i*0.5,\ziyaY) circle (0.13);",
    r"    \fi\fi}%",
    r"  \ifx\ziyafretfingers\empty\else",
    r"    \foreach \f [count=\i from 0] in \ziyafretfingers{%",
    r"      \ifnum\f>0 \node[font=\small,anchor=north,inner sep=2pt] at (\i*0.5,-\ziyaH) {\f};\fi}%",
    r"  \fi",
    r"\end{tikzpicture}\hspace{0.8em}%",
    r"\endgroup}",
)


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
        #
        # amp/adc/dac/dsp: a model writes both ``\node[amp]`` (which draws
        # nothing in stock circuitikz, D-042) and ``to[amp, l={LNA}]`` (a real
        # bipole).  The same ``/tikz/<name>`` key serves both, so the alias
        # must dispatch on context -- see _circuitikz_block_alias.
        extra_preamble=(
            r"\tikzset{quartz/.style={piezoelectric=#1},quartz/.default=,"
            r"crystal/.style={piezoelectric=#1},crystal/.default=,"
            r"xtal/.style={piezoelectric=#1},xtal/.default=}",
        ) + _CIRCUITIKZ_BLOCK_ALIASES,
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
            # chemfig 1.81 (2026/09/01) removed \CF_expafter and
            # \CF_swapunbrace from chemfig.tex but shipped an UNCHANGED
            # chemfig-lewis.tex that still calls \CF_expafter four times, so
            # every \lewis on >= 1.81 aborts with "Undefined control sequence"
            # -- verified against the CTAN sources and reproduced by stripping
            # the two defs from a 1.71 chemfig.tex.  Supply the 1.71
            # definitions (two lines, byte-for-byte) when chemfig has not.
            # Guarded so on <= 1.71 this is a no-op, and placed BEFORE the
            # chemfig-lewis.tex \input below.  chemfig defines its internals
            # with \catcode`\_=11, so the group matches that.  Drop once an
            # upstream release restores or replaces the macro.
            "\\begingroup\\catcode`\\_=11\n"
            "\\ifdefined\\CF_expafter\\else\n"
            "\\gdef\\CF_swapunbrace#1#2{#2#1}%\n"
            "\\gdef\\CF_expafter#1#2{\\expandafter\\CF_swapunbrace\\expandafter{#2}{#1}}%\n"
            "\\fi\\endgroup",
            r"\IfFileExists{chemfig-lewis.tex}{\input{chemfig-lewis.tex}}{}",
            # \chemname caption wider than the molecule it labels was cropped at
            # BOTH ends -> silent caption text loss (D-039, chemfig-w4-08 /
            # w4-14).  chemfig stacks the name under the molecule with
            # ``\CF_parsemolname``, which sets every caption line in
            # ``\hbox to\CF_wdstuffbox{\hss#1\hss}`` -- a box FIXED to the
            # MOLECULE width.  When the caption is wider, the ``\hss`` glue lets
            # it overflow the box symmetrically, so the enclosing ``\vtop``
            # still reports only the molecule width and the standalone crop
            # (a UNIFORM ``border``, which cannot grow one side) slices the
            # caption at both ends.  The content is fully recovered upstream
            # (entity decode + unicode transliteration) yet then truncated, so
            # the render "succeeds" while dropping text -- a structural failure.
            #
            # Redefine ONLY the name-line typesetter so a caption WIDER than the
            # molecule is set at its natural width (left-origin, so the ``\vtop``
            # grows to include it and the crop captures the whole caption); a
            # caption NARROWER than the molecule keeps the original centred
            # ``\hbox to\CF_wdstuffbox{\hss#1\hss}`` behaviour byte-for-byte, so
            # the common case (and the \chemname regression set) is unchanged.
            # chemfig sets ``\catcode`\_=11`` while defining its internals, so
            # the redefinition is wrapped in a matching catcode group; guarded
            # by ``\@ifundefined`` so a future chemfig without this internal
            # degrades to the stock (clipping) behaviour rather than erroring.
            "\\makeatletter\n"
            "\\newbox\\CFZIYAnamebox\n"
            "\\begingroup\n"
            "\\catcode`\\_=11\n"
            "\\@ifundefined{CF_parsemolname}{}{%\n"
            "\\gdef\\CF_parsemolname#1\\\\#2\\_nil{%\n"
            "\\setbox\\CFZIYAnamebox\\hbox{#1}%\n"
            "\\ifdim\\wd\\CFZIYAnamebox>\\CF_wdstuffbox\\relax\n"
            "\\hbox{#1}%\n"
            "\\else\n"
            "\\hbox to\\CF_wdstuffbox{\\hss#1\\hss}%\n"
            "\\fi\n"
            "\\CF_doifnotempty{#2}{\\CF_parsemolname#2\\_nil}%\n"
            "}%\n"
            "}%\n"
            "\\endgroup\n"
            "\\makeatother",
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
    # Fretted-instrument chord diagrams (guitar, ukulele, bass, banjo ...): the
    # dotted fretboard box every chord chart uses.  The VexFlow music renderer
    # has no primitive for these, so this is the one place they can come from.
    # Built on tikz alone -- see _FRETBOARD_PREAMBLE for why no chord package.
    "fretboard": LatexProfile(
        key="fretboard",
        # xcolor[svgnames,dvipsnames] FIRST (see the tikz profile) so a colour
        # name in a body-level \color resolves instead of aborting (D-004).
        packages=(LatexPackage("xcolor", "svgnames,dvipsnames"), LatexPackage("tikz")),
        # No wrap_env: each \chord opens its own tikzpicture, and several in a
        # body sit side by side in standalone's LR-mode box as a chord row.
        wrap_env=None,
        # Only pgf is needed -- the whole point of hand-rolling the macro.
        tl_packages=("pgf",),
        probe_files=("tikz.sty",),
        extra_preamble=_FRETBOARD_PREAMBLE,
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


#: pgfplots ``shader=interp`` (and ``faceted interp``) is refused outright by
#: the dvisvgm driver -- "surface shading (shader=interp) is NOT available for
#: the selected driver `pgfsys-dvisvgm.def'" -- and under ``-halt-on-error``
#: that is "No pages of output".  The same body compiles through pdflatex.
#: Third member of the survives-PDF-but-not-DVI family with position marks and
#: coloured \charge.  The lookahead keeps ``interpolate``-style keys out.
_INTERP_SHADER_RE = re.compile(r"shader\s*=\s*(?:faceted\s+)?interp(?![A-Za-z])")


def requires_interp_shading(body: str) -> bool:
    """True when ``body`` asks pgfplots for Gouraud (``interp``) shading.

    Such bodies must be routed to the PNG path; see downgrade_interp_shading
    for the SVG-only fallback.
    """
    try:
        return bool(_INTERP_SHADER_RE.search(body))
    except Exception:                      # pragma: no cover - defensive
        return False


def downgrade_interp_shading(body: str) -> tuple[str, Optional[str]]:
    """Rewrite ``shader=interp`` to ``shader=faceted`` for the SVG path.

    Used only when PNG is not possible (no pdflatex/ghostscript, or the caller
    pinned SVG): a flat-shaded surface is a degraded answer, a compile abort
    is no answer.  Returns the body unchanged with ``None`` when nothing needed
    rewriting, so callers can treat the note as the "did anything happen" flag.
    """
    try:
        out, n = _INTERP_SHADER_RE.subn("shader=faceted", body)
    except Exception:                      # pragma: no cover - defensive
        return body, None
    if not n:
        return body, None
    return out, (
        "shader=interp is not supported by the dvisvgm (SVG) driver; rendered "
        "with shader=faceted instead. Install pdflatex + ghostscript for the "
        "PNG path to get smooth shading."
    )

def get_profile(diagram_type: str) -> Optional[LatexProfile]:
    """Look up a profile by diagram type, case/whitespace insensitively."""
    if not diagram_type:
        return None
    return PROFILES.get(diagram_type.strip().lower())


def install_command(tl_packages: tuple[str, ...] | list[str]) -> str:
    """Build the ``tlmgr install`` line shown in the not-installed notice."""
    ordered = sorted(set(tl_packages))
    return "tlmgr install " + " ".join(ordered) if ordered else ""
