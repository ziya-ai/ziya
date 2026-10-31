"""
Optional extras survive an upgrade (app/utils/extras_registry.py).

Two failure modes this guards:
  * a NEW extra appears in a release and an existing install never hears of
    it -- the banner and the Settings endpoint must list every registry entry
    that is missing, not a hard-coded two;
  * an EXISTING extra goes stale -- a Playwright bump moves the Chromium
    build, so an opted-in --browser must be re-installed at startup without
    the user being told to run anything, while sudo targets (TeX on macOS)
    and never-opted-in extras are only announced.

The state file is redirected to a temp ZIYA_HOME; no pip, no browser download
and no shell script run here -- the install step is observed through the
``start`` callable reconcile receives, and the runner is driven with a
harmless subprocess.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

from app.utils import extras_registry as er
from app.utils import install_extras as ie
from app.utils import optional_features as of

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    monkeypatch.setattr(er, "_runs", {})
    return tmp_path


def _present(monkeypatch, *keys):
    """Make exactly ``keys`` report installed; everything else missing, nothing blocked."""
    def missing(self):
        return None if self.key in keys else f"{self.key} bits"
    monkeypatch.setattr(er.Extra, "missing", missing)
    monkeypatch.setattr(er.Extra, "blocked", lambda self: None)
    monkeypatch.setattr(er.Extra, "supported", lambda self: True)


# -- registry is the single source -------------------------------------------

def test_registry_matches_pyproject_extras():
    text = (ROOT / "pyproject.toml").read_text()
    for key in ("data", "stats", "drag"):
        m = re.search(rf"^{key} = \[(.*)\]", text, re.M)
        assert m, key
        declared = {p.strip().strip('"') for p in m.group(1).split(",")}
        ours = {p.split(">")[0] for p in er.BY_KEY[key].pip_packages}
        assert ours == declared, (key, ours, declared)


def test_installer_tables_are_views_of_the_registry():
    assert set(ie.PYTHON_EXTRAS) == {x.target for x in er.REGISTRY if x.kind == "pip"}
    assert ie.PYTHON_EXTRAS["--data"] == list(er.BY_KEY["data"].pip_packages)
    assert ie.PYTHON_EXTRA_PROBES["--drag"] == ["AppKit"]
    assert {"--browser", "--latex", "--all"} <= ie.SCRIPT_TARGETS


def test_banner_names_every_missing_registry_extra(monkeypatch):
    _present(monkeypatch)  # nothing installed
    lines = of.missing_feature_lines()
    joined = "\n".join(lines)
    for x in er.REGISTRY:
        assert x.title in joined, x.key
        assert x.target in joined, x.key
    assert "ziya-install-extras" in joined
    _present(monkeypatch, *er.BY_KEY)  # everything installed
    assert of.missing_feature_lines() == []


def test_latex_is_never_auto_refreshed_and_needs_sudo_on_macos():
    x = er.BY_KEY["latex"]
    assert x.auto_refresh is False
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "darwin")
        assert x.needs_sudo() and not x.server_installable()
        mp.setattr(sys, "platform", "linux")
        assert not x.needs_sudo()


# -- state --------------------------------------------------------------------

def test_record_opted_in_writes_versions(home, monkeypatch):
    monkeypatch.setattr(er, "current_versions",
                        lambda: {"ziya": "1.0", "playwright": "1.58.0", "chromium_build": "c-1208"})
    er.record_opted_in(["browser", "bogus"])
    st = json.loads((home / er.STATE_FILE).read_text())
    assert set(st["opted_in"]) == {"browser"}
    assert st["opted_in"]["browser"]["playwright"] == "1.58.0"


def test_corrupt_state_is_empty_not_fatal(home):
    (home / er.STATE_FILE).write_text("{nope")
    assert er.read_state()["opted_in"] == {}


# -- reconcile ------------------------------------------------------------------

def test_reconcile_adopts_installed_extras_as_opted_in(home, monkeypatch):
    """Migration: a user who installed Chromium before this file existed is
    treated as opted in, so the NEXT upgrade refreshes it."""
    _present(monkeypatch, "browser", "data")
    started = []
    rep = er.reconcile(auto_install=True, start=started.append)
    assert set(rep.adopted) == {"browser", "data"}
    assert started == [], "nothing missing, nothing installed"
    assert set(er.read_state()["opted_in"]) == {"browser", "data"}


def test_reconcile_refreshes_opted_in_extra_that_went_missing(home, monkeypatch):
    """The upgrade case: --browser was opted in, Playwright moved its build."""
    er.record_opted_in(["browser"])
    _present(monkeypatch)
    started = []
    rep = er.reconcile(auto_install=True, start=started.append)
    assert started == ["browser"]
    assert rep.refreshed == ["browser"]
    # An automatic refresh is information, not a warning, and says nothing
    # about Playwright unless it is the browser.
    assert rep.notices == [], rep.notices
    assert any("Chromium" in n and "background" in n for n in rep.refreshing), rep.refreshing


def test_pip_refresh_line_does_not_mention_playwright(home, monkeypatch):
    er.record_opted_in(["data"])
    _present(monkeypatch)
    rep = er.reconcile(auto_install=True, start=lambda k: None, previous=None)
    (line,) = [l for l in rep.refreshing if "Local analytics" in l]
    assert "Playwright" not in line and "this version's environment" in line


def test_banner_does_not_call_an_in_progress_install_missing(home, monkeypatch):
    """2026-10-27: the startup banner listed 'duckdb, pyarrow' as missing while
    the startup refresh was still pip-installing them."""
    _present(monkeypatch)
    monkeypatch.setattr(er, "REGISTRY", [er.BY_KEY["data"], er.BY_KEY["stats"]])
    er._runs["data"] = er.InstallRun(key="data")          # running
    lines = of.missing_feature_lines()
    joined = "\n".join(lines)
    assert "Installing in the background: Local analytics (DuckDB)" in joined
    assert "missing data bits" not in joined
    assert "missing stats bits" in joined                 # positive: real gaps still named
    er._runs["data"].done = True
    assert "missing data bits" in "\n".join(of.missing_feature_lines())
    # Only installing: one line, no "install everything" advice.
    monkeypatch.setattr(er, "REGISTRY", [er.BY_KEY["data"]])
    er._runs["data"] = er.InstallRun(key="data")
    assert of.missing_feature_lines() == ["  Installing in the background: Local analytics (DuckDB)."]


def test_install_outcome_is_logged_and_import_caches_dropped(home, monkeypatch):
    import importlib
    from app.utils import logging_utils
    _present(monkeypatch)
    dropped, logged = [], []
    monkeypatch.setattr(importlib, "invalidate_caches", lambda: dropped.append(1))
    monkeypatch.setattr(logging_utils.logger, "info", lambda m, *a, **k: logged.append(("info", m)))
    monkeypatch.setattr(logging_utils.logger, "warning", lambda m, *a, **k: logged.append(("warn", m)))
    monkeypatch.setattr(er, "install_command", lambda x: [sys.executable, "-c", "raise SystemExit(1)"])
    run = er.start_install("stats")
    _wait(run)
    assert dropped and any(lvl == "warn" and "install failed" in m and "--stats" in m for lvl, m in logged), logged
    logged.clear()
    monkeypatch.setattr(er, "install_command", lambda x: [sys.executable, "-c", "pass"])
    monkeypatch.setattr(er.Extra, "missing", lambda self: None)
    run = er.start_install("data")
    _wait(run)
    assert ("info", "🧩 Local analytics (DuckDB): installed.") in logged, logged


def test_every_extra_is_in_the_default_run():
    """'Install everything with: ziya-install-extras' must be literally true."""
    assert all(x.default_in_all for x in er.REGISTRY), [x.key for x in er.REGISTRY if not x.default_in_all]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "darwin")
        assert set(ie.default_python_extras()) == {"--data", "--stats", "--drag"}
        mp.setattr(sys, "platform", "linux")
        assert set(ie.default_python_extras()) == {"--data", "--stats"}


# -- carry-over from the previous version's venv (per-version venv layouts) ----

def _versioned_layout(tmp_path, *, old_has):
    """<tools>/ziya/<old>/.venv/lib/python3.12/site-packages holding ``old_has``;
    <tools>/ziya/<new>/.venv is the interpreter under test."""
    tools = tmp_path / "tools" / "ziya"
    old_sp = tools / "0.8.11.0.post6" / ".venv" / "lib" / "python3.12" / "site-packages"
    old_sp.mkdir(parents=True)
    for m in old_has:
        (old_sp / m).mkdir()
    new_prefix = tools / "0.8.11.0.post7" / ".venv"
    (new_prefix / "lib" / "python3.14" / "site-packages").mkdir(parents=True)
    return new_prefix, old_sp


def test_previous_install_site_packages_finds_the_sibling_venv(tmp_path):
    new_prefix, old_sp = _versioned_layout(tmp_path, old_has=["duckdb"])
    assert er.previous_install_site_packages(new_prefix) == old_sp
    # Not a per-version layout: nothing to carry from.
    assert er.previous_install_site_packages(tmp_path / "pyenv" / "versions" / "3.12.8") is None
    assert er.previous_install_site_packages(tmp_path / "lonely" / ".venv") is None


def test_installed_in_checks_paths_not_imports(tmp_path):
    _, old_sp = _versioned_layout(tmp_path, old_has=["duckdb", "sqlglot", "pyarrow", "scipy"])
    assert er.installed_in(old_sp, er.BY_KEY["data"])
    assert not er.installed_in(old_sp, er.BY_KEY["stats"])
    assert not er.installed_in(old_sp, er.BY_KEY["browser"]), "only pip extras live in a venv"


def test_reconcile_carries_over_and_refreshes_extras_from_the_previous_venv(home, tmp_path, monkeypatch):
    """toolbox update: the new venv has none of the pip extras, the old one
    had data + drag.  Both must be recorded as opted in and re-installed, not
    announced as never-installed."""
    _present(monkeypatch)
    new_prefix, old_sp = _versioned_layout(
        tmp_path, old_has=["duckdb", "sqlglot", "pyarrow", "scipy", "AppKit"])
    started = []
    rep = er.reconcile(auto_install=True, start=started.append, previous=old_sp)
    assert set(rep.carried) == {"data", "drag"}
    assert set(started) == {"data", "drag"}
    assert "stats" not in rep.carried and "stats" not in started
    assert {"data", "drag"} <= set(er.read_state()["opted_in"])
    # A second reconcile with the previous venv gone keeps the opt-in.
    rep2 = er.reconcile(auto_install=False, previous=None)
    assert rep2.carried == []
    assert any("Local analytics" in n and "ziya-install-extras --data" in n for n in rep2.notices), rep2.notices


def test_reconcile_never_installs_what_the_user_did_not_opt_into(home, monkeypatch):
    _present(monkeypatch)
    started = []
    rep = er.reconcile(auto_install=True, start=started.append, previous=None)
    assert started == []
    assert rep.refreshed == []
    # First sight of every extra at this version is recorded for the UI's "new" badge.
    assert set(rep.new) == set(er.BY_KEY)
    assert er.reconcile(auto_install=True, start=started.append).new == []


def test_reconcile_notice_not_install_for_sudo_or_non_refresh_targets(home, monkeypatch):
    er.record_opted_in(["latex"])
    _present(monkeypatch)
    started = []
    rep = er.reconcile(auto_install=True, start=started.append)
    assert started == []
    assert any("LaTeX" in n and "ziya-install-extras --latex" in n for n in rep.notices), rep.notices


def test_reconcile_skips_platform_blocked(home, monkeypatch):
    er.record_opted_in(["browser"])
    _present(monkeypatch)
    monkeypatch.setattr(er.Extra, "blocked", lambda self: "glibc 2.26" if self.key == "browser" else None)
    started = []
    rep = er.reconcile(auto_install=True, start=started.append)
    assert started == [] and rep.notices == []


def test_env_disables_auto_refresh(home, monkeypatch):
    er.record_opted_in(["browser"])
    _present(monkeypatch)
    monkeypatch.setenv(er.AUTO_REFRESH_ENV, "0")
    started = []
    rep = er.reconcile(start=started.append)
    assert started == []
    assert any("Run: ziya-install-extras --browser" in n for n in rep.notices), rep.notices


def test_reconcile_start_failure_is_a_notice_not_a_crash(home, monkeypatch):
    er.record_opted_in(["browser"])
    _present(monkeypatch)

    def boom(key):
        raise ValueError("no installer")
    rep = er.reconcile(auto_install=True, start=boom)
    assert rep.refreshed == [] and any("no installer" in n for n in rep.notices)


# -- status for the endpoint / UI -----------------------------------------------

def test_status_marks_stale_and_new(home, monkeypatch):
    _present(monkeypatch)
    monkeypatch.setattr(er, "current_versions",
                        lambda: {"ziya": "9.9", "playwright": None, "chromium_build": None})
    er.record_opted_in(["browser"])
    er.reconcile(auto_install=False)
    by = {s["key"]: s for s in er.all_status()["extras"]}
    assert by["browser"]["stale"] and by["browser"]["opted_in"] and not by["browser"]["installed"]
    assert by["data"]["new_in_this_version"] and not by["data"]["stale"]
    assert by["browser"]["command"] == "ziya-install-extras --browser"


def test_status_reports_a_failed_background_install(home, monkeypatch):
    """A startup refresh that failed (expired index login) must show on the
    Settings page, not only in a log line nobody read."""
    _present(monkeypatch)
    monkeypatch.setattr(er, "install_command",
                        lambda x: [sys.executable, "-c", "print('401 nope'); raise SystemExit(1)"])
    run = er.start_install("data")
    _wait(run)
    by = {s["key"]: s for s in er.all_status()["extras"]}
    assert by["data"]["last_install_failed"] is True
    assert any("401 nope" in l for l in by["data"]["last_install_tail"])
    assert by["stats"]["last_install_failed"] is False and by["stats"]["last_install_tail"] == []


# -- the in-server installer ----------------------------------------------------

def _wait(run, timeout=10):
    t0 = time.time()
    while not run.done and time.time() - t0 < timeout:
        time.sleep(0.02)
    assert run.done, run.lines


def test_start_install_records_opt_in_and_clears_browser_caches(home, monkeypatch):
    _present(monkeypatch)
    monkeypatch.setattr(er, "install_command",
                        lambda x: [sys.executable, "-c", "print('installing'); print('done')"])
    cleared = []
    monkeypatch.setattr(of, "invalidate_browser_caches", lambda: cleared.append(1))
    # The probe flips to installed once the command has run.
    monkeypatch.setattr(er.Extra, "missing", lambda self: None)
    run = er.start_install("data")
    _wait(run)
    assert run.rc == 0 and run.as_dict()["ok"]
    assert "installing" in run.lines and run.lines[0].startswith("$ ")
    assert "data" in er.read_state()["opted_in"]
    assert cleared == [1]
    assert er.install_status("data")["done"]


def test_start_install_exit_zero_but_still_missing_is_a_failure(home, monkeypatch):
    _present(monkeypatch)
    monkeypatch.setattr(er, "install_command", lambda x: [sys.executable, "-c", "pass"])
    run = er.start_install("data")
    _wait(run)
    assert run.rc == 2 and not run.as_dict()["ok"]
    assert "data" not in er.read_state()["opted_in"]


def test_start_install_refuses_sudo_blocked_and_unknown(home, monkeypatch):
    _present(monkeypatch)
    with pytest.raises(ValueError):
        er.start_install("nope")
    monkeypatch.setattr(er.Extra, "blocked", lambda self: "glibc 2.26")
    with pytest.raises(ValueError, match="glibc"):
        er.start_install("browser")
    monkeypatch.setattr(er.Extra, "blocked", lambda self: None)
    monkeypatch.setattr(er.Extra, "needs_sudo", lambda self: True)
    with pytest.raises(ValueError, match="terminal"):
        er.start_install("latex")


def test_start_install_is_idempotent_while_running(home, monkeypatch):
    _present(monkeypatch)
    monkeypatch.setattr(er, "install_command",
                        lambda x: [sys.executable, "-c", "import time; time.sleep(0.5)"])
    a = er.start_install("stats")
    b = er.start_install("stats")
    assert a is b
    _wait(a)


def test_install_commands_target_this_interpreter():
    assert er.install_command(er.BY_KEY["data"])[:4] == [sys.executable, "-m", "pip", "install"]
    assert er.install_command(er.BY_KEY["browser"]) == [sys.executable, "-m", "playwright", "install", "chromium"]
    latex = er.install_command(er.BY_KEY["latex"])
    assert latex is not None and latex[-2:] == ["--latex", "--yes"]


# -- seam: the console installer records what it installed ------------------------

def test_console_installer_records_opt_in_after_script_run(home, monkeypatch):
    ran = []
    monkeypatch.setattr(ie, "_run_script", lambda args: ran.append(args) or 0)
    monkeypatch.setattr(er.Extra, "missing", lambda self: None)
    monkeypatch.setattr(er.Extra, "supported", lambda self: True)
    assert ie.run(["--browser"]) == 0
    assert ran == [["--browser"]]
    assert set(er.read_state()["opted_in"]) == {"browser"}


def test_console_installer_does_not_record_a_dry_run(home, monkeypatch):
    monkeypatch.setattr(ie, "_run_script", lambda args: 0)
    monkeypatch.setattr(er.Extra, "missing", lambda self: None)
    assert ie.run(["--browser", "--dry-run"]) == 0
    assert er.read_state()["opted_in"] == {}


def test_console_installer_records_pip_extra(home, monkeypatch):
    monkeypatch.setattr(ie, "python_extra_installed", lambda t: False)
    monkeypatch.setattr(ie.subprocess, "run", lambda cmd, **kw: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(er.Extra, "missing", lambda self: None)
    assert ie.run(["--data"]) == 0
    assert set(er.read_state()["opted_in"]) == {"data"}


# -- step 5: the status text names the build ----------------------------------------

def test_playwright_status_names_required_build_and_stale_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(of, "playwright_package_available", lambda: True)
    monkeypatch.setattr(of, "required_chromium_build", lambda: "chromium_headless_shell-1208")
    monkeypatch.setattr(of, "playwright_version", lambda: "1.58.0")
    (tmp_path / "chromium_headless_shell-1194").mkdir()
    s = of.playwright_status()
    assert "chromium_headless_shell-1208" in s and "1.58.0" in s
    assert "found chromium_headless_shell-1194" in s
    (tmp_path / "chromium_headless_shell-1208").mkdir()
    (tmp_path / "chromium_headless_shell-1208" / "INSTALLATION_COMPLETE").write_text("")
    assert of.playwright_status() is None
