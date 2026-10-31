"""Managed jobs reach the frontend: the status index and the jobs API.

A job started with ``job_start`` lived only in tool results that scrolled
away with the turn.  Two seams now carry it to surfaces that persist:

  * ``GET .../task-runs/status-index`` gains ``jobs``: per conversation,
    the chat-started jobs (no ``run_id`` owner) with state / exit / times,
    and ``live`` is true while any such job runs even with no task run.
  * ``GET .../jobs`` / ``GET .../jobs/{id}`` / ``POST .../jobs/{id}/kill``
    expose the JobManager's view for the open conversation's rail.

Real jobs (``sleep``, ``echo``) through the real manager and the real
routers; nothing about the manager is mocked.
"""
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PID = "proj-jobs"
CONV = "conv-a"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    home = tmp_path / ".ziya"
    (home / "projects" / PID / "chats").mkdir(parents=True)
    (home / "projects" / PID / "project.json").write_text(json.dumps({
        "id": PID, "name": "Jobs", "path": str(root),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": 1, "lastAccessedAt": 1,
    }))
    monkeypatch.setenv("ZIYA_HOME", str(home))
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    from app.context import set_project_root
    set_project_root(str(root))
    import app.jobs.manager as mgr_mod
    import app.jobs.policy as pol_mod
    mgr_mod._MANAGER = None
    pol_mod._POLICY = pol_mod._ShellPolicy()
    from app.api import jobs as jobs_api, task_runs
    app = FastAPI()
    app.include_router(task_runs.router)
    app.include_router(jobs_api.router)
    yield root, TestClient(app), mgr_mod.get_job_manager()
    try:
        for st in mgr_mod.get_job_manager().list(str(root)):
            if st["state"] == "running":
                mgr_mod.get_job_manager().kill(st["job_id"])
    except Exception:  # noqa: BLE001
        pass


def _start(mgr, root, command, conv=CONV, **owner_extra):
    owner = {"conversation_id": conv, **owner_extra}
    return mgr.start(command, project_root=str(root), owner=owner, conversation_id=conv,
                     label=command[:20])["job_id"]


def _wait_exit(mgr, job_id, root):
    r = mgr.wait(job_id, until=["exit"], timeout_s=20, project_root=str(root))
    assert r.get("reason") == "exit", r


# ── the index module ─────────────────────────────────────────────────

def test_index_lists_chat_started_jobs_per_conversation_and_excludes_run_owned(env):
    root, _, mgr = env
    from app.jobs.index import conversation_job_index, has_running_jobs
    running = _start(mgr, root, "sleep 30")
    done = _start(mgr, root, "echo finished")
    _wait_exit(mgr, done, root)
    _start(mgr, root, "sleep 30", conv="conv-b", run_id="run-1", block_id="b1")  # task-run job

    idx = conversation_job_index(str(root))
    assert set(idx) == {CONV}, "a job owned by a task run is the run's gear, not a job glyph"
    by_id = {j["job_id"]: j for j in idx[CONV]}
    assert by_id[running]["state"] == "running" and by_id[running]["runtime_s"] >= 0
    assert by_id[done]["state"] == "exited" and by_id[done]["exit_code"] == 0
    assert by_id[done]["ended_at"] > by_id[done]["started_at"]
    assert by_id[done]["label"] == "echo finished"
    # Summaries carry no command text or owner: the sidebar needs neither.
    assert "command" not in by_id[done] and "owner" not in by_id[done]
    assert has_running_jobs(idx) is True
    mgr.kill(running)
    assert has_running_jobs(conversation_job_index(str(root))) is False


def test_exited_state_is_stable_and_names_its_conversation(env):
    root, _, mgr = env
    jid = _start(mgr, root, "bash -c 'exit 3'")
    _wait_exit(mgr, jid, root)
    job = mgr.get(jid, str(root))
    a, b = job.state(), job.state()
    assert a["state"] == "exited" and a["exit_code"] == 3
    assert a["conversation_id"] == CONV
    assert a == b
    # The cached final state is handed out as a copy: a caller annotating it
    # (kill() sets ``killed``) must not rewrite what the next caller sees.
    a["killed"] = True
    assert "killed" not in job.state()


# ── the API seams ────────────────────────────────────────────────────

def test_status_index_carries_jobs_and_live_follows_them(env):
    root, client, mgr = env
    r = client.get(f"/api/v1/projects/{PID}/task-runs/status-index")
    assert r.status_code == 200, r.text
    assert r.json()["jobs"] == {} and r.json()["live"] is False

    jid = _start(mgr, root, "sleep 30")
    body = client.get(f"/api/v1/projects/{PID}/task-runs/status-index").json()
    assert body["conversations"] == {}, "no task runs exist; only the job keeps this live"
    assert body["live"] is True
    assert [j["job_id"] for j in body["jobs"][CONV]] == [jid]
    assert body["jobs"][CONV][0]["state"] == "running"

    mgr.kill(jid)
    body = client.get(f"/api/v1/projects/{PID}/task-runs/status-index").json()
    assert body["live"] is False
    assert body["jobs"][CONV][0]["state"] == "exited"
    assert body["jobs"][CONV][0]["signal"]  # TERM'd, not a clean exit


def test_jobs_listing_filters_by_conversation_and_state(env):
    root, client, mgr = env
    a = _start(mgr, root, "sleep 30")
    b = _start(mgr, root, "echo b", conv="conv-b")
    _wait_exit(mgr, b, root)
    base = f"/api/v1/projects/{PID}/jobs"
    assert {j["job_id"] for j in client.get(base).json()} == {a, b}
    assert [j["job_id"] for j in client.get(base, params={"conversation_id": CONV}).json()] == [a]
    assert [j["job_id"] for j in client.get(base, params={"state": "exited"}).json()] == [b]
    assert client.get(f"/api/v1/projects/nope/jobs").status_code == 404


def test_job_detail_has_tail_and_kill_stops_it(env):
    root, client, mgr = env
    jid = _start(mgr, root, "echo line-one; echo line-two; sleep 30")
    deadline = time.time() + 10
    while time.time() < deadline:
        detail = client.get(f"/api/v1/projects/{PID}/jobs/{jid}", params={"tail_lines": 1}).json()
        if "line-two" in detail.get("tail", ""):
            break
        time.sleep(0.2)
    assert detail["state"] == "running"
    assert detail["tail"].splitlines() == ["line-two"], detail["tail"]
    assert detail["command"].startswith("echo line-one")
    assert detail["conversation_id"] == CONV

    k = client.post(f"/api/v1/projects/{PID}/jobs/{jid}/kill")
    assert k.status_code == 200, k.text
    assert k.json()["killed"] is True and k.json()["state"] != "running"
    assert client.get(f"/api/v1/projects/{PID}/jobs/{jid}").json()["state"] == "exited"
    assert client.get(f"/api/v1/projects/{PID}/jobs/job_000000000000").status_code == 404
