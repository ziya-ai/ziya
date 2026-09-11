"""
Optional-feature detection: what is missing, and the ONE command that fixes it.

Ziya's optional capabilities were installed by four unrelated recipes -- a pip
extra, a post-install ``playwright install chromium``, a brew+tlmgr shell
script nothing referenced, and a Poetry dependency *group* with no pip
spelling at all.  Every message that noticed a gap quoted a different one.
This module is the single source of truth both for detecting the gap and for
naming the remedy, so the startup banner, the tool gate, the LaTeX renderer
and the HTTP routes all point at ``ziya-install-extras``.

Detection is deliberately cheap and import-free at call time (no Playwright
driver start, no TeX subprocess): it is consulted while building the tool list
and on the startup banner.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

INSTALL_CMD = "ziya-install-extras"


def playwright_package_available() -> bool:
    """True when the ``playwright`` Python package imports."""
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


def _browsers_dir() -> Optional[Path]:
    """Where ``playwright install`` puts browsers on this machine.

    Mirrors Playwright's own registry rules: ``PLAYWRIGHT_BROWSERS_PATH``
    wins; the literal ``0`` means "inside the package"; otherwise a per-OS
    cache directory.
    """
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override == "0":
        try:
            import playwright
            return Path(playwright.__file__).resolve().parent / "driver" / "package" / ".local-browsers"
        except ImportError:
            return None
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "ms-playwright"


def chromium_browser_available() -> bool:
    """True when a Chromium build ``playwright install chromium`` produces is present.

    Playwright >= 1.49 launches ``headless=True`` on the ``chromium_headless_shell``
    build; older releases and ``channel="chromium"`` use ``chromium-*``.  The
    install command lays down both, so either directory counts.  ``pip install
    playwright`` alone leaves this False, which is the case the package-only
    check missed: the tool was offered and failed at launch with "Executable
    doesn't exist".
    """
    root = _browsers_dir()
    if root is None or not root.is_dir():
        return False
    try:
        for entry in root.iterdir():
            if entry.is_dir() and (
                entry.name.startswith("chromium-")
                or entry.name.startswith("chromium_headless_shell-")
            ):
                return True
    except OSError:
        return False
    return False


def playwright_status() -> Optional[str]:
    """None when headless rendering is usable; otherwise what is missing."""
    if not playwright_package_available():
        return "playwright package"
    if not chromium_browser_available():
        return "Chromium browser for Playwright"
    return None


def playwright_missing_description() -> str:
    """What to name in an error/log line when rendering has been REFUSED.

    ``playwright_status()`` is a query and correctly returns None when nothing
    is missing -- but a refusal site can reach here when the cached gate result
    disagrees with a fresh probe (a stale ``_playwright_available``), and
    interpolating None produced the log line "missing None."  Never returns
    None: by the time a caller is reporting, something was judged missing, and
    the browser is the piece pip cannot supply.
    """
    return playwright_status() or "Chromium browser for Playwright"


def tex_status() -> Optional[str]:
    """None when a usable TeX toolchain is present; otherwise what is missing.

    Mirrors ``latex_renderer.Capability.available``: DVI+dvisvgm or
    pdflatex+ghostscript.  Missing TeX *packages* are reported by the renderer
    itself at render time; this is only the toolchain.
    """
    has_dvi = bool(shutil.which("latex") and shutil.which("dvisvgm"))
    has_pdf = bool(shutil.which("pdflatex") and (shutil.which("gs") or shutil.which("ghostscript")))
    if has_dvi or has_pdf:
        return None
    if not (shutil.which("latex") or shutil.which("pdflatex")):
        return "TeX distribution"
    return "dvisvgm or ghostscript"


def browser_hint() -> str:
    return f"Run: {INSTALL_CMD} --browser"


def latex_hint() -> str:
    return f"Run: {INSTALL_CMD} --latex"


def missing_feature_lines() -> List[str]:
    """Human-readable lines for the startup banner.  Empty when nothing is missing."""
    lines: List[str] = []
    pw = playwright_status()
    if pw:
        lines.append(
            f"  • Diagram screenshots for the model, PDF export: missing {pw}."
        )
    tex = tex_status()
    if tex:
        lines.append(
            f"  • LaTeX diagrams (circuits, chemistry, pgfplots, TikZ): missing {tex}."
        )
    if lines:
        lines.append(f"  Install everything with:  {INSTALL_CMD}")
        lines.append(f"  (or one at a time: {INSTALL_CMD} --browser / --latex; --dry-run shows the plan)")
    return lines
