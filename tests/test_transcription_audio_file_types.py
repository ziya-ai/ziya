"""/api/transcribe accepts dropped .m4a and .wav files.

Browsers label these formats inconsistently (audio/x-m4a, audio/wave, an
empty type, application/octet-stream), so the route accepts the known
spellings and, for a generic or missing type only, falls back to the file
extension.  The model call is stubbed; what is under test is admission and
that the temporary file handed to the model keeps a suffix the decoder can
use.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import transcription_routes as tr


@pytest.fixture
def client(monkeypatch):
    seen: list[Path] = []

    def fake_transcribe(path: Path):
        seen.append(path)
        assert path.exists() and path.stat().st_size > 0
        return {"text": "hello", "language": "en", "duration": 1.0}

    monkeypatch.setattr(tr, "_dependency_available", lambda: True)
    monkeypatch.setattr(tr, "_transcribe_file", fake_transcribe)
    app = FastAPI()
    app.include_router(tr.router)
    c = TestClient(app)
    c.seen = seen
    return c


def _post(client, filename, content_type):
    return client.post(
        "/api/transcribe",
        files={"file": (filename, b"RIFF....WAVEfmt ", content_type)},
    )


@pytest.mark.parametrize("filename,content_type,suffix", [
    ("memo.m4a", "audio/x-m4a", ".m4a"),
    ("memo.m4a", "audio/m4a", ".m4a"),
    ("memo.m4a", "audio/mp4", ".mp4"),
    ("take.wav", "audio/wav", ".wav"),
    ("take.wav", "audio/x-wav", ".wav"),
    ("take.wav", "audio/wave", ".wav"),
    ("take.wav", "audio/vnd.wave", ".wav"),
    ("take.WAV", "application/octet-stream", ".wav"),
    ("memo.m4a", "application/octet-stream", ".m4a"),
])
def test_m4a_and_wav_are_accepted(client, filename, content_type, suffix):
    resp = _post(client, filename, content_type)
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "hello"
    assert len(client.seen) == 1 and client.seen[0].suffix == suffix
    # The temporary upload is removed after transcription.
    assert not client.seen[0].exists()


@pytest.mark.parametrize("filename,content_type", [
    ("notes.txt", "application/octet-stream"),  # generic type, wrong extension
    ("take.wav", "text/plain"),                 # specific wrong type: no fallback
    ("clip.flac", "audio/flac"),
])
def test_other_files_are_rejected(client, filename, content_type):
    resp = _post(client, filename, content_type)
    assert resp.status_code == 415
    assert client.seen == []
