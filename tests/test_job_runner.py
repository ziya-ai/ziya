"""Managed job runner (design/job-runner.md), seam-first.

Runs the real chain -- job tools -> JobManager -> app.jobs.policy (an
in-process ShellServer over the shell-subprocess env) -> ``ziya-jobwrap``
in its own session -> journal -> shared wait engine -> evidence ledger --
in a temp project.  Nothing is mocked except the MCP manager's shell env
(there is no running MCP manager in a unit test; the env is the process
env, which is exactly what the shell subprocess would receive).

Security assertions are the point of several tests: refused means NOTHING
was created and NOTHING ran; the allowlist floor is the shell tool's; the
child env has no credentials; killing is by process group.
"""
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")
pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    # A credential the job must never see.
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sekrit-do-not-leak")
    from app.context import set_project_root
    set_project_root(str(root))
    # Fresh manager and a fresh policy server per test: the policy caches a
    # ShellServer per env fingerprint, and HOME/root change per test.
    import app.jobs.manager as mgr_mod
    import app.jobs.policy as pol_mod
    mgr_mod._MANAGER = None
    pol_mod._POLICY = pol_mod._ShellPolicy()
    yield root
    # Kill anything a test left running so the process tree is clean.
    try:
        for st in mgr_mod.get_job_manager().list(str(root)):
            if st["state"] == "running":
                mgr_mod.get_job_manager().kill(st["job_id"])
    except Exception:  # noqa: BLE001
        pass


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _tool(cls, **kw):
    from app.mcp.tools import job_tools
    return _run(getattr(job_tools, cls)().execute(**kw))


def _job_dirs(root: Path):
    d = root / ".ziya" / "jobs"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


# ── admission: all or nothing ─────────────────────────────────────────────

def test_refused_segment_creates_nothing_and_runs_nothing(proj):
    marker = proj / "ran.txt"
    # mkdir is allowed; nohup is refused.  The scan's failure mode was the
    # allowed part running (or being assumed to have run) while the rest was
    # refused.  Here nothing may happen: no output dir, no job dir, no marker.
    r = _tool("JobStartTool", command=f"mkdir -p {proj}/evidence/x; nohup touch {marker}",
              outputs=["evidence/x/"])
    assert r["ok"] is False and r["error"] == "refused"
    assert "nohup" in r["message"]
    assert not (proj / "evidence").exists()
    assert _job_dirs(proj) == []
    time.sleep(0.3)
    assert not marker.exists()


@pytest.mark.parametrize("cmd", ["sleep 100 &", "nohup sleep 1", "setsid sleep 1", "sleep 1; disown"])
def test_background_operators_refused_with_pointer_to_runner(proj, cmd):
    r = _tool("JobStartTool", command=cmd)
    assert r["ok"] is False and r["error"] == "refused"
    assert "runner" in r["message"] or "job" in r["message"]
    assert _job_dirs(proj) == []


def test_allowlist_floor_is_the_shell_tools(proj):
    # `sh` is not on the default allowlist for run_shell_command; a job must
    # not widen that.  Positive control: the same command via bash (allowed).
    r = _tool("JobStartTool", command="sh -c 'echo hi'", wait_s=10)
    assert r["ok"] is False and r["error"] == "refused" and "'sh'" in r["message"]
    ok = _tool("JobStartTool", command="echo hi", wait_s=10)
    assert ok["ok"] is True and ok["exit_code"] == 0


def test_declared_output_outside_policy_is_refused(proj, tmp_path):
    outside = tmp_path / "elsewhere" / "out.txt"
    r = _tool("JobStartTool", command="echo x", outputs=[str(outside)])
    assert r["ok"] is False and "declared output" in r["message"]
    assert not outside.parent.exists()
    assert _job_dirs(proj) == []


def test_declared_output_inside_server_only_path_is_refused(proj):
    r = _tool("JobStartTool", command="echo x", outputs=[".ziya/jobs/forged/status.json"])
    assert r["ok"] is False and "server-only" in r["message"]
    assert _job_dirs(proj) == []


# ── a job that runs ───────────────────────────────────────────────────────

def test_declared_output_dir_exists_before_first_line_and_is_hashed_on_exit(proj):
    # Under .ziya/: the default safe-write floor.  A declared output outside
    # it is refused (test_declared_output_outside_policy_is_refused).
    out = proj / ".ziya" / "evidence" / "new"
    cmd = (f"test -d {out} && echo DIR_PRESENT; echo payload > {out}/r.txt; "
           f"echo one; sleep 1; echo two")
    r = _tool("JobStartTool", command=cmd, outputs=[".ziya/evidence/new/"], wait_s=30)
    assert r["ok"] is True, r
    assert r["reason"] == "exit" and r["exit_code"] == 0 and r["still_running"] is False
    assert "DIR_PRESENT" in r["tail"] and "one" in r["tail"] and "two" in r["tail"]
    assert r["obs_id"]
    # The ledger observation names the command and hashes the output dir.
    from app.utils.evidence.store import ledger_for
    rec = ledger_for(str(proj)).get(r["obs_id"]) if hasattr(ledger_for(str(proj)), "get") else None
    job_meta = json.loads((proj / ".ziya" / "jobs" / r["job_id"] / "job.json").read_text())
    assert job_meta["obs_id"] == r["obs_id"]
    assert job_meta["outputs_missing"] == []
    if rec is not None:
        inline = rec["result"]["inline"]
        assert inline["exit_code"] == 0 and inline["outputs"][0]["dir"] is True
        assert rec["provenance"]["via"] == "job"


def test_missing_declared_output_is_reported_not_hidden(proj):
    r = _tool("JobStartTool", command="echo did-nothing", outputs=[".ziya/evidence/never.txt"], wait_s=15)
    assert r["ok"] is True and r["exit_code"] == 0, r
    meta = json.loads((proj / ".ziya" / "jobs" / r["job_id"] / "job.json").read_text())
    assert meta["outputs_missing"] == [str(proj / ".ziya" / "evidence" / "never.txt")]


def test_long_job_outlives_the_shell_ceiling_shape(proj):
    """job_start returns immediately; job_wait sees the exit later.  (A real
    40 s sleep is in the design; 3 s exercises the same path without the
    wall-clock cost -- the shell tool's ceiling is pinned elsewhere.)"""
    r = _tool("JobStartTool", command="echo 1; sleep 3; echo 2")
    assert r["ok"] is True and r["state"] == "running" and "job_wait" in r["next"]
    st = _tool("JobStatusTool", job_id=r["job_id"])
    assert st["state"] == "running"
    # Make sure "1" has landed BEFORE the first wait: a first job_wait with
    # no from_seq must replay the whole journal, not start at the tail.
    time.sleep(0.8)
    w = _tool("JobWaitTool", job_id=r["job_id"], timeout_s=30)
    assert w["reason"] == "exit" and w["exit_code"] == 0
    assert "1" in w["tail"] and "2" in w["tail"]


def test_inline_wait_timeout_is_a_checkpoint_then_job_wait_sees_exit(proj):
    r = _tool("JobStartTool", command="echo a; sleep 4; echo b", wait_s=1)
    assert r["ok"] is True and r["reason"] == "timeout" and r["still_running"] is True
    assert "from_seq" in r["next"]
    w = _tool("JobWaitTool", job_id=r["job_id"], from_seq=r["next_seq"], timeout_s=30)
    assert w["reason"] == "exit" and "b" in w["tail"]


def test_pattern_condition_fires_before_exit(proj):
    r = _tool("JobStartTool", command="echo starting; sleep 1; echo FAILED: boom; sleep 5; echo end")
    w = _tool("JobWaitTool", job_id=r["job_id"], until=["exit", "pattern"], pattern="FAILED|ERR", timeout_s=20)
    assert w["reason"] == "pattern" and "boom" in w["matched"]["text"]
    assert w["still_running"] is True


def test_job_read_pages_and_searches(proj):
    r = _tool("JobStartTool", command="echo alpha; echo beta; echo gamma", wait_s=10)
    page = _tool("JobReadTool", job_id=r["job_id"])
    texts = [x.get("text", "") for x in page["records"]]
    assert any("beta" in t for t in texts) and page["mode"] == "page"
    hits = _tool("JobReadTool", job_id=r["job_id"], search="gam+a")
    assert hits["mode"] == "search" and any("gamma" in x.get("text", "") for x in hits["records"])


# ── kill: always the process group ────────────────────────────────────────

def test_kill_leaves_no_process_of_the_group_alive(proj):
    r = _tool("JobStartTool", command='bash -c "sleep 300 & sleep 300"')
    time.sleep(1.0)
    st = _tool("JobStatusTool", job_id=r["job_id"])
    assert st["state"] == "running" and st["pgid"]
    os.killpg(st["pgid"], 0)  # alive before (positive control)
    k = _tool("JobKillTool", job_id=r["job_id"])
    assert k["killed"] is True and k["state"] in ("exited", "lost")
    with pytest.raises(ProcessLookupError):
        os.killpg(st["pgid"], 0)


def test_kill_of_exited_job_is_a_noop(proj):
    r = _tool("JobStartTool", command="echo x", wait_s=10)
    k = _tool("JobKillTool", job_id=r["job_id"])
    assert k["killed"] is False and k["state"] == "exited"


def test_max_runtime_ends_the_job_with_reason(proj):
    r = _tool("JobStartTool", command="sleep 60", max_runtime_s=1, wait_s=20)
    assert r["reason"] == "exit" and r["state"] == "exited"
    st = _tool("JobStatusTool", job_id=r["job_id"])
    assert st["reason"] == "max_runtime"


def test_max_runtime_over_ungranted_ceiling_is_refused(proj):
    r = _tool("JobStartTool", command="sleep 1", max_runtime_s=25 * 3600)
    assert r["ok"] is False and r["error"] == "limit"
    assert _job_dirs(proj) == []


# ── ownership ─────────────────────────────────────────────────────────────

def test_kill_owned_kills_only_that_owner(proj):
    from app.jobs.manager import get_job_manager
    m = get_job_manager()
    a = m.start("sleep 100", project_root=str(proj), owner={"run_id": "r1", "block_id": "b1"})
    b = m.start("sleep 100", project_root=str(proj), owner={"run_id": "r2", "block_id": "b9"})
    time.sleep(0.5)
    killed = m.kill_owned({"run_id": "r1"}, "block_end")
    assert killed == [a["job_id"]]
    states = {s["job_id"]: s["state"] for s in m.list(str(proj))}
    assert states[a["job_id"]] != "running" and states[b["job_id"]] == "running"
    m.kill(b["job_id"])


def test_status_mine_only_filters_by_owner(proj):
    from app.jobs.manager import get_job_manager
    get_job_manager().start("sleep 30", project_root=str(proj), owner={"conversation_id": "someone-else"})
    mine = _tool("JobStatusTool", conversation_id="conv-A")
    assert mine["count"] == 0
    everything = _tool("JobStatusTool", mine_only=False)
    assert everything["count"] == 1


# ── re-attach and pid reuse ───────────────────────────────────────────────

def test_fresh_manager_reattaches_running_then_sees_exit(proj):
    from app.jobs.manager import JobManager, get_job_manager
    r = get_job_manager().start("sleep 2; echo fin", project_root=str(proj))
    m2 = JobManager()
    assert m2.scan(str(proj)) == 1
    st = m2.get(r["job_id"]).state()
    assert st["state"] == "running" and st["pid"] == r["pid"]
    w = m2.wait(r["job_id"], timeout_s=20)
    assert w["reason"] == "exit" and w["exit_code"] == 0


def test_sigkilled_wrapper_is_lost_and_wait_returns_promptly(proj):
    from app.jobs.manager import get_job_manager
    m = get_job_manager()
    r = m.start("sleep 100", project_root=str(proj))
    time.sleep(0.5)
    st = m.get(r["job_id"]).state()
    # -9 the WRAPPER (pid), not the child group: killing the group lets the
    # wrapper observe the exit and write status.json, which is the normal
    # "exited" path.  Only a dead wrapper leaves no status behind.
    os.kill(st["pid"], signal.SIGKILL)
    t0 = time.monotonic()
    try:
        w = m.wait(r["job_id"], timeout_s=30)
        assert w["reason"] == "lost" and w["still_running"] is False
        assert time.monotonic() - t0 < 10
    finally:
        # The orphaned child group is exactly what a lost wrapper leaves
        # behind; reap it so the test host stays clean.
        try:
            os.killpg(st["pgid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_pid_reuse_guard_reports_lost_not_running(proj):
    from app.jobs.manager import get_job_manager
    m = get_job_manager()
    r = m.start("echo x", project_root=str(proj))
    m.wait(r["job_id"], timeout_s=10)
    job = m.get(r["job_id"])
    # Forge: no status, pid = this test process (alive), start time wrong.
    (job.dir / "status.json").unlink()
    job.meta["wrapper_pid"] = os.getpid()
    job.meta["wrapper_start_time"] = 1.0
    assert job.state()["state"] == "lost"


# ── environment ───────────────────────────────────────────────────────────

def test_child_env_has_job_id_and_no_credentials(proj):
    # `env` is not on the allowlist (a refusal the floor must keep); python3 is.
    r = _tool("JobStartTool", command="python3 -c 'import os; [print(k, v) for k, v in os.environ.items()]'",
              wait_s=15, outputs=[])
    assert r["ok"] is True, r
    page = _tool("JobReadTool", job_id=r["job_id"], max_records=500)
    env_text = "\n".join(x.get("text", "") for x in page["records"] if x.get("t") == "output")
    assert f"ZIYA_JOB_ID {r['job_id']}" in env_text
    assert "sekrit-do-not-leak" not in env_text
    assert "AWS_SECRET_ACCESS_KEY" not in env_text
    assert "ZIYA_SESSION_GRANT" not in env_text and "ZIYA_SCOPE_SIG" not in env_text


def test_detach_run_drops_block_from_owner_so_block_end_kill_skips_it(proj, monkeypatch):
    """The tool derives the owner from the server's context, never the caller;
    detach='run' removes only block_id so run-end cleanup still reaches it."""
    from app.utils.evidence import service
    monkeypatch.setattr(service, "task_context",
                        lambda cid=None: {"conversation_id": "c1", "run_id": "r1", "block_id": "b1"})
    plain = _tool("JobStartTool", command="sleep 30")
    detached = _tool("JobStartTool", command="sleep 30", detach="run")
    assert plain["owner"] == {"conversation_id": "c1", "run_id": "r1", "block_id": "b1"}
    assert detached["owner"] == {"conversation_id": "c1", "run_id": "r1"}
    from app.jobs.manager import get_job_manager
    m = get_job_manager()
    assert m.kill_owned({"run_id": "r1", "block_id": "b1"}, "block_end") == [plain["job_id"]]
    assert m.kill_owned({"run_id": "r1"}, "run_end") == [detached["job_id"]]


# ── registration ──────────────────────────────────────────────────────────

def test_jobs_category_registered_and_wait_lifts_executor_guard():
    from app.mcp.builtin_tools import BUILTIN_TOOL_CATEGORIES, get_builtin_tools_for_category
    assert "jobs" in BUILTIN_TOOL_CATEGORIES
    names = {cls().name for cls in get_builtin_tools_for_category("jobs")}
    assert names == {"job_start", "job_wait", "job_status", "job_read", "job_kill"}
    from app.mcp.tools.job_tools import JobWaitTool, JobStartTool
    assert JobWaitTool().exec_timeout_for({"timeout_s": 1800}) >= 1800
    assert JobStartTool().exec_timeout_for({"wait_s": 120}) >= 120
    assert JobStartTool().exec_timeout_for({}) is None


def test_jobs_dir_is_server_only_for_tools(proj):
    from app.config.write_policy import WritePolicyManager
    pm = WritePolicyManager()
    ok, why = pm.is_direct_write_allowed(str(proj / ".ziya" / "jobs" / "job_x" / "job.json"), str(proj))
    assert ok is False and "server" in why
