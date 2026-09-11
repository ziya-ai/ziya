#!/usr/bin/env bash
#
# install_extras.sh — installs the parts of Ziya that pip cannot.
#
# Normally invoked for you by the `ziya-install-extras` command. Two targets:
#
#   --browser  the Chromium build Playwright drives. Lets the model LOOK AT its
#              own rendered diagram (render_diagram), enables PDF export and
#              frozen diagram artifacts in task runs.  (~150 MB, no sudo)
#   --latex    a TeX distribution + the TeX Live packages the LaTeX renderer
#              needs: circuitikz, chemfig, pgfplots, tikz, forest, bussproofs,
#              tikz-cd.  (sudo for tlmgr)
#   --all      both.  This is the DEFAULT when no target is given.
#
# Ziya WILL run without either; you just lose those features.
#
# Design:
#   * Nothing is installed silently. The full, explicit list of every package
#     is printed BEFORE anything runs, and you must confirm to continue.
#   * The packages install into the root-owned TeX Live tree and so require
#     sudo. That sudo prompt is the natural gate: the script prints the exact
#     tlmgr command and runs it under sudo, so the prompt appears with the
#     package list in front of you. You can also copy and run it yourself.
#   * We do NOT auto-install Homebrew. If it's missing we print the official
#     one-line installer and ask you to run it, then re-run.
#   * Homebrew refuses to run as root, so DO NOT run this under sudo. Run it
#     normally; it elevates only the tlmgr step.
#
# Usage:
#   ziya-install-extras            # everything (same as --all)
#   ziya-install-extras --browser  # just Chromium for Playwright
#   ziya-install-extras --latex    # just TeX
#   <script> --dry-run             # print the plan and exit; install nothing
#   <script> --yes                 # skip the confirmation prompt
#   <script> --help
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Authoritative TeX-package list: the union of TOOLCHAIN_TL_PACKAGES and every
# PROFILES[*].tl_packages / optional_tl_packages declared in
# app/services/latex_profiles.py (this script ships in the same wheel as that
# module, so this is the single source of truth). Optional packages (mhchem,
# siunitx) are included deliberately: the renderer notes installing them
# alongside the required set is strictly better. Kept in sync by the plan test.
# ---------------------------------------------------------------------------
TEX_PACKAGES=(bussproofs chemfig circuitikz dvisvgm forest mhchem pgf pgfplots siunitx standalone tikz-cd)

ASSUME_YES=""
DRY_RUN=""
TEX_FRESHLY_INSTALLED=""
DO_BROWSER=""
DO_LATEX=""

usage() {
    sed -n '3,33p' "$0" | sed 's/^#\{0,1\} \{0,1\}//'
    exit "${1:-0}"
}

log()  { printf '%s\n' "$*"; }
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
rule() { printf '%s\n' "------------------------------------------------------------------"; }

find_tlmgr() {
    command -v tlmgr 2>/dev/null && return 0
    [ -x /Library/TeX/texbin/tlmgr ] && { printf '%s\n' /Library/TeX/texbin/tlmgr; return 0; }
    return 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|--plan) DRY_RUN=1 ;;
        -y|--yes)         ASSUME_YES=1 ;;
        --browser)        DO_BROWSER=1 ;;
        --latex)          DO_LATEX=1 ;;
        --all)            DO_BROWSER=1; DO_LATEX=1 ;;
        -h|--help)        usage 0 ;;
        *) log "Unknown option: $1"; usage 1 ;;
    esac
    shift
done
# No target named -> everything.  The old script was LaTeX-only, so a bare
# invocation must not silently narrow to that.
if [ -z "$DO_BROWSER" ] && [ -z "$DO_LATEX" ]; then
    DO_BROWSER=1; DO_LATEX=1
fi

OS="$(uname -s)"
ARCH="$(uname -m)"
if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then PLATFORM="macOS (Apple Silicon)"; else PLATFORM="macOS (Intel)"; fi
else
    PLATFORM="$OS"
fi

HAVE_TEX=""
find_tlmgr >/dev/null 2>&1 && HAVE_TEX=1
HAVE_BREW=""
command -v brew >/dev/null 2>&1 && HAVE_BREW=1

# `playwright` the CLI is installed with the wheel (hard dependency).  Prefer
# the interpreter that runs Ziya (exported as ZIYA_PYTHON by the entry point)
# so the browser lands where THAT Playwright will look for it. A wheel in a
# venv is driven by the venv python, not the bare python3 on PATH, and that is
# where Playwright is installed -- probing python3 falsely reported it missing.
find_playwright() {
    if [ -n "${ZIYA_PYTHON:-}" ] && "$ZIYA_PYTHON" -c 'import playwright' 2>/dev/null; then
        printf '%s\n' "$ZIYA_PYTHON -m playwright"; return 0
    fi
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import playwright' 2>/dev/null; then
        printf '%s\n' "python3 -m playwright"; return 0
    fi
    command -v playwright 2>/dev/null && return 0
    return 1
}
HAVE_PW=""
PW_CMD="$(find_playwright || true)"
[ -n "$PW_CMD" ] && HAVE_PW=1

# Is the Chromium build already there?  Ask the same probe the running server
# uses (app.utils.optional_features mirrors Playwright's browser registry:
# PLAYWRIGHT_BROWSERS_PATH, the "0" in-package mode, per-OS cache dirs), so
# this script and the startup banner can never disagree.  Falls back to the
# default cache location when that module is not importable.
chromium_present() {
    local py rc
    for py in "${ZIYA_PYTHON:-}" python3; do
        [ -n "$py" ] || continue
        # 0 = present, 3 = absent; anything else (ImportError exits 1, missing
        # interpreter 127) means "could not ask" -> try the next probe.
        "$py" -c 'from app.utils.optional_features import chromium_browser_available as c; raise SystemExit(0 if c() else 3)' 2>/dev/null
        rc=$?
        [ "$rc" -eq 0 ] && return 0
        [ "$rc" -eq 3 ] && return 1
    done
    local root="${PLAYWRIGHT_BROWSERS_PATH:-}"
    if [ -z "$root" ]; then
        if [ "$OS" = "Darwin" ]; then root="$HOME/Library/Caches/ms-playwright"
        else root="${XDG_CACHE_HOME:-$HOME/.cache}/ms-playwright"; fi
    fi
    [ -d "$root" ] || return 1
    local d
    for d in "$root"/chromium-* "$root"/chromium_headless_shell-*; do
        [ -d "$d" ] && return 0
    done
    return 1
}
HAVE_CHROMIUM=""
if [ -n "$DO_BROWSER" ] && chromium_present; then HAVE_CHROMIUM=1; fi

# Which of TEX_PACKAGES are already installed?  `tlmgr info --only-installed`
# reads the local tlpdb (no sudo, no network) and prints the names it finds;
# anything it does not print is missing.  If tlmgr cannot answer at all, every
# package is treated as missing -- tlmgr install is idempotent, so the worst
# case is a no-op reinstall, never a skipped package.
MISSING_TEX=()
INSTALLED_TEX_COUNT=0
if [ -n "$DO_LATEX" ]; then
    if [ -n "$HAVE_TEX" ]; then
        installed="$("$(find_tlmgr)" info --only-installed --data name "${TEX_PACKAGES[@]}" 2>/dev/null || true)"
        for p in "${TEX_PACKAGES[@]}"; do
            if printf '%s\n' "$installed" | grep -qx -- "$p"; then
                INSTALLED_TEX_COUNT=$((INSTALLED_TEX_COUNT + 1))
            else
                MISSING_TEX+=("$p")
            fi
        done
    else
        MISSING_TEX=("${TEX_PACKAGES[@]}")
    fi
fi

bold "Ziya optional features — installation plan"
log ""
log "This machine: $PLATFORM"
bold "We strongly recommend installing these for the best Ziya experience."
log "Ziya runs without them — you just lose the features listed."
log ""

if [ -n "$DO_BROWSER" ]; then
    log "Chromium for Playwright (the model sees its own diagrams; PDF export):"
    if [ -n "$HAVE_CHROMIUM" ]; then
        log "  ok already installed - nothing to do."
    elif [ -n "$HAVE_PW" ]; then
        log "  * $PW_CMD install chromium     (~150 MB into your user cache; no sudo)"
    else
        log "  !! the 'playwright' Python package is not importable from python3."
        log "     It ships with ziya; reinstall ziya (pip install -U ziya) and re-run."
    fi
    log ""
fi

if [ -n "$DO_LATEX" ]; then
NEED_BREW_FIRST=""
if [ -z "$HAVE_TEX" ] && [ "$OS" = "Darwin" ] && [ -z "$HAVE_BREW" ]; then
    NEED_BREW_FIRST=1
    log "Homebrew - NOT found, and it's required to install BasicTeX on macOS."
    log "  We won't install Homebrew for you. Install it (one line), then re-run:"
    log '      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    log "  (Homebrew's installer explains what it does and prompts for your password.)"
    log ""
fi

log "LaTeX diagram rendering (server-side; needs a local TeX install):"
if [ -n "$HAVE_TEX" ] && [ ${#MISSING_TEX[@]} -eq 0 ]; then
    log "  ok TeX Live present ($(find_tlmgr)) and all ${#TEX_PACKAGES[@]} packages installed - nothing to do."
elif [ -n "$HAVE_TEX" ]; then
    log "  * TeX Live already present ($(find_tlmgr)); $INSTALLED_TEX_COUNT of ${#TEX_PACKAGES[@]} packages installed."
    log "    Will (sudo) tlmgr install the missing:"
elif [ "$OS" = "Darwin" ]; then
    log "  * BasicTeX  (brew install --cask basictex - you'll be asked for your password)"
    log "  * then, under sudo, tlmgr install these TeX Live packages:"
else
    log "  * TeX Live  (install for your distro - see the note below), then these packages:"
fi
if [ ${#MISSING_TEX[@]} -gt 0 ]; then
    for p in "${MISSING_TEX[@]}"; do log "      - $p"; done
    log "    (installs into the root-owned TeX tree, so tlmgr runs under sudo)"
fi
log ""
fi

# Nothing selected is actually missing: say so and stop before prompting.
BROWSER_TODO=""; [ -n "$DO_BROWSER" ] && [ -z "$HAVE_CHROMIUM" ] && BROWSER_TODO=1
LATEX_TODO="";   [ -n "$DO_LATEX" ] && { [ -z "$HAVE_TEX" ] || [ ${#MISSING_TEX[@]} -gt 0 ]; } && LATEX_TODO=1
if [ -z "$BROWSER_TODO" ] && [ -z "$LATEX_TODO" ]; then
    rule
    log "Everything requested is already installed. Nothing to do."
    exit 0
fi

if [ -n "${NEED_BREW_FIRST:-}" ] && [ -z "$DO_BROWSER" ]; then
    rule
    log "Install Homebrew (above), then re-run:  ziya-install-extras --latex"
    exit 0
fi

if [ -n "$DRY_RUN" ]; then
    rule
    log "(--dry-run: nothing was installed.)"
    exit 0
fi

if [ -z "$ASSUME_YES" ]; then
    rule
    printf 'Proceed with the above? [y/N] '
    read -r ans || ans=""
    case "$ans" in
        y|Y|yes|YES) ;;
        *) log "Aborted; nothing installed."; exit 0 ;;
    esac
fi

if [ -n "$DO_BROWSER" ]; then
    log ""
    bold "> Chromium for Playwright..."
    if [ -n "$HAVE_CHROMIUM" ]; then
        log "  ok already installed; skipped."
    elif [ -n "$HAVE_PW" ]; then
        if $PW_CMD install chromium; then
            log "  ok Chromium installed."
        else
            log "  !! playwright install chromium failed. Re-run: ziya-install-extras --browser"
            [ -z "$DO_LATEX" ] && exit 1
        fi
    else
        log "  !! skipped: playwright package not importable (see plan above)."
        [ -z "$DO_LATEX" ] && exit 1
    fi
fi

if [ -n "$DO_LATEX" ] && [ -z "$LATEX_TODO" ]; then
    log ""
    bold "> LaTeX rendering support..."
    log "  ok already installed; skipped."
elif [ -n "$DO_LATEX" ]; then
if [ -n "${NEED_BREW_FIRST:-}" ]; then
    log ""
    log "  !! LaTeX skipped: install Homebrew (above), then re-run: ziya-install-extras --latex"
    exit 0
fi
log ""
bold "> LaTeX rendering support..."

if ! find_tlmgr >/dev/null 2>&1; then
    if [ "$OS" = "Darwin" ]; then
        # brew presence was validated in the plan phase.
        log "  -> brew install --cask basictex   (Homebrew will prompt for sudo)"
        brew install --cask basictex || {
            log "  !! BasicTeX install failed. Install it, then re-run: ziya-install-extras --latex"
            exit 1
        }
        TEX_FRESHLY_INSTALLED=1
        log "  ok BasicTeX installed."
    else
        log "  !! No TeX Live install found and this is not macOS."
        log "     Install TeX Live for your distro (e.g. 'sudo yum install texlive-scheme-basic'"
        log "     or the official install-tl), then re-run: ziya-install-extras --latex"
        exit 1
    fi
fi

# find_tlmgr may return non-zero here (BasicTeX just installed, not yet on
# PATH), so tolerate failure and fall back to the known BasicTeX location.
TLMGR="$(find_tlmgr || true)"
if [ -z "$TLMGR" ]; then
    TLMGR=/Library/TeX/texbin/tlmgr
fi

log ""
log "  TeX Live is root-owned, so package installs need sudo. Running:"
log "      sudo $TLMGR update --self"
log "      sudo $TLMGR install ${MISSING_TEX[*]}"
log ""

SUDO=""
if [ "$(id -u)" != "0" ]; then
    SUDO="sudo"
fi

$SUDO "$TLMGR" update --self || log "  (tlmgr self-update skipped/failed - continuing)"
if $SUDO "$TLMGR" install "${MISSING_TEX[@]}"; then
    log "  ok TeX Live packages installed."
else
    log "  !! Some TeX packages failed. You can re-run the printed tlmgr command."
    exit 1
fi

log ""
rule
bold "Done. LaTeX rendering support is set up."

if [ -n "$TEX_FRESHLY_INSTALLED" ]; then
    log ""
    log "!! One more step - TeX isn't on this shell's PATH yet."
    log "   BasicTeX added /Library/TeX/texbin, but your current shell and the running"
    log "   Ziya server won't see it until PATH is refreshed. Do ONE of:"
    log ""
    log "     * open a new terminal, or"
    log '     * run:  eval "$(/usr/libexec/path_helper -s)"'
    log ""
    log "   then restart the Ziya server so it picks up tlmgr/latex (Ctrl-C it first):"
    log "     ziya"
    log ""
    log "   Verify:  which tlmgr   ->   /Library/TeX/texbin/tlmgr"
else
log "If the Ziya server was running, restart it to pick up the new tools:  ziya"
fi
fi
