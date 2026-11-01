"""
Local voice-transcription routes.

Audio is processed entirely on the Ziya host with faster-whisper. The model
and its native dependencies are optional and loaded only on first use.

``POST /api/transcribe`` transcribes within the request and suits short
clips.  ``POST /api/transcribe/jobs`` returns at once and runs the work in
the background (``app.utils.transcription_jobs``) with progress and cancel;
the composer uses it for everything, since a 30-minute meeting takes
minutes on CPU.

Handlers that await nothing are plain ``def`` so Starlette runs them on its
worker threadpool instead of the event loop (``transcription_status`` calls
``importlib.util.find_spec``, a filesystem walk, and is polled).  Guarded by
``tests/test_transcription_routes_not_event_loop_blocking.py``.
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
from typing import Any, Callable

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.config.env_registry import ziya_env
from app.utils.logging_utils import logger
from app.utils.paths import get_ziya_home
from app.utils.transcription_jobs import (
    TooManyJobs,
    TranscriptionCancelled,
    job_manager,
)

router = APIRouter(tags=["transcription"])

# Bytes a multipart request carries beyond the audio itself (boundary, part
# headers), so a file exactly at the ceiling is not refused on its
# Content-Length.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
COPY_CHUNK_BYTES = 1024 * 1024


def transcription_max_bytes() -> int:
    """Largest audio file accepted, from ZIYA_TRANSCRIBE_MAX_MB."""
    return int(ziya_env("ZIYA_TRANSCRIBE_MAX_MB")) * 1024 * 1024


def transcription_request_limit() -> int:
    """Largest transcription request body, for RequestSizeMiddleware."""
    return transcription_max_bytes() + MULTIPART_OVERHEAD_BYTES


ALLOWED_AUDIO_TYPES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/vnd.wave": ".wav",
}

# A dropped file can arrive with no usable type: an empty one, or the
# generic application/octet-stream.  Only for those, and only for these
# formats, the filename extension decides.
GENERIC_CONTENT_TYPES = {"", "application/octet-stream"}
EXTENSION_FALLBACK_SUFFIXES = {".m4a", ".wav"}

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


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise TranscriptionCancelled()


def _transcribe_file(
    path: Path,
    on_progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Transcribe one audio file without blocking the event-loop thread.

    ``on_progress(fraction, text_so_far)`` is called once this call holds
    the model (fraction 0) and after each decoded segment.  ``cancel`` is
    checked at the same points.  faster-whisper decodes lazily, so leaving
    the segment loop stops the work.
    """
    model = _get_model()

    # CTranslate2 model calls are serialized. This avoids overlapping CPU-heavy
    # jobs and protects model implementations that are not re-entrant.
    with _transcription_lock:
        _check_cancel(cancel)
        if on_progress is not None:
            on_progress(0.0, "")
        segments, info = model.transcribe(
            str(path),
            beam_size=5,
            vad_filter=True,
        )
        total = getattr(info, "duration", None) or 0.0
        parts: list[str] = []
        for segment in segments:
            piece = segment.text.strip()
            if piece:
                parts.append(piece)
            if on_progress is not None and total > 0:
                on_progress(min(segment.end / total, 1.0), " ".join(parts))
            _check_cancel(cancel)
        text = " ".join(parts)

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
def transcription_status():
    """Report whether local voice transcription can be used."""
    model_name, device, compute_type = _model_configuration()
    available = _dependency_available()
    return {
        "available": available,
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "model_loaded": _model is not None,
        "max_bytes": transcription_max_bytes(),
        "install_hint": None if available else "Click the microphone to install local transcription",
    }


def _require_dependency() -> None:
    if not _dependency_available():
        raise HTTPException(
            status_code=503,
            detail="Voice transcription is unavailable. Install it with: pip install faster-whisper",
        )


def _format_limit(max_bytes: int) -> str:
    megabytes = max_bytes / (1024 * 1024)
    return f"{megabytes / 1024:g} GB" if megabytes >= 1024 else f"{megabytes:g} MB"


def _copy_bounded(source, destination, max_bytes: int) -> int:
    """Copy in chunks; return the byte count, or -1 once max_bytes is passed."""
    size = 0
    while chunk := source.read(COPY_CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            return -1
        destination.write(chunk)
    return size


async def _receive_upload(request: Request, file: UploadFile) -> Path:
    """Validate an audio upload and copy it to a temporary file.

    Returns the temporary path, which the caller owns and must delete.
    Raises HTTPException (415, 413, 400) leaving nothing on disk.
    """
    content_type = (file.content_type or "").split(";", 1)[0].lower()
    suffix = ALLOWED_AUDIO_TYPES.get(content_type)
    if suffix is None and content_type in GENERIC_CONTENT_TYPES:
        name_suffix = Path(file.filename or "").suffix.lower()
        if name_suffix in EXTENSION_FALLBACK_SUFFIXES:
            suffix = name_suffix
    if suffix is None:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio type: {content_type or 'unknown'}",
        )

    max_bytes = transcription_max_bytes()
    too_large = HTTPException(
        status_code=413,
        detail=f"Audio file exceeds the {_format_limit(max_bytes)} limit",
    )
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            declared = int(declared_length)
        except ValueError:
            declared = None
        if declared is not None and declared > max_bytes + MULTIPART_OVERHEAD_BYTES:
            raise too_large

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
            temporary_path = Path(temporary.name)
            # Up to a gigabyte of disk I/O: off the event loop.
            size = await asyncio.to_thread(_copy_bounded, file.file, temporary, max_bytes)
        if size < 0:
            raise too_large
        if size == 0:
            raise HTTPException(status_code=400, detail="Audio recording is empty")
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


@router.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
):
    """Transcribe an audio upload within the request (short clips)."""
    _require_dependency()

    temporary_path: Path | None = None
    try:
        temporary_path = await _receive_upload(request, file)
        try:
            return await asyncio.to_thread(_transcribe_file, temporary_path)
        except Exception as exc:
            logger.exception("Local voice transcription failed")
            raise HTTPException(status_code=500, detail="Voice transcription failed") from exc
    finally:
        await file.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _run_job(path: Path, on_progress, cancel: threading.Event) -> dict[str, Any]:
    """Job runner.  Resolves _transcribe_file at call time so tests can stub it."""
    return _transcribe_file(path, on_progress=on_progress, cancel=cancel)


@router.post("/api/transcribe/jobs", status_code=202)
async def start_transcription_job(
    request: Request,
    file: UploadFile = File(...),
):
    """Accept an audio upload and transcribe it in the background.

    Returns the job snapshot at once; poll ``GET /api/transcribe/jobs/{id}``.
    """
    _require_dependency()
    filename = file.filename or "audio"
    try:
        temporary_path = await _receive_upload(request, file)
    finally:
        await file.close()

    try:
        return job_manager.submit(temporary_path, filename, _run_job)
    except TooManyJobs as exc:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/api/transcribe/jobs/{job_id}")
def transcription_job_status(job_id: str):
    """Report a job's state, progress, and (when done) its transcript."""
    snapshot = job_manager.get(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Unknown transcription job")
    return snapshot


@router.post("/api/transcribe/jobs/{job_id}/cancel")
def cancel_transcription_job(job_id: str):
    """Request cancellation; the job stops at its next segment."""
    snapshot = job_manager.cancel(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Unknown transcription job")
    return snapshot


