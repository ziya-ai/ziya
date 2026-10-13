"""``ziya jobs`` CLI and the keep-awake inhibitor (design/job-runner.md, steps 9-10).

The CLI reads job directories directly (no server), which is the situation
it exists for: a job that outlived its server.  ``gc`` must never remove a
running job or one the ledger cites.  The inhibitor is spawned by the
server with a fixed argv, never adjudicated as a job command, and the job
starts whether or not an inhibitor exists.
"""
import argparse
import io
import os
import sys
import time
from contextlib import redirect_stdout
from unittest.mock import patch

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
    from app.context import set_project_root
    set_project_root(str(root))
    import app.jobs.manager as mgr_mod
    import app.jobs.policy as pol_mod
    mgr_mod._MANAGER = None
    pol_mod._POLICY = pol_mod._ShellPolicy()
    yield root
    m = mgr_mod.get_job_manager()
    for st in m.list(str(root)):
        if st["state"] == "running":
            m.kill(st["job_id"])


def _cli(root, *argv):
    from app.cli import cmd_jobs
    ns = argparse.Namespace(jobs_root=str(root), jobs_cmd=argv[0] if argv else None)
    if argv and argv[0] == "tail":
        ns.job_id, ns.n = argv[1], int(argv[2]) if len(argv) > 2 else 40
    elif argv and argv[0] == "kill":
        ns.job_id = argv[1]
    elif argv and argv[0] == "gc":
        ns.days = int(argv[1]) if len(argv) > 1 else 30
        ns.dry_run = "--dry-run" in argv
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_jobs(ns)
    return buf.getvalue()


def test_jobs_is_a_registered_subcommand():
    from app.cli import create_parser
    ns = create_parser().parse_args(["jobs", "tail", "job_abc", "-n", "5"])
    assert ns.jobs_cmd == "tail" and ns.job_id == "job_abc" and ns.n == 5
    ns = create_parser().parse_args(["jobs", "gc", "--days", "7", "--dry-run"])
    assert ns.days == 7 and ns.dry_run is True


def test_list_tail_and_kill_work_without_a_server(proj):
    from app.jobs.manager import get_job_manager
    m = get_job_manager()
    done = m.start("echo alpha; echo omega", project_root=str(proj), label="short")
    m.wait(done["job_id"], timeout_s=10)
    live = m.start("sleep 100", project_root=str(proj), label="long")
    time.sleep(0.5)

    listing = _cli(proj, "list")
    assert done["job_id"] in listing and "exited" in listing and "short" in listing
    assert live["job_id"] in listing and "running" in listing

    tail = _cli(proj, "tail", done["job_id"], 2)
    assert "omega" in tail and "exit 0" in tail

    out = _cli(proj, "kill", live["job_id"])
    assert live["job_id"] in out and "was not running" not in out
    assert m.get(live["job_id"]).state()["state"] != "running"
    # Killing again is reported as a no-op, not an error.
    assert "was not running" in _cli(proj, "kill", live["job_id"])


def test_gc_keeps_running_recent_and_cited_jobs(proj):
    from app.jobs.manager import _jobs_root, get_job_manager
    m = get_job_manager()
    cited = m.start("echo cited", project_root=str(proj))
    m.wait(cited["job_id"], timeout_s=10)          # records a ledger observation
    stale = m.start("echo stale", project_root=str(proj))
    m.wait(stale["job_id"], timeout_s=10)
    live = m.start("sleep 100", project_root=str(proj))
    time.sleep(0.3)
    # Both finished jobs are "old"; only the stale one lacks a citation.
    for jid in (cited["job_id"], stale["job_id"], live["job_id"]):
        j = m.get(jid)
        j.meta["started_at"] = time.time() - 90 * 86400
        from app.jobs.manager import _write_json
        _write_json(j.dir / "job.json", j.meta)
    # Forget the stale job's observation so it is uncited.
    stale_job = m.get(stale["job_id"])
    from app.utils.evidence.store import ledger_for
    led = ledger_for(str(proj))
    obs_ids = {r["id"] for r in led.read("observation")}
    cited_obs = m.get(cited["job_id"]).meta.get("obs_id")
    assert cited_obs and cited_obs in obs_ids
    # Remove the stale job's observation record by rewriting without it, and
    # drop the store's read cache so the rewrite is seen.
    recs = [r for r in led.read("observation")
            if ((r.get("result") or {}).get("inline") or {}).get("job_id") != stale["job_id"]]
    led._path("observation").write_text("".join(__import__("json").dumps(r) + "\n" for r in recs))
    import app.utils.evidence.store as _store
    _store._ledgers.clear()

    dry = _cli(proj, "gc", 30, "--dry-run")
    assert "would remove 1" in dry and stale["job_id"] in dry
    assert (_jobs_root(str(proj)) / stale["job_id"]).is_dir()

    out = _cli(proj, "gc", 30)
    assert "removed 1" in out
    assert not (_jobs_root(str(proj)) / stale["job_id"]).exists()
    assert (_jobs_root(str(proj)) / cited["job_id"]).is_dir(), "cited job must survive gc"
    assert (_jobs_root(str(proj)) / live["job_id"]).is_dir(), "running job must survive gc"


# ── keep-awake ────────────────────────────────────────────────────────────

def test_keep_awake_held_when_inhibitor_exists_and_released_on_kill(proj):
    from app.jobs import inhibit
    from app.jobs.manager import get_job_manager
    spawned = {}

    class _Fake:
        pid = 424242

    def fake_popen(argv, **kw):
        spawned["argv"] = argv
        return _Fake()

    # Patch the names as bound in the inhibit module only: ``subprocess`` and
    # ``sys`` are shared with the manager, whose own spawn must stay real.
    fake_sub = type("S", (), {"Popen": staticmethod(fake_popen), "DEVNULL": -3})
    with patch.object(inhibit, "shutil", type("W", (), {"which": staticmethod(lambda n: f"/usr/bin/{n}")})), \
         patch.object(inhibit, "subprocess", fake_sub), \
         patch.object(inhibit, "sys", type("P", (), {"platform": "darwin"})):
        r = get_job_manager().start("sleep 100", project_root=str(proj), keep_awake=True)
    assert r["keep_awake"] == "held"
    # Bound to the wrapper pid, so it dies with the job even if we never release.
    assert spawned["argv"][:3] == ["caffeinate", "-i", "-w"] and spawned["argv"][3] == str(r["pid"])
    job = get_job_manager().get(r["job_id"])
    assert job.meta["inhibitor_pid"] == 424242
    with patch("os.kill") as kill:
        get_job_manager().kill(r["job_id"])
    assert any(c.args[0] == 424242 for c in kill.call_args_list), "kill releases the inhibitor"


def test_keep_awake_unavailable_still_starts_the_job(proj):
    from app.jobs import inhibit
    from app.jobs.manager import get_job_manager
    with patch.object(inhibit, "shutil", type("W", (), {"which": staticmethod(lambda n: None)})):
        r = get_job_manager().start("echo x", project_root=str(proj), keep_awake=True)
    assert r["keep_awake"] == "unavailable" and "detail" in r
    assert get_job_manager().wait(r["job_id"], timeout_s=10)["exit_code"] == 0


def test_keep_awake_not_requested_spawns_no_inhibitor(proj):
    from app.jobs import inhibit
    from app.jobs.manager import get_job_manager
    with patch.object(inhibit, "acquire") as acquire:
        r = get_job_manager().start("echo x", project_root=str(proj))
    acquire.assert_not_called()
    assert "keep_awake" not in r
