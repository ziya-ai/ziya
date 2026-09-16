"""Tests for app/utils/startup_warmup.py — boot-time warm-up of the chat
summary caches.

WHY THIS EXISTS

On a cold server start the first ``GET /projects/{pid}/chats`` had to read,
decrypt and parse every chat file in every project (1,689 files / 459 MB on a
real workspace) and exceeded the client's 25s deadline, leaving an empty
sidebar; meanwhile the lifespan had ALREADY walked every one of those files
synchronously (chat_integrity) before accepting any connection.  The warm-up
moves that cost into a daemon thread and makes it populate the caches the
list endpoint actually reads.

The seam that matters is "the cache the endpoint reads is the cache the
warm-up fills" — a warm-up that populated its own private cache would pass a
unit test and fix nothing.  So the end-to-end test warms, then calls the REAL
``list_summaries`` / ``collect_global_chat_summaries`` with file reads
instrumented, and asserts zero reads.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils import startup_warmup as sw


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    (home / "projects").mkdir(parents=True)
    return home


@pytest.fixture(autouse=True)
def _clear_caches():
    """Both caches are process-global; isolate every test."""
    from app.storage import chats as chats_mod
    from app.storage import global_items as gi_mod
    chats_mod._summary_cache.clear()
    gi_mod._summary_cache.clear()
    yield
    chats_mod._summary_cache.clear()
    gi_mod._summary_cache.clear()


@pytest.fixture(autouse=True)
def _no_retention(monkeypatch):
    """list_summaries enforces retention while scanning; keep files alive."""
    class _Never:
        def is_expired(self, *_a, **_k):
            return False
    monkeypatch.setattr(
        "app.plugins.data_retention.get_retention_enforcer", lambda: _Never()
    )


def _make_project(ziya_home: Path, project_id: str) -> Path:
    pdir = ziya_home / "projects" / project_id
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": project_id, "name": project_id, "path": f"/tmp/{project_id}",
        "createdAt": 1, "lastAccessedAt": 1,
    }))
    return pdir


def _write_chat(ziya_home: Path, project_id: str, chat_id: str, *, is_global=False) -> Path:
    cf = ziya_home / "projects" / project_id / "chats" / f"{chat_id}.json"
    data = {"id": chat_id, "title": f"chat {chat_id}", "messages": [],
            "createdAt": 1, "lastActiveAt": 1000, "projectId": project_id}
    if is_global:
        data["isGlobal"] = True
    cf.write_text(json.dumps(data))
    return cf


def _seed(ziya_home: Path):
    _make_project(ziya_home, "p1")
    _make_project(ziya_home, "p2")
    _write_chat(ziya_home, "p1", "c1")
    _write_chat(ziya_home, "p1", "c2")
    _write_chat(ziya_home, "p2", "c3", is_global=True)
    # Not a project: the `p` scratch dir and a dir with no chats/ must be skipped.
    (ziya_home / "projects" / "p").mkdir()
    (ziya_home / "projects" / "nochats").mkdir()


# --------------------------------------------------------------------------
# iter_project_dirs
# --------------------------------------------------------------------------

def test_iter_project_dirs_skips_non_projects(ziya_home):
    _seed(ziya_home)
    names = [p.name for p in sw.iter_project_dirs(ziya_home)]
    assert names == ["p1", "p2"]


def test_iter_project_dirs_missing_root_is_empty(tmp_path):
    assert list(sw.iter_project_dirs(tmp_path / "nope")) == []


# --------------------------------------------------------------------------
# warm_summary_caches — the seam
# --------------------------------------------------------------------------

def test_warm_populates_the_caches_the_endpoint_reads(ziya_home):
    """After warm-up, the real list paths do NO file reads.

    This is the whole point: the caches filled must be the ones the
    endpoint consults, and a cache hit is keyed on stat() alone.
    """
    from app.storage.chats import ChatStorage
    from app.storage.global_items import collect_global_chat_summaries

    _seed(ziya_home)
    stats = sw.warm_summary_caches(ziya_home)
    assert stats["projects"] == 2
    assert stats["summaries"] == 3
    assert stats["global_summaries"] == 1
    assert stats["errors"] == 0

    # Instrument the two read primitives the cold paths use.  A hit never
    # reaches either; a miss must.
    with patch.object(Path, "read_bytes", side_effect=AssertionError("cold read in global path")), \
         patch("app.storage.chats.ChatStorage._read_json",
               side_effect=AssertionError("cold read in list_summaries")):
        own = ChatStorage(ziya_home / "projects" / "p1").list_summaries()
        globals_for_p1 = collect_global_chat_summaries(ziya_home, exclude_project_id="p1")

    assert sorted(s.id for s in own) == ["c1", "c2"]
    assert [s.id for s in globals_for_p1] == ["c3"]


def test_cold_paths_do_read_without_warmup(ziya_home):
    """Positive control for the test above: with no warm-up the same
    instrumented calls DO hit the read primitives.  Without this, the
    assertion above could pass because the patches were never reached."""
    from app.storage.chats import ChatStorage
    _seed(ziya_home)
    with patch("app.storage.chats.ChatStorage._read_json",
               side_effect=AssertionError("cold read")):
        with pytest.raises(AssertionError, match="cold read"):
            ChatStorage(ziya_home / "projects" / "p1").list_summaries()


def test_warm_survives_a_broken_project(ziya_home):
    """One unreadable project must not stop the rest or the global pass."""
    _seed(ziya_home)
    bad = _make_project(ziya_home, "broken")
    # A directory where a chat file should be makes glob+stat succeed but
    # read fail inside ChatStorage; that's fenced per-file already, so force
    # a project-level failure instead by making chats/ unreadable to glob.
    (bad / "chats").rmdir()
    (bad / "chats").write_text("not a directory")
    stats = sw.warm_summary_caches(ziya_home)
    # `chats` exists but is a file: iter_project_dirs yields it, list_summaries
    # raises from glob → counted as an error; the two good projects still warm.
    assert stats["projects"] == 2
    assert stats["errors"] == 1
    assert stats["global_summaries"] == 1


# --------------------------------------------------------------------------
# start_background_warmup — threading contract
# --------------------------------------------------------------------------

def test_background_warmup_returns_immediately_and_runs_integrity_after_caches(ziya_home):
    _seed(ziya_home)
    order: list = []
    gate = threading.Event()

    def slow_warm(home):
        order.append("warm")
        gate.wait(2)
        return {}

    def integrity(home):
        order.append(("integrity", home))

    with patch.object(sw, "warm_summary_caches", side_effect=slow_warm):
        t0 = time.perf_counter()
        thread = sw.start_background_warmup(ziya_home, integrity_check=integrity)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, "start_background_warmup must not block the caller"
        assert thread.daemon is True
        assert thread.is_alive()
        gate.set()
        thread.join(3)
    assert not thread.is_alive()
    assert order == ["warm", ("integrity", ziya_home)]


def test_background_warmup_runs_integrity_even_if_warmup_raises(ziya_home):
    ran = threading.Event()
    with patch.object(sw, "warm_summary_caches", side_effect=RuntimeError("boom")):
        t = sw.start_background_warmup(ziya_home, integrity_check=lambda h: ran.set())
        t.join(3)
    assert ran.is_set()


def test_background_warmup_swallows_integrity_failure(ziya_home):
    _seed(ziya_home)

    def bad_integrity(h):
        raise RuntimeError("integrity exploded")
    t = sw.start_background_warmup(ziya_home, integrity_check=bad_integrity)
    t.join(5)
    assert not t.is_alive()  # thread finished cleanly despite the raise


# --------------------------------------------------------------------------
# Wiring: the lifespan must start the thread, not call run_startup_check inline
# --------------------------------------------------------------------------

def test_lifespan_uses_background_warmup_not_inline_integrity():
    src = (Path(__file__).resolve().parents[1] / "app" / "server.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "start_background_warmup(" in code, "lifespan must start the warm-up thread"
    assert "integrity_check=run_startup_check" in code, \
        "integrity check must be handed to the warm-up thread"
    # The inline, blocking call is what made a cold boot accept no
    # connections until every chat file had been walked.
    assert "run_startup_check(_get_ziya_home_ci())" not in code
