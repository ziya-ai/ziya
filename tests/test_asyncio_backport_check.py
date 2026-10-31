"""The obsolete PyPI ``asyncio`` backport is detected and reported.

It came in through mcp-shell-server and made pytest crash with
``SyntaxError: tasks.async(...)`` under the managed job runner, whose
sys.path put site-packages ahead of the stdlib.
"""
import io
import os
import sysconfig
import types

import pytest

from app.utils import asyncio_backport_check as chk


def _fake_dist(version="3.4.3", loc="/x/site-packages"):
    d = types.SimpleNamespace(version=version)
    d.locate_file = lambda _p: loc
    return d


def test_running_asyncio_is_the_stdlib_copy():
    # The environment the suite runs in must not be shadowed; this is the
    # assertion that would have caught the job-runner failure.
    import asyncio
    stdlib = os.path.realpath(sysconfig.get_paths()["stdlib"])
    assert os.path.realpath(asyncio.__file__).startswith(stdlib + os.sep)


def test_no_backport_means_no_warning(monkeypatch):
    from importlib import metadata

    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "distribution", missing)
    assert chk.find_asyncio_backport() is None
    assert chk.format_asyncio_backport_warning() is None
    buf = io.StringIO()
    assert chk.warn_if_asyncio_backport(buf) is False
    assert buf.getvalue() == ""


def test_installed_backport_is_reported(monkeypatch):
    from importlib import metadata
    monkeypatch.setattr(metadata, "distribution",
                        lambda name: _fake_dist() if name == "asyncio" else None)
    info = chk.find_asyncio_backport()
    assert info is not None and info["version"] == "3.4.3"
    assert info["location"] == "/x/site-packages"
    # This interpreter imports the stdlib copy, so it is not shadowing.
    assert info["shadows"] is False
    buf = io.StringIO()
    assert chk.warn_if_asyncio_backport(buf) is True
    out = buf.getvalue()
    assert "pip uninstall asyncio" in out
    assert "is installed" in out and "3.4.3" in out


def test_shadowing_wording():
    msg = chk.format_asyncio_backport_warning(
        {"version": "3.4.3", "location": "/x", "shadows": True})
    assert "shadowing the standard library" in msg


def test_wired_into_server_startup():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py")).read()
    assert "warn_if_asyncio_backport" in src
