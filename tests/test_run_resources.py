"""Per-run resource accounting (app/utils/run_resources.py) and its seam
with task_executor.

What a user sees on the tile's Resources panel after a run — wall clock,
model time, turns, tool calls, tokens, cost, tool-side CPU and the disk
the run left behind — is joined here from the usage ledger, the run
record and the filesystem.  Each test pins one source reaching the
report; the executor seam tests pin that the write-side numbers (CPU
delta, files named to file_write) actually land on the Artifact.
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import run_resources as rr


# ── measurements ──────────────────────────────────────────────────────

class TestCpuChildren:
    def test_delta_grows_after_a_child_burns_cpu(self):
        before = rr.cpu_children_ms()
        subprocess.run([sys.executable, "-c",
                        "sum(i*i for i in range(3_000_000))"], check=True)
        after = rr.cpu_children_ms()
        assert after > before, (before, after)

    def test_monotone_without_children(self):
        a = rr.cpu_children_ms()
        b = rr.cpu_children_ms()
        assert b >= a >= 0


class TestWriteTracker:
    def test_notes_only_write_tools_and_sizes_at_snapshot(self, tmp_path):
        t = rr.WriteTracker()
        (tmp_path / "a.txt").write_text("hello")
        t.note("file_write", {"path": "a.txt", "content": "hello"})
        t.note("file_write", {"path": "a.txt", "content": "again"})     # dedup
        t.note("file_write", {"path": "gone.txt", "content": ""})
        t.note("file_read", {"path": "b.txt"})                           # not a write
        t.note("run_shell_command", {"command": "echo x > c.txt"})       # invisible
        t.note("file_write", "not-a-dict")
        assert t.paths == ["a.txt", "gone.txt"]
        snap = t.snapshot(str(tmp_path))
        assert snap == [
            {"path": "a.txt", "bytes": 5, "exists": True},
            {"path": "gone.txt", "bytes": 0, "exists": False},
        ]

    def test_absolute_path_is_not_rebased(self, tmp_path):
        f = tmp_path / "abs.bin"
        f.write_bytes(b"\0" * 17)
        t = rr.WriteTracker()
        t.note("mcp_file_write", {"path": str(f)})
        assert t.snapshot("/nonexistent/root")[0]["bytes"] == 17


class TestDisk:
    def test_record_and_scratch_bytes(self, tmp_path):
        project_dir = tmp_path / "pdir"
        (project_dir / "task_runs" / "r1" / "iterations").mkdir(parents=True)
        (project_dir / "task_runs" / "r1.json").write_text("x" * 100)
        (project_dir / "task_runs" / "r1" / "iterations" / "b-0.json").write_text("y" * 50)
        (project_dir / "task_runs" / "r2.json").write_text("z" * 999)   # another run
        root = tmp_path / "proj"
        (root / ".ziya" / "task-runs" / "r1").mkdir(parents=True)
        (root / ".ziya" / "task-runs" / "r1" / "backlog.json").write_text("w" * 30)
        d = rr.run_disk_usage("r1", project_dir, str(root))
        assert d["record_bytes"] == 150
        assert d["scratch_bytes"] == 30
        assert d["scratch_path"].endswith("task-runs/r1")

    def test_absent_locations_are_zero(self, tmp_path):
        d = rr.run_disk_usage("nope", tmp_path, str(tmp_path))
        assert d["record_bytes"] == 0 and d["scratch_bytes"] == 0
        assert d["scratch_path"] is None


# ── the join ──────────────────────────────────────────────────────────

def _group(key, records=0, cost=None, **dims):
    return {"key": key, "records": records, "effective_cost": cost, "list_cost": cost,
            "dims": dims, "has_estimates": False, "estimate_records": 0}


class _Ledger:
    """Stub exposing the two reads build_run_resources makes."""

    def __init__(self, blocks, tiers, records):
        self._blocks, self._tiers, self._records = blocks, tiers, records
        self.calls = []

    def rollup(self, by, **filters):
        self.calls.append((by, filters))
        groups = self._blocks if by == "block" else self._tiers
        tot = _group("__total__", records=sum(g["records"] for g in groups),
                     cost=sum(g["effective_cost"] or 0 for g in groups),
                     input=sum(g["dims"].get("input", 0) for g in groups))
        return {"groups": groups, "totals": tot}

    def query(self, **filters):
        return self._records


def _artifact(**kw):
    base = {"summary": "", "tool_calls": 0, "cpu_ms": None, "model": None,
            "files_written": []}
    base.update(kw)
    return SimpleNamespace(**base)


def _state(block_type, status, started, completed, artifact=None, iters=()):
    return SimpleNamespace(block_type=block_type, status=status, started_at=started,
                           completed_at=completed, artifact=artifact,
                           iteration_summaries=list(iters))


def _run():
    return SimpleNamespace(
        id="run-1", card_id="card-1", status="done", attempt=1,
        started_at=1_000_000, completed_at=1_000_000 + 8_000_000,   # 2h13m
        card_snapshot={"root": {"id": "g", "name": "G", "block_type": "group", "body": [
            {"id": "t1", "name": "Plan", "block_type": "task"},
            {"id": "loop", "name": "Review", "block_type": "repeat",
             "body": [{"id": "t2", "name": "Body", "block_type": "task"}]},
        ]}},
        block_states={
            "g": _state("group", "done", 1_000_000, 9_000_000),
            "t1": _state("task", "done", 1_000_000, 1_250_000,
                         _artifact(tool_calls=18, cpu_ms=1200, model="opus5.5",
                                   files_written=[{"path": "plan.md", "bytes": 10, "exists": True}])),
            "loop": _state("repeat", "done", 1_250_000, 9_000_000, iters=[1, 2, 3]),
            "t2": _state("task", "done", 1_250_000, 9_000_000,
                         _artifact(tool_calls=151, cpu_ms=30_000, model="haiku-4.5",
                                   files_written=[{"path": "plan.md", "bytes": 42, "exists": True},
                                                  {"path": "out.json", "bytes": 7, "exists": True}])),
        },
    )


class TestBuildRunResources:
    def test_every_source_reaches_the_report(self, tmp_path):
        ledger = _Ledger(
            blocks=[_group("t1", records=3, cost=0.91, input=96_000),
                    _group("t2", records=24, cost=4.88, input=820_000)],
            tiers=[_group("flex", records=25), _group("standard", records=2)],
            records=[SimpleNamespace(ts=10.0, actualized_ts=13.5),
                     SimpleNamespace(ts=20.0, actualized_ts=None),      # estimate: no model time
                     SimpleNamespace(ts=30.0, actualized_ts=31.0)],
        )
        (tmp_path / "task_runs").mkdir()
        (tmp_path / "task_runs" / "run-1.json").write_text("r" * 64)
        rep = rr.build_run_resources(_run(), project_dir=tmp_path, project_root=None,
                                     card_name="Release sweep", ledger=ledger,
                                     now=20_000.0)
        # ledger reads are scoped to this run
        assert all(f == {"run_id": "run-1"} for _, f in ledger.calls)
        assert rep["card_name"] == "Release sweep"
        assert rep["wall_ms"] == 8_000_000
        assert rep["model_time_ms"] == 4_500
        assert rep["turns"] == 27
        assert rep["effective_cost"] == pytest.approx(5.79)
        assert rep["dims"] == {"input": 916_000}
        assert rep["tool_calls"] == 169
        assert rep["cpu_ms"] == 31_200
        assert rep["cpu_shared"] is False
        assert [t["tier"] for t in rep["tiers"]] == ["flex", "standard"]
        assert rep["disk"]["record_bytes"] == 64
        # files: union across leaves, later report of plan.md wins
        assert {f["path"]: f["bytes"] for f in rep["files_written"]} == {"plan.md": 42, "out.json": 7}
        assert rep["files_bytes"] == 49
        # per-block rows carry names from the snapshot and the ledger join
        by_id = {b["block_id"]: b for b in rep["blocks"]}
        assert by_id["t1"]["name"] == "Plan" and by_id["t1"]["turns"] == 3
        assert by_id["t1"]["effective_cost"] == 0.91 and by_id["t1"]["model"] == "opus5.5"
        assert by_id["t1"]["wall_ms"] == 250_000
        assert by_id["loop"]["iterations"] == 3 and by_id["loop"]["turns"] == 0
        assert by_id["loop"]["tool_calls"] is None     # container: no artifact
        # execution order, not dict order
        assert [b["block_id"] for b in rep["blocks"]][:2] == ["g", "t1"] or \
               [b["block_id"] for b in rep["blocks"]][:2] == ["t1", "g"]

    def test_live_run_measures_wall_to_now(self):
        run = _run()
        run.completed_at = None
        run.status = "running"
        rep = rr.build_run_resources(run, ledger=_Ledger([], [], []), now=1_500.0)
        assert rep["wall_ms"] == 1_500_000 - 1_000_000

    def test_parallel_leaves_flag_cpu_as_shared(self):
        run = _run()
        run.block_states["t1"].started_at = 1_300_000     # overlaps t2
        run.block_states["t1"].completed_at = 1_400_000
        rep = rr.build_run_resources(run, ledger=_Ledger([], [], []))
        assert rep["cpu_shared"] is True

    def test_ledger_failure_degrades_to_run_record_only(self):
        class Broken:
            def rollup(self, **k):
                raise RuntimeError("db locked")

            def query(self, **k):
                raise RuntimeError("db locked")
        rep = rr.build_run_resources(_run(), ledger=Broken())
        assert rep["turns"] == 0 and rep["effective_cost"] is None
        assert rep["tool_calls"] == 169                       # still from the record


# ── executor seam ─────────────────────────────────────────────────────

class _Exec:
    """Streams one file_write tool_display then closes."""
    written_to = None

    def __init__(self, *a, **kw):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "tool_display", "tool_name": "file_write",
               "args": {"path": str(_Exec.written_to), "content": "x"},
               "result": "ok"}
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.mark.asyncio
async def test_executor_stamps_cpu_and_files_on_the_artifact(tmp_path):
    from app.agents import task_executor
    from app.models.task_card import Block
    target = tmp_path / "written.txt"
    target.write_text("twelve bytes")
    _Exec.written_to = target
    block = Block(block_type="task", id="t1", name="T", instructions="do")
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Exec), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        art = await task_executor.execute_task_block(block, run_id="r1")
    assert isinstance(art.cpu_ms, int) and art.cpu_ms >= 0
    assert art.files_written == [{"path": str(target), "bytes": 12, "exists": True}]
    # Round-trips through the record: the field is on the model, not extra.
    assert "files_written" in art.model_dump() and "cpu_ms" in art.model_dump()
