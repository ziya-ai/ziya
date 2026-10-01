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
#              tikz-cd.  (macOS: BasicTeX via Homebrew, sudo for tlmgr.
#              Linux: TeX Live scheme-basic into ~/texlive, no sudo.)
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

# Where the Linux route installs TeX Live: under $HOME, so no sudo is ever
# needed and nothing root-owned lands in the user's tree.  Both overridable.
TL_HOME="${ZIYA_TEXLIVE_DIR:-$HOME/texlive}"
TL_MIRROR="${ZIYA_TEXLIVE_MIRROR:-https://mirror.ctan.org/systems/texlive/tlnet}"
TL_BIN=""
TLMGR=""

usage() {
    sed -n '3,34p' "$0" | sed 's/^#\{0,1\} \{0,1\}//'
    exit "${1:-0}"
}

log()  { printf '%s\n' "$*"; }
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
rule() { printf '%s\n' "------------------------------------------------------------------"; }

find_tlmgr() {
    command -v tlmgr 2>/dev/null && return 0
    [ "$OS" = "Darwin" ] && [ -x /Library/TeX/texbin/tlmgr ] && { printf '%s\n' /Library/TeX/texbin/tlmgr; return 0; }
    # A TeX Live this script installed into $HOME but not yet on this shell's PATH.
    local t
    for t in "$TL_HOME"/bin/*/tlmgr; do
        [ -x "$t" ] && { printf '%s\n' "$t"; return 0; }
    done
    return 1
}

# The file kpsewhich must find for a TeX Live package to count as present on a
# TeX that has no tlmgr (distro packages).  dvisvgm is a program, not a file.
tex_pkg_file() {
    case "$1" in
        standalone) printf 'standalone.cls\n' ;;
        pgf)        printf 'tikz.sty\n' ;;
        dvisvgm)    printf '\n' ;;
        *)          printf '%s.sty\n' "$1" ;;
    esac
}

# tlmgr needs sudo only when the TeX tree is not writable by this user.  A TeX
# Live installed into $HOME (the Linux route below) must NOT run under sudo,
# which would scatter root-owned files through the user's tree.
tlmgr_needs_sudo() {
    local d
    d="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" || return 0
    [ -w "$d" ] && return 1
    return 0
}

# Linux route: the official installer (install-tl) into $TL_HOME.  Distro
# texlive-* packages are years old, ship no tlmgr, and cannot have packages
# added, so they are never the answer.  install-tl needs only perl, tar and
# curl or wget, and no sudo when the target is under $HOME.
can_install_tl() {
    [ "$OS" = "Linux" ] || return 1
    command -v perl >/dev/null 2>&1 || return 1
    command -v tar >/dev/null 2>&1 || return 1
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1
}

fetch() {
    if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"; else wget -qO "$2" "$1"; fi
}

linux_install_tl_plan() {
    log "  * TeX Live scheme-basic via the official installer (install-tl) into"
    log "      $TL_HOME     (~300 MB download from CTAN; no sudo)"
    log "  * then tlmgr install these TeX Live packages:"
}

linux_install_tl_blocked_hint() {
    log "     install-tl (the official TeX Live installer) cannot run here: it needs perl, tar"
    log "     and curl or wget. Add those and re-run, or install TeX Live yourself:"
    log "         https://tug.org/texlive/acquire-netinstall.html"
}

# Download and run install-tl non-interactively into $TL_HOME.  On success sets
# TLMGR/TL_BIN to the new tree and prepends its bin dir to PATH for the rest of
# this run.  ZIYA_TEXLIVE_INSTALLER may name an already-unpacked install-tl
# directory, which skips the download (the tests use this).
install_tl_home() {
    local work dir bin
    work="$(mktemp -d)"
    dir="${ZIYA_TEXLIVE_INSTALLER:-}"
    if [ -z "$dir" ]; then
        log "  -> fetching $TL_MIRROR/install-tl-unx.tar.gz"
        fetch "$TL_MIRROR/install-tl-unx.tar.gz" "$work/install-tl-unx.tar.gz" || return 1
        tar -xzf "$work/install-tl-unx.tar.gz" -C "$work" || return 1
        dir="$(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'install-tl-*' | head -n 1)"
        [ -n "$dir" ] || return 1
    fi
    cat > "$work/ziya.profile" <<EOF
selected_scheme scheme-basic
TEXDIR $TL_HOME
TEXMFLOCAL $TL_HOME/texmf-local
TEXMFSYSCONFIG $TL_HOME/texmf-config
TEXMFSYSVAR $TL_HOME/texmf-var
TEXMFCONFIG $HOME/.texlive/texmf-config
TEXMFVAR $HOME/.texlive/texmf-var
TEXMFHOME $HOME/texmf
tlpdbopt_autobackup 0
tlpdbopt_install_docfiles 0
tlpdbopt_install_srcfiles 0
instopt_adjustpath 0
instopt_letter 0
EOF
    log "  -> $dir/install-tl --profile $work/ziya.profile --repository $TL_MIRROR"
    "$dir/install-tl" --profile "$work/ziya.profile" --repository "$TL_MIRROR" || return 1
    bin="$(find "$TL_HOME/bin" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -n 1)"
    [ -n "$bin" ] && [ -x "$bin/tlmgr" ] || return 1
    TL_BIN="$bin"
    TLMGR="$bin/tlmgr"
    export PATH="$bin:$PATH"
    TEX_FRESHLY_INSTALLED=1
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
# A TeX that ships without tlmgr (distro packages) cannot have packages added,
# so it is reported separately; the only question there is whether the
# renderer's files are already present (kpsewhich).
HAVE_DISTRO_TEX=""
if [ -z "$HAVE_TEX" ] && { command -v latex >/dev/null 2>&1 || command -v pdflatex >/dev/null 2>&1; }; then
    HAVE_DISTRO_TEX=1
fi

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

# Playwright's bundled Node needs glibc >= 2.28.  On an older Linux the install
# dies with a linker error, and no re-run will ever succeed; say so up front
# instead of printing the failure and inviting a retry.
PW_BLOCKED=""
if [ "$OS" = "Linux" ] && command -v getconf >/dev/null 2>&1; then
    GLIBC_VER="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
    GLIBC_MAJ="${GLIBC_VER%%.*}"; GLIBC_MIN="${GLIBC_VER#*.}"; GLIBC_MIN="${GLIBC_MIN%%.*}"
    case "$GLIBC_MAJ.$GLIBC_MIN" in
        [0-9]*.[0-9]*)
            if [ "$GLIBC_MAJ" -lt 2 ] || { [ "$GLIBC_MAJ" -eq 2 ] && [ "$GLIBC_MIN" -lt 28 ]; }; then
                PW_BLOCKED="glibc $GLIBC_VER"
            fi ;;
    esac
fi

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
    elif [ -n "$HAVE_DISTRO_TEX" ] && command -v kpsewhich >/dev/null 2>&1; then
        # No tlmgr to ask; check for the files the renderer will actually load.
        for p in "${TEX_PACKAGES[@]}"; do
            f="$(tex_pkg_file "$p")"
            if { [ -z "$f" ] && command -v "$p" >/dev/null 2>&1; } || { [ -n "$f" ] && kpsewhich "$f" >/dev/null 2>&1; }; then
                INSTALLED_TEX_COUNT=$((INSTALLED_TEX_COUNT + 1))
            else
                MISSING_TEX+=("$p")
            fi
        done
    else
        MISSING_TEX=("${TEX_PACKAGES[@]}")
    fi
fi
# Whether the tlmgr step will need sudo (root-owned tree) - decided once so
# the plan and the install phase cannot disagree.
TL_SUDO_NOTE=""
if [ -n "$HAVE_TEX" ]; then
    tlmgr_needs_sudo "$(find_tlmgr)" && TL_SUDO_NOTE=1
elif [ "$OS" = "Darwin" ]; then
    TL_SUDO_NOTE=1
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
    elif [ -n "$PW_BLOCKED" ]; then
        log "  !! cannot be installed on this host: it has $PW_BLOCKED, and Playwright's bundled"
        log "     Node needs glibc 2.28 or newer. Use Ziya from a newer host for render_diagram"
        log "     and PDF export; everything else works without Chromium."
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
    log "    Will tlmgr install the missing:"
elif [ -n "$HAVE_DISTRO_TEX" ] && [ ${#MISSING_TEX[@]} -eq 0 ]; then
    log "  ok TeX present ($(command -v latex || command -v pdflatex)) with all ${#TEX_PACKAGES[@]} required pieces - nothing to do."
elif [ -n "$HAVE_DISTRO_TEX" ]; then
    log "  !! A TeX is present ($(command -v latex || command -v pdflatex)) but it has no tlmgr, so nothing"
    log "     can be added to it ($INSTALLED_TEX_COUNT of ${#TEX_PACKAGES[@]} required pieces found). Instead:"
    if can_install_tl; then linux_install_tl_plan; else linux_install_tl_blocked_hint; fi
elif [ "$OS" = "Darwin" ]; then
    log "  * BasicTeX  (brew install --cask basictex - you'll be asked for your password)"
    log "  * then, under sudo, tlmgr install these TeX Live packages:"
elif can_install_tl; then
    linux_install_tl_plan
else
    log "  !! No TeX Live found."
    linux_install_tl_blocked_hint
fi
if [ ${#MISSING_TEX[@]} -gt 0 ]; then
    for p in "${MISSING_TEX[@]}"; do log "      - $p"; done
    if [ -n "$TL_SUDO_NOTE" ]; then
        log "    (installs into the root-owned TeX tree, so tlmgr runs under sudo)"
    fi
fi
log ""
fi

# Nothing selected is actually missing: say so and stop before prompting.
# A selected target this host cannot satisfy (glibc too old for Playwright; no
# tlmgr TeX and install-tl cannot run) is BLOCKED, not "already installed":
# exit non-zero so it is not mistaken for success, and never prompt.
BROWSER_BLOCKED=""; [ -n "$DO_BROWSER" ] && [ -z "$HAVE_CHROMIUM" ] && [ -n "$PW_BLOCKED" ] && BROWSER_BLOCKED=1
LATEX_SATISFIED=""; { [ -n "$HAVE_TEX" ] || [ -n "$HAVE_DISTRO_TEX" ]; } && [ ${#MISSING_TEX[@]} -eq 0 ] && LATEX_SATISFIED=1
LATEX_BLOCKED="";   [ -n "$DO_LATEX" ] && [ -z "$LATEX_SATISFIED" ] && [ -z "$HAVE_TEX" ] && [ "$OS" != "Darwin" ] && ! can_install_tl && LATEX_BLOCKED=1
BROWSER_TODO=""; [ -n "$DO_BROWSER" ] && [ -z "$HAVE_CHROMIUM" ] && [ -z "$BROWSER_BLOCKED" ] && BROWSER_TODO=1
LATEX_TODO="";   [ -n "$DO_LATEX" ] && [ -z "$LATEX_SATISFIED" ] && [ -z "$LATEX_BLOCKED" ] && LATEX_TODO=1
if [ -z "$BROWSER_TODO" ] && [ -z "$LATEX_TODO" ]; then
    rule
    if [ -n "$BROWSER_BLOCKED" ] || [ -n "$LATEX_BLOCKED" ]; then
        log "Nothing this installer can set up on this host yet; see the notes above."
        exit 1
    fi
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
    elif [ -n "$PW_BLOCKED" ]; then
        log "  !! skipped: $PW_BLOCKED is too old for Playwright's Node (needs 2.28); see plan above."
        [ -z "$DO_LATEX" ] && exit 1
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
    if [ -n "$LATEX_BLOCKED" ]; then
        log "  !! skipped: no usable TeX Live on this host (see plan above)."
    else
        log "  ok already installed; skipped."
    fi
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
        log "  -> TeX Live scheme-basic into $TL_HOME  (~300 MB download; this takes a few minutes)"
        install_tl_home || {
            log "  !! install-tl failed. Re-run: ziya-install-extras --latex   (or install TeX Live"
            log "     yourself: https://tug.org/texlive/acquire-netinstall.html)"
            exit 1
        }
        log "  ok TeX Live installed into $TL_HOME."
    fi
fi

# install_tl_home already set TLMGR.  Otherwise find_tlmgr may return non-zero
# here (BasicTeX just installed, not yet on PATH), so tolerate failure and fall
# back to the known BasicTeX location.
[ -n "$TLMGR" ] || TLMGR="$(find_tlmgr || true)"
if [ -z "$TLMGR" ]; then
    TLMGR=/Library/TeX/texbin/tlmgr
fi

log ""
SUDO=""
if [ "$(id -u)" != "0" ] && tlmgr_needs_sudo "$TLMGR"; then
    SUDO="sudo"
fi
if [ -n "$SUDO" ]; then
    log "  TeX Live is root-owned, so package installs need sudo. Running:"
else
    log "  TeX Live tree is writable by you; no sudo needed. Running:"
fi
log "      ${SUDO:+$SUDO }$TLMGR update --self"
log "      ${SUDO:+$SUDO }$TLMGR install ${MISSING_TEX[*]}"
log ""

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

if [ -n "$TEX_FRESHLY_INSTALLED" ] && [ -n "$TL_BIN" ]; then
    log ""
    log "!! One more step - TeX isn't on this shell's PATH yet."
    log "   Put the new TeX Live on PATH AHEAD of /usr/bin (so it wins over any distro TeX)"
    log "   in your shell startup file (~/.bashrc or ~/.zshrc):"
    log ""
    log "     export PATH=\"$TL_BIN:\$PATH\""
    log ""
    log "   then open a new terminal (or source that file) and restart the Ziya server"
    log "   so it picks up tlmgr/latex/dvisvgm (Ctrl-C it first):"
    log "     ziya"
    log ""
    log "   Verify:  which tlmgr   ->   $TL_BIN/tlmgr"
elif [ -n "$TEX_FRESHLY_INSTALLED" ]; then
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
