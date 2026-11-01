"""Background transcription jobs (30-minute meetings).

Three layers, each asserted on what its caller sees:
  * TranscriptionJobManager: state machine, progress, cancel, cleanup.
  * /api/transcribe/jobs routes: upload -> 202 job -> poll -> transcript,
    cancel, unknown job, configurable size ceiling.
  * RequestSizeMiddleware path override: a transcription upload above the
    global 20 MB request cap reaches the route; other paths stay capped.
"""
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.utils import transcription_jobs as tj
from app.utils.transcription_jobs import (
    TooManyJobs,
    TranscriptionCancelled,
    TranscriptionJobManager,
)


def _wait_terminal(get, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = get(job_id)
        if snap["state"] in tj.TERMINAL_STATES:
            return snap
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never finished: {get(job_id)}")


def _audio(tmp_path, name="memo.m4a"):
    path = tmp_path / name
    path.write_bytes(b"audio")
    return path


# ── manager ────────────────────────────────────────────────────────────────

def test_job_reports_progress_then_text_and_deletes_the_upload(tmp_path):
    manager = TranscriptionJobManager()
    path = _audio(tmp_path)
    seen = []
    release = threading.Event()

    def runner(p, on_progress, cancel):
        on_progress(0.0, "")
        on_progress(0.5, "first half")
        release.wait(2)
        return {"text": "first half second half", "language": "en", "duration": 1800.0}

    started = manager.submit(path, "memo.m4a", runner)
    assert started["state"] == tj.QUEUED

    deadline = time.time() + 2
    while time.time() < deadline:
        snap = manager.get(started["job_id"])
        seen.append(snap["state"])
        if snap["progress"] == 0.5:
            break
        time.sleep(0.01)
    assert snap["state"] == tj.RUNNING and snap["text"] == "first half"
    assert path.exists()  # still in use while running

    release.set()
    done = _wait_terminal(manager.get, started["job_id"])
    assert done["state"] == tj.DONE
    assert done["progress"] == 1.0
    assert done["text"] == "first half second half"
    assert done["duration"] == 1800.0
    assert not path.exists()


def test_cancel_stops_a_running_job_and_deletes_the_upload(tmp_path):
    manager = TranscriptionJobManager()
    path = _audio(tmp_path)
    running = threading.Event()

    def runner(p, on_progress, cancel):
        on_progress(0.0, "")
        running.set()
        if cancel.wait(5):
            raise TranscriptionCancelled()
        return {"text": "should not finish"}

    job_id = manager.submit(path, "memo.m4a", runner)["job_id"]
    assert running.wait(2)
    assert manager.cancel(job_id)["cancel_requested"] is True
    done = _wait_terminal(manager.get, job_id)
    assert done["state"] == tj.CANCELLED
    assert not path.exists()


def test_runner_failure_becomes_an_error_state(tmp_path):
    manager = TranscriptionJobManager()
    path = _audio(tmp_path)

    def runner(p, on_progress, cancel):
        raise ValueError("Invalid data found when processing input")

    job_id = manager.submit(path, "memo.m4a", runner)["job_id"]
    done = _wait_terminal(manager.get, job_id)
    assert done["state"] == tj.ERROR
    assert "Invalid data found" in done["error"]
    assert not path.exists()


def test_active_job_ceiling_refuses_without_taking_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "MAX_ACTIVE_JOBS", 1)
    manager = TranscriptionJobManager()
    gate = threading.Event()

    def runner(p, on_progress, cancel):
        gate.wait(5)
        return {"text": ""}

    manager.submit(_audio(tmp_path, "a.wav"), "a.wav", runner)
    second = _audio(tmp_path, "b.wav")
    with pytest.raises(TooManyJobs):
        manager.submit(second, "b.wav", runner)
    assert second.exists()  # caller still owns it
    gate.set()


def test_unknown_jobs_and_retention(tmp_path, monkeypatch):
    manager = TranscriptionJobManager()
    assert manager.get("nope") is None
    assert manager.cancel("nope") is None

    job_id = manager.submit(_audio(tmp_path), "m.m4a", lambda p, o, c: {"text": "x"})["job_id"]
    _wait_terminal(manager.get, job_id)
    monkeypatch.setattr(tj, "RETENTION_SECONDS", -1)
    assert manager.get(job_id) is None


# ── routes ─────────────────────────────────────────────────────────────────

@pytest.fixture
def routes(monkeypatch):
    from app.routes import transcription_routes as tr

    calls = {"gate": None}

    def fake_transcribe(path: Path, on_progress=None, cancel=None):
        assert path.exists() and path.stat().st_size > 0
        if on_progress:
            on_progress(0.0, "")
            on_progress(0.25, "hello")
        gate = calls["gate"]
        if gate is not None:
            while not gate.wait(0.01):
                if cancel is not None and cancel.is_set():
                    raise TranscriptionCancelled()
        return {"text": "hello world", "language": "en", "duration": 1800.0}

    monkeypatch.setattr(tr, "_dependency_available", lambda: True)
    monkeypatch.setattr(tr, "_transcribe_file", fake_transcribe)
    monkeypatch.setattr(tr, "job_manager", TranscriptionJobManager())
    app = FastAPI()
    app.include_router(tr.router)
    client = TestClient(app)
    client.calls = calls
    return client


def _poll(client, job_id):
    return _wait_terminal(lambda j: client.get(f"/api/transcribe/jobs/{j}").json(), job_id)


def test_job_route_returns_at_once_and_polls_to_the_transcript(routes):
    resp = routes.post(
        "/api/transcribe/jobs",
        files={"file": ("meeting.m4a", b"x" * 4096, "audio/x-m4a")},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["filename"] == "meeting.m4a" and body["job_id"]
    done = _poll(routes, body["job_id"])
    assert done["state"] == "done"
    assert done["text"] == "hello world"


def test_job_route_cancel(routes):
    routes.calls["gate"] = threading.Event()
    job_id = routes.post(
        "/api/transcribe/jobs",
        files={"file": ("meeting.wav", b"RIFF" * 512, "audio/wav")},
    ).json()["job_id"]
    resp = routes.post(f"/api/transcribe/jobs/{job_id}/cancel")
    assert resp.status_code == 200 and resp.json()["cancel_requested"] is True
    assert _poll(routes, job_id)["state"] == "cancelled"


def test_unknown_job_is_404(routes):
    assert routes.get("/api/transcribe/jobs/missing").status_code == 404
    assert routes.post("/api/transcribe/jobs/missing/cancel").status_code == 404


def test_job_route_rejects_non_audio(routes):
    resp = routes.post(
        "/api/transcribe/jobs",
        files={"file": ("notes.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 415


def test_ceiling_is_configurable_and_reported(routes, monkeypatch):
    monkeypatch.setenv("ZIYA_TRANSCRIBE_MAX_MB", "1")
    status = routes.get("/api/transcribe/status").json()
    assert status["max_bytes"] == 1024 * 1024

    over = routes.post(
        "/api/transcribe/jobs",
        files={"file": ("big.wav", b"\0" * (2 * 1024 * 1024), "audio/wav")},
    )
    assert over.status_code == 413
    assert "1 MB" in over.json()["detail"]

    under = routes.post(
        "/api/transcribe/jobs",
        files={"file": ("ok.wav", b"\0" * (512 * 1024), "audio/wav")},
    )
    assert under.status_code == 202


def test_default_ceiling_fits_a_30_minute_stereo_wav(monkeypatch):
    from app.routes import transcription_routes as tr

    monkeypatch.delenv("ZIYA_TRANSCRIBE_MAX_MB", raising=False)
    thirty_min_48k_stereo_wav = 30 * 60 * 48_000 * 2 * 2
    assert tr.transcription_max_bytes() > thirty_min_48k_stereo_wav


# ── middleware ─────────────────────────────────────────────────────────────

def test_request_size_middleware_path_override():
    from app.middleware.request_size import RequestSizeMiddleware

    app = FastAPI()

    @app.post("/api/transcribe/jobs")
    async def big(request: Request):
        return {"bytes": len(await request.body())}

    @app.post("/api/other")
    async def other(request: Request):
        return {"bytes": len(await request.body())}

    app.add_middleware(
        RequestSizeMiddleware,
        default_max_size_mb=1,
        path_limits={"/api/transcribe": 4 * 1024 * 1024},
    )
    client = TestClient(app)
    body = b"\0" * (2 * 1024 * 1024)

    allowed = client.post("/api/transcribe/jobs", content=body)
    assert allowed.status_code == 200 and allowed.json()["bytes"] == len(body)
    assert client.post("/api/other", content=body).status_code == 413
    assert client.post("/api/transcribe/jobs", content=body * 3).status_code == 413
