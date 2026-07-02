"""
Regression tests for three PenPal findings landed together as a
path-traversal / arbitrary-write cluster:

  #163 (CWE-22, HIGH): POST /save wrote request.file_path directly to
       open() with no path validation at all — an unauthenticated
       arbitrary-file-write primitive (folder_routes.save_file).

  #156/#166/#14 (CWE-22/94, HIGH): /api/apply-changes validated the
       resolved path ONLY on the validated.filePath fallback branch;
       the diff-extracted path (attacker-controlled via a crafted
       "+++ b/../../.." header) reached the diff pipeline unchecked.
       /api/files/validate had no containment check at all, making it
       a file-existence oracle for arbitrary filesystem paths.

  create_new_file() (the actual write sink reached by three separate
  upstream path extractions) had no containment check of its own.

Each test targets the SPECIFIC bypass shape from the report — not just
an outcome that would also be blocked by unrelated logic.
"""

import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch


# ── #163: POST /save gated through WritePolicyManager ──────────────

@pytest.fixture
def save_client():
    from app.routes.folder_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def project_root(tmp_path):
    """A temp project root, with the real WritePolicyManager singleton
    pointed at it (not mocked) so these tests exercise the actual
    policy logic end-to-end."""
    root = tmp_path / "proj"
    root.mkdir()
    return str(root)


class TestSaveEndpointGated:
    def test_traversal_to_ssh_authorized_keys_blocked(self, save_client, project_root, tmp_path):
        """The report's exact PoC: writing outside the project root via
        '../../../home/user/.ssh/authorized_keys'-style traversal."""
        target = tmp_path / "outside_project.txt"
        assert not target.exists()
        with patch("app.routes.folder_routes.get_project_root", return_value=project_root):
            resp = save_client.post("/save", json={
                "file_path": str(target),
                "content": "ssh-ed25519 AAAA...attacker-key...",
            })
        assert resp.status_code == 403
        assert not target.exists(), "write must be blocked, not merely reported as blocked"

    def test_traversal_via_dotdot_segments_blocked(self, save_client, project_root):
        """A relative path with '..' segments that resolves outside the
        project root via os.path.realpath must also be blocked."""
        outside_marker = os.path.join(os.path.dirname(project_root), "escaped.txt")
        traversal_path = os.path.join(project_root, "..", "escaped.txt")
        with patch("app.routes.folder_routes.get_project_root", return_value=project_root):
            resp = save_client.post("/save", json={
                "file_path": traversal_path,
                "content": "pwned",
            })
        assert resp.status_code == 403
        assert not os.path.exists(outside_marker)

    def test_write_inside_ziya_dir_allowed(self, save_client, project_root):
        """The endpoint must remain usable for its legitimate purpose —
        writes inside the always-safe .ziya/ directory succeed."""
        ziya_dir = os.path.join(project_root, ".ziya")
        os.makedirs(ziya_dir, exist_ok=True)
        target = os.path.join(ziya_dir, "notes.md")
        with patch("app.routes.folder_routes.get_project_root", return_value=project_root):
            resp = save_client.post("/save", json={
                "file_path": target,
                "content": "hello",
            })
        assert resp.status_code == 200
        assert resp.json().get("success") is True
        assert os.path.exists(target)
        with open(target) as f:
            assert f.read() == "hello"


# ── #156/#166/#14: diff_routes traversal cluster ────────────────────

@pytest.fixture
def diff_client():
    from app.routes.diff_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestApplyChangesExtractedPathValidated:
    """The extracted_path branch (diff-header-derived) must be validated
    identically to the filePath fallback branch — previously it was not
    validated at all."""

    def test_traversal_via_diff_header_rejected(self, diff_client, tmp_path):
        """The report's exact PoC: a '+++ b/../../../etc/cron.d/x'-style
        header must not resolve outside user_codebase_dir."""
        project_root = tmp_path / "proj"
        project_root.mkdir()
        malicious_diff = (
            "diff --git a/evil b/evil\n"
            "new file mode 100644\n"
            "index 0000000..1234567\n"
            "--- /dev/null\n"
            "+++ b/../../../../tmp/penpal_traversal_poc.txt\n"
            "@@ -0,0 +1,1 @@\n"
            "+pwned\n"
        )
        resp = diff_client.post("/api/apply-changes", json={
            "diff": malicious_diff,
            "filePath": "evil",
            "projectRoot": str(project_root),
        })
        # Must fail closed — either a validation ValueError (400/422/500
        # depending on how the route surfaces it) or the pipeline's own
        # error, but never a 200 with changes actually written outside root.
        assert resp.status_code != 200 or resp.json().get("status") != "success"
        assert not (tmp_path / "penpal_traversal_poc.txt").exists()

    def test_legitimate_extracted_path_still_works(self, diff_client, tmp_path):
        """A normal, in-root diff via the extracted_path branch must
        still apply successfully — the fix must not be overly strict."""
        project_root = tmp_path / "proj"
        project_root.mkdir()
        target = project_root / "new_file.txt"
        new_file_diff = (
            "diff --git a/new_file.txt b/new_file.txt\n"
            "new file mode 100644\n"
            "index 0000000..1234567\n"
            "--- /dev/null\n"
            "+++ b/new_file.txt\n"
            "@@ -0,0 +1,1 @@\n"
            "+hello world\n"
        )
        resp = diff_client.post("/api/apply-changes", json={
            "diff": new_file_diff,
            "filePath": "new_file.txt",
            "projectRoot": str(project_root),
        })
        assert resp.status_code == 200
        assert target.exists()


class TestValidateFilesContainment:
    """/api/files/validate must not act as a file-existence oracle for
    paths outside the codebase directory."""

    def test_traversal_path_never_reported_as_existing(self, diff_client, tmp_path):
        project_root = tmp_path / "proj"
        project_root.mkdir()
        outside_secret = tmp_path / "secret.txt"
        outside_secret.write_text("sensitive")

        resp = diff_client.post("/api/files/validate", json={
            "files": ["../secret.txt"],
            "projectRoot": str(project_root),
        })
        assert resp.status_code == 200
        assert resp.json()["existingFiles"] == []

    def test_in_root_file_still_reported_as_existing(self, diff_client, tmp_path):
        project_root = tmp_path / "proj"
        project_root.mkdir()
        (project_root / "real.txt").write_text("content")

        resp = diff_client.post("/api/files/validate", json={
            "files": ["real.txt"],
            "projectRoot": str(project_root),
        })
        assert resp.status_code == 200
        assert resp.json()["existingFiles"] == ["real.txt"]


class TestCreateNewFileWriteSiteGuard:
    """create_new_file() is the actual write sink reached by three
    separate upstream path-extraction sites; it must independently
    reject a traversal target rather than depend on callers to have
    pre-validated it."""

    def test_traversal_target_raises_via_diff_git_header(self, tmp_path):
        """Traversal in the 'diff --git a/X b/X' header — the first,
        highest-priority extraction branch in create_new_file."""
        from app.utils.diff_utils.file_ops.file_handlers import create_new_file

        base_dir = tmp_path / "proj"
        base_dir.mkdir()
        malicious_diff = (
            "diff --git a/evil b/../../outside_via_githeader.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/../../outside_via_githeader.txt\n"
            "@@ -0,0 +1,1 @@\n"
            "+pwned\n"
        )
        with pytest.raises(ValueError, match="Path traversal"):
            create_new_file(malicious_diff, str(base_dir))
        assert not (tmp_path / "outside_via_githeader.txt").exists()

    def test_traversal_target_raises_via_plus_plus_plus_header(self, tmp_path):
        """Traversal reaching create_new_file via the '+++ b/' fallback
        branch, exercised when no 'diff --git' header line is present —
        this is the exact shape the report's PoC used."""
        from app.utils.diff_utils.file_ops.file_handlers import create_new_file

        base_dir = tmp_path / "proj"
        base_dir.mkdir()
        malicious_diff = (
            "--- /dev/null\n"
            "+++ b/../../outside_via_plusplus.txt\n"
            "@@ -0,0 +1,1 @@\n"
            "+pwned\n"
        )
        with pytest.raises(ValueError, match="Path traversal"):
            create_new_file(malicious_diff, str(base_dir))
        assert not (tmp_path / "outside_via_plusplus.txt").exists()

    def test_legitimate_new_file_still_created(self, tmp_path):
        from app.utils.diff_utils.file_ops.file_handlers import create_new_file

        base_dir = tmp_path / "proj"
        base_dir.mkdir()
        diff = (
            "diff --git a/sub/new.txt b/sub/new.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/sub/new.txt\n"
            "@@ -0,0 +1,1 @@\n"
            "+hello\n"
        )
        create_new_file(diff, str(base_dir))
        created = base_dir / "sub" / "new.txt"
        assert created.exists()
