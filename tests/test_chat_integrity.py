"""
Tests for cross-project chat shadow-copy detection/recovery and the
bulk-sync prevention guard.

Covers the defect where global chats surfaced into other projects got
cloned into the viewed project by bulk-sync and re-stamped with that
project's id, producing cross-project duplicates with divergent
groupId/isGlobal.

Two layers under test:
  1. app.utils.chat_integrity — scan + reconcile existing damage.
  2. app.api.chats.bulk_sync_chats — the guard that stops NEW shadows.
"""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.utils.chat_integrity import (
    scan_chat_integrity,
    reconcile_chat_integrity,
    report_dict,
    run_startup_check,
    _choose_canonical,
    ChatCopy,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    (home / "projects").mkdir(parents=True)
    return home


def _make_project(ziya_home: Path, project_id: str, name: str = None) -> Path:
    pdir = ziya_home / "projects" / project_id
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": project_id,
        "name": name or project_id,
        "path": f"/tmp/{project_id}",
        "createdAt": 1, "lastAccessedAt": 1,
    }))
    return pdir


def _write_chat(ziya_home: Path, project_id: str, chat_id: str,
                *, stated_project_id=None, group_id=None, is_global=None,
                last_active=1000, messages=None) -> Path:
    cf = ziya_home / "projects" / project_id / "chats" / f"{chat_id}.json"
    data = {
        "id": chat_id,
        "title": f"chat {chat_id}",
        "messages": messages or [],
        "createdAt": 1,
        "lastActiveAt": last_active,
    }
    if stated_project_id is not None:
        data["projectId"] = stated_project_id
    if group_id is not None:
        data["groupId"] = group_id
    if is_global is not None:
        data["isGlobal"] = is_global
    cf.write_text(json.dumps(data))
    return cf


# ── scan_chat_integrity ─────────────────────────────────────────────

class TestScan:

    def test_no_duplicates_clean(self, ziya_home):
        _make_project(ziya_home, "p1")
        _make_project(ziya_home, "p2")
        _write_chat(ziya_home, "p1", "a", stated_project_id="p1")
        _write_chat(ziya_home, "p2", "b", stated_project_id="p2")
        assert scan_chat_integrity(ziya_home) == []

    def test_detects_cross_project_duplicate(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        _write_chat(ziya_home, "owner", "dup", stated_project_id="owner",
                    group_id="g1", is_global=True)
        _write_chat(ziya_home, "viewer", "dup", stated_project_id="viewer",
                    group_id=None)
        dups = scan_chat_integrity(ziya_home)
        assert len(dups) == 1
        d = dups[0]
        assert d.chat_id == "dup"
        assert len(d.copies) == 2
        assert len(d.shadows) == 1

    def test_canonical_prefers_owner_matched_copy(self, ziya_home):
        # The copy whose own projectId matches its dir is canonical even if
        # another copy is more recently active.
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        _write_chat(ziya_home, "owner", "dup", stated_project_id="owner",
                    group_id="g1", last_active=100)
        # viewer copy is NOT owner-matched (its projectId points at owner) and
        # is newer — the owner-matched rule must still win.
        _write_chat(ziya_home, "viewer", "dup", stated_project_id="owner",
                    group_id=None, last_active=9999)
        dups = scan_chat_integrity(ziya_home)
        assert len(dups) == 1
        assert dups[0].canonical.dir_project_id == "owner"

    def test_salvage_group_from_shadow(self, ziya_home):
        # Canonical lost its group; a shadow retained it -> flagged salvageable.
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        _write_chat(ziya_home, "owner", "dup", stated_project_id="owner",
                    group_id=None, last_active=100)
        _write_chat(ziya_home, "viewer", "dup", stated_project_id="owner",
                    group_id="rescue-group", last_active=50)
        dups = scan_chat_integrity(ziya_home)
        assert dups[0].salvageable_group_id == "rescue-group"


class TestChooseCanonical:

    def _copy(self, dir_pid, stated, last_active=0, msgs=0):
        return ChatCopy(
            chat_id="x", dir_project_id=dir_pid, path=Path("/x"),
            stated_project_id=stated, group_id=None, is_global=None,
            last_active=last_active, message_count=msgs,
        )

    def test_owner_match_beats_recency(self):
        owner = self._copy("A", "A", last_active=1)
        newer = self._copy("B", "A", last_active=9999)  # not owner-matched
        assert _choose_canonical([newer, owner]) is owner

    def test_recency_breaks_tie_when_no_owner_match(self):
        c1 = self._copy("A", "Z", last_active=10)
        c2 = self._copy("B", "Z", last_active=20)
        assert _choose_canonical([c1, c2]) is c2


# ── reconcile_chat_integrity ────────────────────────────────────────

class TestReconcile:

    def test_dry_run_removes_nothing(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        _write_chat(ziya_home, "owner", "dup", stated_project_id="owner")
        shadow = _write_chat(ziya_home, "viewer", "dup", stated_project_id="owner")
        res = reconcile_chat_integrity(ziya_home, dry_run=True)
        assert res["duplicate_sets"] == 1
        assert res["shadows_removed"] == 1  # counts what WOULD be removed
        assert res["dry_run"] is True
        assert shadow.exists()  # nothing actually deleted

    def test_reconcile_removes_shadow_keeps_canonical(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        canonical = _write_chat(ziya_home, "owner", "dup",
                                stated_project_id="owner", last_active=100)
        shadow = _write_chat(ziya_home, "viewer", "dup",
                             stated_project_id="owner", last_active=50)
        res = reconcile_chat_integrity(ziya_home, dry_run=False)
        assert res["shadows_removed"] == 1
        assert canonical.exists()
        assert not shadow.exists()

    def test_reconcile_salvages_group_onto_canonical(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        canonical = _write_chat(ziya_home, "owner", "dup",
                                stated_project_id="owner", group_id=None,
                                last_active=100)
        _write_chat(ziya_home, "viewer", "dup", stated_project_id="owner",
                    group_id="rescue", last_active=50)
        res = reconcile_chat_integrity(ziya_home, dry_run=False)
        assert res["metadata_salvaged"] == 1
        data = json.loads(canonical.read_text())
        assert data["groupId"] == "rescue"

    def test_reconcile_is_idempotent(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        _write_chat(ziya_home, "owner", "dup", stated_project_id="owner")
        _write_chat(ziya_home, "viewer", "dup", stated_project_id="owner")
        reconcile_chat_integrity(ziya_home, dry_run=False)
        second = reconcile_chat_integrity(ziya_home, dry_run=False)
        assert second["duplicate_sets"] == 0
        assert second["shadows_removed"] == 0


# ── bulk-sync prevention guard ──────────────────────────────────────

class TestBulkSyncGuard:

    @pytest.fixture
    def two_projects(self, ziya_home):
        _make_project(ziya_home, "owner-proj")
        _make_project(ziya_home, "viewer-proj")
        return ziya_home

    @contextmanager
    def _client(self, ziya_home):
        """Yield a TestClient with paths patched for the FULL test body.

        Must be used as ``with self._client(h) as tc:`` so the patches stay
        active while requests are made — returning the client from inside the
        ``with`` would tear the patches down first (which spuriously 404s).
        """
        with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
            with patch("app.api.chats.get_ziya_home", return_value=ziya_home):
                with patch("app.api.chats.get_project_dir",
                           side_effect=lambda pid: ziya_home / "projects" / pid):
                    from fastapi import FastAPI
                    from app.api.chats import router
                    app = FastAPI()
                    app.include_router(router)
                    yield TestClient(app)

    def test_foreign_chat_not_cloned(self, two_projects):
        ziya_home = two_projects
        now = int(time.time() * 1000)
        # A chat owned by owner-proj, pushed while viewing viewer-proj.
        foreign = {
            "id": "foreign1", "title": "owned elsewhere", "messages": [],
            "createdAt": now, "lastActiveAt": now,
            "projectId": "owner-proj",
        }
        with self._client(ziya_home) as tc:
            resp = tc.post("/api/v1/projects/viewer-proj/chats/bulk-sync",
                           json={"chats": [foreign]})
        assert resp.status_code == 200
        assert resp.json()["skipped"] == 1
        assert resp.json()["created"] == 0
        # No shadow file created in viewer-proj
        shadow = ziya_home / "projects" / "viewer-proj" / "chats" / "foreign1.json"
        assert not shadow.exists()

    def test_own_chat_still_written(self, two_projects):
        ziya_home = two_projects
        now = int(time.time() * 1000)
        mine = {
            "id": "mine1", "title": "belongs here", "messages": [],
            "createdAt": now, "lastActiveAt": now,
            "projectId": "viewer-proj",
        }
        with self._client(ziya_home) as tc:
            resp = tc.post("/api/v1/projects/viewer-proj/chats/bulk-sync",
                           json={"chats": [mine]})
        assert resp.json()["created"] == 1
        assert (ziya_home / "projects" / "viewer-proj" / "chats" / "mine1.json").exists()

    def test_unowned_chat_still_written(self, two_projects):
        # No projectId field (legacy/new chat) -> writes normally.
        ziya_home = two_projects
        now = int(time.time() * 1000)
        legacy = {
            "id": "legacy1", "title": "no owner", "messages": [],
            "createdAt": now, "lastActiveAt": now,
        }
        with self._client(ziya_home) as tc:
            resp = tc.post("/api/v1/projects/viewer-proj/chats/bulk-sync",
                           json={"chats": [legacy]})
        assert resp.json()["created"] == 1

    def test_stale_owner_not_stranded(self, two_projects):
        # projectId points at a project that no longer exists -> not foreign,
        # write normally so the chat isn't stranded.
        ziya_home = two_projects
        now = int(time.time() * 1000)
        orphan = {
            "id": "orphan1", "title": "dead owner", "messages": [],
            "createdAt": now, "lastActiveAt": now,
            "projectId": "deleted-project-xyz",
        }
        with self._client(ziya_home) as tc:
            resp = tc.post("/api/v1/projects/viewer-proj/chats/bulk-sync",
                           json={"chats": [orphan]})
        assert resp.json()["created"] == 1


# ── report_dict (JSON report for the endpoint) ──────────────────────

class TestReportDict:

    def test_empty_report(self, ziya_home):
        rep = report_dict(scan_chat_integrity(ziya_home))
        assert rep == {
            "duplicate_sets": 0,
            "shadow_copies": 0,
            "sets_with_salvageable_metadata": 0,
            "sets": [],
        }

    def test_report_counts_and_shape(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        # canonical (owner, high mtime, keeps group) + shadow (viewer, demoted)
        _write_chat(ziya_home, "owner", "c1", stated_project_id="owner",
                    group_id="g-asr", last_active=2000)
        _write_chat(ziya_home, "viewer", "c1", stated_project_id="viewer",
                    group_id=None, last_active=1000)
        rep = report_dict(scan_chat_integrity(ziya_home))
        assert rep["duplicate_sets"] == 1
        assert rep["shadow_copies"] == 1
        s = rep["sets"][0]
        assert s["chat_id"] == "c1"
        assert s["copy_count"] == 2
        assert s["canonical"]["project"] == "owner"
        assert s["canonical"]["owner_matches_dir"] is True
        assert len(s["shadows"]) == 1
        assert s["shadows"][0]["project"] == "viewer"

    def test_report_flags_salvageable_metadata(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        # canonical is the demoted viewer copy (higher mtime, owner-matched);
        # the owner shadow retains the group -> salvageable.
        _write_chat(ziya_home, "viewer", "c1", stated_project_id="viewer",
                    group_id=None, last_active=5000)
        _write_chat(ziya_home, "owner", "c1", stated_project_id="owner",
                    group_id="g-asr", last_active=1000)
        rep = report_dict(scan_chat_integrity(ziya_home))
        assert rep["sets_with_salvageable_metadata"] == 1
        assert rep["sets"][0]["salvageable_group_id"] == "g-asr"


# ── run_startup_check (self-detection hook) ─────────────────────────

class TestStartupCheck:

    def _dup(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        _write_chat(ziya_home, "owner", "c1", stated_project_id="owner",
                    group_id="g", last_active=2000)
        _write_chat(ziya_home, "viewer", "c1", stated_project_id="viewer",
                    group_id=None, last_active=1000)

    def test_clean_workspace(self, ziya_home):
        res = run_startup_check(ziya_home, auto_reconcile=False)
        assert res["duplicate_sets"] == 0
        assert res["shadows_removed"] == 0

    def test_warn_only_does_not_delete(self, ziya_home):
        self._dup(ziya_home)
        shadow = ziya_home / "projects" / "viewer" / "chats" / "c1.json"
        res = run_startup_check(ziya_home, auto_reconcile=False)
        assert res["dry_run"] is True
        assert res["duplicate_sets"] == 1
        # File must still be on disk — warn-only never deletes.
        assert shadow.exists()

    def test_auto_reconcile_deletes(self, ziya_home):
        self._dup(ziya_home)
        shadow = ziya_home / "projects" / "viewer" / "chats" / "c1.json"
        res = run_startup_check(ziya_home, auto_reconcile=True)
        assert res["dry_run"] is False
        assert res["shadows_removed"] == 1
        assert not shadow.exists()
        # Canonical survives.
        assert (ziya_home / "projects" / "owner" / "chats" / "c1.json").exists()

    def test_env_flag_gates_reconcile(self, ziya_home):
        self._dup(ziya_home)
        shadow = ziya_home / "projects" / "viewer" / "chats" / "c1.json"
        # auto_reconcile=None -> resolved from env; unset -> warn-only.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_AUTO_RECONCILE_CHATS", None)
            res = run_startup_check(ziya_home, auto_reconcile=None)
        assert res["dry_run"] is True
        assert shadow.exists()
        # Set -> reconciles.
        with patch.dict(os.environ, {"ZIYA_AUTO_RECONCILE_CHATS": "1"}):
            res = run_startup_check(ziya_home, auto_reconcile=None)
        assert res["dry_run"] is False
        assert not shadow.exists()

    def test_missing_projects_dir_is_safe(self, tmp_path):
        # Never raises even when there is no projects/ dir.
        res = run_startup_check(tmp_path / "nonexistent-home")
        assert res["duplicate_sets"] == 0


# ── Chat-integrity HTTP endpoints ───────────────────────────────────

class TestIntegrityEndpoints:

    @contextmanager
    def _client(self, ziya_home):
        with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
            with patch("app.api.chats.get_ziya_home", return_value=ziya_home):
                from fastapi import FastAPI
                from app.api.chats import router
                app = FastAPI()
                app.include_router(router)
                yield TestClient(app)

    def _dup(self, ziya_home):
        _make_project(ziya_home, "owner")
        _make_project(ziya_home, "viewer")
        _write_chat(ziya_home, "owner", "c1", stated_project_id="owner",
                    group_id="g-asr", last_active=2000)
        _write_chat(ziya_home, "viewer", "c1", stated_project_id="viewer",
                    group_id=None, last_active=1000)

    def test_get_report_readonly(self, ziya_home):
        self._dup(ziya_home)
        shadow = ziya_home / "projects" / "viewer" / "chats" / "c1.json"
        with self._client(ziya_home) as tc:
            resp = tc.get("/api/v1/chat-integrity")
        assert resp.status_code == 200
        body = resp.json()
        assert body["duplicate_sets"] == 1
        assert body["shadow_copies"] == 1
        # GET must not mutate anything.
        assert shadow.exists()

    def test_reconcile_defaults_to_dry_run(self, ziya_home):
        self._dup(ziya_home)
        shadow = ziya_home / "projects" / "viewer" / "chats" / "c1.json"
        with self._client(ziya_home) as tc:
            resp = tc.post("/api/v1/chat-integrity/reconcile")
        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True
        assert shadow.exists()  # dry-run never deletes

    def test_reconcile_apply_removes_shadow(self, ziya_home):
        self._dup(ziya_home)
        shadow = ziya_home / "projects" / "viewer" / "chats" / "c1.json"
        with self._client(ziya_home) as tc:
            resp = tc.post("/api/v1/chat-integrity/reconcile?dry_run=false")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is False
        assert body["shadows_removed"] == 1
        assert not shadow.exists()
        assert (ziya_home / "projects" / "owner" / "chats" / "c1.json").exists()
