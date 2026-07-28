"""
Tests for artifact-blob serving: path authorization, media-type mapping,
and the HTTP route that streams a run's frozen artifact bytes.

The path resolver is the security chokepoint — the filename component of
the request URL originates from a model-chosen artifact name, so it is
attacker-influenced and must never address a file outside the run's own
artifacts directory.  Those tests run unconditionally.

The route tests skip until the app/api/task_runs.py diff is applied.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.utils.task_artifacts import (
    INLINE_SAFE_MEDIA_TYPES,
    media_type_for_filename,
    resolve_artifact_blob_path,
)


class TestMediaTypeMapping:
    """media_type_for_filename maps a bounded, fixed extension table."""

    @pytest.mark.parametrize("filename,expected", [
        ("chart.png", "image/png"),
        ("CHART.PNG", "image/png"),          # case-insensitive
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("anim.gif", "image/gif"),
        ("modern.webp", "image/webp"),
        ("old.bmp", "image/bmp"),
        ("vector.svg", "image/svg+xml"),
        ("doc.pdf", "application/pdf"),
        ("notes.txt", "text/plain"),
        ("notes.md", "text/plain"),
        ("data.json", "application/json"),
    ])
    def test_known_extensions(self, filename, expected):
        assert media_type_for_filename(filename) == expected

    @pytest.mark.parametrize("filename", [
        "mystery.xyz", "noextension", "archive.tar.zst", "script.html",
        "page.htm", "code.js", "styles.css",
    ])
    def test_unknown_extensions_fall_back_to_octet_stream(self, filename):
        assert media_type_for_filename(filename) == "application/octet-stream"

    def test_html_and_js_are_not_inline_safe(self):
        """A model-named .html/.js artifact must not be servable inline —
        that would let model output execute in Ziya's own origin."""
        for name in ("evil.html", "evil.js", "evil.svg"):
            assert media_type_for_filename(name) not in INLINE_SAFE_MEDIA_TYPES

    def test_images_and_pdf_are_inline_safe(self):
        for name in ("a.png", "b.jpg", "c.gif", "d.webp", "e.pdf", "f.txt"):
            assert media_type_for_filename(name) in INLINE_SAFE_MEDIA_TYPES

    def test_svg_deliberately_excluded_from_inline(self):
        """SVG can carry script; it is recognized but never inline-safe."""
        assert media_type_for_filename("x.svg") == "image/svg+xml"
        assert "image/svg+xml" not in INLINE_SAFE_MEDIA_TYPES


class TestResolveArtifactBlobPath:
    """The authorization chokepoint for the blob route."""

    @pytest.fixture
    def artifacts_dir(self, tmp_path):
        d = tmp_path / "run-1" / "artifacts"
        d.mkdir(parents=True)
        (d / "chart.png").write_bytes(b"\x89PNG fake")
        return d

    def test_resolves_existing_file(self, artifacts_dir):
        path, err = resolve_artifact_blob_path(str(artifacts_dir), "chart.png")
        assert err is None
        assert path == (artifacts_dir / "chart.png").resolve()

    def test_missing_file_is_not_found(self, artifacts_dir):
        path, err = resolve_artifact_blob_path(str(artifacts_dir), "absent.png")
        assert path is None
        assert "not found" in err

    @pytest.mark.parametrize("bad", [
        "../secret.txt",
        "../../etc/passwd",
        "..",
        ".",
        "sub/nested.png",
        "sub\\nested.png",
        "/etc/passwd",
        "/absolute.png",
        "..%2Fpasswd",          # pre-decoded form still contains ..
        "a/../../b.png",
    ])
    def test_traversal_and_separators_rejected(self, artifacts_dir, bad):
        path, err = resolve_artifact_blob_path(str(artifacts_dir), bad)
        assert path is None, f"{bad!r} should be rejected"
        assert err

    def test_empty_filename_rejected(self, artifacts_dir):
        path, err = resolve_artifact_blob_path(str(artifacts_dir), "")
        assert path is None
        assert err

    def test_nul_byte_rejected(self, artifacts_dir):
        path, err = resolve_artifact_blob_path(str(artifacts_dir), "ok.png\x00.txt")
        assert path is None
        assert err

    def test_directory_is_not_servable(self, artifacts_dir):
        (artifacts_dir / "adir").mkdir()
        path, err = resolve_artifact_blob_path(str(artifacts_dir), "adir")
        assert path is None
        assert "not found" in err

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
    def test_symlink_escaping_base_rejected(self, artifacts_dir, tmp_path):
        """Guard 2: a symlink whose *resolved* target is outside the base
        must be refused even though its filename has no separators."""
        outside = tmp_path / "outside_secret.png"
        outside.write_bytes(b"secret")
        (artifacts_dir / "link.png").symlink_to(outside)
        path, err = resolve_artifact_blob_path(str(artifacts_dir), "link.png")
        assert path is None
        assert "escapes" in err

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
    def test_symlink_inside_base_allowed(self, artifacts_dir):
        """A symlink resolving to a sibling *inside* the base is fine."""
        (artifacts_dir / "alias.png").symlink_to(artifacts_dir / "chart.png")
        path, err = resolve_artifact_blob_path(str(artifacts_dir), "alias.png")
        assert err is None
        assert path == (artifacts_dir / "chart.png").resolve()

    def test_nonexistent_base_dir_reports_not_found(self, tmp_path):
        path, err = resolve_artifact_blob_path(str(tmp_path / "nope"), "x.png")
        assert path is None
        assert err

    def test_dotfile_name_allowed_when_present(self, artifacts_dir):
        """A leading dot is not traversal; only '..' components are."""
        (artifacts_dir / ".hidden.png").write_bytes(b"x")
        path, err = resolve_artifact_blob_path(str(artifacts_dir), ".hidden.png")
        assert err is None
        assert path.name == ".hidden.png"


# ---------------------------------------------------------------------------
# HTTP route — activates once the app/api/task_runs.py diff is applied
# ---------------------------------------------------------------------------

def _route_available() -> bool:
    try:
        from app.api import task_runs
    except Exception:
        return False
    return any(
        "artifacts" in getattr(r, "path", "")
        for r in getattr(task_runs.router, "routes", [])
    )


route_required = pytest.mark.skipif(
    not _route_available(),
    reason="artifact blob route not yet wired (apply the task_runs.py diff)",
)


@route_required
class TestArtifactBlobRoute:
    """End-to-end behavior of GET .../task-runs/{run_id}/artifacts/{filename}."""

    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        """Point the route's project dir at a temp tree with one blob."""
        from app.api import task_runs as mod

        run_id = "run-abc"
        artifacts = tmp_path / "task_runs" / run_id / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        (artifacts / "evil.html").write_bytes(b"<script>alert(1)</script>")

        monkeypatch.setattr(mod, "get_project_dir", lambda pid: tmp_path)

        class _Run:
            id = run_id

        class _Storage:
            def get(self, rid):
                return _Run() if rid == run_id else None

        monkeypatch.setattr(mod, "_get_storage", lambda pid: _Storage())
        return mod, run_id

    @pytest.mark.asyncio
    async def test_serves_png_inline(self, wired):
        mod, run_id = wired
        resp = await mod.get_artifact_blob("proj", run_id, "chart.png")
        assert resp.media_type == "image/png"
        assert resp.body.startswith(b"\x89PNG")
        assert "inline" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_html_forced_to_attachment_octet_stream(self, wired):
        """A model-named .html artifact must never be served inline as
        text/html — it would execute in Ziya's origin."""
        mod, run_id = wired
        resp = await mod.get_artifact_blob("proj", run_id, "evil.html")
        assert resp.media_type == "application/octet-stream"
        assert "attachment" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_traversal_rejected_with_400(self, wired):
        from fastapi import HTTPException
        mod, run_id = wired
        with pytest.raises(HTTPException) as ei:
            await mod.get_artifact_blob("proj", run_id, "../../etc/passwd")
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_blob_404(self, wired):
        from fastapi import HTTPException
        mod, run_id = wired
        with pytest.raises(HTTPException) as ei:
            await mod.get_artifact_blob("proj", run_id, "absent.png")
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_run_404(self, wired):
        from fastapi import HTTPException
        mod, _ = wired
        with pytest.raises(HTTPException) as ei:
            await mod.get_artifact_blob("proj", "no-such-run", "chart.png")
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_encrypted_blob_decrypted_on_read(self, wired, monkeypatch):
        """When ALE is on, the route must return plaintext bytes."""
        mod, run_id = wired
        import app.utils.task_artifacts as ta
        monkeypatch.setattr(ta, "read_artifact_blob", lambda uri: b"DECRYPTED")
        resp = await mod.get_artifact_blob("proj", run_id, "chart.png")
        assert resp.body == b"DECRYPTED"
