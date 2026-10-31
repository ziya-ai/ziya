"""
validate_run_command accepts a service venv's own interpreter.

A venv's ``bin/python`` is a symlink to the interpreter it was created from,
so the realpath containment check refused it and every PyPI install that
falls back to ``<venv>/bin/python -m <module>`` (no console script), and every
``python ...`` command resolved into the venv, was rejected at install time.
The acceptance is narrow: a planted symlink to a non-Python binary, a
``.venv`` without ``pyvenv.cfg``, and ``-c`` stay refused.
"""

import os
import subprocess
import sys

import pytest

from app.mcp.registry.command_policy import (
    RegistryCommandRejected, validate_run_command,
)
from app.mcp.registry.installation_helper import InstallationHelper


@pytest.fixture(scope="module")
def venv_dir(tmp_path_factory):
    install_dir = tmp_path_factory.mktemp("svc")
    python, err = InstallationHelper.ensure_venv(install_dir)
    assert err is None, err
    return install_dir, python


def test_venv_interpreter_module_command_is_accepted(venv_dir):
    install_dir, python = venv_dir
    cmd = [python, "-m", "json.tool", "--help"]
    assert validate_run_command(cmd, source="t", install_dir=install_dir) == cmd
    # ...and it is a working interpreter.
    assert subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0


def test_resolved_python_script_command_is_accepted(venv_dir):
    install_dir, _ = venv_dir
    cmd = InstallationHelper.resolve_in_venv(["python", "-u", str(install_dir / "s.py")],
                                             install_dir)
    assert validate_run_command(cmd, source="t", install_dir=install_dir) == cmd


def test_inline_code_still_refused(venv_dir):
    install_dir, python = venv_dir
    with pytest.raises(RegistryCommandRejected):
        validate_run_command([python, "-c", "print(1)"], source="t", install_dir=install_dir)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink layout")
def test_planted_non_python_symlink_refused(tmp_path):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (bin_dir / "python").symlink_to("/bin/sh")
    with pytest.raises(RegistryCommandRejected):
        validate_run_command([str(bin_dir / "python"), "x.sh"], source="t",
                             install_dir=tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink layout")
def test_venv_without_pyvenv_cfg_refused(tmp_path):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(os.path.realpath(sys.executable))
    with pytest.raises(RegistryCommandRejected):
        validate_run_command([str(bin_dir / "python"), "-m", "x"], source="t",
                             install_dir=tmp_path)


def test_another_services_venv_refused(venv_dir, tmp_path):
    _, python = venv_dir
    with pytest.raises(RegistryCommandRejected):
        validate_run_command([python, "-m", "x"], source="t", install_dir=tmp_path)
