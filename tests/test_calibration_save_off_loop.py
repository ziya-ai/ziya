"""Token-calibration saves must not run on the event loop.

Stall watchdog, 2026-10-10:

    streaming_tool_executor._handle_usage_event -> _record_calibration
      -> TokenCalibrator.record_actual_usage -> _save_calibration_data
      -> open(temp_file, 'w')

The save takes a cross-process FileLock (10 s timeout), re-reads the file and
rewrites it; record_actual_usage made it inline for each of the first 20
samples and every 5th after that.  Hot-path callers now use request_save(),
which hands the write to a single daemon thread.
"""
import ast
import json
import threading
from pathlib import Path

import pytest

from app.utils.token_calibrator import TokenCalibrator

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cal(tmp_path):
    return TokenCalibrator(cache_file=str(tmp_path / "cal.json"))


def _record(cal, n=1):
    for i in range(n):
        cal.record_actual_usage(
            conversation_id="c", file_contents={f"f{i}.py": "x" * 3000},
            actual_tokens=1000, model_id="us.anthropic.claude-sonnet-4-6",
            model_family="claude")


def _spy_writes(cal, monkeypatch):
    threads = []
    real = cal._save_calibration_data

    def spy():
        threads.append(threading.current_thread())
        real()
    monkeypatch.setattr(cal, "_save_calibration_data", spy)
    return threads


def test_record_actual_usage_writes_off_the_calling_thread(cal, monkeypatch, tmp_path):
    threads = _spy_writes(cal, monkeypatch)
    _record(cal)
    assert cal.wait_for_save(timeout=10)
    assert threads, "no save happened -- the seam was not exercised"
    assert all(t is not threading.current_thread() for t in threads), \
        "calibration was saved on the caller's (event-loop) thread"
    on_disk = json.loads((tmp_path / "cal.json").read_text())
    assert on_disk["samples"]["claude"][".py"]


def test_baseline_establishment_writes_off_the_calling_thread(cal, monkeypatch, tmp_path):
    threads = _spy_writes(cal, monkeypatch)
    cal.establish_baseline_if_needed("claude", total_tokens=20000,
                                     file_chars=4000, mcp_tool_count=10)
    assert cal.wait_for_save(timeout=10)
    assert threads and all(t is not threading.current_thread() for t in threads)
    on_disk = json.loads((tmp_path / "cal.json").read_text())
    assert on_disk["baseline_overhead_tokens"]["claude"] == 19000


def test_a_burst_of_samples_all_reach_disk(cal, tmp_path):
    _record(cal, n=12)
    assert cal.wait_for_save(timeout=10)
    on_disk = json.loads((tmp_path / "cal.json").read_text())
    assert len(on_disk["samples"]["claude"][".py"]) == 12


def test_flush_writes_an_outstanding_save_on_the_calling_thread(cal, tmp_path, monkeypatch):
    # Hold the worker off so the request is still outstanding at flush.
    started = threading.Event()
    monkeypatch.setattr(cal, "_save_worker", lambda: started.set())
    _record(cal)
    assert started.wait(5)
    assert not (tmp_path / "cal.json").exists()
    cal.flush()
    assert json.loads((tmp_path / "cal.json").read_text())["samples"]["claude"][".py"]
    assert cal.wait_for_save(timeout=0)


def test_flush_with_nothing_requested_does_not_write(cal, tmp_path):
    cal.flush()
    assert not (tmp_path / "cal.json").exists()


def test_synchronous_save_still_writes_immediately(cal, tmp_path):
    """Explicit callers (and tests) keep the blocking save."""
    with cal.lock:            # reentrant: callable from under the lock
        cal._save_calibration_data()
    assert (tmp_path / "cal.json").exists()


def test_streaming_executor_does_not_save_calibration_inline():
    """The baseline path in _stream_with_tools_impl runs on the event loop."""
    tree = ast.parse((ROOT / "app/streaming_tool_executor.py").read_text())
    inline = [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "_save_calibration_data"]
    assert inline == [], f"_save_calibration_data called inline at L{inline}; use request_save()"
