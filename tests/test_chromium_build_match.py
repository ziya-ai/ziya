"""
The Chromium check accepts only the build the installed Playwright launches.

Incident 2026-10-26: a machine with two Ziya installs (pyenv wheel, toolbox
venv) shared one Playwright browser cache.  ``ziya-install-extras`` had laid
down the build for one install's Playwright; the other's needed a different
revision.  The old check accepted any ``chromium*`` directory, so the
installer reported "already installed" and skipped, the server offered
render_diagram, and the launch failed with "Executable doesn't exist".

Covered: the manifest read (``required_chromium_build``), the presence check
against a stale build / a half-installed build / the right build, the
manifest-less fallback, and the seam the installer script crosses (its
``chromium_present`` runs the same probe through an interpreter).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.utils import optional_features as of

ROOT = Path(__file__).resolve().parents[1]
PROBE = ("from app.utils.optional_features import chromium_browser_available as c; "
         "raise SystemExit(0 if c() else 3)")


def _fake_playwright(tmp_path: Path, browsers) -> Path:
    pkg = tmp_path / "pw"
    (pkg / "driver" / "package").mkdir(parents=True)
    (pkg / "driver" / "package" / "browsers.json").write_text(json.dumps({"browsers": browsers}))
    return pkg


def _build(root: Path, name: str, complete: bool = True) -> None:
    (root / name).mkdir(parents=True)
    if complete:
        (root / name / "INSTALLATION_COMPLETE").write_text("")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(root))
    return root


MODERN = [{"name": "chromium", "revision": "1208"},
          {"name": "chromium-headless-shell", "revision": "1208"},
          {"name": "firefox", "revision": "1509"}]


def test_manifest_names_the_headless_shell_build(tmp_path, monkeypatch):
    monkeypatch.setattr(of, "_playwright_package_dir", lambda: _fake_playwright(tmp_path, MODERN))
    assert of.required_chromium_build() == "chromium_headless_shell-1208"


def test_pre_headless_shell_playwright_needs_the_chromium_build(tmp_path, monkeypatch):
    monkeypatch.setattr(of, "_playwright_package_dir",
                        lambda: _fake_playwright(tmp_path, [{"name": "chromium", "revision": "1140"}]))
    assert of.required_chromium_build() == "chromium-1140"


def test_unreadable_manifest_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(of, "_playwright_package_dir", lambda: tmp_path / "nothing")
    assert of.required_chromium_build() is None


def test_another_installs_build_does_not_count(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(of, "_playwright_package_dir", lambda: _fake_playwright(tmp_path, MODERN))
    _build(cache, "chromium_headless_shell-1194")
    _build(cache, "chromium-1194")
    assert of.chromium_browser_available() is False


def test_half_installed_build_does_not_count(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(of, "_playwright_package_dir", lambda: _fake_playwright(tmp_path, MODERN))
    _build(cache, "chromium_headless_shell-1208", complete=False)
    assert of.chromium_browser_available() is False


def test_the_required_build_counts(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(of, "_playwright_package_dir", lambda: _fake_playwright(tmp_path, MODERN))
    _build(cache, "chromium_headless_shell-1194")
    _build(cache, "chromium_headless_shell-1208")
    assert of.chromium_browser_available() is True


def test_without_a_manifest_any_build_still_counts(cache, monkeypatch):
    monkeypatch.setattr(of, "required_chromium_build", lambda: None)
    _build(cache, "chromium_headless_shell-1194")
    assert of.chromium_browser_available() is True


def test_real_installed_playwright_has_a_readable_manifest():
    """The real package this suite runs under, not a fixture: if Playwright
    moves browsers.json, the check silently degrades to the any-build rule."""
    pytest.importorskip("playwright")
    req = of.required_chromium_build()
    assert req is not None and req.split("-")[-1].isdigit(), req


# -- seam: install_extras.sh's chromium_present runs this probe ---------------

@pytest.mark.parametrize("staged,expected", [("stale", 3), ("required", 0)])
def test_installer_probe_reports_the_required_build(tmp_path, staged, expected):
    pytest.importorskip("playwright")
    req = of.required_chromium_build()
    assert req, "harness: installed Playwright has no manifest"
    root = tmp_path / "cache"
    _build(root, f"{req.rsplit('-', 1)[0]}-1" if staged == "stale" else req)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(root), "PYTHONPATH": str(ROOT)}
    rc = subprocess.run([sys.executable, "-c", PROBE], env=env, cwd=ROOT).returncode
    assert rc == expected
