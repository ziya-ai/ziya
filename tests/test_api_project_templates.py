"""
API-level tests for the project-template endpoints.

These cover the things unit tests over ``project_templates`` /
``template_store`` structurally cannot:

  1. **Route ordering.**  ``/templates`` is declared before
     ``/{project_id}`` in app/api/projects.py.  FastAPI matches in
     declaration order, so if that order is ever changed a GET of
     ``/templates`` silently becomes a project lookup for the id
     "templates" and returns 404 — a failure no unit test would see.
  2. **End-to-end seeding.**  That a POST to create a project actually
     lands ``defaultSkillIds`` and ``templateId`` in the persisted
     record, through ProjectStorage.create's template hook.
  3. **The snapshot round trip.**  Save a project's settings as a
     template, then create a second project from it.

Every test drives the real router with a real (temp) ZIYA_HOME, so the
storage layer, model validation, and template resolution are all live.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


CONT_DOCS_ID = "builtin-continuous-documentation"
TESTS_ID = "builtin-tests-for-everything"


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    (home / "projects").mkdir()
    return home


@pytest.fixture
def client(ziya_home):
    """Real projects router against a temp ZIYA_HOME.

    Both the env var and the module-level ``get_ziya_home`` reference are
    patched: template_store resolves the home via get_ziya_home() at call
    time (uncached, by design), and the API module holds its own import.
    """
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.projects.get_ziya_home", return_value=ziya_home):
            app = FastAPI()
            from app.api.projects import router
            app.include_router(router)
            yield TestClient(app)


def _make_project(ziya_home: Path, pid: str, path: str, settings=None) -> str:
    """Write a project record directly, bypassing the create path.

    Used where the test is about reading/snapshotting an EXISTING project
    and the creation-time template hook would only add noise.
    """
    pdir = ziya_home / "projects" / pid
    for sub in ("chats", "contexts", "skills"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": pid, "path": path,
        "settings": settings or {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": now, "lastAccessedAt": now,
    }))
    return pid


# ── 1. Route ordering ────────────────────────────────────────────────────

class TestRouteOrdering:
    """/templates must not be swallowed by /{project_id}."""

    def test_templates_list_is_not_a_project_lookup(self, client):
        r = client.get("/api/v1/projects/templates")
        assert r.status_code == 200, (
            "GET /templates returned %s — it is being matched by the "
            "/{project_id} route. Move the /templates routes above it."
            % r.status_code
        )
        body = r.json()
        assert "templates" in body
        assert "defaultTemplateId" in body

    def test_detect_is_not_a_project_lookup(self, client, tmp_path):
        d = tmp_path / "somewhere"
        d.mkdir()
        r = client.get("/api/v1/projects/templates/detect",
                       params={"path": str(d)})
        assert r.status_code == 200
        assert "templateId" in r.json()

    def test_default_put_is_not_a_project_update(self, client):
        r = client.put("/api/v1/projects/templates/default",
                       json={"templateId": "software_development"})
        assert r.status_code == 200
        assert r.json()["defaultTemplateId"] == "software_development"

    def test_a_real_project_id_still_resolves(self, client, ziya_home):
        # The guard in the other direction: making /templates work must not
        # have shadowed genuine project lookups.
        pid = _make_project(ziya_home, "realproj", "/tmp/realproj")
        r = client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["id"] == pid


# ── 2. Template listing ──────────────────────────────────────────────────

class TestTemplateListing:
    def test_builtins_are_listed(self, client):
        ids = {t["id"] for t in client.get("/api/v1/projects/templates").json()["templates"]}
        assert "software_development" in ids
        assert "general" in ids

    def test_software_development_carries_the_two_skills(self, client):
        tpls = client.get("/api/v1/projects/templates").json()["templates"]
        sd = next(t for t in tpls if t["id"] == "software_development")
        skills = sd["settings"]["defaultSkillIds"]
        assert CONT_DOCS_ID in skills
        assert TESTS_ID in skills

    def test_software_development_does_not_widen_write_policy(self, client):
        # Detection is silent, so a detected template must not grant write
        # permission the user never asked for.
        tpls = client.get("/api/v1/projects/templates").json()["templates"]
        sd = next(t for t in tpls if t["id"] == "software_development")
        assert "writePolicy" not in sd["settings"]

    def test_default_is_unset_initially(self, client):
        assert client.get("/api/v1/projects/templates").json()["defaultTemplateId"] is None


# ── 3. Default-template preference ───────────────────────────────────────

class TestDefaultTemplatePreference:
    def test_set_then_read_back(self, client):
        client.put("/api/v1/projects/templates/default",
                   json={"templateId": "software_development"})
        r = client.get("/api/v1/projects/templates")
        assert r.json()["defaultTemplateId"] == "software_development"

    def test_null_clears_the_preference(self, client):
        client.put("/api/v1/projects/templates/default",
                   json={"templateId": "software_development"})
        client.put("/api/v1/projects/templates/default", json={"templateId": None})
        assert client.get("/api/v1/projects/templates").json()["defaultTemplateId"] is None

    def test_unknown_template_is_rejected(self, client):
        r = client.put("/api/v1/projects/templates/default",
                       json={"templateId": "no_such_template"})
        assert r.status_code == 404
        # And the preference must be left alone, not half-written.
        assert client.get("/api/v1/projects/templates").json()["defaultTemplateId"] is None

    def test_preference_persists_to_disk(self, client, ziya_home):
        client.put("/api/v1/projects/templates/default",
                   json={"templateId": "general"})
        raw = json.loads((ziya_home / "templates.json").read_text())
        assert raw["defaultTemplateId"] == "general"


# ── 4. Detection ─────────────────────────────────────────────────────────

class TestDetectEndpoint:
    def test_pyproject_detects_software_development(self, client, tmp_path):
        d = tmp_path / "svc"
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\nname='x'\n")
        body = client.get("/api/v1/projects/templates/detect",
                          params={"path": str(d)}).json()
        assert body["templateId"] == "software_development"
        assert body["detected"] is True
        assert body["marker"] == "pyproject.toml"

    def test_marker_is_reported_so_ui_can_explain_itself(self, client, tmp_path):
        d = tmp_path / "js"
        d.mkdir()
        (d / "package.json").write_text("{}")
        body = client.get("/api/v1/projects/templates/detect",
                          params={"path": str(d)}).json()
        assert body["marker"] == "package.json"

    def test_plain_directory_is_general(self, client, tmp_path):
        d = tmp_path / "notes"
        d.mkdir()
        (d / "todo.md").write_text("# notes")
        body = client.get("/api/v1/projects/templates/detect",
                          params={"path": str(d)}).json()
        assert body["templateId"] == "general"
        assert body["detected"] is False

    def test_git_alone_is_not_software(self, client, tmp_path):
        # A notes repo is still a notes repo.
        d = tmp_path / "notesrepo"
        (d / ".git").mkdir(parents=True)
        body = client.get("/api/v1/projects/templates/detect",
                          params={"path": str(d)}).json()
        assert body["templateId"] == "general"

    def test_nonexistent_path_degrades_rather_than_erroring(self, client, tmp_path):
        body = client.get("/api/v1/projects/templates/detect",
                          params={"path": str(tmp_path / "nope")}).json()
        assert body["templateId"] == "general"
        assert body["detected"] is False


# ── 5. Creation seeds settings (the whole point) ─────────────────────────

class TestCreationSeedsSettings:
    def test_detected_software_project_gets_the_two_skills(self, client, tmp_path):
        d = tmp_path / "myservice"
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\nname='x'\n")
        r = client.post("/api/v1/projects", json={"path": str(d)})
        assert r.status_code == 200
        s = r.json()["settings"]
        assert CONT_DOCS_ID in s["defaultSkillIds"]
        assert TESTS_ID in s["defaultSkillIds"]

    def test_templateid_is_recorded_as_provenance(self, client, tmp_path):
        d = tmp_path / "myservice2"
        d.mkdir()
        (d / "Cargo.toml").write_text("[package]\n")
        s = client.post("/api/v1/projects", json={"path": str(d)}).json()["settings"]
        assert s["templateId"] == "software_development"

    def test_plain_directory_gets_no_skills(self, client, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        s = client.post("/api/v1/projects", json={"path": str(d)}).json()["settings"]
        assert s["defaultSkillIds"] == []

    def test_explicit_choice_overrides_detection(self, client, tmp_path):
        d = tmp_path / "override"
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n")
        s = client.post("/api/v1/projects",
                        json={"path": str(d), "templateId": "general"}).json()["settings"]
        assert s["templateId"] == "general"
        assert s["defaultSkillIds"] == []

    def test_global_default_applies_when_nothing_detected(self, client, tmp_path):
        client.put("/api/v1/projects/templates/default",
                   json={"templateId": "software_development"})
        d = tmp_path / "plain2"
        d.mkdir()
        s = client.post("/api/v1/projects", json={"path": str(d)}).json()["settings"]
        assert CONT_DOCS_ID in s["defaultSkillIds"], (
            "the user's default-template preference should apply when the "
            "directory has no build markers"
        )

    def test_detection_beats_the_global_default(self, client, tmp_path):
        # Detection is the more specific signal.
        client.put("/api/v1/projects/templates/default", json={"templateId": "general"})
        d = tmp_path / "detected"
        d.mkdir()
        (d / "go.mod").write_text("module x\n")
        s = client.post("/api/v1/projects", json={"path": str(d)}).json()["settings"]
        assert s["templateId"] == "software_development"

    def test_seeded_settings_are_persisted_not_just_returned(self, client, ziya_home, tmp_path):
        d = tmp_path / "persisted"
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n")
        pid = client.post("/api/v1/projects", json={"path": str(d)}).json()["id"]
        raw = json.loads((ziya_home / "projects" / pid / "project.json").read_text())
        assert CONT_DOCS_ID in raw["settings"]["defaultSkillIds"]

    def test_apply_once_survives_a_later_template_default_change(self, client, tmp_path):
        """Apply-once: changing the default must not retroactively alter an
        existing project.  This is the contract that keeps settings reads
        literal."""
        d = tmp_path / "stamped"
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n")
        pid = client.post("/api/v1/projects", json={"path": str(d)}).json()["id"]
        client.put("/api/v1/projects/templates/default", json={"templateId": "general"})
        s = client.get(f"/api/v1/projects/{pid}").json()["settings"]
        assert CONT_DOCS_ID in s["defaultSkillIds"]

    def test_reopening_an_existing_path_does_not_reseed(self, client, tmp_path):
        # ProjectStorage.create returns the existing project for a known
        # path; that early return must not run the template hook again.
        d = tmp_path / "reopen"
        d.mkdir()
        first = client.post("/api/v1/projects", json={"path": str(d)}).json()
        second = client.post("/api/v1/projects",
                             json={"path": str(d), "templateId": "software_development"}).json()
        assert second["id"] == first["id"]
        assert second["settings"]["defaultSkillIds"] == first["settings"]["defaultSkillIds"]


# ── 6. Explicit template application to existing projects ───────────────

class TestApplyTemplateToExistingProject:
    def test_applies_software_defaults_and_records_provenance(self, client, ziya_home):
        pid = _make_project(ziya_home, "legacy", "/tmp/legacy")
        r = client.post(
            f"/api/v1/projects/{pid}/apply-template",
            json={"templateId": "software_development"},
        )
        assert r.status_code == 200
        settings = r.json()["settings"]
        assert settings["templateId"] == "software_development"
        assert CONT_DOCS_ID in settings["defaultSkillIds"]
        assert TESTS_ID in settings["defaultSkillIds"]

    def test_unions_template_skills_with_existing_project_defaults(self, client, ziya_home):
        pid = _make_project(ziya_home, "configured", "/tmp/configured", settings={
            "defaultContextIds": [], "defaultSkillIds": ["builtin-concise"],
        })
        settings = client.post(
            f"/api/v1/projects/{pid}/apply-template",
            json={"templateId": "software_development"},
        ).json()["settings"]
        assert settings["defaultSkillIds"][0] == "builtin-concise"
        assert CONT_DOCS_ID in settings["defaultSkillIds"]
        assert TESTS_ID in settings["defaultSkillIds"]

    def test_unknown_template_is_404(self, client, ziya_home):
        pid = _make_project(ziya_home, "legacy2", "/tmp/legacy2")
        r = client.post(
            f"/api/v1/projects/{pid}/apply-template",
            json={"templateId": "does-not-exist"},
        )
        assert r.status_code == 404


# ── 7. Snapshot round trip ───────────────────────────────────────────────

class TestSnapshotRoundTrip:
    def test_snapshot_captures_templatable_settings(self, client, ziya_home):
        pid = _make_project(ziya_home, "srcproj", "/tmp/srcproj", settings={
            "defaultContextIds": ["ctx-1"],
            "defaultSkillIds": ["builtin-concise"],
            "writePolicy": {"safe_write_paths": ["scratch/"],
                            "allowed_write_patterns": [], "allowed_interpreters": [],
                            "always_blocked": []},
        })
        r = client.post(f"/api/v1/projects/{pid}/save-as-template", json={
            "id": "my_style", "name": "My Style", "description": "how I work",
            "detectMarkers": [],
        })
        assert r.status_code == 200
        settings = r.json()["settings"]
        assert settings["defaultSkillIds"] == ["builtin-concise"]
        # A snapshot IS allowed to carry writePolicy: the user explicitly
        # chose to save these settings, so there is no silent widening.
        assert "writePolicy" in settings

    def test_snapshot_drops_context_ids(self, client, ziya_home):
        # Context ids are per-project record ids; carrying them would seed
        # dangling references into every project the template is applied to.
        pid = _make_project(ziya_home, "ctxproj", "/tmp/ctxproj", settings={
            "defaultContextIds": ["ctx-1", "ctx-2"], "defaultSkillIds": [],
        })
        r = client.post(f"/api/v1/projects/{pid}/save-as-template", json={
            "id": "no_ctx", "name": "No Ctx",
        })
        assert "defaultContextIds" not in r.json()["settings"]

    def test_snapshot_then_create_from_it(self, client, ziya_home, tmp_path):
        pid = _make_project(ziya_home, "seed", "/tmp/seed", settings={
            "defaultContextIds": [], "defaultSkillIds": ["builtin-debug-mode"],
        })
        client.post(f"/api/v1/projects/{pid}/save-as-template",
                    json={"id": "debuggy", "name": "Debuggy"})
        d = tmp_path / "fromtpl"
        d.mkdir()
        s = client.post("/api/v1/projects",
                        json={"path": str(d), "templateId": "debuggy"}).json()["settings"]
        assert s["defaultSkillIds"] == ["builtin-debug-mode"]
        assert s["templateId"] == "debuggy"

    def test_snapshot_with_markers_participates_in_detection(self, client, ziya_home, tmp_path):
        pid = _make_project(ziya_home, "mk", "/tmp/mk", settings={
            "defaultContextIds": [], "defaultSkillIds": ["builtin-concise"],
        })
        client.post(f"/api/v1/projects/{pid}/save-as-template", json={
            "id": "terraform", "name": "Terraform", "detectMarkers": ["main.tf"],
        })
        d = tmp_path / "infra"
        d.mkdir()
        (d / "main.tf").write_text("resource {}")
        body = client.get("/api/v1/projects/templates/detect",
                          params={"path": str(d)}).json()
        assert body["templateId"] == "terraform"

    def test_snapshot_of_missing_project_is_404(self, client):
        r = client.post("/api/v1/projects/nope/save-as-template",
                        json={"id": "x", "name": "X"})
        assert r.status_code == 404

    def test_snapshot_cannot_shadow_a_builtin_id(self, client, ziya_home):
        pid = _make_project(ziya_home, "shadow", "/tmp/shadow")
        r = client.post(f"/api/v1/projects/{pid}/save-as-template", json={
            "id": "software_development", "name": "Mine",
        })
        assert r.status_code == 400


# ── 7. User-template deletion ────────────────────────────────────────────

class TestTemplateDeletion:
    def test_delete_user_template(self, client, ziya_home):
        pid = _make_project(ziya_home, "dsrc", "/tmp/dsrc")
        client.post(f"/api/v1/projects/{pid}/save-as-template",
                    json={"id": "temp_tpl", "name": "Temp"})
        assert client.delete("/api/v1/projects/templates/temp_tpl").status_code == 200
        ids = {t["id"] for t in client.get("/api/v1/projects/templates").json()["templates"]}
        assert "temp_tpl" not in ids

    def test_builtin_cannot_be_deleted(self, client):
        r = client.delete("/api/v1/projects/templates/software_development")
        assert r.status_code == 403

    def test_delete_unknown_is_404(self, client):
        assert client.delete("/api/v1/projects/templates/nope").status_code == 404

    def test_deleting_a_template_leaves_projects_alone(self, client, ziya_home, tmp_path):
        """Apply-once makes deletion safe: the project owns its settings and
        templateId is only provenance."""
        pid = _make_project(ziya_home, "dsrc2", "/tmp/dsrc2", settings={
            "defaultContextIds": [], "defaultSkillIds": ["builtin-concise"],
        })
        client.post(f"/api/v1/projects/{pid}/save-as-template",
                    json={"id": "doomed", "name": "Doomed"})
        d = tmp_path / "child"
        d.mkdir()
        child = client.post("/api/v1/projects",
                            json={"path": str(d), "templateId": "doomed"}).json()["id"]
        client.delete("/api/v1/projects/templates/doomed")
        s = client.get(f"/api/v1/projects/{child}").json()["settings"]
        assert s["defaultSkillIds"] == ["builtin-concise"]
        assert s["templateId"] == "doomed"  # provenance survives, dangling


# ── 8. Robustness ────────────────────────────────────────────────────────

class TestRobustness:
    def test_malformed_templates_file_does_not_break_listing(self, client, ziya_home):
        (ziya_home / "templates.json").write_text("{ this is not json")
        r = client.get("/api/v1/projects/templates")
        assert r.status_code == 200
        ids = {t["id"] for t in r.json()["templates"]}
        assert "software_development" in ids, "builtins must survive a bad user file"

    def test_malformed_templates_file_does_not_break_creation(self, client, ziya_home, tmp_path):
        # The reason ProjectStorage.create wraps the template hook: a
        # hand-edited config file must never block project creation.
        (ziya_home / "templates.json").write_text("]]not json[[")
        d = tmp_path / "stillworks"
        d.mkdir()
        r = client.post("/api/v1/projects", json={"path": str(d)})
        assert r.status_code == 200
        assert r.json()["settings"]["defaultSkillIds"] == []

    def test_unknown_templateid_on_create_falls_back_to_general(self, client, tmp_path):
        d = tmp_path / "badtpl"
        d.mkdir()
        r = client.post("/api/v1/projects",
                        json={"path": str(d), "templateId": "does_not_exist"})
        assert r.status_code == 200
        assert r.json()["settings"]["defaultSkillIds"] == []
