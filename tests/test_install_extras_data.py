"""``ziya-install-extras --data / --stats`` (app/utils/install_extras.py).

They pip-install into the running interpreter and are removed before the
remaining arguments reach the shell script.  The script is never run for a
--data-only call, since with no target it would default to --all.

The handling lives in ``run`` because the internal edition's console script
(``plugins.install_extras.main``) forwards its argv to ``run`` and never
calls ``main``.  The first version handled --data in ``main`` only, so on the
internal install ``--data`` reached the shell script, which rejected it
(2026-10-04).  The wrapper test below drives that route.
"""
import sys

import pytest

import app.utils.install_extras as ie

_REAL_PROBE = ie.python_extra_installed


@pytest.fixture(autouse=True)
def _nothing_preinstalled(monkeypatch):
    # These tests are about what the installer DOES for a target; the
    # already-installed short-circuit is covered by its own tests below.
    monkeypatch.setattr(ie, "python_extra_installed", lambda t: False)


@pytest.fixture
def calls(monkeypatch):
    seen = {"script": [], "pip": []}
    monkeypatch.setattr(ie, "_run_script", lambda args: seen["script"].append(list(args)) or 0)

    class _R:
        returncode = 0

    def fake_sub(cmd, *a, **k):
        seen["pip"].append(cmd)
        return _R()

    monkeypatch.setattr(ie.subprocess, "run", fake_sub)
    return seen


@pytest.mark.parametrize("entry", ["main", "run"])
def test_data_only_installs_with_this_python_and_skips_script(calls, entry):
    assert getattr(ie, entry)(["--data"]) == 0
    assert calls["script"] == []
    (cmd,) = calls["pip"]
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
    assert {p.split(">")[0] for p in cmd[4:]} == {"duckdb", "sqlglot", "pyarrow", "scipy"}


def test_data_and_browser_runs_both_and_strips_data(calls):
    ie.run(["--data", "--browser", "--yes"])
    assert calls["script"] == [["--browser", "--yes"]]
    assert len(calls["pip"]) == 1


def test_stats_and_dry_run(calls, capsys):
    ie.run(["--stats", "--dry-run"])
    assert calls["pip"] == [] and calls["script"] == []
    assert "statsmodels" in capsys.readouterr().out


def test_existing_targets_are_forwarded_unchanged(calls, monkeypatch):
    # Off macOS there are no default Python extras, so the no-target run is
    # exactly the script's own --all.
    monkeypatch.setattr(ie.sys, "platform", "linux")
    ie.main(["--latex", "--dry-run"])
    ie.main([])
    ie.main(["--all"])
    ie.run(["--help"])
    assert calls["script"] == [["--latex", "--dry-run"], [], ["--all"], ["--help"]]
    assert calls["pip"] == []


@pytest.mark.parametrize("argv", [[], ["--all"], ["--all", "--yes"]])
def test_default_run_on_macos_installs_drag_then_runs_the_script(calls, monkeypatch, argv):
    monkeypatch.setattr(ie.sys, "platform", "darwin")
    monkeypatch.setattr(ie, "python_extra_installed", lambda t: False)
    assert ie.main(argv) == 0
    (cmd,) = calls["pip"]
    assert cmd[4:] == ["pyobjc-framework-Cocoa>=10"]
    # The script still gets its own argv (no target == its --all default).
    assert calls["script"] == [argv]


def test_default_run_skips_drag_when_pyobjc_is_present_and_off_macos(calls, monkeypatch, capsys):
    monkeypatch.setattr(ie.sys, "platform", "darwin")
    monkeypatch.setattr(ie, "python_extra_installed", lambda t: t == "--drag")
    assert ie.main([]) == 0
    assert calls["pip"] == [] and calls["script"] == [[]]
    assert "--drag: ok already installed" in capsys.readouterr().out
    monkeypatch.setattr(ie.sys, "platform", "linux")
    monkeypatch.setattr(ie, "python_extra_installed", lambda t: False)
    ie.main([])
    assert calls["pip"] == []


def test_explicit_drag_alone_does_not_run_the_script(calls, monkeypatch):
    monkeypatch.setattr(ie, "python_extra_installed", lambda t: False)
    ie.run(["--drag"])
    assert calls["script"] == [] and len(calls["pip"]) == 1


def test_probe_matches_the_installed_module(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda m: object() if m == "AppKit" else None)
    # The autouse fixture stubs the module attribute; test the real function.
    assert _REAL_PROBE("--drag") is True
    assert _REAL_PROBE("--data") is False
    assert _REAL_PROBE("--nope") is False


def test_internal_wrapper_route_installs_data(calls, monkeypatch):
    """The installed ``ziya-install-extras`` on the internal edition."""
    wrapper = pytest.importorskip("plugins.install_extras")
    # The wrapper treats --data as "no target" and also installs its own
    # tooling; that part is the wrapper's business, stub it out.
    monkeypatch.setattr(wrapper, "_install_internal_tooling", lambda *a, **k: None)
    assert wrapper.main(["--data"]) == 0
    assert len(calls["pip"]) == 1
    assert all("--data" not in a for a in calls["script"])


def test_install_hint_names_the_real_flag():
    from app.utils.analytics import INSTALL_HINT
    flag = INSTALL_HINT.split("ziya-install-extras", 1)[1].split()[0]
    assert flag in ie.PYTHON_EXTRAS


def test_engine_imports_match_data_packages():
    # analytics.available() imports duckdb and sqlglot; both must be installed by --data.
    names = {p.split(">")[0] for p in ie.PYTHON_EXTRAS["--data"]}
    assert {"duckdb", "sqlglot"} <= names


# A failed install behind a private mirror (2026-10-04: expired token, 401,
# then "no matching distribution") must say how to retry against PyPI.

def _failing_pip(monkeypatch, config_out=""):
    seen = []

    class _R:
        def __init__(self, rc, out=""):
            self.returncode, self.stdout = rc, out

    def fake(cmd, *a, **k):
        seen.append(list(cmd))
        return _R(0, config_out) if cmd[3:5] == ["config", "list"] else _R(1)

    monkeypatch.setattr(ie.subprocess, "run", fake)
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.setattr(ie, "_resolves", lambda host: True)
    return seen


def test_failed_install_from_a_mirror_names_it_and_hides_the_token(monkeypatch, capsys):
    _failing_pip(monkeypatch, "global.index-url='https://aws:SECRET@mirror.example.com/pypi/simple/'\n")
    assert ie.run(["--data"]) == 1
    out = capsys.readouterr().out
    assert "mirror.example.com" in out
    assert "PIP_INDEX_URL=https://pypi.org/simple/ ziya-install-extras --data" in out
    assert "SECRET" not in out


def test_env_index_takes_precedence_over_pip_config(monkeypatch, capsys):
    seen = _failing_pip(monkeypatch, "global.index-url='https://pypi.org/simple/'\n")
    monkeypatch.setenv("PIP_INDEX_URL", "https://other.example.net/simple/")
    ie.run(["--stats"])
    assert "other.example.net" in capsys.readouterr().out
    assert not any(c[3:5] == ["config", "list"] for c in seen)


def test_failed_install_from_pypi_adds_no_hint(monkeypatch, capsys):
    seen = _failing_pip(monkeypatch, "global.index-url='https://pypi.org/simple/'\n")
    assert ie.run(["--data"]) == 1
    out = capsys.readouterr().out
    assert "==> --data" in out
    assert any(c[2:4] == ["pip", "install"] for c in seen)
    assert "PIP_INDEX_URL" not in out


def test_hook_replaces_the_generic_hint_and_retries_once(monkeypatch, capsys):
    seen, rcs = [], iter([1, 0])
    url = "https://aws:T@mirror.example.com/simple/"

    class _R:
        def __init__(self, rc, out=""):
            self.returncode, self.stdout = rc, out

    def fake(cmd, *a, **k):
        seen.append(list(cmd))
        return _R(0, f"global.index-url='{url}'\n") if cmd[3:5] == ["config", "list"] else _R(next(rcs))

    monkeypatch.setattr(ie.subprocess, "run", fake)
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.setattr(ie, "_resolves", lambda host: True)
    got = []
    monkeypatch.setattr(ie, "index_failure_hook", lambda u, t: got.append((u, t)) or True)
    assert ie.run(["--data"]) == 0
    assert got == [(url, ["--data"])]
    assert sum(c[3:4] == ["install"] for c in seen) == 2
    assert "PIP_INDEX_URL" not in capsys.readouterr().out


def test_hook_returning_none_keeps_the_generic_hint(monkeypatch, capsys):
    seen = _failing_pip(monkeypatch, "global.index-url='https://mirror.example.com/simple/'\n")
    monkeypatch.setattr(ie, "index_failure_hook", lambda u, t: None)
    assert ie.run(["--data"]) == 1
    assert "PIP_INDEX_URL=https://pypi.org/simple/" in capsys.readouterr().out
    assert sum(c[3:4] == ["install"] for c in seen) == 1


def test_unresolvable_mirror_is_reported_as_network_not_login(monkeypatch, capsys):
    # 2026-10-04: DNS failed mid-run; the hook renewed a valid login and retried for nothing.
    seen = _failing_pip(monkeypatch, "global.index-url='https://aws:T@mirror.example.com/simple/'\n")
    monkeypatch.setattr(ie, "_resolves", lambda host: False)
    called = []
    monkeypatch.setattr(ie, "index_failure_hook", lambda u, t: called.append(u) or True)
    assert ie.run(["--data"]) == 1
    out = capsys.readouterr().out
    assert "Could not resolve mirror.example.com" in out
    assert called == [] and "login has probably expired" not in out
    assert sum(c[3:4] == ["install"] for c in seen) == 1      # no retry


def test_resolves_is_false_on_lookup_error(monkeypatch):
    import socket
    def boom(*a, **k):
        raise socket.gaierror(8, "nodename nor servname provided")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert ie._resolves("anything.invalid") is False
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("x",)])
    assert ie._resolves("anything") is True
