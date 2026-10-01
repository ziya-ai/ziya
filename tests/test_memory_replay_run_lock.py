"""engine.py refuses to be a second writer on a run.

2026-09-27: a fan-out iteration that owned ``sep-current-aa`` started an
engine on ``sep-baseline`` while that run's own iteration was mid-chunk.
Two engines on one run interleave checkpoint/cursor/transition writes.
The lock is taken after the PYTHONHASHSEED re-exec (an inherited fd would
not survive it) and before any arm or run state is touched, so the test
drives the real script as a subprocess -- with an arm that does not exist,
which proves the refusal happens before arm setup.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "scripts", "memory_replay", "engine.py")
RUNS = os.path.join(ROOT, ".ziya", "memory-replay", "runs")


@pytest.fixture
def run_dir():
    d = os.path.join(RUNS, f"pytest-lock-{uuid.uuid4().hex[:8]}")
    os.makedirs(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _engine(run_id):
    return subprocess.run(
        [sys.executable, ENGINE, "--arm", "no-such-arm", "--run", run_id, "--resume"],
        capture_output=True, text=True, timeout=60, cwd=ROOT)


def test_second_writer_is_refused_and_names_the_holder(run_dir):
    fd = os.open(os.path.join(run_dir, "engine.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.pwrite(fd, json.dumps({"pid": 4242}).encode(), 0)
        res = _engine(os.path.basename(run_dir))
    finally:
        os.close(fd)
    assert res.returncode == 4, res.stderr[-800:]
    assert "already being advanced" in res.stderr and "4242" in res.stderr
    assert "advance only your own run id" in res.stderr


def test_unlocked_run_gets_past_the_lock(run_dir):
    """Positive control: with no holder the engine proceeds and fails LATER,
    on the missing arm -- not with the lock exit code."""
    res = _engine(os.path.basename(run_dir))
    assert res.returncode not in (0, 4), res.stderr[-800:]
    assert "already being advanced" not in res.stderr
    assert json.loads(open(os.path.join(run_dir, "engine.lock")).read())["pid"] > 0
