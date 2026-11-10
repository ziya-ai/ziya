"""The lifecycle simulation must never read or write the live memory dir.

Three leaks were found and closed on 2026-11-08/09, each of which silently
changed what the sim measured: extraction cursors (live cursors made every
replayed chat "already extracted"), domain clocks (the extractor ticked the
live clock and the lifecycle aged sandbox proposals against it), and the
activity counter (patched earlier).  This test runs the sim's patch set
around a fake extraction and asserts the live-path resolvers all point into
the sandbox.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_memory_lifecycle_simulation.py"


@pytest.fixture(scope="module")
def sim():
    spec = importlib.util.spec_from_file_location("lifecycle_sim_safety", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lifecycle_sim_safety"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sandboxed_paths_stay_under_sandbox(sim, tmp_path, monkeypatch):
    """Every path resolver the extractor/lifecycle use must resolve into the
    sandbox while the sim's patches are active."""
    from app.storage.memory import MemoryStorage
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    store = MemoryStorage(memory_dir=sandbox)

    from unittest.mock import patch
    with patch("app.memory.extraction_cursor._path",
               side_effect=lambda cid: sim._sandbox_cursor_path(store, cid)), \
         patch("app.utils.memory_clock._default_path", new=sandbox / "domain_clocks.json"):
        from app.memory import extraction_cursor
        from app.utils import memory_clock
        cursor = extraction_cursor._path("conv-1")
        clock = memory_clock._path()
        assert sandbox in cursor.parents, cursor
        assert clock.parent == sandbox, clock

        # A tick lands in the sandbox file, not the live one.
        memory_clock.tick_session("conv-1", str(tmp_path / "proj"))
        assert (sandbox / "domain_clocks.json").exists()

    live = Path.home() / ".ziya" / "memory" / "domain_clocks.json"
    if live.exists():
        # The live file was not touched by the tick above.
        import time
        assert time.time() - live.stat().st_mtime > 2


def test_sim_patch_list_names_both_leaks(sim):
    """Seam check: the production patch list in _extract_one must include the
    cursor and clock redirects, not just this test's hand-built copy."""
    import inspect
    src = inspect.getsource(sim._extract_one)
    assert 'patch("app.memory.extraction_cursor._path"' in src
    assert 'patch("app.utils.memory_clock._default_path"' in src
