"""
ziya-install-extras — installer for Ziya's optional extras (Community Edition).

Exposed as the ``ziya-install-extras`` console command (pyproject.toml
[tool.poetry.scripts]). Install users have only the installed wheel — no
source checkout — so this is a packaged entry point that drives the packaged
installer script at app/scripts/install_extras.sh.

The script installs, opt-in and never silently, the two things ``pip`` cannot:

  --browser   the Chromium build Playwright drives (``playwright install
              chromium``) — lets the model look at its own rendered diagrams,
              and enables PDF export and frozen diagram artifacts.
  --latex     a TeX distribution plus the TeX Live packages the LaTeX
              renderer needs (circuitikz, chemfig, pgfplots, TikZ, ...).
  --all       both; the default when no target is given.

Ziya runs without either; you just lose those features. The Python-level
dependencies (playwright, scapy) are ordinary hard requirements of the wheel
and need no extra step.

Two Python-level targets are handled here rather than in the script, by
``pip install`` into the interpreter Ziya runs under (``sys.executable``),
which is the same set the ``ziya[data]`` / ``ziya[stats]`` extras name:

  --data      DuckDB, sqlglot, pyarrow and scipy: the ``analyze`` tool
              (design/local-analytics.md).
  --stats     statsmodels, for the analytics skill's method recipes.
  --drag      pyobjc (macOS): the native drag helper that makes a diagram or
              table dragged out of the chat drop into Slack desktop as a
              PNG file (Docs/UserInterface.md).

All three are included by ``--all`` and the no-target default (``--drag`` only
on macOS, where it has something to do); each is skipped when its modules are
already importable, so the default run never needs the index just to re-check
what is there.  The flags remain for installing one thing.

Editions that add their own extras wrap this: they run their additions and
then call ``run(args)`` here, so the script location and invocation live in
exactly one place.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

SCRIPT_RELPATH = Path("scripts") / "install_extras.sh"


def latex_script_path() -> Optional[Path]:
    """Locate app/scripts/install_extras.sh inside the installed package.

    Resolved via the ``app`` package's location rather than importlib.resources
    because app/scripts ships as data, not as an importable subpackage.
    """
    import app  # local import: this module may be imported before app is on sys.path
    candidate = Path(app.__file__).resolve().parent / SCRIPT_RELPATH
    return candidate if candidate.is_file() else None


def tex_packages(script: Path) -> List[str]:
    """The TeX Live package list, read from the script's TEX_PACKAGES=(...) line."""
    for line in script.read_text(encoding="utf-8").splitlines():
        if line.startswith("TEX_PACKAGES=("):
            return line[len("TEX_PACKAGES=("):line.index(")")].split()
    return []


WINDOWS_USAGE = """usage: ziya-install-extras [--browser | --latex | --all] [--dry-run] [--yes]

  --browser  the Chromium build Playwright drives (~150 MB)
  --latex    the TeX Live packages the LaTeX renderer needs, installed with
             TeX Live's tlmgr; without a TeX distribution, how to get one
  --all      both (the default)
  --dry-run  print the plan and exit; install nothing
  --yes      skip the confirmation prompt"""


def run_windows(args: List[str], script: Optional[Path]) -> int:
    """The installer for Windows, where the bash script cannot run.

    ``bash`` there is WSL's launcher, which would install into the Linux VM,
    or Git Bash, which has no package manager.  Chromium installs natively.
    For TeX, TeX Live's tlmgr is used if one is installed; otherwise this
    says how to get a distribution, as the script does for Homebrew.
    """
    from app.utils.process_utils import resolve_executable

    if "-h" in args or "--help" in args:
        print(WINDOWS_USAGE)
        return 0
    targets = {a for a in args if a in ("--browser", "--latex", "--all")}
    browser = not targets or bool(targets & {"--all", "--browser"})
    latex = not targets or bool(targets & {"--all", "--latex"})
    steps = []
    print("Ziya optional extras")
    if browser:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        print("  * Chromium for Playwright (~150 MB):  " + subprocess.list2cmdline(cmd))
        steps.append(("Chromium", cmd))
    missing = 0
    if latex:
        tlmgr = shutil.which("tlmgr")
        packages = tex_packages(script) if script else []
        if tlmgr and packages:
            cmd = [resolve_executable("tlmgr"), "install", *packages]
            print("  * TeX Live packages:  " + subprocess.list2cmdline(["tlmgr", "install", *packages]))
            steps.append(("TeX Live packages", cmd))
        elif shutil.which("latex") and shutil.which("dvisvgm"):
            print("  ok A TeX distribution is on PATH (latex, dvisvgm). If a diagram reports a missing")
            print("     package, install it with your distribution's package manager.")
        else:
            missing = 1
            print("  !! No TeX distribution found. Install TeX Live (https://tug.org/texlive/windows.html)")
            print("     or MiKTeX (winget install MiKTeX.MiKTeX), open a new terminal, then re-run:")
            print("         ziya-install-extras --latex")
    if "--dry-run" in args or "--plan" in args:
        print("(--dry-run: nothing was installed.)")
        return 0
    if not steps:
        return missing
    if not ({"-y", "--yes"} & set(args)):
        try:
            answer = input("Proceed with the above? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted; nothing installed.")
            return 0
    failed = 0
    for name, cmd in steps:
        print(f"  -> {subprocess.list2cmdline(cmd)}", flush=True)
        if subprocess.run(cmd).returncode != 0:
            print(f"  !! {name} failed.")
            failed = 1
    return failed or missing


def run(args: List[str]) -> int:
    """Install what ``args`` names and return the exit code.

    The single entry point every edition calls: the internal edition's
    ``plugins.install_extras`` wrapper forwards its argv here verbatim and
    never calls ``main``.  So the Python-level targets (--data / --stats) are
    handled HERE, and only the remaining arguments reach the shell script,
    which rejects options it does not know.  When --data / --stats were the
    only targets, the script is not run at all (with no target it would
    default to --all).
    """
    args = list(args)
    if "--help" in args or "-h" in args:
        return _run_script(args)
    py_targets = [a for a in args if a in PYTHON_EXTRAS]
    # No target at all means --all (the script's own default), and --all
    # also covers every Python-level extra that applies to this platform
    # (extras_registry: default_in_all + supported).
    wants_all = "--all" in args or not any(
        a in PYTHON_EXTRAS or a in SCRIPT_TARGETS for a in args)
    if wants_all:
        py_targets += [t for t in default_python_extras() if t not in py_targets]
    dry = "--dry-run" in args or "--plan" in args
    if not py_targets:
        rc = _run_script(args)
        if not dry:
            _record_installed(_script_targets_requested(args))
        return rc
    rc = install_python_extras(py_targets, dry_run=dry)
    rest = [a for a in args if a not in PYTHON_EXTRAS]
    if wants_all or any(a in SCRIPT_TARGETS for a in rest):
        rc = _run_script(rest) or rc
        if not dry:
            _record_installed(_script_targets_requested(rest))
    return rc


def _script_targets_requested(args: List[str]) -> List[str]:
    """The script-level targets ``args`` asks for; none / --all means all of them."""
    named = [a for a in args if a in SCRIPT_TARGETS and a != "--all"]
    if named and "--all" not in args:
        return named
    return [x.target for x in _REGISTRY if x.kind != "pip" and x.supported()]


def _run_script(args: List[str]) -> int:
    """Run the packaged shell installer with ``args``; return its exit code."""
    script = latex_script_path()
    if sys.platform == "win32":
        return run_windows(args, script)
    if script is None:
        print("!! Packaged installer not found (app/scripts/install_extras.sh).")
        print("   Reinstall ziya, or install a TeX distribution manually.")
        return 1
    # Invoke through bash explicitly so the file's exec bit is irrelevant;
    # wheel installs do not reliably preserve it.
    bash = shutil.which("bash") or "/bin/bash"
    if not os.path.exists(bash):
        print("!! bash not found; run the installer by hand:")
        print(f"   sh {script} {' '.join(args)}".rstrip())
        return 1
    # Tell the script which interpreter Ziya actually runs under. A wheel
    # installed into a venv (or a launcher-managed ``.venv``) is driven by that
    # venv's python, NOT the bare ``python3`` on PATH -- and Playwright ships
    # inside that venv. Without this the script probed the system ``python3``,
    # found no ``playwright`` there, and wrongly told the user to reinstall
    # ziya even though it was installed. sys.executable is exactly the venv
    # python whose Playwright will look for the Chromium build.
    env = {**os.environ, "ZIYA_PYTHON": sys.executable}
    return subprocess.run([bash, str(script), *args], env=env).returncode


# The extras themselves are declared once, in app/utils/extras_registry.py
# (which also drives the startup reconcile, /api/extras and the Settings
# page).  These tables are views of it kept under their historical names.
from .extras_registry import REGISTRY as _REGISTRY, BY_TARGET as _BY_TARGET  # noqa: E402

PYTHON_EXTRAS = {x.target: list(x.pip_packages) for x in _REGISTRY if x.kind == "pip"}
SCRIPT_TARGETS = {x.target for x in _REGISTRY if x.kind != "pip"} | {"--all"}

# Modules whose presence means a target is already installed.  Checked before
# pip so the default run does not pay for (or need a login to) an index just
# to confirm what is there -- mirroring the shell script's "already installed".
PYTHON_EXTRA_PROBES = {x.target: list(x.probe_modules) for x in _REGISTRY if x.kind == "pip"}


def default_python_extras() -> List[str]:
    """Python-level targets --all (and the no-target default) include on
    this platform.  The drag helper only has anything to do on macOS."""
    return [x.target for x in _REGISTRY
            if x.kind == "pip" and x.default_in_all and x.supported()]


def _record_installed(targets: List[str]) -> None:
    """Remember, in ~/.ziya/extras_state.json, the targets the user has
    installed, so a later upgrade that leaves one of them missing can refresh
    it rather than merely announce it.  Only targets the probe now finds
    present are recorded; a declined or failed install is not an opt-in."""
    try:
        from .extras_registry import record_opted_in
        keys = [_BY_TARGET[t].key for t in targets
                if t in _BY_TARGET and _BY_TARGET[t].missing() is None]
        if keys:
            record_opted_in(keys)
    except Exception as e:  # noqa: BLE001 -- bookkeeping must never fail an install
        print(f"!! could not record installed extras: {e}")


def python_extra_installed(target: str) -> bool:
    import importlib.util
    probes = PYTHON_EXTRA_PROBES.get(target)
    if not probes:
        return False
    return all(importlib.util.find_spec(m) is not None for m in probes)

# An edition may set this to explain, and repair, access to its own package
# index.  Called as hook(index_url, targets) after an install from a
# non-PyPI index fails.  Return None to fall through to the generic hint,
# False when it printed its own advice, True when it renewed access and the
# install should be retried once.
index_failure_hook: Optional[Callable[[str, List[str]], Optional[bool]]] = None


def install_python_extras(targets: List[str], dry_run: bool = False) -> int:
    """``pip install`` the packages for ``targets`` into this interpreter."""
    missing = []
    for t in targets:
        if not python_extra_installed(t):
            missing.append(t)
    have = [t for t in targets if t not in missing]
    if have:
        # One line, not one per target: on a machine with everything present
        # the whole run should read as "already installed", not as a log.
        print(f"ok already installed: {' '.join(have)}", flush=True)
    targets = missing
    if not targets:
        return 0
    pkgs = [p for t in targets for p in PYTHON_EXTRAS[t]]
    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    print(f"==> {' '.join(targets)}: {' '.join(cmd)}", flush=True)
    if dry_run:
        return 0
    rc = subprocess.run(cmd).returncode
    if rc != 0 and _index_failure_hint(targets):
        print(f"==> retrying: {' '.join(cmd)}")
        rc = subprocess.run(cmd).returncode
    if rc == 0:
        _record_installed(targets)
    return rc


def _configured_index() -> Optional[str]:
    """The index URL pip uses: PIP_INDEX_URL, else pip's config, else None."""
    url = os.environ.get("PIP_INDEX_URL")
    if url:
        return url
    try:
        out = subprocess.run([sys.executable, "-m", "pip", "config", "list"],
                             capture_output=True, text=True, timeout=30).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None
    found = {}
    for line in out.splitlines():
        key, sep, val = line.partition("=")
        if sep and key.strip().endswith(".index-url"):
            found[key.strip()] = val.strip().strip("'\"")
    # install.* overrides global.* for pip install.
    return found.get("install.index-url") or found.get("global.index-url") or next(iter(found.values()), None)


def _resolves(host: str) -> bool:
    """Whether ``host`` resolves now.  A mirror that does not is a network problem, not a login."""
    import socket
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def _index_failure_hint(targets: List[str]) -> bool:
    """After a failed install, say so when pip was pointed at a private mirror.

    A mirror whose login token has expired answers 401, and pip then reports
    "no matching distribution", which reads like a missing package.  Only the
    host is printed: such URLs often embed the token.
    Returns True when ``index_failure_hook`` renewed access (retry once).
    """
    from urllib.parse import urlparse
    url = _configured_index()
    host = urlparse(url).hostname if url else None
    if not host or host in ("pypi.org", "pypi.python.org"):
        return False
    if not _resolves(host):
        print(f"\n!! Could not resolve {host}: a network or DNS problem, not an expired login.")
        print("   Check your connection (or VPN), then re-run:")
        print(f"     ziya-install-extras {' '.join(targets)}")
        return False
    if index_failure_hook is not None:
        try:
            verdict = index_failure_hook(url, list(targets))
        except Exception as e:  # noqa: BLE001 - a broken hook must not hide the hint
            print(f"!! index hint failed: {e}")
            verdict = None
        if verdict is not None:
            return bool(verdict)
    print(f"\n!! pip installs from {host} (your pip configuration), not public PyPI.")
    print("   If that mirror refused the request (401, or 'no matching distribution'),")
    print("   its login has probably expired. Renew it, or use PyPI for this run:")
    print(f"     PIP_INDEX_URL=https://pypi.org/simple/ ziya-install-extras {' '.join(targets)}")
    return False


def main(argv: Optional[List[str]] = None) -> int:
    """Console entry point: everything goes through ``run`` (see there)."""
    args = list(sys.argv[1:] if argv is None else argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
