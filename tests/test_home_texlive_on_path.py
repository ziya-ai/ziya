"""
A TeX Live that ziya-install-extras put under $HOME must be found by the
server without a shell PATH edit.

Observed on an AL2 dev desktop: the installer wrote ~/texlive and all eleven
packages, and its own probe (find_tlmgr globs $TL_HOME/bin/*/) reported
"all 11 packages installed" -- yet the startup banner said "LaTeX diagrams:
missing dvisvgm or ghostscript", because tex_status() and the renderer used
shutil.which alone and found only the yum /usr/bin/latex (no dvisvgm).  The
engine now mirrors the script's TL_HOME rule and prepends that bin dir to the
process PATH, ahead of the distro TeX.
"""
import os
import shutil
import stat
from pathlib import Path

import pytest

from app.utils import optional_features as of

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "app" / "scripts" / "install_extras.sh"
ENVIRONMENT = ROOT / "app" / "config" / "environment.py"


def _exe(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _stage(tmp_path, monkeypatch, *, home_tex=("latex", "dvisvgm", "tlmgr")):
    """PATH = a distro bin with only `latex`; $ZIYA_TEXLIVE_DIR = a full TeX."""
    distro = tmp_path / "usr-bin"
    distro.mkdir()
    _exe(distro / "latex")
    tl = tmp_path / "texlive"
    bindir = tl / "bin" / "x86_64-linux"
    bindir.mkdir(parents=True)
    for name in home_tex:
        _exe(bindir / name)
    monkeypatch.setenv("PATH", str(distro))
    monkeypatch.setenv(of.TEXLIVE_DIR_ENV, str(tl))
    return distro, bindir


def test_home_texlive_bin_finds_the_arch_dir(tmp_path, monkeypatch):
    _, bindir = _stage(tmp_path, monkeypatch)
    assert of.home_texlive_bin() == bindir


def test_home_texlive_bin_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv(of.TEXLIVE_DIR_ENV, str(tmp_path / "nowhere"))
    assert of.home_texlive_bin() is None
    monkeypatch.setenv("PATH", "/nonexistent")
    assert of.ensure_home_texlive_on_path() is None
    assert os.environ["PATH"] == "/nonexistent"      # untouched


def test_default_location_is_home_texlive(tmp_path, monkeypatch):
    monkeypatch.delenv(of.TEXLIVE_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    bindir = tmp_path / "texlive" / "bin" / "aarch64-linux"
    bindir.mkdir(parents=True)
    _exe(bindir / "tlmgr")
    assert of.home_texlive_bin() == bindir


def test_ensure_prepends_ahead_of_distro_and_is_idempotent(tmp_path, monkeypatch):
    distro, bindir = _stage(tmp_path, monkeypatch)
    assert of.ensure_home_texlive_on_path() == bindir
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == str(bindir), parts
    assert str(distro) in parts
    # The NEW latex wins over the distro one, as the installer's PATH advice says.
    assert Path(shutil.which("latex")) == bindir / "latex"
    of.ensure_home_texlive_on_path()
    assert os.environ["PATH"].split(os.pathsep).count(str(bindir)) == 1


def test_tex_status_sees_home_texlive_without_shell_path_edit(tmp_path, monkeypatch):
    # The observed host: distro latex on PATH, no dvisvgm there, full TeX in
    # $HOME.  Unpatched, this returned "dvisvgm or ghostscript".
    _, bindir = _stage(tmp_path, monkeypatch)
    assert of.tex_status() is None
    assert Path(shutil.which("dvisvgm")) == bindir / "dvisvgm"   # positive: found via ~/texlive


def test_tex_status_still_reports_when_home_texlive_absent(tmp_path, monkeypatch):
    # Pair for the assertion above: the same PATH with no $HOME TeX must still
    # be diagnosed, so the fix did not simply silence the check.
    distro = tmp_path / "usr-bin"
    distro.mkdir()
    _exe(distro / "latex")
    monkeypatch.setenv("PATH", str(distro))
    monkeypatch.setenv(of.TEXLIVE_DIR_ENV, str(tmp_path / "nowhere"))
    assert of.tex_status() == "dvisvgm or ghostscript"


def test_python_location_rule_matches_the_installer_script():
    # Seam: the script decides WHERE to install; the engine decides where to
    # LOOK.  The env var and default must be the same names on both sides.
    text = SCRIPT.read_text()
    assert 'TL_HOME="${ZIYA_TEXLIVE_DIR:-$HOME/texlive}"' in text
    assert of.TEXLIVE_DIR_ENV == "ZIYA_TEXLIVE_DIR"
    assert of.DEFAULT_TEXLIVE_DIR == "~/texlive"


def test_shared_environment_setup_applies_it():
    # Seam: the renderer runs bare `latex`/`dvisvgm`/`gs` via subprocess, so
    # the PATH fix must land at the entry point both server and CLI share, not
    # only inside tex_status().
    src = ENVIRONMENT.read_text()
    assert "ensure_home_texlive_on_path()" in src
    assert src.index("ensure_home_texlive_on_path()") < src.index('os.environ["ZIYA_USER_CODEBASE_DIR"]')
