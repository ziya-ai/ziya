"""Launch-time structural validation of a task card.

Complements tests/test_task_card_validation.py (which covers the
validator in isolation) by pinning that the launch endpoint actually
CONSULTS it.  That distinction matters: the signature-surfacing defect
earlier in this codebase was exactly this shape — the checking machinery
was correct and fully covered while one call site simply never asked.

The properties pinned here:
  - a card with a structural error is refused with 422 and NO run record
  - the response names the offending block so the author can act
  - warnings alone never block a launch
  - a valid card is unaffected
  - the escape hatch works, for a caller that knows better
"""

import json
import os
import time
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-lv1"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "id": project_id,
        "name": "Test",
        "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return project_id


@pytest.fixture
def client(ziya_home, project_dir):
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.task_cards.get_ziya_home", return_value=ziya_home):
            with patch(
                "app.api.task_cards.get_project_dir",
                return_value=ziya_home / "projects" / project_dir,
            ):
                from app.api.task_cards import router

                app = FastAPI()
                app.include_router(router)
                yield TestClient(app), project_dir


def _create(tc, pid, name, root):
    return tc.post(
        f"/api/v1/projects/{pid}/task-cards",
        json={"name": name, "root": root},
    ).json()


def _launch(tc, pid, card_id):
    with patch(
        "app.api.task_cards.execute_block",
        new=AsyncMock(return_value=Artifact(summary="done")),
    ):
        return tc.post(
            f"/api/v1/projects/{pid}/task-cards/{card_id}/launch", json={},
        )


def _runs(tc, pid):
    r = tc.get(f"/api/v1/projects/{pid}/task-runs")
    return r.json() if r.status_code == 200 else []


class TestErrorsBlockLaunch:
    def test_task_without_instructions_is_refused(self, client):
        tc, pid = client
        card = _create(tc, pid, "broken", {
            "block_type": "task", "name": "empty",
        })
        resp = _launch(tc, pid, card["id"])
        assert resp.status_code == 422

    def test_refusal_names_the_offending_block(self, client):
        tc, pid = client
        card = _create(tc, pid, "broken", {
            "block_type": "group", "name": "Root", "body": [
                {"block_type": "task", "name": "Audit step"},
            ],
        })
        resp = _launch(tc, pid, card["id"])
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["errors"], "the refusal must say what is wrong"
        blob = json.dumps(detail)
        # Without a locator the message is unactionable in a large card.
        assert "Audit step" in blob
        assert "instructions" in blob

    def test_for_each_without_source_is_refused(self, client):
        tc, pid = client
        card = _create(tc, pid, "fanout", {
            "block_type": "repeat", "name": "Fan out",
            "repeat_mode": "for_each", "repeat_max": 60,
            "body": [{"block_type": "task", "name": "w",
                      "instructions": "process {{item}}"}],
        })
        resp = _launch(tc, pid, card["id"])
        assert resp.status_code == 422

    def test_refused_launch_creates_no_run_record(self, client):
        """A refusal must leave no misleading run behind.

        Distinct from the credentials preflight, which DOES mint a held
        run: that check happens inside the background coroutine after the
        HTTP response is already sent, so 'held' is the only way to tell
        the user anything.  This check is synchronous, so the 422 IS the
        report and an extra run record would be noise.
        """
        tc, pid = client
        before = len(_runs(tc, pid))
        card = _create(tc, pid, "broken", {
            "block_type": "task", "name": "empty",
        })
        assert _launch(tc, pid, card["id"]).status_code == 422
        assert len(_runs(tc, pid)) == before

    def test_executor_is_never_invoked_for_a_refused_card(self, client):
        tc, pid = client
        card = _create(tc, pid, "broken", {
            "block_type": "task", "name": "empty",
        })
        with patch(
            "app.api.task_cards.execute_block", new=AsyncMock(),
        ) as exec_mock:
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
        assert resp.status_code == 422
        exec_mock.assert_not_called()


class TestWarningsDoNotBlock:
    def test_unknown_field_still_launches(self, client):
        """A typo'd field is worth reporting but not worth refusing.

        Blocking here would be a behaviour change: cards carrying stray
        fields (including ones written by a newer version) run fine today
        and must keep running.
        """
        tc, pid = client
        card = _create(tc, pid, "typo", {
            "block_type": "repeat", "name": "L",
            "repeat_mode": "count", "repeat_count": 2,
            "repeat_maximum": 60,
            "body": [{"block_type": "task", "name": "t",
                      "instructions": "go"}],
        })
        assert _launch(tc, pid, card["id"]).status_code == 200

    def test_empty_container_still_launches(self, client):
        tc, pid = client
        card = _create(tc, pid, "empty-group", {
            "block_type": "group", "name": "G", "body": [],
        })
        assert _launch(tc, pid, card["id"]).status_code == 200


class TestValidCardsUnaffected:
    def test_plain_task_launches(self, client):
        tc, pid = client
        card = _create(tc, pid, "ok", {
            "block_type": "task", "name": "T", "instructions": "do it",
        })
        assert _launch(tc, pid, card["id"]).status_code == 200

    def test_templated_fanout_launches(self, client):
        """The canonical planner-then-fan-out shape must not be flagged.

        Its source resolves against a runtime artifact, so a validator
        that judged it statically would block the most useful card shape
        in the system.
        """
        tc, pid = client
        card = _create(tc, pid, "planner", {
            "block_type": "group", "name": "G", "body": [
                {"block_type": "task", "name": "plan",
                 "instructions": "emit a roster"},
                {"block_type": "repeat", "name": "fan",
                 "repeat_mode": "for_each",
                 "repeat_for_each_source": "{{previous_sibling.summary}}",
                 "body": [{"block_type": "task", "name": "w",
                           "instructions": "handle {{item}}"}]},
            ],
        })
        assert _launch(tc, pid, card["id"]).status_code == 200


class TestEscapeHatch:
    def test_env_var_skips_validation(self, client):
        """An operator who knows better can bypass the gate.

        Mirrors ZIYA_SKIP_LAUNCH_PREFLIGHT: a check that cannot be turned
        off eventually blocks a launch that would have worked.
        """
        tc, pid = client
        card = _create(tc, pid, "broken", {
            "block_type": "task", "name": "empty",
        })
        with patch.dict(os.environ, {"ZIYA_SKIP_CARD_VALIDATION": "1"}):
            assert _launch(tc, pid, card["id"]).status_code == 200


class TestValidatorFaultIsNotFatal:
    def test_a_validator_exception_does_not_block_the_launch(self, client):
        """"Cannot verify" is not "invalid".

        Same rule the credentials preflight follows: any failure of the
        CHECK itself proceeds to launch, because a validator bug must
        never become an outage.
        """
        tc, pid = client
        card = _create(tc, pid, "ok", {
            "block_type": "task", "name": "T", "instructions": "do it",
        })
        with patch(
            "app.utils.task_card_validation.validate_card_tree",
            side_effect=RuntimeError("validator exploded"),
        ):
            assert _launch(tc, pid, card["id"]).status_code == 200
