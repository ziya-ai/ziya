"""
Server-side LaTeX diagram renderer.

Renders model-authored LaTeX (TikZ, CircuiTikZ, chemfig, ...) to SVG or PNG by
driving a local TeX installation.  Profile-agnostic: what to render is declared
in ``latex_profiles.py``; this module only compiles and converts.

Pipeline, preferred first:

    latex -no-shell-escape -> DVI -> dvisvgm --exact-bbox -> SVG
    pdflatex -no-shell-escape -> PDF -> gs -sDEVICE=pngalpha -> PNG

DVI/dvisvgm is preferred because dvisvgm reads DVI natively and ``--exact-bbox``
computes true ink bounds, which makes the ``standalone`` class optional and
yields selectable text plus theme-reactive recoloring in the browser.

SECURITY (F-027)
----------------
LaTeX is a Turing-complete language with filesystem access, so rendering
untrusted input is remote code execution by default.  Even though input here is
model-authored, prompt injection through any tool result (web page, wiki, search
hit) can induce an arbitrary document.  Three layers, each empirically verified:

1. ``-no-shell-escape`` defeats ``\\write18{...}`` (verified: silently ignored,
   no file created, render still succeeds).

2. ``sandbox-exec`` confines filesystem access.  This is load-bearing, not
   defense in depth: ``openin_any=p`` does NOT block ``\\input{/etc/passwd}``.
   Verified by A/B on identical input -- sandbox off leaks /etc/passwd as
   *typeset text* in the output PDF; sandbox on fails cleanly with
   "File `/etc/passwd.tex' not found".  With SVG output such a leak would be
   selectable text in the user's browser.

   The profile denies reads of sensitive trees and confines writes to the
   per-render temp dir.  An allow-list profile was tried first and aborts
   pdflatex before startup (SIGABRT), because a TeX tree cannot practically be
   enumerated; targeted denial is what works.

3. Deny-list prescan rejects known-dangerous constructs before compilation.
   Weaker than the sandbox (TeX's surface is large: ``\\lowercase{\\input}``,
   ``\\expandafter`` chains, ``\\@@input``) but it produces good error messages
   and still applies on platforms without a sandbox binary.

Plus: hard timeout with process-group kill (pdflatex spawns children and can
survive a naive terminate), and output size caps.
"""
from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.services.latex_profiles import (
    LatexProfile,
    TOOLCHAIN_TL_PACKAGES,
    charge_color_breaks_dvisvgm,
    downgrade_interp_shading,
    get_profile,
    install_command,
    requires_interp_shading,
    requires_position_marks,
)
from app.utils.latex_color import normalize_colors
from app.utils.latex_unicode import transliterate

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20
MAX_BODY_CHARS = 64_000
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
PNG_DPI = 150

#: Bounds on the rasterisation resolution when a caller requests explicit
#: width/height (D-006).  The floor keeps a "far below natural size" request
#: (the documented downscaling escape hatch) from collapsing to an unreadable
#: sub-pixel smear or a 0px image; the ceiling keeps a huge request from
#: producing a raster that blows MAX_OUTPUT_BYTES or the render budget.
MIN_PNG_DPI = 12
MAX_PNG_DPI = 600

#: Glyph-legibility floor for the fit-INSIDE-a-box case (D-007,
#: aspect-ratio-collapses-tick-labels).  When BOTH width and height are given
#: the caller is expressing a bounding box, not an exact dimension, and
#: fit-inside picks the tighter axis.  For an extreme-aspect drawing (a ~22:1
#: wide axis of rotated \tiny labels) the tight axis forces a DPI so low that
#: glyphs rasterise to 4-5px and no label is readable, even though the roomier
#: axis has abundant unused resolution.  Below this floor the fit is treated as
#: a crush and lifted to it (letting the constrained axis overflow the box --
#: a legible raster the viewer can scroll beats an in-box illegible one), but
#: NEVER above what the roomier axis itself allows, so a uniformly-small box
#: stays a small thumbnail (the honoured downscale contract).  Comfortably
#: below the 150 default (an in-box render is never inflated) and well above
#: the 12 DPI non-zero floor.  Applies ONLY when both dimensions are supplied;
#: a single-dimension request stays honoured literally (D-006 contract).
MIN_LEGIBLE_DPI = 96

#: Cap on LaTeX passes.  Position-mark documents need two; the cap exists
#: because a pathological document can request a rerun indefinitely and each
#: pass spends the full compile budget against one fixed request timeout.
MAX_LATEX_PASSES = 3
#: LaTeX's own request for another pass; driving the loop off this rather than
#: off body inspection covers \label/\ref and tikz-cd too, not just chemfig.
_RERUN_SIGNAL = "Rerun to get"

# A TeX control word ends at the first non-letter, so ``\openin1`` invokes the
# same primitive as ``\openin\z``.  ``\b`` cannot express that boundary: in
# ``\openin1`` both ``n`` and ``1`` are word characters, so there is no word
# boundary between them and the rule silently fails to match.  Every
# ``\b``-terminated rule below was bypassable by appending a digit
# (``\def1``, ``\catcode1=12``, ``\usepackage1{shellesc}``, ...).
_CS_END = r"(?![A-Za-z@])"

# Constructs rejected before compilation.  Each entry is (pattern, reason).
# Ordered most-specific first so the reported reason is the most informative.
_DENIED: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), reason)
    for pat, reason in (
        (r"\\write\s*18", "shell escape (\\write18)"),
        (r"\\immediate\s*\\write\s*18", "shell escape (\\write18)"),
        (r"\\(directlua|luaexec|latelua)" + _CS_END, "Lua execution"),
        (r"\\(input|include|InputIfFileExists|openin|openout|read|write)" + _CS_END,
         "file system access"),
        (r"\\special" + _CS_END, "raw driver access (\\special)"),
        (r"\\(usepackage|RequirePackage|documentclass)" + _CS_END,
         "package or class injection (the preamble is supplied by the profile)"),
        (r"\\(def|gdef|edef|xdef|let|newcommand|renewcommand)" + _CS_END,
         "macro definition (can construct unbounded expansion)"),
        (r"\\catcode" + _CS_END, "catcode manipulation (defeats other filters)"),
        (r"\\(csname|expandafter)" + _CS_END, "indirect macro construction"),
        (r"\\pdf(ximage|image|literal|obj)" + _CS_END, "raw PDF object access"),
        (r"\\(shipout|output)" + _CS_END, "output routine manipulation"),
    )
)

_SANDBOX_DENY_PATHS = ("/etc", "/private/etc", "/Users", "/var/root", "/root")


# -- multi-tree TeX package backfill (D-215) --------------------------------
#
# A machine can carry several TeX Live installs side by side, e.g.
# ``/usr/local/texlive/2023`` and ``/usr/local/texlive/2026basic``.  The active
# one is whichever ``kpsewhich`` resolves to.  A trimmed "basic" scheme omits
# packages that an older full tree still ships -- ``tikz-cd.sty`` is the concrete
# case: absent from 2026basic, present in 2023 -- so the profile probe reports
# the package missing and ``render()`` short-circuits with the install advisory
# before any pdflatex runs, producing zero pixels for every tikz-cd spec in BOTH
# themes.
#
# The fix does not require a network ``tlmgr install``.  For the specific
# packages listed below, we locate their (narrow, package-specific) directories
# in a co-installed fuller sibling tree -- or in an explicit
# ``ZIYA_LATEX_EXTRA_TEXINPUTS`` override -- and prepend just those directories
# to ``TEXINPUTS`` for BOTH the capability probe (``_kpsewhich``) and the
# compile (``_run``).  A trailing empty ``TEXINPUTS`` element preserves the
# compiled-in default search path, and because only the tikz-cd package
# directories are added (not a whole foreign tree) nothing else is shadowed --
# every package present in the active tree still resolves there unchanged.

# probe filename -> directories (relative to a ``texmf-dist`` root) that, taken
# together, satisfy the package.  ``tikz-cd`` needs BOTH its wrapper .sty
# (tex/latex/tikz-cd) and its generic library dir (tex/generic/tikz-cd), or the
# .sty would load but ``\usetikzlibrary{cd}`` would still abort the compile.
_TEXINPUTS_BACKFILL: dict[str, tuple[str, ...]] = {
    "tikz-cd.sty": (
        "tex/latex/tikz-cd",
        "tex/generic/tikz-cd",
    ),
}


def _sibling_texmf_dist_roots() -> tuple[str, ...]:
    """``texmf-dist`` roots of TeX Live editions OTHER than the active one.

    Derives the TeX Live root from the resolved ``kpsewhich`` (or ``pdflatex``)
    location -- ``<root>/<edition>/bin/<arch>/kpsewhich`` -- then globs sibling
    editions under the same root, excluding the active edition.  Best-effort:
    any error yields an empty tuple, so a single-tree or non-TeX-Live install
    behaves exactly as before.
    """
    launcher = shutil.which("kpsewhich") or shutil.which("pdflatex")
    if not launcher:
        return ()
    try:
        real = Path(os.path.realpath(launcher))
        # real == <edition>/bin/<arch>/kpsewhich
        active_edition = real.parent.parent.parent
        root = active_edition.parent
        found: list[str] = []
        for dist in sorted(root.glob("*/texmf-dist")):
            if dist.parent == active_edition:
                continue                          # active tree is already default
            if dist.is_dir():
                found.append(str(dist))
        return tuple(found)
    except (OSError, IndexError):
        return ()


@functools.lru_cache(maxsize=1)
def _supplemental_texinputs() -> tuple[str, ...]:
    """Package-specific directories to prepend to ``TEXINPUTS``, if any.

    Two sources, in priority order:

      1. ``ZIYA_LATEX_EXTRA_TEXINPUTS`` -- an ``os.pathsep``-separated operator
         override of directories to expose verbatim (added if they exist).
      2. Auto-discovery -- for each backfilled package that is ABSENT from the
         active tree, the matching directories from the first sibling tree that
         actually ships it (kept version-consistent by taking all of a package's
         dirs from the same tree).

    Memoised; call ``cache_clear()`` after changing the environment.  Returns an
    empty tuple when there is nothing to backfill, so the common single-tree
    install is untouched.
    """
    dirs: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if path and path not in seen and os.path.isdir(path):
            seen.add(path)
            dirs.append(path)

    # 1. Explicit operator override (highest priority).
    override = os.environ.get("ZIYA_LATEX_EXTRA_TEXINPUTS", "")
    for p in override.split(os.pathsep):
        _add(p.strip())

    # 2. Auto-discovery of sibling-tree package dirs for missing packages.
    roots = _sibling_texmf_dist_roots()
    if roots:
        for probe, reldirs in _TEXINPUTS_BACKFILL.items():
            for root in roots:
                sty_dir = os.path.join(root, *reldirs[0].split("/"))
                if os.path.isfile(os.path.join(sty_dir, probe)):
                    for rel in reldirs:
                        _add(os.path.join(root, *rel.split("/")))
                    break                         # first sibling with it wins

    return tuple(dirs)


def _augment_texinputs(env: dict) -> dict:
    """Return ``env`` with supplemental package dirs prepended to ``TEXINPUTS``.

    No-op (returns the same mapping) when there is nothing to backfill.  When
    extras exist they are prepended (so the package-specific dir is found first)
    and a trailing empty element is guaranteed so the compiled-in default search
    path -- the active tree -- is still consulted.
    """
    extras = _supplemental_texinputs()
    if not extras:
        return env
    out = dict(env)
    parts = list(extras)
    existing = out.get("TEXINPUTS", "")
    if existing:
        parts.append(existing)
    joined = os.pathsep.join(parts)
    if not joined.endswith(os.pathsep):
        joined += os.pathsep          # trailing empty -> compiled-in defaults
    out["TEXINPUTS"] = joined
    return out


# Recovery preprocessing (F-...): the wrapper shapes a model commonly emits
# around a diagram body.  These are stripped BEFORE the security prescan so the
# commonest legitimate outputs -- a full ``\documentclass`` document, a body
# with ``\usepackage`` lines prepended, a markdown code fence -- are recovered
# to a bare body instead of being hard-rejected by the denylist (which
# otherwise fires on ``\usepackage``/``\documentclass`` before any stripping).
#
# This does NOT weaken the security posture: whatever survives stripping is
# still scanned, so a ``\usepackage`` or ``\input`` sitting inside the diagram
# body (rather than a recognised preamble/wrapper) is rejected exactly as
# before.  The profile always supplies the real preamble, so a discarded author
# preamble costs nothing.
_DOCUMENT_BODY_RE = re.compile(
    r"\\begin\s*\{document\}(.*?)\\end\s*\{document\}", re.DOTALL)
_PREAMBLE_LINE_RE = re.compile(
    r"^[ \t]*\\(?:documentclass|usepackage|RequirePackage|usetikzlibrary)\b[^\n]*\n",
    re.MULTILINE,
)

#: Picture-level environments the PROFILE supplies around the body (see
#: latex_profiles ``wrap_env`` / ``_DRAWING_ENVS``).  When a model emits an
#: orphan ``\end{<env>}`` for one of these with no matching ``\begin`` -- the
#: off-by-one "close what I did not open" slip (D-005, circuitikz-w4-10) -- the
#: profile then wraps the body in its own ``\begin/\end`` and the stray extra
#: ``\end`` aborts with a mismatched-environment error.  Stripping the orphan
#: trailing ``\end`` lets the wrap succeed.  A body that supplies BOTH its own
#: ``\begin`` and ``\end`` is already passed through unwrapped by the profile,
#: so it has balanced counts here and is left untouched.
_PICTURE_ENVS: tuple[str, ...] = ("tikzpicture", "circuitikz", "tikzcd", "chemfig")

#: A body-level ``\usetikzlibrary{...}`` request.  ``_sanitize_input`` strips
#: such preamble lines, but the profile emits only its OWN libraries, so a
#: shape from a stripped library (``\node[diamond]`` after a stripped
#: ``\usetikzlibrary{shapes.geometric}``) is then an undefined key -- fatal
#: (D-005, tikz-w4-10).  ``_extract_requested_libraries`` collects these names
#: so ``render`` can merge them into the profile preamble.
_USETIKZLIBRARY_RE = re.compile(r"\\usetikzlibrary\s*\{([^}]*)\}")

#: Profiles whose preamble is TikZ-based and can therefore accept an extra
#: ``\usetikzlibrary`` line merged in from a body request.
_TIKZ_FAMILY: frozenset = frozenset(
    {"tikz", "tikz-cd", "pgfplots", "circuitikz", "forest"})


def _strip_orphan_picture_ends(text: str) -> str:
    """Remove an orphan trailing ``\\end{<picture env>}`` (D-005).

    For each picture-level environment the profile wraps, when the body carries
    more ``\\end{env}`` than ``\\begin{env}`` (the model closed an environment
    it did not open), the excess trailing ``\\end{env}`` tokens are removed so
    the profile's own wrap is not left with a dangling extra ``\\end``.  Purely
    subtractive and balanced-count-guarded: a body with matched begin/end (or
    none at all) is returned byte-for-byte unchanged.
    """
    for env in _PICTURE_ENVS:
        begins = re.findall(r"\\begin\s*\{" + env + r"\}", text)
        ends = list(re.finditer(r"\\end\s*\{" + env + r"\}", text))
        excess = len(ends) - len(begins)
        if excess <= 0:
            continue
        # Remove the last ``excess`` \end{env} occurrences, right-to-left so the
        # earlier match offsets stay valid.
        for m in reversed(ends[-excess:]):
            text = text[:m.start()] + text[m.end():]
    return text


@dataclass
class Capability:
    """Result of probing the local toolchain.

    ``available`` means *some* render path exists.  A profile can still be
    unrenderable when its own packages are absent, which is reported separately
    so the notice can name exactly what to install.
    """
    has_latex: bool = False
    has_pdflatex: bool = False
    has_dvisvgm: bool = False
    has_ghostscript: bool = False
    has_sandbox: bool = False
    has_standalone: bool = False
    tex_distribution: str = ""
    missing_toolchain: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return (self.has_latex and self.has_dvisvgm) or (
            self.has_pdflatex and self.has_ghostscript
        )

    @property
    def preferred_format(self) -> str:
        if self.has_latex and self.has_dvisvgm:
            return "svg"
        if self.has_pdflatex and self.has_ghostscript:
            return "png"
        return ""


@dataclass
class RenderResult:
    """Outcome of a render attempt.

    Exactly one of ``content`` / ``error`` is meaningful.  ``install_hint`` is
    populated when the failure is a missing installation rather than bad input,
    letting the UI show actionable instructions instead of a TeX error.

    ``warnings`` is the exception to that split: it is populated on SUCCESS.  A
    structurally wrong chemfig ring still compiles -- it renders as an open
    chain, so the output is a picture of a different molecule with no error
    anywhere in the pipeline.  Advisory warnings on a successful render are the
    only channel that can surface it.
    """
    ok: bool
    content: bytes = b""
    fmt: str = ""
    error: str = ""
    error_kind: str = ""       # rejected | not_installed | compile | timeout | internal
    install_hint: str = ""
    missing_packages: tuple[str, ...] = ()
    log_excerpt: str = ""
    duration_ms: int = 0
    cached: bool = False
    warnings: tuple[str, ...] = ()
    autofixes: tuple[str, ...] = ()


class LatexRenderer:
    """Compiles LaTeX diagram bodies to SVG/PNG with a content-hash cache."""

    def __init__(self, cache_dir: Optional[Path] = None,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self._cache_dir = cache_dir or Path(tempfile.gettempdir()) / "ziya-latex-cache"
        self._timeout = timeout
        self._capability: Optional[Capability] = None

    # -- capability probing -------------------------------------------------

    def probe(self, refresh: bool = False) -> Capability:
        """Detect the local toolchain.  Cached; pass ``refresh`` to re-run."""
        if self._capability is not None and not refresh:
            return self._capability

        cap = Capability(
            has_latex=bool(shutil.which("latex")),
            has_pdflatex=bool(shutil.which("pdflatex")),
            has_dvisvgm=bool(shutil.which("dvisvgm")),
            has_ghostscript=bool(shutil.which("gs")),
            has_sandbox=bool(shutil.which("sandbox-exec")),
            has_standalone=self._kpsewhich("standalone.cls"),
        )
        cap.tex_distribution = self._tex_version()

        missing: list[str] = []
        if not cap.has_dvisvgm:
            missing.append("dvisvgm")
        if not cap.has_standalone:
            missing.append("standalone")
        cap.missing_toolchain = tuple(missing)

        self._capability = cap
        logger.info(
            "LaTeX toolchain probe: available=%s preferred=%s sandbox=%s missing=%s",
            cap.available, cap.preferred_format, cap.has_sandbox, cap.missing_toolchain,
        )
        return cap

    @staticmethod
    def _kpsewhich(filename: str) -> bool:
        """True when the TeX installation can resolve ``filename``.

        Preferred over parsing ``tlmgr list``: no network, no write access, and
        an order of magnitude faster.
        """
        if not shutil.which("kpsewhich"):
            return False
        try:
            proc = subprocess.run(
                ["kpsewhich", filename],
                capture_output=True, text=True, timeout=10,
                # D-215: consult sibling-tree package dirs so a package absent
                # from the active tree (tikz-cd on a "basic" scheme) but present
                # in an older sibling is found here too -- otherwise the probe
                # declares it missing and render() short-circuits before compile.
                env=_augment_texinputs(dict(os.environ)),
            )
            return proc.returncode == 0 and bool(proc.stdout.strip())
        except (subprocess.TimeoutExpired, OSError):
            return False

    @staticmethod
    def _tex_version() -> str:
        if not shutil.which("pdflatex"):
            return ""
        try:
            proc = subprocess.run(
                ["pdflatex", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return proc.stdout.splitlines()[0].strip() if proc.stdout else ""
        except (subprocess.TimeoutExpired, OSError, IndexError):
            return ""

    def missing_for_profile(self, profile: LatexProfile) -> tuple[str, ...]:
        """Distribution packages this profile needs but the system lacks."""
        missing = [
            tl for probe, tl in zip(profile.probe_files, profile.tl_packages)
            if not self._kpsewhich(probe)
        ]
        # A profile may list more distribution packages than probe files
        # (siunitx has no distinct .sty to probe under circuitikz); include the
        # remainder when any probe failed, so the install line is complete.
        if missing and len(profile.tl_packages) > len(profile.probe_files):
            missing.extend(profile.tl_packages[len(profile.probe_files):])
        return tuple(dict.fromkeys(missing))

    # -- validation --------------------------------------------------------

    @staticmethod
    def _sanitize_input(body: str) -> str:
        """Strip common wrapper shapes so a diagram body reaches the prescan bare.

        Recovers three shapes a model routinely emits, in order:

          1. a single enclosing markdown code fence (``\u0060\u0060\u0060latex ... \u0060\u0060\u0060``);
          2. a full document -- keep only what is between ``\\begin{document}``
             and ``\\end{document}`` (the profile supplies the real preamble);
          3. otherwise, preamble declarations (``\\documentclass`` /
             ``\\usepackage`` / ``\\usetikzlibrary`` / ``\\RequirePackage``)
             prepended before the body.

        Purely subtractive and best-effort: if stripping would empty the body,
        the original is returned so behaviour degrades to "render as written".
        Runs before the security prescan, and only removes recognised
        preamble/wrapper text -- anything else (including a ``\\usepackage`` or
        ``\\input`` sitting inside the diagram body) is left for the prescan to
        judge, so the deny-list still governs genuine in-body injection.
        """
        text = body.strip()

        # 1. Unwrap a single enclosing markdown code fence.
        if text.startswith("```"):
            newline = text.find("\n")
            if newline != -1:
                text = text[newline + 1:]
            trimmed = text.rstrip()
            if trimmed.endswith("```"):
                text = trimmed[:-3]
            text = text.strip()

        # 2. Full document: keep only the body between the document markers.
        match = _DOCUMENT_BODY_RE.search(text)
        if match:
            text = match.group(1).strip()
        else:
            # 3. Prepended preamble lines (no document environment).
            text = _PREAMBLE_LINE_RE.sub("", text).strip()

        # 4. Orphan trailing ``\end{<picture env>}`` (the "close what I did not
        #    open" off-by-one).  Removed so the profile's own wrap is not left
        #    with a dangling extra ``\end`` (D-005).
        text = _strip_orphan_picture_ends(text)

        return text or body

    @staticmethod
    def _extract_requested_libraries(body: str) -> tuple[str, ...]:
        """TikZ libraries the body asks for via ``\\usetikzlibrary{...}``.

        Scanned from the ORIGINAL body (before ``_sanitize_input`` strips the
        preamble line) so the requested libraries can be merged into the
        profile preamble instead of being silently dropped -- otherwise a shape
        from that library (``\\node[diamond]``) is an undefined key and aborts
        the compile (D-005, tikz-w4-10).  De-duplicated, order preserved.
        """
        libs: list[str] = []
        for m in _USETIKZLIBRARY_RE.finditer(body):
            for lib in m.group(1).split(","):
                lib = lib.strip()
                if lib and lib not in libs:
                    libs.append(lib)
        return tuple(libs)

    @staticmethod
    def prescan(body: str) -> Optional[str]:
        """Return a rejection reason for dangerous input, else None."""
        if len(body) > MAX_BODY_CHARS:
            return f"document too large ({len(body)} > {MAX_BODY_CHARS} chars)"
        for pattern, reason in _DENIED:
            match = pattern.search(body)
            if match:
                return f"{reason}: {match.group(0)!r} is not permitted"
        return None

    # -- rendering ---------------------------------------------------------

    def render(self, diagram_type: str, body: str,
               fmt: str = "auto", use_cache: bool = True,
               theme: str = "light",
               width: Optional[int] = None,
               height: Optional[int] = None) -> RenderResult:
        """Render ``body`` for ``diagram_type`` to SVG or PNG.

        ``theme`` selects the raster (PNG) surface: ``dark`` bakes a dark page
        with light default ink, ``light`` a white page with dark ink.  Without
        it the renderer emitted black ink on a transparent background for both
        themes, so the same PNG shown on a dark panel was ~1.27:1 -- invisible.
        The SVG path ignores theme (the browser recolours it live).

        ``width``/``height`` are the caller's requested pixel bounds for the
        PNG raster -- the only escape hatch for dense or extreme-aspect layouts.
        They were previously dropped on the floor: the LaTeX path forwarded only
        type/definition/fmt/theme, so ``standalone`` cropped to the natural
        bounding box and every diagram rasterised at a fixed ``PNG_DPI`` (150)
        regardless of the request (D-006).  They now scale the pdf->png
        resolution so the tight crop is fit INSIDE the requested box (aspect
        ratio preserved -- the cropped drawing has no slack to distort).  The
        SVG path is resolution-independent, so they do not apply there.
        """
        started = time.monotonic()

        profile = get_profile(diagram_type)
        if profile is None:
            return RenderResult(
                ok=False, error_kind="internal",
                error=f"no LaTeX profile for diagram type {diagram_type!r}",
            )

        # Capture any body-level ``\usetikzlibrary{...}`` request BEFORE
        # sanitisation strips it, so a TikZ-family profile can merge the
        # requested libraries into its preamble (D-005, tikz-w4-10).  Scanned
        # on the raw body; harmless (empty) for a body that requests none.
        requested_libraries = self._extract_requested_libraries(body)

        # Recover common wrapper shapes (markdown fence, full document,
        # prepended preamble) BEFORE the security prescan, so a legitimate
        # ``\documentclass``/``\usepackage`` wrapper is stripped rather than
        # hard-rejected by the deny-list.  Subtractive only; in-body injection
        # still reaches the prescan below.
        body = self._sanitize_input(body)

        rejection = self.prescan(body)
        if rejection:
            logger.warning("LaTeX render rejected (%s): %s", diagram_type, rejection)
            return RenderResult(ok=False, error_kind="rejected", error=rejection)

        # Structural lint.  Runs after the security prescan (so a rejected body
        # is never rewritten) and before the cache key is computed, so the key
        # covers the body actually compiled.  Advisory only: a lint bug must
        # never turn a working render into a failure.
        # Colour-form + Unicode normalisation (D-004, D-005).  Applied to EVERY
        # LaTeX engine, after the security prescan (so a rejected body is never
        # rewritten) and BEFORE the per-profile lint (so a rewritten
        # ``fill={rgb,...}`` is already brace-protected when the circuitikz lint
        # scans option values, and a transliterated symbol is in place before
        # any ring/charge inspection).  Both are advisory and degrade to the
        # body unchanged on any fault, so neither can break a working render.
        pre_fixes: list[str] = []
        body, uni_fixes = transliterate(body)
        pre_fixes.extend(uni_fixes)
        body, colour_fixes = normalize_colors(body, theme=theme)
        pre_fixes.extend(colour_fixes)

        lint_warnings: tuple[str, ...] = ()
        lint_fixes: tuple[str, ...] = ()
        if profile.key == "chemfig":
            body, lint_fixes, lint_warnings = self._lint_chemfig(body)
        elif profile.key == "circuitikz":
            body, lint_fixes, lint_warnings = self._lint_circuitikz(body)
        elif profile.key in ("tikz", "tikz-cd", "pgfplots"):
            body, lint_fixes, lint_warnings = self._lint_tikz(body)
        elif profile.key == "fretboard":
            body, lint_fixes, lint_warnings = self._lint_fretboard(body)
        if pre_fixes:
            lint_fixes = tuple(pre_fixes) + tuple(lint_fixes)

        cap = self.probe()
        if not cap.available:
            return self._not_installed(profile, cap)

        missing = self.missing_for_profile(profile)
        if missing:
            return self._not_installed(profile, cap, missing)

        target = cap.preferred_format if fmt == "auto" else fmt
        # Position marks cannot survive the DVI path, so an SVG render would
        # succeed while silently omitting the arrow.  A mechanism diagram
        # missing its arrow is a wrong answer rather than a degraded one, so
        # prefer PNG and accept the loss of selectable text and dark-mode
        # recolouring.  Degrades silently: the diagram is correct, only its
        # theme reactivity differs, which is not worth interrupting the user.
        if requires_position_marks(body) and target == "svg" \
                and cap.has_pdflatex and cap.has_ghostscript:
            logger.info(
                "%s: forcing PNG, body uses pgf position marks which the "
                "dvisvgm driver cannot place correctly", profile.key,
            )
            target = "png"
        # A \color inside a \charge argument compiles to PDF but sends the
        # dvisvgm colour driver into an unbounded expansion ("TeX capacity
        # exceeded") on the DVI/SVG path -- verified against a live chemfig
        # install.  Like position marks, this is a driver limitation that
        # survives PDF but not DVI, so force PNG rather than returning a
        # compile failure for a body that renders perfectly as a raster.
        if target == "svg" and profile.key == "chemfig" \
                and charge_color_breaks_dvisvgm(body) \
                and cap.has_pdflatex and cap.has_ghostscript:
            logger.info(
                "chemfig: forcing PNG, body colours a \\charge argument which "
                "sends the dvisvgm colour driver into a runaway expansion",
            )
            target = "png"
        if target == "svg" and not (cap.has_latex and cap.has_dvisvgm):
            target = "png"          # silently degrade rather than fail
        # pgfplots shader=interp: the dvisvgm driver refuses it outright (a
        # fatal "surface shading ... is NOT available", hence "No pages of
        # output"), while pdflatex renders it.  Prefer the PNG path when the
        # caller left the format open; when SVG is pinned or PNG is impossible,
        # downgrade the shader and say so -- a flat surface beats no surface.
        if profile.key == "pgfplots" and requires_interp_shading(body):
            if target == "svg" and fmt == "auto" \
                    and cap.has_pdflatex and cap.has_ghostscript:
                logger.info(
                    "pgfplots: forcing PNG, body uses shader=interp which the "
                    "dvisvgm driver does not support",
                )
                target = "png"
            elif target == "svg":
                body, note = downgrade_interp_shading(body)
                if note:
                    lint_fixes = tuple(lint_fixes) + ("shader=interp -> shader=faceted",)
                    lint_warnings = tuple(lint_warnings) + (note,)
        if target == "png" and not (cap.has_pdflatex and cap.has_ghostscript):
            return self._not_installed(profile, cap)

        # Merge a body-requested \usetikzlibrary only into a TikZ-family
        # preamble; other profiles ignore it (a stray library line without
        # tikz loaded would itself fail).
        extra_libraries = (
            requested_libraries if profile.key in _TIKZ_FAMILY else ())
        document = profile.build_document(
            body, standalone=cap.has_standalone, fmt=target, theme=theme,
            extra_libraries=extra_libraries)
        # width/height only affect the PNG rasterisation resolution, not the
        # compiled document, so they must join the cache key or a second call
        # at a different requested size would be served the first size's PNG.
        raster_w = width if target == "png" else None
        raster_h = height if target == "png" else None
        key = self._cache_key(document, target, raster_w, raster_h)

        if use_cache:
            hit = self._cache_get(key, target)
            if hit is not None:
                return RenderResult(
                    ok=True, content=hit, fmt=target, cached=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    warnings=lint_warnings, autofixes=lint_fixes,
                )

        # pgf position marks (chemfig \chemmove, TikZ ``remember picture``)
        # resolve .aux-recorded coordinates and MUST get a second pass, which
        # pgf does not request via the log (D-043).  Force a floor of two.
        min_passes = 2 if requires_position_marks(body) else 1
        result = self._compile(document, target, cap, min_passes=min_passes,
                               width=raster_w, height=raster_h)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        result.warnings = lint_warnings
        result.autofixes = lint_fixes
        if result.ok and use_cache:
            self._cache_put(key, target, result.content)
        return result

    @staticmethod
    def _lint_chemfig(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        """Repair \\charge syntax and close unambiguously-short chemfig rings.

        Two independent fixers, run in this order because they address
        different constructs and the charge repair can turn a body that would
        not compile at all into one the ring lint can then usefully inspect.

        Wrapped in a blanket except because this is a convenience check on
        model-authored input: any defect in either fixer must degrade to
        "render the body as written", never to a failed render.  Each is
        guarded separately so a fault in one cannot cost the other.
        """
        applied: list[str] = []
        warnings: list[str] = []

        # HTML-entity decode (D-059).  A chemfig label pasted from a rich-text
        # source can carry HTML entities (``&amp;``, ``&lt;``, ``&#8594;``).  A
        # decoded ``&`` is a FATAL "Misplaced alignment tab" in a chemfig body,
        # and a numeric entity for a symbol never renders.  Decode named
        # entities to safe LaTeX and numeric entities to their character, then
        # re-run the Unicode transliteration so a decoded symbol (e.g. the
        # arrow from ``&#8594;``) routes through the maths fonts a minimal TeX
        # install ships.  Chemfig-scoped, NOT global: rewriting ``&`` would
        # corrupt a tikz-cd matrix, whose columns are ``&``-separated; chemfig
        # is the one profile where a bare ``&`` is always an error.  Run first
        # so the passes below see the decoded body.
        try:
            from app.utils.chemfig_lint import decode_entities

            body, entity_fixes = decode_entities(body)
            if entity_fixes:
                body, tl_fixes = transliterate(body)
                entity_fixes = tuple(entity_fixes) + tuple(tl_fixes)
            for note in entity_fixes:
                logger.info("chemfig entity decode: %s", note)
            applied.extend(entity_fixes)
        except Exception:                  # pragma: no cover - defensive
            logger.exception("chemfig entity decode failed; body unchanged")

        # Markdown-bold recovery (D-039, chemfig-w4-14).  A \chemname caption
        # pasted from a markdown source keeps its ``**bold**`` markers; with
        # nothing to convert them, chemfig typesets two literal asterisks around
        # the word.  ``convert_markdown_bold`` (added under an earlier group but
        # never wired into the pipeline -- it was only unit-tested, so the leak
        # persisted) rewrites ``**text**`` to ``\textbf{text}``.  It is
        # deliberately restricted to a run carrying no ring/branch/option/macro
        # character, so the aromatic ring opener ``**6(...)`` can never match.
        # Chemfig-scoped for the same reason as the entity decode: ``**`` has
        # other meanings elsewhere.  Run after the entity decode so a caption
        # carrying both is fully recovered.
        try:
            from app.utils.chemfig_lint import convert_markdown_bold

            body, bold_fixes = convert_markdown_bold(body)
            for note in bold_fixes:
                logger.info("chemfig markdown bold: %s", note)
            applied.extend(bold_fixes)
        except Exception:                  # pragma: no cover - defensive
            logger.exception("chemfig markdown bold failed; body unchanged")

        # Quoted-numeric recovery.  Model output frequently quotes a numeric
        # chemfig argument as if the source were JSON -- a ring size *"6"(, a
        # bond angle [:"30"], a setter dimension atom sep="2.4em".  Each is a
        # FATAL compile error as written (*"6"( aborts with "Missing number"),
        # and *"6"( in particular never matches the ring grammar so no other
        # lint step can even see the ring.  Run FIRST so the deprecated-setter,
        # charge and ring passes below all operate on unquoted numbers.
        try:
            from app.utils.chemfig_lint import unquote_numeric_fields

            body, unquote_fixes = unquote_numeric_fields(body)
            for note in unquote_fixes:
                logger.info("chemfig unquote: %s", note)
            applied.extend(unquote_fixes)
        except Exception:                  # pragma: no cover - defensive
            logger.exception("chemfig unquote failed; body unchanged")

        # Stray statement-terminator ``;`` (D-005, chemfig-w4-12).  A model
        # borrows the semicolon terminator from other diagram dialects
        # (``\chemfig{C=O};``); chemfig has no such operator, so a top-level
        # ``;`` typesets stray debris.  Drop the depth-0 ones; punctuation
        # inside a caption/label brace is kept.  Run before the ring lint so
        # its scan sees the cleaned body.
        try:
            from app.utils.chemfig_lint import strip_statement_terminators

            body, semi_fixes = strip_statement_terminators(body)
            for note in semi_fixes:
                logger.info("chemfig semicolon strip: %s", note)
            applied.extend(semi_fixes)
        except Exception:                  # pragma: no cover - defensive
            logger.exception("chemfig semicolon strip failed; body unchanged")

        # Deprecated-setter rewrite.  \setatomsep / \setbondoffset / \setdoublesep
        # and friends were removed from modern chemfig and are a FATAL
        # "Undefined control sequence" (which the log-parser then misattributes
        # to a missing package); the modern \setchemfig{key=value} form renders
        # identically.  Run first so the rest of the lint sees the repaired body.
        try:
            from app.utils.chemfig_lint import rewrite_deprecated_setters

            body, setter_fixes = rewrite_deprecated_setters(body)
            for note in setter_fixes:
                logger.info("chemfig setter rewrite: %s", note)
            applied.extend(setter_fixes)
        except Exception:                  # pragma: no cover - defensive
            logger.exception("chemfig setter rewrite failed; body unchanged")

        # Parameterised-submol argument syntax (D-030, chemfig-w3-08).  A model
        # trained on LaTeX ``\newcommand{\f}[1]{..}`` writes the chemfig submol
        # argument count as a bracket -- ``\definesubmol{arm}[1]{...#1...}`` --
        # and passes the argument in a bracket too -- ``!{arm}[30]``.  chemfig
        # wants a BARE digit count (``\definesubmol{arm}1{...}``) and a BRACE
        # argument (``!{arm}{30}``); as written ``#1`` is never substituted and
        # the literal ``#`` reaches pgfmath during an angle evaluation, a FATAL
        # ``Unknown operator `#'``.  Rewrite both to chemfig's real syntax.  Run
        # before the charge/ring passes so they see the substituted body.
        try:
            from app.utils.chemfig_lint import normalize_parameterised_submol

            body, submol_fixes = normalize_parameterised_submol(body)
            for note in submol_fixes:
                logger.info("chemfig submol arg: %s", note)
            applied.extend(submol_fixes)
        except Exception:                  # pragma: no cover - defensive
            logger.exception("chemfig submol arg fix failed; body unchanged")

        # \charge separator / math-mode repair.  Its failures are hard compile
        # errors whose messages name the wrong cause entirely, so repairing is
        # strictly better than reporting -- see app/utils/chemfig_charge.py.
        try:
            from app.utils.chemfig_charge import autofix as charge_autofix

            body, charge_fixes, _ = charge_autofix(body)
            for note in charge_fixes:
                logger.info("chemfig charge repair: %s", note)
            applied.extend(charge_fixes)
        except Exception:                  # pragma: no cover - defensive
            logger.exception("chemfig charge repair failed; body unchanged")

        # Ring-closure lint.  Unlike the charge repair, an under-specified ring
        # still COMPILES, so these warnings are the only signal a caller gets.
        try:
            from app.utils.chemfig_lint import autofix

            body, ring_fixes, ring_warnings = autofix(body)
            for note in ring_fixes:
                logger.info("chemfig autofix: %s", note)
            for note in ring_warnings:
                logger.warning("chemfig lint: %s", note)
            applied.extend(ring_fixes)
            warnings.extend(ring_warnings)
        except Exception:                      # pragma: no cover - defensive
            logger.exception("chemfig lint failed; rendering body unchanged")

        return body, tuple(applied), tuple(warnings)

    @staticmethod
    def _lint_circuitikz(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        """Brace pgfkeys-hostile option values in a circuitikz body.

        An unbraced ``=`` inside an option value (``l=$R_C=\\SI{2.2}{\\kilo
        \\ohm}$``) is split by pgfkeys before TeX evaluates it and aborts the
        whole compile with ``Extra }, or forgotten $`` -- a fatal error naming
        a cause the author never wrote.  The fix wraps such values in braces so
        pgfkeys treats them as opaque.

        Wrapped in a blanket except for the same reason as ``_lint_chemfig``:
        this is a convenience check on model-authored input, and any defect
        must degrade to "render the body as written", never to a failed render.
        """
        try:
            from app.utils.circuitikz_lint import autofix

            body, fixes, warnings = autofix(body)
            for note in fixes:
                logger.info("circuitikz autofix: %s", note)
            for note in warnings:
                logger.warning("circuitikz lint: %s", note)
            return body, fixes, warnings
        except Exception:                  # pragma: no cover - defensive
            logger.exception("circuitikz lint failed; rendering body unchanged")
            return body, (), ()

    @staticmethod
    def _lint_tikz(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        r"""Structural recovery pass for TikZ / tikz-cd (fix group G-06).

        Before this, ``tikz`` and ``tikz-cd`` matched neither the chemfig nor
        the circuitikz branch, so they had no structural preprocessor at all.
        This runs a small set of provably safe rewrites -- literal ``\n``
        restoration, a periodic ``mod(...,360)`` clamp on loop-derived trig
        arguments (output-preserving; avoids the pgfmath dimen overflow), and a
        ``\pgfmathparse`` -> ``\pgfmathsetmacro`` capture (fixes the
        node-coordinate clobber that silently prints the wrong number).

        Wrapped in a blanket except for the same reason as ``_lint_chemfig``:
        this is a convenience check on model-authored input, and any defect must
        degrade to "render the body as written", never to a failed render.
        """
        try:
            from app.utils.tikz_lint import autofix

            body, fixes, warnings = autofix(body)
            for note in fixes:
                logger.info("tikz autofix: %s", note)
            for note in warnings:
                logger.warning("tikz lint: %s", note)
            return body, fixes, warnings
        except Exception:                  # pragma: no cover - defensive
            logger.exception("tikz lint failed; rendering body unchanged")
            return body, (), ()

    @staticmethod
    def _lint_fretboard(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        r"""Input normalisation for the ``fretboard`` chord-diagram profile.

        The profile's ``\chord`` macro iterates its positions with pgffor, which
        splits only on commas, while every chord chart in existence writes the
        shape as the compact ``x02210``.  ``fretboard_lint`` rewrites compact
        and space-separated shapes to comma lists, expands bare ``Am x02210``
        lines into ``\chord`` calls, and turns ``#``/``♯``/``♭`` in a chord
        name into the text-safe ``{\sharp}``/``{\flat}`` (a bare ``#`` is a
        fatal macro-parameter error in text mode).

        Wrapped in a blanket except like the other lint passes: a defect here
        must degrade to "render the body as written", never to a failed render.
        """
        try:
            from app.utils.fretboard_lint import normalize_fretboard

            body, fixes = normalize_fretboard(body)
            for note in fixes:
                logger.info("fretboard autofix: %s", note)
            return body, fixes, ()
        except Exception:                  # pragma: no cover - defensive
            logger.exception("fretboard lint failed; rendering body unchanged")
            return body, (), ()

    def _not_installed(self, profile: LatexProfile, cap: Capability,
                       missing: tuple[str, ...] = ()) -> RenderResult:
        """Build the actionable not-installed result."""
        needed = list(missing) or list(profile.tl_packages)
        # Optional packages are appended to the install line but never to the
        # missing set: installing them alongside is strictly better (chemfig
        # without mhchem cannot typeset \ce{} equations), yet their absence must
        # not be reported as the reason a render failed.
        # Read through the effective view, not the raw field, so packages the
        # preamble loads for EVERY profile (siunitx) are named here too --
        # otherwise a guarded load degrades with no hint about how to fix it.
        needed.extend(profile.effective_optional_tl_packages)
        if not cap.available:
            needed.extend(TOOLCHAIN_TL_PACKAGES)
        needed.extend(cap.missing_toolchain)
        needed = list(dict.fromkeys(n for n in needed if n))

        if not cap.has_pdflatex and not cap.has_latex:
            # One command, not a distribution name and a package list the
            # user has to assemble: the installer prints the plan and runs it.
            error = ("No TeX installation found.  LaTeX diagrams require a local "
                     "TeX distribution (BasicTeX or TeX Live).  "
                     "Run: ziya-install-extras --latex")
        else:
            error = ("The LaTeX renderer is installed but missing packages "
                     f"required for {profile.key}.  Run: ziya-install-extras "
                     "--latex (or the tlmgr line below)")
        return RenderResult(
            ok=False, error_kind="not_installed", error=error,
            install_hint=install_command(needed), missing_packages=tuple(needed),
        )

    # -- toolchain ---------------------------------------------------------

    @staticmethod
    def _needs_another_pass(log_text: str, passes_done: int,
                            min_passes: int) -> bool:
        """Decide whether the LaTeX engine should be run again.

        Two independent triggers, OR'd:

        * ``passes_done < min_passes`` -- a floor set by the caller for bodies
          whose second-pass requirement is NOT announced in the log (pgf
          position marks; see ``_compile``).  Without this floor a chemfig
          ``\\chemmove`` body stopped after one pass and its arrow was
          misplaced/absent (D-043), because pgf's aux plumbing never prints the
          LaTeX rerun message below.
        * ``_RERUN_SIGNAL in log_text`` -- LaTeX's own "Rerun to get
          cross-references right" request (\\label/\\ref, tikz-cd overlays).

        Pure and side-effect free so the pass policy can be unit-tested without
        a TeX installation.  ``min_passes == 1`` reproduces the historical
        signal-only behaviour exactly.
        """
        if passes_done < min_passes:
            return True
        return _RERUN_SIGNAL in log_text

    @staticmethod
    def _pdf_media_box_points(pdf_path: Path) -> Optional[tuple[float, float]]:
        """Natural (width, height) of ``pdf_path`` in PostScript points.

        Parsed from the ``/MediaBox`` array, which ``standalone`` sets to the
        cropped drawing (content + border) and which appears in plaintext in
        the PDFs pdflatex emits.  Returns ``None`` on any parse failure so the
        caller degrades to the fixed default DPI rather than raising.
        """
        try:
            raw = pdf_path.read_bytes()
        except OSError:
            return None
        # /MediaBox [x0 y0 x1 y1]  (whitespace and the leading space are all
        # optional; values may be ints or reals, possibly negative).
        m = re.search(
            rb"/MediaBox\s*\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+"
            rb"(-?[\d.]+)\s+(-?[\d.]+)\s*\]",
            raw,
        )
        if not m:
            return None
        try:
            x0, y0, x1, y1 = (float(m.group(i)) for i in range(1, 5))
        except ValueError:
            return None
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w <= 0 or h <= 0:
            return None
        return w, h

    @classmethod
    def _raster_dpi(cls, pdf_path: Path,
                    width: Optional[int], height: Optional[int]) -> float:
        """Resolution (DPI) to rasterise ``pdf_path`` at.

        With no size request this is the fixed ``PNG_DPI`` -- byte-identical to
        the previous behaviour.  With a request, the natural point size is read
        from the PDF and the DPI is chosen so the cropped drawing is fit INSIDE
        the requested ``width`` x ``height`` box: for each supplied dimension
        ``dpi = target_px * 72 / natural_pt`` and the smaller (fit-inside)
        value wins, so the aspect ratio of the tight crop is preserved and
        neither dimension overshoots.  Clamped to [MIN_PNG_DPI, MAX_PNG_DPI].

        Wide/tall-aspect crush (D-007): when BOTH dimensions are supplied the
        request is a bounding box, and for an extreme-aspect drawing the tight
        axis alone would force a fit-inside DPI so low that glyphs rasterise to
        a few pixels (the ~22:1 axis of rotated \\tiny tick labels) while the
        roomier axis has resolution to spare.  In that case the fit is lifted
        to ``MIN_LEGIBLE_DPI`` -- capped at what the roomier axis itself allows
        so a uniformly-small box stays a thumbnail -- and the constrained axis
        is allowed to overflow the box (a legible raster the viewer can scroll
        beats an in-box illegible one).  A SINGLE-dimension request is left
        exactly as before (honoured literally, no legibility lift), preserving
        the D-006 downscale contract; the no-request path is untouched.
        """
        if not width and not height:
            return float(PNG_DPI)
        size = cls._pdf_media_box_points(pdf_path)
        if size is None:
            return float(PNG_DPI)
        w_pt, h_pt = size
        candidates: list[float] = []
        if width and width > 0:
            candidates.append(width * 72.0 / w_pt)
        if height and height > 0:
            candidates.append(height * 72.0 / h_pt)
        if not candidates:
            return float(PNG_DPI)
        dpi = min(candidates)
        # Legibility floor only for a genuine bounding box (both axes given):
        # rescue an extreme-aspect crush up to MIN_LEGIBLE_DPI, but never above
        # the roomier axis's own limit, so a deliberately small box is honoured
        # rather than inflated.  A single-dimension request keeps the literal
        # downscale behaviour (D-006).
        if width and width > 0 and height and height > 0:
            floor = min(float(MIN_LEGIBLE_DPI), max(candidates))
            dpi = max(dpi, floor)
        return max(float(MIN_PNG_DPI), min(float(MAX_PNG_DPI), dpi))

    def _compile(self, document: str, target: str, cap: Capability,
                 min_passes: int = 1,
                 width: Optional[int] = None,
                 height: Optional[int] = None) -> RenderResult:
        with tempfile.TemporaryDirectory(prefix="ziya-latex-") as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "doc.tex").write_text(document, encoding="utf-8")

            engine = "latex" if target == "svg" else "pdflatex"
            artifact = tmpdir / ("doc.dvi" if target == "svg" else "doc.pdf")
            argv = [engine, "-no-shell-escape", "-interaction=nonstopmode",
                    "-halt-on-error", "doc.tex"]
            log_path = tmpdir / "doc.log"

            # Rerun until LaTeX stops asking, bounded by MAX_LATEX_PASSES.
            # Driven by LaTeX's own "Rerun to get cross-references right"
            # signal (for \label/\ref, tikz-cd overlays, ...) PLUS a
            # ``min_passes`` floor for constructs whose second-pass need is not
            # announced by that string.
            #
            # pgf "position marks" (chemfig \chemmove electron-pushing arrows,
            # TikZ ``remember picture`` overlays) resolve coordinates recorded
            # in the .aux on the PREVIOUS run: on pass 1 the mark does not exist
            # yet, so the arrow is drawn from a default position (or not at all).
            # Crucially, pgf records those positions with its OWN aux plumbing
            # and does not emit LaTeX's "Rerun to get cross-references right"
            # message, so the signal-only loop stopped after a single pass and
            # the arrow was silently misplaced/absent (D-043).  The profile layer
            # already flags these bodies (requires_position_marks); render()
            # turns that into min_passes>=2 so the documented mandatory second
            # pass actually happens.  The floor is output-preserving for every
            # other body (min_passes stays 1) and the pgf pass converges by
            # pass 2, so a forced rerun on an already-settled body is a no-op.
            step: Optional[str] = ""
            passes_done = 0
            for _ in range(MAX_LATEX_PASSES):
                step = self._run(argv, tmpdir, cap)
                passes_done += 1
                if step is None:
                    break
                if not log_path.exists():
                    break
                if not self._needs_another_pass(
                        log_path.read_text(encoding="utf-8", errors="replace"),
                        passes_done, min_passes):
                    break

            if step is None:
                return RenderResult(
                    ok=False, error_kind="timeout",
                    error=f"LaTeX compilation exceeded {self._timeout}s and was terminated",
                )

            log_text = ""
            if log_path.exists():
                log_text = log_path.read_text(encoding="utf-8", errors="replace")

            if not artifact.exists() or artifact.stat().st_size == 0:
                return RenderResult(
                    ok=False, error_kind="compile",
                    error=self._extract_error(log_text) or "LaTeX produced no output",
                    log_excerpt=self._tail(log_text),
                )

            if target == "svg":
                conv = self._run(
                    # No --no-fonts: it converts every glyph to a vector outline
                    # in <defs> referenced by <use>, which destroys selectable
                    # text and leaves nothing for enhanceSVGVisibility to
                    # recolour in dark mode.  Verified on real output: with the
                    # flag, <text>=0 and <use>=5; without it, <text>=1, <use>=0.
                    ["dvisvgm", "--exact-bbox", "--optimize",
                     "-o", "doc.svg", "doc.dvi"], tmpdir, cap)
                out = tmpdir / "doc.svg"
            else:
                # Scale the rasterisation resolution to fit the caller's
                # requested pixel box (D-006).  With no request this is exactly
                # the previous fixed PNG_DPI; with one, the tight standalone
                # crop is fit INSIDE width x height, aspect preserved.
                dpi = self._raster_dpi(artifact, width, height)
                conv = self._run(
                    ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pngalpha",
                     f"-r{dpi:g}", "-sOutputFile=doc.png", "doc.pdf"], tmpdir, cap)
                out = tmpdir / "doc.png"

            if conv is None:
                return RenderResult(ok=False, error_kind="timeout",
                                    error=f"{target.upper()} conversion timed out")
            if not out.exists() or out.stat().st_size == 0:
                return RenderResult(
                    ok=False, error_kind="compile",
                    error=f"{target.upper()} conversion produced no output",
                    log_excerpt=self._tail(conv),
                )

            size = out.stat().st_size
            if size > MAX_OUTPUT_BYTES:
                return RenderResult(
                    ok=False, error_kind="compile",
                    error=f"rendered output too large ({size} bytes)",
                )
            return RenderResult(ok=True, content=out.read_bytes(), fmt=target)

    def _run(self, argv: list[str], cwd: Path, cap: Capability) -> Optional[str]:
        """Run a toolchain step, sandboxed when possible.

        Returns combined output, or None if the step timed out.  The process is
        started in its own process group so a hung TeX and any children can be
        killed together -- a plain terminate() leaves them running.
        """
        if cap.has_sandbox:
            argv = ["sandbox-exec", "-p", self._sandbox_profile(cwd)] + argv

        env = dict(os.environ)
        env.update({
            "openin_any": "p",      # belt-and-braces; the sandbox is the real guard
            "openout_any": "p",
            "TEXMFOUTPUT": str(cwd),
            "SOURCE_DATE_EPOCH": "0",   # deterministic output -> cache hits
            "TEXMFVAR": str(cwd / ".texmf-var"),
        })
        # D-215: prepend the sibling-tree package dirs the probe located (e.g.
        # tikz-cd from an older tree) so the package is also resolvable at
        # compile time; a trailing empty element keeps the active tree (defaults)
        # in the search path, so nothing present is overridden.
        env = _augment_texinputs(env)

        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", start_new_session=True,
            )
        except OSError as exc:
            logger.error("LaTeX step failed to start (%s): %s", argv[0], exc)
            return ""

        try:
            out, _ = proc.communicate(timeout=self._timeout)
            return out or ""
        except subprocess.TimeoutExpired:
            logger.warning("LaTeX step timed out, killing process group: %s", argv[0])
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait(timeout=5)
            return None

    @staticmethod
    def _sandbox_profile(cwd: Path) -> str:
        """A sandbox-exec profile confining reads and writes.

        Deliberately deny-based.  An allow-list profile was tried first and
        aborts pdflatex before startup (SIGABRT) because the TeX tree, shared
        libraries and dyld cache cannot practically be enumerated.
        """
        denies = "\n".join(
            f'(deny file-read* (subpath "{p}"))' for p in _SANDBOX_DENY_PATHS
        )
        return f"""(version 1)
(allow default)
{denies}
(allow file-read* (subpath "{cwd}"))
(deny file-write*)
(allow file-write* (subpath "{cwd}"))
(allow file-write* (subpath "/private/tmp"))
(allow file-write* (subpath "/private/var/folders"))
(deny network*)
"""

    # -- log handling ------------------------------------------------------

    @staticmethod
    def _extract_error(log_text: str) -> str:
        """Pull the first actionable message out of a TeX log."""
        # Commands from optional packages otherwise surface as a bare
        # "Undefined control sequence", which gives the user no path forward.
        # Only mhchem is listed: \lewis comes from chemfig's own bundled
        # module, which the profile always loads when present, so a \lewis
        # failure is not a missing-package problem and must not claim to be.
        #
        # \pu is deliberately ABSENT from this table.  It is provided by
        # KaTeX's mhchem port (prose math) but NOT by the LaTeX mhchem package
        # -- verified: mhchem.sty v4.10 contains no definition of it.  Claiming
        # an install would fix it is simply wrong, so \pu falls through to the
        # generic message.
        _OPTIONAL_COMMANDS = {
            r"\ce": ("mhchem", "chemical equations"),
        }
        missing_file = re.search(r"! LaTeX Error: File `([^']+)' not found", log_text)
        if missing_file:
            return (f"LaTeX could not find `{missing_file.group(1)}'.  This usually "
                    "means a required package is not installed.")
        # TeX puts the offending command on the FIRST continuation line, in one
        # of several shapes: "<argument> \ce", "l.5 \notreal", "<recently read>
        # \foo".  Matching across a second newline (the previous ".*?\n" with
        # DOTALL) therefore never matched any real log -- verified against four
        # captured logs, all of which fell through to the generic branch, so
        # this whole hint had been dead code.  Stay within one line instead.
        #
        # Anchor to the END of that line, not the first command on it.  When an
        # expl3-based package (mhchem) is in the expansion stack, the line opens
        # with an internal macro and the user's actual mistake is last:
        #   \l__mhchem_cf_result_tl ...ipAfterAmount: \degree
        # Taking the first match reported "\l", which names nothing the user
        # wrote and sends them looking for a package that does not exist.
        #
        # Checked FIRST: an undefined chemfig INTERNAL (\CF_...) is never the
        # user's typo.  Known instance: chemfig 1.81 dropped \CF_expafter while
        # chemfig-lewis.tex still calls it (the profile preamble shims it; this
        # is the fallback should the shim not be in effect).  The generic regex
        # below cannot even match these names -- '_' is outside [A-Za-z@] -- so
        # the user was told to look for a typo in a body that had none.
        cf_internal = re.search(
            r"! Undefined control sequence\.\s*\n[^\n]*?(\\CF_[A-Za-z_]+)\s*$",
            log_text, re.MULTILINE)
        if cf_internal:
            return (f"chemfig internal macro {cf_internal.group(1)} is undefined. "
                    "This is a chemfig package bug, not an error in the diagram: "
                    "chemfig 1.81 (2026/09/01) removed this macro but its bundled "
                    "chemfig-lewis.tex still uses it. Ziya normally supplies the "
                    "missing definition; report this if you see it.")
        undefined = re.search(
            r"! Undefined control sequence\.\s*\n[^\n]*?(\\[A-Za-z@]+)\s*$",
            log_text, re.MULTILINE)
        if undefined:
            cmd = undefined.group(1)
            if cmd in _OPTIONAL_COMMANDS:
                pkg, feature = _OPTIONAL_COMMANDS[cmd]
                return (f"{cmd} requires the optional `{pkg}' package ({feature}), "
                        f"which is not installed.  Run: sudo tlmgr install {pkg}")
            return (f"Unknown or undefined command {undefined.group(1)} -- check for a "
                    "typo or a stray token, or a command from a package this diagram "
                    "type does not load.")
        # A non-Latin / unsupported Unicode codepoint aborts (pdf)LaTeX with a
        # TWO-LINE message whose SECOND line ("not set up for use with LaTeX")
        # carries the actual cause.  The generic "! (.+)" fallback below
        # captures only the first line, surfacing the sentence FRAGMENT
        # "LaTeX Error: Unicode character X (U+NNNN)" -- it ends mid-clause
        # with no verb, cause or remedy, verified against a live render of a
        # CJK label ("电流探针").  Stock (pdf)LaTeX has no font for scripts such
        # as CJK or emoji, so name the offending character and give the user an
        # actionable remedy instead.  Applies to every diagram type, not just
        # circuitikz.  Placed before the generic branch so the fragment never
        # wins.
        unicode_char = re.search(
            r"! LaTeX Error: Unicode character (.+?) \((U\+[0-9A-Fa-f]+)\)",
            log_text)
        if unicode_char:
            ch, code = unicode_char.group(1), unicode_char.group(2)
            return (f"The diagram contains the character {ch} ({code}), which the "
                    "LaTeX engine cannot typeset -- stock (pdf)LaTeX has no font for "
                    "non-Latin scripts such as Chinese, Japanese, Korean or emoji.  "
                    "Replace it with Latin text or a math label, or remove it.")
        first = re.search(r"^! (.+)$", log_text, re.MULTILINE)
        if first:
            return first.group(1).strip()
        return ""

    @staticmethod
    def _tail(text: str, lines: int = 40) -> str:
        if not text:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    # -- cache -------------------------------------------------------------

    @staticmethod
    def _cache_key(document: str, fmt: str,
                   width: Optional[int] = None,
                   height: Optional[int] = None) -> str:
        digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
        if width or height:
            # The same document rasterised at a different requested size is a
            # different artifact; fold the request into the key so the size is
            # never served from a stale entry.
            return f"{digest}.{width or 0}x{height or 0}.{fmt}"
        return f"{digest}.{fmt}"

    def _cache_get(self, key: str, fmt: str) -> Optional[bytes]:
        path = self._cache_dir / key
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError as exc:
            logger.debug("LaTeX cache read failed for %s: %s", key, exc)
        return None

    def _cache_put(self, key: str, fmt: str, content: bytes) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_dir / f".{key}.partial"
            tmp.write_bytes(content)
            tmp.replace(self._cache_dir / key)   # atomic; concurrent renders are safe
        except OSError as exc:
            logger.debug("LaTeX cache write failed for %s: %s", key, exc)


#: Process-wide instance.  The capability probe and cache are both shared.
latex_renderer = LatexRenderer()
