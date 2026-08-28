"""
ASR PT-02 — the unauthenticated ``/file`` read must be contained.

``/save`` was given realpath + write-policy containment because a
prompt-injected agent inside the loopback boundary can reach it. ``/file`` has
identical reachability and had none: only the error-message oracle was closed
(PenPal #98). The injection path does not care which verb it is, so the agent
could read outside its tool sandbox -- ``~/.aws/credentials`` being the obvious
target -- and pair that with egress.

Containment reuses a boundary the user already controls in the file explorer:
the project root, plus paths explicitly registered via
``/api/add-explicit-paths``. Nothing new to configure.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import folder_service


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('in project')\n")
    return root


@pytest.fixture
def outside_secret(tmp_path):
    secret = tmp_path / "home" / ".aws" / "credentials"
    secret.parent.mkdir(parents=True)
    secret.write_text("[default]\naws_secret_access_key = SHOULD-NOT-LEAK\n")
    return secret


@pytest.fixture
def client(project_root, monkeypatch):
    monkeypatch.setattr(
        "app.routes.folder_routes.get_project_root", lambda: str(project_root)
    )
    # The external-path allowlist is process-global; start every test from a
    # known-empty set so one test's registration cannot grant another's read.
    monkeypatch.setattr(folder_service, "_explicit_external_paths", set())

    from app.routes.folder_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _read(client, path):
    return client.post("/file", json={"file_path": str(path)})


class TestReadsInsideProjectStillWork:
    def test_in_project_file_readable(self, client, project_root):
        """Positive control. Without this, a handler that 403'd unconditionally
        would satisfy every rejection case below and break the file explorer."""
        resp = _read(client, project_root / "src" / "main.py")
        assert resp.status_code == 200
        assert resp.json()["content"] == "print('in project')\n"

    def test_relative_path_inside_project_readable(self, client, project_root):
        with_dots = project_root / "src" / ".." / "src" / "main.py"
        resp = _read(client, with_dots)
        assert resp.status_code == 200
        assert "in project" in resp.json()["content"]


class TestReadsOutsideProjectRefused:
    def test_absolute_path_outside_project_refused(self, client, outside_secret):
        resp = _read(client, outside_secret)
        assert resp.status_code == 403

    def test_secret_content_not_returned(self, client, outside_secret):
        """The impact assertion: refusal is only meaningful if the bytes stay
        out of the response body."""
        resp = _read(client, outside_secret)
        assert "SHOULD-NOT-LEAK" not in resp.text

    def test_traversal_out_of_project_refused(self, client, project_root, outside_secret):
        escape = project_root / ".." / "home" / ".aws" / "credentials"
        resp = _read(client, escape)
        assert resp.status_code == 403
        assert "SHOULD-NOT-LEAK" not in resp.text

    def test_symlink_out_of_project_refused(self, client, project_root, outside_secret):
        """realpath runs before the containment check, so a symlink planted
        inside the project does not launder an outside target."""
        link = project_root / "src" / "creds.txt"
        link.symlink_to(outside_secret)
        resp = _read(client, link)
        assert resp.status_code == 403
        assert "SHOULD-NOT-LEAK" not in resp.text

    def test_refusal_message_is_generic(self, client, outside_secret):
        """Must not become an existence oracle for paths outside the boundary
        (the neighbouring PenPal #98 control)."""
        present = _read(client, outside_secret)
        absent = _read(client, outside_secret.parent / "does-not-exist")
        assert present.status_code == absent.status_code == 403
        assert present.json() == absent.json()

    def test_no_project_root_means_no_reads(self, project_root, monkeypatch, outside_secret):
        """A request that arrives before a project root is resolved must fail
        closed, not fall back to reading anything."""
        monkeypatch.setattr(
            "app.routes.folder_routes.get_project_root", lambda: ""
        )
        monkeypatch.setattr(folder_service, "_explicit_external_paths", set())
        from app.routes.folder_routes import router

        app = FastAPI()
        app.include_router(router)
        local = TestClient(app)
        assert _read(local, outside_secret).status_code == 403


class TestRegisteredExternalPathsStillWork:
    """The boundary is project root PLUS user-registered external paths, so a
    deliberately added path must remain readable -- otherwise the fix silently
    breaks the explorer's add-external-folder feature."""

    def test_registered_external_file_readable(self, client, tmp_path, monkeypatch):
        external = tmp_path / "shared" / "notes.md"
        external.parent.mkdir(parents=True)
        external.write_text("# shared notes\n")

        monkeypatch.setattr(
            folder_service,
            "_explicit_external_paths",
            {os.path.realpath(str(external.parent))},
        )
        resp = _read(client, external)
        assert resp.status_code == 200
        assert "shared notes" in resp.json()["content"]

    def test_sibling_of_registered_path_still_refused(
        self, client, tmp_path, monkeypatch
    ):
        """Registering ``/a/shared`` must not grant ``/a/shared-other``."""
        allowed = tmp_path / "shared"
        allowed.mkdir()
        sibling = tmp_path / "shared-other"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("nope\n")

        monkeypatch.setattr(
            folder_service,
            "_explicit_external_paths",
            {os.path.realpath(str(allowed))},
        )
        resp = _read(client, sibling / "secret.txt")
        assert resp.status_code == 403


class TestBothVerbsAreContained:
    """The finding was the asymmetry between the verbs, so assert the pair.

    A future refactor that contains one and not the other reproduces PT-02
    exactly; testing read alone would not catch it.
    """

    def test_write_outside_project_refused(self, client, tmp_path):
        target = tmp_path / "home" / ".ssh" / "authorized_keys"
        target.parent.mkdir(parents=True)
        resp = client.post(
            "/save", json={"file_path": str(target), "content": "ssh-rsa EVIL"}
        )
        assert resp.status_code == 403
        assert not target.exists()

    def test_read_and_write_refuse_the_same_path(self, client, outside_secret):
        read = _read(client, outside_secret)
        write = client.post(
            "/save", json={"file_path": str(outside_secret), "content": "x"}
        )
        assert read.status_code == 403
        assert write.status_code == 403
