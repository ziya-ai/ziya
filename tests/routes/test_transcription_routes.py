"""Tests for the optional local voice-transcription routes."""
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import transcription_routes


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(transcription_routes.router)
    return TestClient(app)


def test_status_reports_missing_optional_dependency(client):
    with patch.object(transcription_routes, "_dependency_available", return_value=False):
        response = client.get("/api/transcribe/status")

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["install_hint"] == "Click the microphone to install local transcription"


def test_status_reports_configured_backend(client):
    with (
        patch.object(transcription_routes, "_dependency_available", return_value=True),
        patch.object(
            transcription_routes,
            "_model_configuration",
            return_value=("small", "cuda", "float16"),
        ),
    ):
        response = client.get("/api/transcribe/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "model": "small",
        "device": "cuda",
        "compute_type": "float16",
        "model_loaded": False,
        "install_hint": None,
    }


def test_install_uses_the_interpreter_running_ziya():
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch.object(
            transcription_routes,
            "_dependency_available",
            side_effect=[False, True],
        ),
        patch.object(transcription_routes.subprocess, "run", return_value=completed) as run,
        patch.object(transcription_routes.importlib, "invalidate_caches") as invalidate,
    ):
        installed, diagnostic = transcription_routes._install_dependency()

    assert installed is True
    assert diagnostic == ""
    run.assert_called_once_with(
        [sys.executable, "-m", "pip", "install", "faster-whisper"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    invalidate.assert_called_once_with()


def test_install_endpoint_skips_pip_when_dependency_is_already_present(client):
    with (
        patch.object(transcription_routes, "_dependency_available", return_value=True),
        patch.object(transcription_routes, "_install_dependency") as install,
    ):
        response = client.post("/api/transcribe/install")

    assert response.status_code == 200
    assert response.json() == {"available": True, "installed": False}
    install.assert_not_called()


def test_install_endpoint_installs_missing_dependency(client):
    with (
        patch.object(transcription_routes, "_dependency_available", return_value=False),
        patch.object(
            transcription_routes,
            "_install_dependency",
            return_value=(True, ""),
        ) as install,
    ):
        response = client.post("/api/transcribe/install")

    assert response.status_code == 200
    assert response.json() == {"available": True, "installed": True}
    install.assert_called_once_with()


def test_install_endpoint_reports_pip_failure(client):
    with (
        patch.object(transcription_routes, "_dependency_available", return_value=False),
        patch.object(
            transcription_routes,
            "_install_dependency",
            return_value=(False, "permission denied"),
        ),
    ):
        response = client.post("/api/transcribe/install")

    assert response.status_code == 500
    assert "permission denied" in response.json()["detail"]


def test_transcribe_rejects_request_when_dependency_missing(client):
    with patch.object(transcription_routes, "_dependency_available", return_value=False):
        response = client.post(
            "/api/transcribe",
            files={"file": ("recording.webm", b"audio", "audio/webm")},
        )

    assert response.status_code == 503
    assert "pip install faster-whisper" in response.json()["detail"]


def test_transcribe_rejects_unsupported_audio_type(client):
    with patch.object(transcription_routes, "_dependency_available", return_value=True):
        response = client.post(
            "/api/transcribe",
            files={"file": ("recording.bin", b"audio", "application/octet-stream")},
        )

    assert response.status_code == 415


def test_transcribe_rejects_oversized_stream(client):
    with (
        patch.object(transcription_routes, "_dependency_available", return_value=True),
        patch.object(transcription_routes, "MAX_AUDIO_BYTES", 4),
    ):
        response = client.post(
            "/api/transcribe",
            files={"file": ("recording.webm", b"12345", "audio/webm")},
        )

    assert response.status_code == 413


def test_transcribe_returns_local_model_result(client):
    expected = {"text": "hello world", "language": "en", "duration": 1.25}
    with (
        patch.object(transcription_routes, "_dependency_available", return_value=True),
        patch.object(transcription_routes, "_transcribe_file", return_value=expected) as transcribe,
    ):
        response = client.post(
            "/api/transcribe",
            files={"file": ("recording.webm", b"audio bytes", "audio/webm")},
        )

    assert response.status_code == 200
    assert response.json() == expected
    assert transcribe.call_count == 1
    assert not transcribe.call_args.args[0].exists()
