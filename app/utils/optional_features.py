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


# The Linux route of app/scripts/install_extras.sh installs TeX Live under
# $HOME with no sudo: ZIYA_TEXLIVE_DIR, default ~/texlive, binaries in
# bin/<arch>/.  These two names must stay equal to the script's TL_HOME.
TEXLIVE_DIR_ENV = "ZIYA_TEXLIVE_DIR"
DEFAULT_TEXLIVE_DIR = "~/texlive"


def home_texlive_bin() -> Optional[Path]:
    """The bin/<arch> dir of a TeX Live the installer put under $HOME, or None."""
    root = Path(os.environ.get(TEXLIVE_DIR_ENV) or os.path.expanduser(DEFAULT_TEXLIVE_DIR))
    try:
        candidates = sorted((root / "bin").iterdir())
    except OSError:
        return None
    for d in candidates:
        if d.is_dir() and ((d / "tlmgr").exists() or (d / "latex").exists()):
            return d
    return None


def ensure_home_texlive_on_path() -> Optional[Path]:
    """Prepend a $HOME TeX Live to this process's PATH; return its bin dir.

    Nothing puts that directory on PATH until the user edits their shell
    startup, so a server started before then found only a distro /usr/bin/latex
    (no dvisvgm) and reported LaTeX missing -- while ziya-install-extras, which
    globs $TL_HOME/bin/*/ itself, reported everything installed.  Prepending
    (not appending) makes the new TeX win over the older distro one for
    shutil.which AND for the renderer's latex/dvisvgm/gs subprocesses, which
    inherit os.environ.  Idempotent; a PATH that already names it is left alone.
    """
    bin_dir = home_texlive_bin()
    if bin_dir is None:
        return None
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if str(bin_dir) not in parts:
        os.environ["PATH"] = os.pathsep.join([str(bin_dir)] + parts)
    return bin_dir


def tex_status() -> Optional[str]:
    """None when a usable TeX toolchain is present; otherwise what is missing.

    Mirrors ``latex_renderer.Capability.available``: DVI+dvisvgm or
    pdflatex+ghostscript.  Missing TeX *packages* are reported by the renderer
    itself at render time; this is only the toolchain.
    """
    ensure_home_texlive_on_path()
    has_dvi = bool(shutil.which("latex") and shutil.which("dvisvgm"))
    has_pdf = bool(shutil.which("pdflatex") and (shutil.which("gs") or shutil.which("ghostscript")))
    if has_dvi or has_pdf:
        return None
    if not (shutil.which("latex") or shutil.which("pdflatex")):
        return "TeX distribution"
    return "dvisvgm or ghostscript"


# The tools that exist only when Playwright's Chromium is installed; see
# builtin_tools.get_diagram_render_tools, which registers all three or none.
RENDER_TOOLS = ("render_diagram", "recall_image", "view_image")


def chromium_blocked_reason() -> Optional[str]:
    """Why Chromium can NEVER be installed on this host, or None if it could be.

    Mirrors install_extras.sh's PW_BLOCKED rule: Playwright's bundled Node
    needs glibc >= 2.28, so on an older Linux (Amazon Linux 2 is 2.26) the
    install fails with a linker error and no retry can change that.  The
    difference matters for what we tell the model: "run the installer" is
    right on a fresh host and a dead end here -- observed as a model advising
    ``playwright install chromium`` and ``install-deps`` on an AL2 desktop.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        import platform
        name, ver = platform.libc_ver()
        major, minor = (int(x) for x in ver.split(".")[:2])
    except (ValueError, OSError):
        return None
    if name != "glibc":
        return None
    if (major, minor) < (2, 28):
        return f"glibc {ver} (Playwright's Chromium needs glibc 2.28 or newer)"
    return None


def headless_browser_unavailable_message(feature: str) -> str:
    """One sentence for an error surface: what is missing and the real remedy.

    Names ``ziya-install-extras --browser`` when the browser can be added, and
    the platform limit when it cannot -- never the raw ``playwright install``
    recipe, which on a blocked host sends the user (and the model) chasing a
    fix that does not exist.
    """
    missing = playwright_status() or "Chromium browser for Playwright"
    blocked = chromium_blocked_reason()
    if blocked:
        return (f"{feature} needs a headless Chromium, which cannot be installed on "
                f"this host: {blocked}. Use a newer host for this feature.")
    return (f"{feature} needs the {missing}, which is not installed. "
            f"{browser_hint()} (then restart Ziya).")


def install_limitations_prompt() -> str:
    """System-prompt text naming what THIS install cannot do; "" if nothing.

    Withholding render_diagram from the tool list is not enough on its own:
    skills, saved memories and imported conversations all name it, and a model
    that has read those calls the tool anyway -- observed on an AL2 dev desktop
    as repeated render_diagram attempts, then a PDF-export attempt diagnosed
    over six tool calls, then advice to run an installer that cannot succeed
    there.  Only a positive statement in the prompt overrides what the model
    has read elsewhere, and it must say whether the gap is fixable.  Contains
    no braces: the text is appended to a langchain ChatPromptTemplate.
    """
    missing = playwright_status()
    if missing is None:
        return ""
    names = ", ".join(f"`{t}`" for t in RENDER_TOOLS)
    blocked = chromium_blocked_reason()
    if blocked:
        remedy = (
            f"This is a platform limit, not a missing install step: the host has {blocked}, "
            "so Chromium cannot be installed here at all. Do not suggest `playwright install "
            "chromium`, `install-deps`, `ziya-install-extras --browser` or any other install "
            "command; the only remedy is running Ziya on a newer host, and you should say so."
        )
    else:
        remedy = (
            f"The operator can add it with `{INSTALL_CMD} --browser` followed by a Ziya "
            "restart; if asked, name exactly that command and nothing else."
        )
    return (
        "\n\nINSTALL LIMITATIONS (this host):\n"
        f"This installation lacks the {missing}. Consequences, none of them fixable from "
        "inside a conversation:\n"
        f"- The {names} tools do not exist here. They are not in your tool list and a call "
        "cannot succeed; do not call them, and do not narrate an intent to, even when a "
        "skill, a saved memory, or an earlier turn refers to them. You cannot look at your "
        "own rendered output on this host.\n"
        "- PDF export -- of a conversation, or of a document from .ziya/documents/ via "
        "/api/export/document -- and chat-message screenshots need the same browser and "
        "fail here. Do not attempt them or probe why they fail; when asked for a PDF, say "
        "plainly that this install cannot produce one and offer the markdown source, or the "
        "HTML export (which needs no browser), instead.\n"
        "- Diagrams still reach the user normally: emit the fenced block (mermaid, graphviz, "
        "vega-lite, drawio, ...) and the user's browser renders it inline.\n"
        f"{remedy}\n"
    )


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
