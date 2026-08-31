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
    get_profile,
    install_command,
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

        return text or body

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
        if target == "png" and not (cap.has_pdflatex and cap.has_ghostscript):
            return self._not_installed(profile, cap)

        document = profile.build_document(
            body, standalone=cap.has_standalone, fmt=target, theme=theme)
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
            error = ("No TeX installation found.  LaTeX diagrams require a local "
                     "TeX distribution (BasicTeX or TeX Live).")
        else:
            error = ("The LaTeX renderer is installed but missing packages "
                     f"required for {profile.key}.")
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
