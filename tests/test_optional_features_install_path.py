"""
One install command for every optional feature, and every gap points at it.

Before this change the optional capabilities were reached by four unrelated
recipes -- a pip extra (`ziya[render]`), a separate `playwright install
chromium`, a brew+tlmgr script nothing in the docs or the product referenced,
and a Poetry dependency GROUP for scapy that had no pip spelling at all -- and
`ziya[all]` contained none of them.  A new user hitting a gap was quoted a
different command at each surface.

Contract pinned here:

  * app.utils.optional_features is the single source of truth for "what is
    missing" and names `ziya-install-extras` as the remedy.
  * The Playwright gate on render_diagram checks for the BROWSER too, not just
    the pip package: `pip install playwright` without `playwright install
    chromium` used to register the tool and fail at launch.
  * pyproject: scapy and playwright are hard deps; the `pcap` group is gone;
    `all` no longer carries torch (nothing imports it).
  * install_extras.sh accepts --browser / --latex / --all and the entry point
    forwards them instead of stripping --latex.
  * The startup banner, the tool gate, the LaTeX not-installed result, and the
    HTTP routes all quote `ziya-install-extras`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.utils import optional_features as of

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# optional_features: browser detection follows Playwright's registry rules
# --------------------------------------------------------------------------

class TestBrowserDetection:

    def test_missing_cache_dir_means_no_browser(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "nope"))
        assert of.chromium_browser_available() is False

    def test_package_only_install_is_not_enough(self, tmp_path, monkeypatch):
        # The directory exists (Playwright creates it) but holds no chromium.
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "firefox-1234").mkdir()
        assert of.chromium_browser_available() is False

    @pytest.mark.parametrize("name", ["chromium-1148", "chromium_headless_shell-1148"])
    def test_either_chromium_build_counts(self, tmp_path, monkeypatch, name):
        # >= 1.49 launches headless on the headless shell; both are laid down
        # by `playwright install chromium`, so either proves the install ran.
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / name).mkdir()
        assert of.chromium_browser_available() is True

    def test_status_distinguishes_package_from_browser(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        monkeypatch.setattr(of, "playwright_package_available", lambda: False)
        assert of.playwright_status() == "playwright package"
        monkeypatch.setattr(of, "playwright_package_available", lambda: True)
        assert "Chromium" in of.playwright_status()
        (tmp_path / "chromium-1").mkdir()
        assert of.playwright_status() is None

    def test_banner_lines_name_the_one_command(self, monkeypatch):
        monkeypatch.setattr(of, "playwright_status", lambda: "playwright package")
        monkeypatch.setattr(of, "tex_status", lambda: "TeX distribution")
        lines = of.missing_feature_lines()
        assert any("ziya-install-extras" in l for l in lines)
        assert any("--browser" in l for l in lines)
        assert any("--latex" in l for l in lines)
        # Positive control: nothing missing -> nothing said.
        monkeypatch.setattr(of, "playwright_status", lambda: None)
        monkeypatch.setattr(of, "tex_status", lambda: None)
        assert of.missing_feature_lines() == []


# --------------------------------------------------------------------------
# The render_diagram gate consults the browser check, via the shared module
# --------------------------------------------------------------------------

class TestRenderGateChecksBrowser:

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        import app.services.diagram_renderer as dr
        before = dr._playwright_available
        dr._playwright_available = None
        yield
        dr._playwright_available = before

    def test_package_without_browser_does_not_register_the_tool(self, monkeypatch):
        import app.services.diagram_renderer as dr
        from app.mcp import builtin_tools
        monkeypatch.setattr(of, "playwright_package_available", lambda: True)
        monkeypatch.setattr(of, "chromium_browser_available", lambda: False)
        assert dr._check_playwright() is False, (
            "REGRESSION: _check_playwright passes on the pip package alone; "
            "the tool would be offered and fail at launch"
        )
        assert builtin_tools.get_diagram_render_tools() == []

    def test_package_and_browser_registers_the_tool(self, monkeypatch):
        import app.services.diagram_renderer as dr
        from app.mcp import builtin_tools
        monkeypatch.setattr(of, "playwright_package_available", lambda: True)
        monkeypatch.setattr(of, "chromium_browser_available", lambda: True)
        assert dr._check_playwright() is True
        names = [t.__name__ for t in builtin_tools.get_diagram_render_tools()]
        assert "RenderDiagramTool" in names

    def test_gate_log_quotes_the_install_command(self, monkeypatch):
        import app.services.diagram_renderer as dr
        from app.mcp import builtin_tools
        from app.utils.logging_utils import logger as ziya_logger
        monkeypatch.setattr(of, "playwright_package_available", lambda: True)
        monkeypatch.setattr(of, "chromium_browser_available", lambda: False)
        seen = []
        monkeypatch.setattr(ziya_logger, "info", lambda msg, *a, **k: seen.append(str(msg)))
        builtin_tools.get_diagram_render_tools()
        assert any("ziya-install-extras" in m for m in seen), seen
        assert not any("pip install" in m for m in seen), (
            "the gate still quotes a raw pip command instead of the installer"
        )


# --------------------------------------------------------------------------
# Every surface that reports the gap names the same command
# --------------------------------------------------------------------------

def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", [
    "app/mcp/tools/diagram_render.py",
    "app/routes/diagram_routes.py",
    "app/services/latex_renderer.py",
    "app/server.py",
])
def test_surface_quotes_the_installer(rel):
    src = _src(rel)
    # A surface may either name the installer itself, consult the shared
    # optional_features module, or forward the ImportError text that
    # DiagramRenderer.create composes (which does both).  All three reach the
    # user with the same command; what is forbidden is a third, hand-written
    # recipe (see test_no_surface_quotes_the_raw_two_step_recipe).
    assert (
        "ziya-install-extras" in src
        or "optional_features" in src
        or "except ImportError as exc" in src
    ), f"{rel} does not point the user at ziya-install-extras"


@pytest.mark.parametrize("rel", [
    "app/mcp/tools/diagram_render.py",
    "app/routes/diagram_routes.py",
    "app/mcp/builtin_tools.py",
])
def test_no_surface_quotes_the_raw_two_step_recipe(rel):
    assert "pip install playwright && playwright install chromium" not in _src(rel), (
        f"{rel} still quotes the two-command recipe the installer replaces"
    )


# --------------------------------------------------------------------------
# pyproject: the dependency story matches the docs
# --------------------------------------------------------------------------

class TestPyprojectDependencyStory:

    @pytest.fixture(scope="class")
    def toml(self):
        return _src("pyproject.toml")

    def test_scapy_is_a_hard_dependency_not_a_group(self, toml):
        assert "[tool.poetry.group.pcap" not in toml, (
            "the `pcap` group has no pip spelling; scapy must be a plain dep"
        )
        main = toml.split("[tool.poetry.dependencies]", 1)[1].split("[tool.poetry.", 1)[0]
        assert re.search(r"^scapy\s*=", main, re.M), "scapy missing from main dependencies"

    def test_playwright_is_a_hard_dependency(self, toml):
        main = toml.split("[tool.poetry.dependencies]", 1)[1].split("[tool.poetry.", 1)[0]
        m = re.search(r"^playwright\s*=\s*(.*)$", main, re.M)
        assert m, "playwright missing from main dependencies"
        assert "optional = true" not in m.group(1)

    def test_torch_is_gone_and_all_does_not_carry_it(self, toml):
        # Nothing in app/ imports torch; it only ever arrived transitively via
        # sentence-transformers, which was never declared.
        extras = toml.split("[tool.poetry.extras]", 1)[1].split("\n[", 1)[0]
        assert "torch" not in extras
        assert "sentence-transformers" in extras

    def test_app_does_not_import_torch_or_numba(self):
        hits = subprocess.run(
            ["grep", "-rln", r"^\s*\(import torch\|from torch\|import numba\|from numba\)",
             str(ROOT / "app"), "--include=*.py"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert hits == "", f"torch/numba imported after all: {hits}"


# --------------------------------------------------------------------------
# install_extras.sh accepts targets; the entry point forwards them
# --------------------------------------------------------------------------

class TestInstallerTargets:

    SCRIPT = ROOT / "app" / "scripts" / "install_extras.sh"

    def _plan(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.SCRIPT), "--dry-run", *args],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
        )

    def test_default_plan_covers_both_targets(self):
        r = self._plan()
        assert r.returncode == 0, r.stderr
        assert "Chromium" in r.stdout or "chromium" in r.stdout
        assert "LaTeX" in r.stdout

    def test_browser_only_plan_omits_latex(self):
        r = self._plan("--browser")
        assert r.returncode == 0, r.stderr
        assert "chromium" in r.stdout.lower()
        assert "tlmgr" not in r.stdout

    def test_latex_only_plan_omits_browser(self):
        r = self._plan("--latex")
        assert r.returncode == 0, r.stderr
        assert "tlmgr" in r.stdout
        assert "playwright install" not in r.stdout

    def test_entry_point_forwards_targets_unchanged(self, monkeypatch):
        from app.utils import install_extras
        got = []
        monkeypatch.setattr(install_extras, "run", lambda args: got.append(list(args)) or 0)
        install_extras.main(["--latex", "--dry-run"])
        assert got == [["--latex", "--dry-run"]], (
            "entry point altered the argv (the old code stripped --latex)"
        )

    def test_script_parses_cleanly(self):
        # REGRESSION: an install-phase `if [ -n "$DO_LATEX" ]; then` block was
        # opened but never closed with `fi`, so bash aborted with
        # `syntax error: unexpected end of file` -- the plan printed and the
        # browser step ran, then the whole installer died right before the
        # LaTeX install. `bash -n` reads the entire script (parsing every
        # if/fi construct) without executing it, so an unclosed block is a
        # non-zero exit here regardless of which branch a run would take.
        r = subprocess.run(
            ["bash", "-n", str(self.SCRIPT)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, (
            f"install_extras.sh has a shell syntax error: {r.stderr!r}"
        )


# --------------------------------------------------------------------------
# The playwright probe targets the interpreter Ziya actually runs under
# --------------------------------------------------------------------------

class TestPlaywrightProbeUsesZiyaPython:
    """A wheel installed into a venv (or BuilderToolbox's .venv) is driven by
    that venv's python, NOT the bare python3 on PATH -- and Playwright ships
    inside the venv. The script used to probe the system python3, find no
    playwright there, and wrongly tell the user to reinstall ziya. The entry
    point now exports ZIYA_PYTHON=sys.executable and the script prefers it."""

    SCRIPT = ROOT / "app" / "scripts" / "install_extras.sh"

    def test_entry_point_exports_ziya_python(self, monkeypatch):
        from app.utils import install_extras
        captured = {}

        def fake_run(cmd, *a, **k):
            captured["env"] = k.get("env")
            class _R:
                returncode = 0
            return _R()

        monkeypatch.setattr(install_extras.subprocess, "run", fake_run)
        install_extras.run(["--dry-run"])
        env = captured.get("env")
        assert env is not None, "run() did not pass an explicit env to the script"
        assert env.get("ZIYA_PYTHON") == sys.executable, (
            "run() must export ZIYA_PYTHON=sys.executable so the script probes "
            "the venv interpreter whose Playwright will look for Chromium"
        )

    def test_script_prefers_ziya_python_over_bare_python3(self, tmp_path):
        # Set ZIYA_PYTHON to this interpreter (which HAS playwright, since it
        # is a hard dep). The ZIYA_PYTHON branch runs first, so its full path
        # -- not the bare "python3" fallback token -- appears in the plan's
        # Chromium line. That full path in stdout proves the branch fired.
        # Point the browser registry at an empty dir so Chromium reads as
        # absent: the install line is only printed when there is something
        # to install, whatever this machine actually has.
        r = subprocess.run(
            ["bash", str(self.SCRIPT), "--dry-run", "--browser"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "ZIYA_PYTHON": sys.executable,
                 "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path),
                 "HOME": os.environ.get("HOME", "/tmp")},
        )
        assert r.returncode == 0, r.stderr
        assert sys.executable in r.stdout, (
            "the ZIYA_PYTHON branch of find_playwright did not fire; the plan "
            f"still resolved playwright some other way. stdout={r.stdout!r}"
        )
        assert "reinstall ziya" not in r.stdout, (
            "playwright was reported missing even though ZIYA_PYTHON has it"
        )
