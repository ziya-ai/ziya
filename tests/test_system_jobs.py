"""
Tests for the internal periodic system-job registry
(app/agents/system_jobs.py) — the minimal cron layer that rides the
task-scheduler loop.  Covers the registry mechanics, interval gating,
gate-decline, per-job isolation, state persistence, and the
memory-organize gate policy (orphans OR stale-organize).

The memory-organize *run* is not exercised end-to-end here (it calls the
LLM); the gate decision is what carries the policy, so that is what we pin.
"""
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

import app.agents.system_jobs as sj


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate ~/.ziya so system_jobs.json writes go to tmp_path."""
    monkeypatch.setattr(sj, "get_ziya_home", lambda: tmp_path)
    sj._reset_for_tests()
    yield tmp_path
    sj._reset_for_tests()


def _job(name="j", interval_s=60, gate=lambda: True, run=None):
    async def _noop():
        return None
    return sj.SystemJob(name=name, interval_s=interval_s, gate=gate,
                        run=run or _noop)


# ── Registry mechanics ─────────────────────────────────────────────

def test_register_is_idempotent_by_name(home):
    sj._reset_for_tests()
    sj.register_system_job(_job(name="dup", interval_s=10))
    sj.register_system_job(_job(name="dup", interval_s=20))
    names = [j.name for j in sj._JOBS]
    assert names.count("dup") == 1
    assert next(j for j in sj._JOBS if j.name == "dup").interval_s == 20


# ── Tick gating + firing ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_gate_true_runs_and_records_state(home):
    sj._reset_for_tests()
    ran_flag = {"n": 0}

    async def _run():
        ran_flag["n"] += 1

    sj.register_system_job(_job(name="go", interval_s=60, gate=lambda: True, run=_run))
    ran = await sj.tick_system_jobs(now_ms=1_000_000)
    assert ran == ["go"]
    assert ran_flag["n"] == 1

    state = json.loads((home / "system_jobs.json").read_text())
    assert state["go"]["runs_so_far"] == 1
    assert state["go"]["last_run_ms"] == 1_000_000
    assert state["go"]["last_check_ms"] == 1_000_000


@pytest.mark.asyncio
async def test_gate_false_checks_but_does_not_run(home):
    sj._reset_for_tests()
    sj.register_system_job(_job(name="no", interval_s=60, gate=lambda: False))
    ran = await sj.tick_system_jobs(now_ms=1_000_000)
    assert ran == []
    state = json.loads((home / "system_jobs.json").read_text())
    # Check timestamp is recorded (so we don't re-gate every tick)…
    assert state["no"]["last_check_ms"] == 1_000_000
    # …but the job never ran.
    assert "last_run_ms" not in state["no"]


@pytest.mark.asyncio
async def test_interval_suppresses_recheck_within_window(home):
    sj._reset_for_tests()
    calls = {"gate": 0}

    def _gate():
        calls["gate"] += 1
        return True

    async def _run():
        return None

    sj.register_system_job(_job(name="iv", interval_s=3600, gate=_gate, run=_run))
    # First tick fires.
    await sj.tick_system_jobs(now_ms=0)
    # Second tick 10 min later: still within the 1h interval → gate NOT re-evaluated.
    await sj.tick_system_jobs(now_ms=10 * 60 * 1000)
    assert calls["gate"] == 1
    # Third tick past the interval → gate evaluated again.
    await sj.tick_system_jobs(now_ms=3600 * 1000 + 1)
    assert calls["gate"] == 2


@pytest.mark.asyncio
async def test_job_failure_is_isolated(home):
    sj._reset_for_tests()
    other_ran = {"n": 0}

    async def _boom():
        raise RuntimeError("kaboom")

    async def _ok():
        other_ran["n"] += 1

    sj.register_system_job(_job(name="boom", interval_s=60, run=_boom))
    sj.register_system_job(_job(name="ok", interval_s=60, run=_ok))
    ran = await sj.tick_system_jobs(now_ms=1_000_000)
    # The failing job is not in 'ran'; the healthy one still executed.
    assert ran == ["ok"]
    assert other_ran["n"] == 1
    # The failing job recorded its check but no successful run.
    state = json.loads((home / "system_jobs.json").read_text())
    assert "last_run_ms" not in state["boom"]
    assert state["ok"]["runs_so_far"] == 1


@pytest.mark.asyncio
async def test_gate_exception_is_isolated(home):
    sj._reset_for_tests()

    def _bad_gate():
        raise ValueError("gate blew up")

    sj.register_system_job(_job(name="bg", interval_s=60, gate=_bad_gate))
    ran = await sj.tick_system_jobs(now_ms=1_000_000)
    assert ran == []
    # Check still recorded so we don't hammer a broken gate every tick.
    state = json.loads((home / "system_jobs.json").read_text())
    assert state["bg"]["last_check_ms"] == 1_000_000


@pytest.mark.asyncio
async def test_empty_registry_is_noop(home):
    sj._reset_for_tests()
    # Override the built-in registration so the registry is truly empty.
    with patch.object(sj, "_ensure_registered", lambda: None):
        ran = await sj.tick_system_jobs(now_ms=1)
    assert ran == []


# ── Built-in memory-organize gate policy ───────────────────────────

def _patch_memory(review, history):
    """Patch the lazy imports inside _memory_organize_gate."""
    store = MagicMock()
    return patch.multiple(
        "app.storage.memory",
        get_memory_storage=MagicMock(return_value=store),
    ), patch.multiple(
        "app.memory",
        get_review_summary=MagicMock(return_value=review),
        load_organize_history=MagicMock(return_value=history),
    )


def test_organize_gate_disabled_when_memory_off(home):
    with patch("app.config.env_registry.ziya_env", return_value=False):
        assert sj._memory_organize_gate() is False


def test_organize_gate_fires_on_orphans(home):
    review = {"total_memories": 40, "orphan_count": 3}
    p1, p2 = _patch_memory(review, history=[{"timestamp": sj._now_ms()}])
    with patch("app.config.env_registry.ziya_env", return_value=True), p1, p2:
        assert sj._memory_organize_gate() is True


def test_organize_gate_declines_when_no_orphans_and_fresh(home):
    review = {"total_memories": 40, "orphan_count": 0}
    fresh = [{"timestamp": sj._now_ms()}]  # organized just now
    p1, p2 = _patch_memory(review, history=fresh)
    with patch("app.config.env_registry.ziya_env", return_value=True), p1, p2:
        assert sj._memory_organize_gate() is False


def test_organize_gate_fires_when_no_orphans_but_stale(home):
    review = {"total_memories": 40, "orphan_count": 0}
    stale_ts = sj._now_ms() - (sj.MEMORY_ORGANIZE_STALE_MS + 60_000)
    p1, p2 = _patch_memory(review, history=[{"timestamp": stale_ts}])
    with patch("app.config.env_registry.ziya_env", return_value=True), p1, p2:
        assert sj._memory_organize_gate() is True


def test_organize_gate_fires_when_no_history(home):
    review = {"total_memories": 40, "orphan_count": 0}
    p1, p2 = _patch_memory(review, history=[])
    with patch("app.config.env_registry.ziya_env", return_value=True), p1, p2:
        assert sj._memory_organize_gate() is True


def test_organize_gate_declines_on_empty_store(home):
    review = {"total_memories": 0, "orphan_count": 0}
    p1, p2 = _patch_memory(review, history=[])
    with patch("app.config.env_registry.ziya_env", return_value=True), p1, p2:
        assert sj._memory_organize_gate() is False


@pytest.mark.asyncio
async def test_builtin_registered_after_first_tick(home):
    sj._reset_for_tests()
    # With memory disabled the gate declines, but the job is still registered.
    with patch("app.config.env_registry.ziya_env", return_value=False):
        await sj.tick_system_jobs(now_ms=1)
    assert any(j.name == "memory_organize" for j in sj._JOBS)
