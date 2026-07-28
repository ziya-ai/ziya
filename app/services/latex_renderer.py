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
    get_profile,
    install_command,
    requires_position_marks,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20
MAX_BODY_CHARS = 64_000
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
PNG_DPI = 150

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
               fmt: str = "auto", use_cache: bool = True) -> RenderResult:
        """Render ``body`` for ``diagram_type`` to SVG or PNG."""
        started = time.monotonic()

        profile = get_profile(diagram_type)
        if profile is None:
            return RenderResult(
                ok=False, error_kind="internal",
                error=f"no LaTeX profile for diagram type {diagram_type!r}",
            )

        rejection = self.prescan(body)
        if rejection:
            logger.warning("LaTeX render rejected (%s): %s", diagram_type, rejection)
            return RenderResult(ok=False, error_kind="rejected", error=rejection)

        # Structural lint.  Runs after the security prescan (so a rejected body
        # is never rewritten) and before the cache key is computed, so the key
        # covers the body actually compiled.  Advisory only: a lint bug must
        # never turn a working render into a failure.
        lint_warnings: tuple[str, ...] = ()
        lint_fixes: tuple[str, ...] = ()
        if profile.key == "chemfig":
            body, lint_fixes, lint_warnings = self._lint_chemfig(body)

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
        if target == "svg" and not (cap.has_latex and cap.has_dvisvgm):
            target = "png"          # silently degrade rather than fail
        if target == "png" and not (cap.has_pdflatex and cap.has_ghostscript):
            return self._not_installed(profile, cap)

        document = profile.build_document(body, standalone=cap.has_standalone, fmt=target)
        key = self._cache_key(document, target)

        if use_cache:
            hit = self._cache_get(key, target)
            if hit is not None:
                return RenderResult(
                    ok=True, content=hit, fmt=target, cached=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    warnings=lint_warnings, autofixes=lint_fixes,
                )

        result = self._compile(document, target, cap)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        result.warnings = lint_warnings
        result.autofixes = lint_fixes
        if result.ok and use_cache:
            self._cache_put(key, target, result.content)
        return result

    @staticmethod
    def _lint_chemfig(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        """Close unambiguously-short chemfig rings; warn about the rest.

        Wrapped in a blanket except because this is a convenience check on
        model-authored input: any defect in the lint itself must degrade to
        "render the body as written", never to a failed render.
        """
        try:
            from app.utils.chemfig_lint import autofix

            fixed, applied, warnings = autofix(body)
            for note in applied:
                logger.info("chemfig autofix: %s", note)
            for note in warnings:
                logger.warning("chemfig lint: %s", note)
            return fixed, applied, warnings
        except Exception:                      # pragma: no cover - defensive
            logger.exception("chemfig lint failed; rendering body unchanged")
            return body, (), ()

    def _not_installed(self, profile: LatexProfile, cap: Capability,
                       missing: tuple[str, ...] = ()) -> RenderResult:
        """Build the actionable not-installed result."""
        needed = list(missing) or list(profile.tl_packages)
        # Optional packages are appended to the install line but never to the
        # missing set: installing them alongside is strictly better (chemfig
        # without mhchem cannot typeset \ce{} equations), yet their absence must
        # not be reported as the reason a render failed.
        needed.extend(profile.optional_tl_packages)
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

    def _compile(self, document: str, target: str, cap: Capability) -> RenderResult:
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
            # signal rather than by inspecting the body, so it serves every
            # construct with that need (position marks, \label/\ref, tikz-cd
            # overlays) without the renderer having to enumerate them.
            step: Optional[str] = ""
            for _ in range(MAX_LATEX_PASSES):
                step = self._run(argv, tmpdir, cap)
                if step is None:
                    break
                if not log_path.exists():
                    break
                if _RERUN_SIGNAL not in log_path.read_text(
                        encoding="utf-8", errors="replace"):
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
                conv = self._run(
                    ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pngalpha",
                     f"-r{PNG_DPI}", "-sOutputFile=doc.png", "doc.pdf"], tmpdir, cap)
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
            return (f"Unknown LaTeX command {undefined.group(1)} -- it may belong to a "
                    "package this diagram type does not load.")
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
    def _cache_key(document: str, fmt: str) -> str:
        digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
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
