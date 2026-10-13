"""Without ``ziya[data]`` installed, ``analyze`` explains how to install it.

Deliberately has no importorskip: this is the one analytics test that must
run on an install without DuckDB.  ``available`` is forced False so it also
runs (and means the same thing) where DuckDB is installed.
"""
from pathlib import Path

import app.utils.analytics as analytics
from app.utils.analytics.analyze import run_analyze


def test_without_engine_returns_install_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    monkeypatch.setattr(analytics, "available", lambda: False)
    r = run_analyze(sql="SELECT 1", resolve_path=Path)
    assert r == {"error": True, "kind": "not_installed", "message": analytics.INSTALL_HINT}
    assert "ziya-install-extras --data" in r["message"]
    # Nothing was written: the call stopped before touching the store.
    assert not (tmp_path / "datasets").exists()
