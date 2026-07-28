"""
Local voice-transcription routes.

Audio is processed entirely on the Ziya host with faster-whisper. The model
and its native dependencies are optional and loaded only on first use.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.config.env_registry import ziya_env
from app.utils.logging_utils import logger
from app.utils.paths import get_ziya_home

router = APIRouter(tags=["transcription"])

MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_AUDIO_TYPES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

_model: Any = None
_model_lock = threading.Lock()
_install_lock = threading.Lock()
_transcription_lock = threading.Lock()


def _dependency_available() -> bool:
    """Return whether the optional faster-whisper package is installed."""
    return importlib.util.find_spec("faster_whisper") is not None


def _install_dependency() -> tuple[bool, str]:
    """Install faster-whisper into the interpreter running Ziya.

    Using ``sys.executable -m pip`` targets the active Ziya environment,
    including pipx installations and virtual environments, rather than an
    unrelated ``pip`` executable from the user's shell PATH.
    """
    with _install_lock:
        if _dependency_available():
            return True, ""

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "faster-whisper"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        importlib.invalidate_caches()
        if result.returncode == 0 and _dependency_available():
            return True, ""

        output = (result.stderr or result.stdout or "pip exited without diagnostic output").strip()
        return False, output[-1000:]


def _model_configuration() -> tuple[str, str, str]:
    """Return the configured model, device, and compute type."""
    return (
        ziya_env("ZIYA_WHISPER_MODEL"),
        ziya_env("ZIYA_WHISPER_DEVICE"),
        ziya_env("ZIYA_WHISPER_COMPUTE_TYPE"),
    )


def _get_model():
    """Lazily construct and cache the configured Whisper model."""
    global _model

    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        from faster_whisper import WhisperModel

        model_name, device, compute_type = _model_configuration()
        download_root = get_ziya_home() / "models" / "whisper"
        download_root.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Loading faster-whisper model %s on %s with compute type %s",
            model_name,
            device,
            compute_type,
        )
        _model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(download_root),
        )
        return _model


def _transcribe_file(path: Path) -> dict[str, Any]:
    """Transcribe one audio file without blocking the event-loop thread."""
    model = _get_model()

    # CTranslate2 model calls are serialized. This avoids overlapping CPU-heavy
    # jobs and protects model implementations that are not re-entrant.
    with _transcription_lock:
        segments, info = model.transcribe(
            str(path),
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()

    return {
        "text": text,
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
    }


@router.post("/api/transcribe/install")
async def install_transcription_backend():
    """Install the fixed local transcription dependency into Ziya's runtime."""
    if _dependency_available():
        return {"available": True, "installed": False}

    try:
        installed, diagnostic = await asyncio.to_thread(_install_dependency)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception("Unable to launch automatic faster-whisper installation")
        raise HTTPException(
            status_code=500,
            detail=f"Automatic transcription installation failed: {exc}",
        ) from exc

    if not installed:
        logger.error("Automatic faster-whisper installation failed: %s", diagnostic)
        raise HTTPException(
            status_code=500,
            detail=f"Automatic transcription installation failed: {diagnostic}",
        )
    return {"available": True, "installed": True}


@router.get("/api/transcribe/status")
async def transcription_status():
    """Report whether local voice transcription can be used."""
    model_name, device, compute_type = _model_configuration()
    available = _dependency_available()
    return {
        "available": available,
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "model_loaded": _model is not None,
        "install_hint": None if available else "Click the microphone to install local transcription",
    }


@router.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
):
    """Accept a browser recording and return its local transcription."""
    if not _dependency_available():
        raise HTTPException(
            status_code=503,
            detail="Voice transcription is unavailable. Install it with: pip install faster-whisper",
        )

    content_type = (file.content_type or "").split(";", 1)[0].lower()
    suffix = ALLOWED_AUDIO_TYPES.get(content_type)
    if suffix is None:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio type: {content_type or 'unknown'}",
        )

    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="Audio recording exceeds the 25 MB limit")
        except ValueError:
            pass

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
            temporary_path = Path(temporary.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Audio recording exceeds the 25 MB limit",
                    )
                temporary.write(chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail="Audio recording is empty")

        try:
            return await asyncio.to_thread(_transcribe_file, temporary_path)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Local voice transcription failed")
            raise HTTPException(status_code=500, detail="Voice transcription failed") from exc
    finally:
        await file.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
