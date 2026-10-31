"""
One registry of Ziya's optional extras, and what is done about them on upgrade.

The problem this solves
-----------------------
pip has no post-install step, so the parts of Ziya it cannot install (the
Chromium build Playwright drives, a TeX distribution) and the parts it
installs only on request (DuckDB for ``analyze``, statsmodels, the macOS
drag helper) are added by ``ziya-install-extras``.  Nothing connected "the
wheel changed" to "the extras caught up":

  * a NEW extra appeared in a release and existing installs never heard of
    it, because the only surface naming gaps was a terminal banner that
    knew about two of the five extras;
  * an EXISTING extra went stale: every Playwright bump changes the Chromium
    build it launches (``optional_features.required_chromium_build``), so a
    user who opted into ``--browser`` lost render_diagram on upgrade until
    they re-ran the installer -- and the old any-build check hid that.

Knowledge of what counts as an extra was split across ``install_extras.py``
(``PYTHON_EXTRAS``), the shell script's ``--browser/--latex``, and
``optional_features.missing_feature_lines``.  It now lives in ``REGISTRY``
below; the installer, the banner, the ``/api/extras`` endpoint and the
Settings page all read it, so adding an extra is one entry.

State: ``~/.ziya/extras_state.json``
-----------------------------------
    {"versions": {"ziya": "...", "playwright": "...", "chromium_build": "..."},
     "opted_in": {"browser": {"since_ziya": "...", "playwright": "..."}, ...},
     "seen": {"data": "<ziya version the extra was first offered at>", ...}}

``opted_in`` is what the user installed through the installer (or had
installed before this file existed: an extra found present is ADOPTED as
opted-in, which is how existing users are migrated).  That is the
distinction the reconcile step needs -- "declined --data" and "never heard
of --data" look the same on disk otherwise.

Reconcile (at startup, see ``reconcile``)
-----------------------------------------
Runs on every server start, which is the first moment the upgraded wheel is
live and the only hook that also covers installs the auto-updater never
touches (per-version launchers, pipx).  Rules:

  * an opted-in extra that is now missing, not platform-blocked, and
    installs without sudo into the user's own cache or venv (browser, data,
    stats, drag) is refreshed automatically, in the background, with a log
    line;
  * an opted-in extra that needs sudo (TeX on macOS via brew) gets a notice
    naming the command, never an attempt;
  * an extra the user never opted into is only ANNOUNCED -- a 150 MB
    download on the first launch of a release is how people learn to turn
    auto-update off.

``ZIYA_EXTRAS_AUTO_REFRESH=0`` turns the automatic refresh off (notices
remain).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import optional_features as of
from .paths import get_ziya_home

STATE_FILE = "extras_state.json"
AUTO_REFRESH_ENV = "ZIYA_EXTRAS_AUTO_REFRESH"


def _darwin() -> bool:
    return sys.platform == "darwin"


@dataclass(frozen=True)
class Extra:
    key: str                 # "browser"
    target: str              # "--browser" (installer flag)
    title: str
    enables: str             # one line, user-facing
    kind: str                # "pip" (into sys.executable) | "browser" | "script"
    pip_packages: tuple = ()
    probe_modules: tuple = ()
    # Status probes for non-pip kinds: None when present, else what is missing.
    status_fn: Optional[Callable[[], Optional[str]]] = None
    blocked_fn: Optional[Callable[[], Optional[str]]] = None
    # Platforms the extra applies to; () means all.  Prefix-matched on sys.platform.
    platforms: tuple = ()
    # --all / no-target default includes it (on its platforms).  Every extra
    # does: the banner says "Install everything with: ziya-install-extras",
    # and for weeks --data / --stats were excluded from that run, so the
    # user had to discover and name them (2026-10-05, 2026-10-26).  Explicit
    # flags remain for installing one thing.
    default_in_all: bool = True
    # Installing needs sudo on this platform, so the server must not do it.
    needs_sudo_fn: Callable[[], bool] = lambda: False
    # Startup may re-install it when it was opted in and has gone missing.
    auto_refresh: bool = True

    def supported(self) -> bool:
        return not self.platforms or any(sys.platform.startswith(p) for p in self.platforms)

    def needs_sudo(self) -> bool:
        return bool(self.needs_sudo_fn())

    def server_installable(self) -> bool:
        return self.supported() and not self.needs_sudo()

    def missing(self) -> Optional[str]:
        """None when installed; otherwise what is missing."""
        if self.kind == "pip":
            import importlib.util
            absent = [m for m in self.probe_modules if importlib.util.find_spec(m) is None]
            return f"Python package(s): {', '.join(absent)}" if absent else None
        return self.status_fn() if self.status_fn else None

    def blocked(self) -> Optional[str]:
        return self.blocked_fn() if self.blocked_fn else None

    def command(self) -> str:
        return f"{of.INSTALL_CMD} {self.target}"


# Keep pip_packages in step with [tool.poetry.extras] data / stats / drag in
# pyproject.toml (tests/test_install_extras_data.py pins the names).
REGISTRY: List[Extra] = [
    Extra(
        key="browser", target="--browser", title="Chromium for Playwright",
        enables="The model can look at its own rendered diagrams (render_diagram); PDF export; frozen diagram artifacts in task runs.",
        kind="browser",
        # Resolved at call time so a probe patched on optional_features (tests,
        # editions) is the one consulted.
        status_fn=lambda: of.playwright_status(), blocked_fn=lambda: of.chromium_blocked_reason(),
    ),
    Extra(
        key="latex", target="--latex", title="LaTeX renderer toolchain",
        enables="Circuit, chemistry, pgfplots, TikZ and music diagrams rendered server-side.",
        kind="script",
        status_fn=lambda: of.tex_status(),
        # macOS installs BasicTeX via Homebrew and runs tlmgr under sudo; the
        # Linux route installs TeX Live under $HOME with no sudo, but it is a
        # multi-hundred-MB download, so it is still never refreshed unasked.
        needs_sudo_fn=_darwin, auto_refresh=False,
    ),
    Extra(
        key="data", target="--data", title="Local analytics (DuckDB)",
        enables="The analyze tool: read-only SQL over datasets and data files.",
        kind="pip",
        pip_packages=("duckdb>=1.1", "sqlglot>=25", "pyarrow>=15", "scipy>=1.11"),
        probe_modules=("duckdb", "sqlglot", "pyarrow", "scipy"),
    ),
    Extra(
        key="stats", target="--stats", title="Statistics (statsmodels)",
        enables="Method recipes in the statistical-charts skill.",
        kind="pip", pip_packages=("statsmodels>=0.14",), probe_modules=("statsmodels",),
    ),
    Extra(
        key="drag", target="--drag", title="Native drag helper (macOS)",
        enables="A diagram or table dragged out of the chat drops into Slack as a PNG file.",
        kind="pip", pip_packages=("pyobjc-framework-Cocoa>=10",), probe_modules=("AppKit",),
        platforms=("darwin",),
    ),
]

BY_KEY: Dict[str, Extra] = {x.key: x for x in REGISTRY}
BY_TARGET: Dict[str, Extra] = {x.target: x for x in REGISTRY}


# ── the previous install's venv ────────────────────────────────────────────

def previous_install_site_packages(prefix: Optional[Path] = None) -> Optional[Path]:
    """site-packages of the newest OTHER versioned install beside this one.

    A per-version venv layout (``<tools>/ziya/<version>/.venv``, as some
    launchers make) throws away every pip-level extra on each
    upgrade, before the new version's reconcile ever runs -- so "adopt what
    is present" finds nothing to adopt and the extras the user installed are
    merely announced, every upgrade (observed 2026-10-26: drag/data/stats
    "not installed" right after an upgrade).  The previous
    version's venv is still on disk, and what it holds is the user's choice.
    None when this interpreter is not in such a layout (pyenv, pipx, plain
    venv: those persist across upgrades and need no carry-over).
    """
    pfx = Path(sys.prefix if prefix is None else prefix)
    try:
        pfx = pfx.resolve()
    except OSError:
        return None
    if pfx.name != ".venv":
        return None
    version_dir, tools_dir = pfx.parent, pfx.parent.parent
    best: Optional[tuple] = None
    try:
        siblings = [d for d in tools_dir.iterdir() if d.is_dir() and d != version_dir]
    except OSError:
        return None
    for sib in siblings:
        for sp in (sib / ".venv" / "lib").glob("python*/site-packages"):
            try:
                key = (sp.stat().st_mtime, str(sp))
            except OSError:
                continue
            if best is None or key > best[0]:
                best = (key, sp)
    return best[1] if best else None


def installed_in(site_packages: Path, x: Extra) -> bool:
    """Whether a pip extra's modules are present in another interpreter's
    site-packages.  A path check, not an import: that interpreter may be a
    different Python version."""
    if x.kind != "pip":
        return False
    return all((site_packages / m).is_dir() or (site_packages / f"{m}.py").is_file()
               for m in x.probe_modules)


# ── versions ───────────────────────────────────────────────────────────────

def current_versions() -> Dict[str, Optional[str]]:
    try:
        from .version_util import get_current_version
        ziya = get_current_version()
    except Exception:  # noqa: BLE001
        ziya = None
    try:
        from importlib.metadata import version
        pw = version("playwright")
    except Exception:  # noqa: BLE001
        pw = None
    return {"ziya": ziya, "playwright": pw, "chromium_build": of.required_chromium_build()}


# ── state file ─────────────────────────────────────────────────────────────

def state_path() -> Path:
    return get_ziya_home() / STATE_FILE


def read_state() -> Dict:
    p = state_path()
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            data.setdefault("opted_in", {})
            data.setdefault("seen", {})
            data.setdefault("versions", {})
            return data
    except (OSError, ValueError):
        pass
    return {"versions": {}, "opted_in": {}, "seen": {}}


def write_state(state: Dict) -> None:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(p)
    except OSError:
        pass  # a missing state file only costs one re-detection next start


def record_opted_in(keys: List[str], state: Optional[Dict] = None) -> Dict:
    """Mark ``keys`` as installed by the user's choice, against current versions."""
    st = state if state is not None else read_state()
    vers = current_versions()
    for k in keys:
        if k in BY_KEY:
            st["opted_in"][k] = {"since_ziya": vers["ziya"], "playwright": vers["playwright"],
                                 "at": int(time.time())}
    if state is None:
        write_state(st)
    return st


# ── status for every surface ───────────────────────────────────────────────

def extra_status(x: Extra, state: Optional[Dict] = None) -> Dict:
    st = state if state is not None else read_state()
    missing = x.missing() if x.supported() else None
    blocked = x.blocked() if x.supported() else None
    opted = x.key in st["opted_in"]
    run = _runs.get(x.key)
    return {
        "key": x.key, "target": x.target, "title": x.title, "enables": x.enables,
        "supported": x.supported(),
        "installed": x.supported() and missing is None,
        "missing": missing, "blocked": blocked,
        "needs_sudo": x.needs_sudo(),
        "server_installable": x.server_installable() and not blocked,
        "opted_in": opted,
        # Opted in, was there, is not now: an upgrade moved the target.
        "stale": opted and missing is not None and not blocked,
        "new_in_this_version": st["seen"].get(x.key) == current_versions()["ziya"]
                               and not opted and missing is not None,
        "command": x.command(),
        "installing": bool(run and not run.done),
        # Outcome of the last in-process install (startup refresh or the
        # Install button), so a background refresh that failed -- an expired
        # index login, say -- is visible on the Settings page, not only in the log.
        "last_install_failed": bool(run and run.done and run.rc != 0),
        "last_install_tail": list(run.lines[-6:]) if run and run.done else [],
    }


def all_status() -> Dict:
    st = read_state()
    return {"versions": current_versions(), "install_cmd": of.INSTALL_CMD,
            "extras": [extra_status(x, st) for x in REGISTRY]}


# ── installing from inside the server ──────────────────────────────────────

@dataclass
class InstallRun:
    key: str
    lines: List[str] = field(default_factory=list)
    done: bool = False
    rc: Optional[int] = None
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict:
        return {"key": self.key, "done": self.done, "rc": self.rc,
                "ok": self.done and self.rc == 0, "lines": list(self.lines)}


_runs: Dict[str, InstallRun] = {}
_runs_lock = threading.Lock()


def install_command(x: Extra) -> Optional[List[str]]:
    """The subprocess that installs ``x`` for the interpreter Ziya runs under."""
    if x.kind == "pip":
        return [sys.executable, "-m", "pip", "install", *x.pip_packages]
    if x.kind == "browser":
        return [sys.executable, "-m", "playwright", "install", "chromium"]
    if x.kind == "script":
        from .install_extras import latex_script_path
        script = latex_script_path()
        if script is None:
            return None
        return [shutil.which("bash") or "/bin/bash", str(script), x.target, "--yes"]
    return None


def start_install(key: str) -> InstallRun:
    """Begin installing ``key`` in a background thread; return its run record.

    Refuses (ValueError) an unknown key, an extra this host cannot install
    (sudo, platform-blocked) and a run already in progress.  On success the
    extra is recorded as opted in and the browser gates are re-probed.
    """
    x = BY_KEY.get(key)
    if x is None:
        raise ValueError(f"unknown extra {key!r}")
    if not x.server_installable():
        raise ValueError(f"{x.title} must be installed from a terminal: {x.command()}")
    blocked = x.blocked()
    if blocked:
        raise ValueError(f"{x.title} cannot be installed on this host: {blocked}")
    cmd = install_command(x)
    if cmd is None:
        raise ValueError(f"no installer available for {x.title}")
    with _runs_lock:
        prev = _runs.get(key)
        if prev and not prev.done:
            return prev
        run = InstallRun(key=key)
        _runs[key] = run

    def _work() -> None:
        run.lines.append("$ " + " ".join(cmd))
        try:
            env = {**os.environ, "ZIYA_PYTHON": sys.executable}
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace", env=env)
            assert proc.stdout is not None
            for line in proc.stdout:
                run.lines.append(line.rstrip("\n"))
                del run.lines[:-400]
            run.rc = proc.wait()
        except OSError as e:
            run.lines.append(f"!! {e}")
            run.rc = 1
        # A package added to site-packages while this process runs may not be
        # found until the path importer's directory caches are dropped; do it
        # before the probe and so later imports (analyze -> duckdb) see it.
        import importlib
        importlib.invalidate_caches()
        if run.rc == 0 and x.missing() is None:
            record_opted_in([key])
            of.invalidate_browser_caches()
            run.lines.append(f"ok {x.title} installed.")
        elif run.rc == 0:
            run.lines.append(f"!! installer exited 0 but {x.title} is still missing: {x.missing()}")
            run.rc = 2
        run.done = True
        # The start was logged; the outcome must be too, or a failed background
        # refresh is indistinguishable from a successful one in the console.
        try:
            from .logging_utils import logger as log
            if run.rc == 0:
                note = " (restart Ziya for render_diagram)" if x.kind == "browser" else ""
                log.info(f"🧩 {x.title}: installed{note}.")
            else:
                tail = "; ".join(l for l in run.lines[-3:] if l.strip())
                log.warning(f"🧩 {x.title}: install failed (exit {run.rc}): {tail}. "
                            f"Run: {x.command()}")
        except Exception:  # noqa: BLE001 -- logging must not break the runner
            pass

    threading.Thread(target=_work, name=f"ziya-extras-{key}", daemon=True).start()
    return run


def install_status(key: str) -> Optional[Dict]:
    run = _runs.get(key)
    return run.as_dict() if run else None


# ── reconcile on startup ───────────────────────────────────────────────────

@dataclass
class ReconcileReport:
    adopted: List[str] = field(default_factory=list)    # found installed, now opted in
    carried: List[str] = field(default_factory=list)    # found in the previous version's venv
    refreshed: List[str] = field(default_factory=list)  # auto re-install started
    refreshing: List[str] = field(default_factory=list) # INFO lines for those starts
    notices: List[str] = field(default_factory=list)    # need the user: WARNING lines
    new: List[str] = field(default_factory=list)        # first offered at this version


def reconcile(auto_install: Optional[bool] = None,
              start: Callable[[str], object] = start_install,
              previous: Optional[Path] = None) -> ReconcileReport:
    if auto_install is None:
        auto_install = os.environ.get(AUTO_REFRESH_ENV, "1") != "0"
    st = read_state()
    vers = current_versions()
    rep = ReconcileReport()
    prev_sp = previous if previous is not None else previous_install_site_packages()
    for x in REGISTRY:
        if not x.supported():
            continue
        if x.key not in st["seen"]:
            st["seen"][x.key] = vers["ziya"]
            rep.new.append(x.key)
        missing = x.missing()
        if missing is None:
            if x.key not in st["opted_in"]:
                rep.adopted.append(x.key)
            record_opted_in([x.key], st)
            continue
        if x.key not in st["opted_in"] and prev_sp is not None and installed_in(prev_sp, x):
            # Gone from THIS venv only because the upgrade made a new one.
            rep.carried.append(x.key)
            record_opted_in([x.key], st)
        if x.blocked() or x.key not in st["opted_in"]:
            continue
        if auto_install and x.auto_refresh and x.server_installable():
            try:
                start(x.key)
                rep.refreshed.append(x.key)
                if x.kind == "browser":
                    why = (f"Playwright {vers['playwright']} needs {vers['chromium_build']}"
                           if vers.get("chromium_build") else "missing after upgrade")
                else:
                    why = "not in this version's environment"
                rep.refreshing.append(f"{x.title} ({why}); installing in the background.")
            except ValueError as e:
                rep.notices.append(f"{x.title}: {missing}. {e}")
        else:
            rep.notices.append(f"{x.title}: {missing} after upgrade. Run: {x.command()}")
    st["versions"] = vers
    write_state(st)
    return rep


async def reconcile_background() -> Optional[ReconcileReport]:
    """Run ``reconcile`` off the event loop and log what it did (server startup)."""
    import asyncio
    # The project logger, not logging.getLogger(__name__): logging_utils caps
    # the 'app' hierarchy at WARNING (and the ZIYA logger does not propagate),
    # so INFO lines from a module-named logger never reached the console --
    # the first startup with this code adopted browser/latex silently.
    from .logging_utils import logger as log
    try:
        rep = await asyncio.to_thread(reconcile)
    except Exception as e:  # noqa: BLE001 -- never affects startup
        log.warning(f"extras reconcile failed: {e}")
        return None
    for key in rep.adopted:
        log.info(f"🧩 Optional extra {key!r} found installed; recorded as opted in.")
    for key in rep.carried:
        log.info(f"🧩 Optional extra {key!r} was in the previous version's venv; carrying it over.")
    for line in rep.refreshing:
        log.info(f"🧩 {line}")
    for line in rep.notices:
        log.warning(f"🧩 {line}")
    return rep


def installing_keys() -> List[str]:
    """Extras with an in-process install still running (startup refresh or UI)."""
    return [k for k, r in _runs.items() if not r.done]
