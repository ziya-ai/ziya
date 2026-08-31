"""Unlisted DRAFT cards: signable without being filed in the deck.

Signing a card's privilege escalation has always required a PERSISTED
card, because an approval keys on a block id and ids are assigned by
``TaskCardStorage.create``.  That requirement was conflated with
membership of the deck: the only route to a signature was "Save to
deck", so a user who wanted to sign a proposal had to file it somewhere
they did not want it.

A draft breaks that conflation — stored (so ids exist, so the
scope-status endpoint can mint a signCommand and stage the scope the
out-of-process signer reads) but absent from every deck listing until an
explicit save promotes it.

The seams under test are the ones a per-layer change would miss:
  * storage hides drafts from ``list`` but ``get`` still resolves them;
  * the LIST ENDPOINT hides them too (a storage-only filter that the API
    then bypassed would leak them straight back into the deck);
  * scope-status treats a draft like any other saved card — the whole
    point — and stages it for the signer;
  * promotion mutates the existing card rather than creating a second
    one, since fresh ids would strand the signature just obtained.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import (
    Block, TaskScope, TaskCardCreate, TaskCardUpdate,
)
from app.storage.task_cards import TaskCardStorage


def _escalating_task(name: str = "leaf") -> Block:
    """A leaf task whose scope escalates, so scope-status reports it."""
    return Block(
        block_type="task",
        name=name,
        instructions="do something",
        scope=TaskScope(shell_commands=["pytest"]),
    )


def _plain_task(name: str = "leaf") -> Block:
    return Block(block_type="task", name=name, instructions="do something")


@pytest.fixture
def storage(tmp_path):
    return TaskCardStorage(tmp_path)


class TestStorageVisibility:
    def test_draft_is_hidden_from_list(self, storage):
        storage.create(TaskCardCreate(
            name="Draft", root=_plain_task(), draft=True))
        assert storage.list() == []

    def test_non_draft_is_listed(self, storage):
        # Positive control: the filter must hide drafts, not everything.
        storage.create(TaskCardCreate(name="Deck card", root=_plain_task()))
        assert [c.name for c in storage.list()] == ["Deck card"]

    def test_draft_visible_when_asked_for(self, storage):
        card = storage.create(TaskCardCreate(
            name="Draft", root=_plain_task(), draft=True))
        ids = [c.id for c in storage.list(include_drafts=True)]
        assert ids == [card.id]

    def test_draft_resolvable_by_id(self, storage):
        # Hidden from the deck but fully usable: the sign path, the launch
        # binding and the inline tile all fetch by id.
        card = storage.create(TaskCardCreate(
            name="Draft", root=_plain_task(), draft=True))
        fetched = storage.get(card.id)
        assert fetched is not None and fetched.draft is True

    def test_templates_only_still_excludes_drafts(self, storage):
        # A draft template would otherwise reappear in the template gallery,
        # which is a deck surface by another name.
        storage.create(TaskCardCreate(
            name="Draft tmpl", root=_plain_task(),
            is_template=True, draft=True))
        assert storage.list(templates_only=True) == []

    def test_default_is_not_a_draft(self, storage):
        card = storage.create(TaskCardCreate(name="X", root=_plain_task()))
        assert card.draft is False


class TestPromotion:
    def test_promotion_keeps_the_same_card_and_block_ids(self, storage):
        # The reason promotion is an update: a second create would assign
        # fresh block ids and strand any signature keyed on the old ones.
        card = storage.create(TaskCardCreate(
            name="Draft", root=_escalating_task(), draft=True))
        block_id_before = card.root.id
        promoted = storage.update(card.id, TaskCardUpdate(draft=False))
        assert promoted is not None
        assert promoted.id == card.id
        assert promoted.root.id == block_id_before
        assert promoted.draft is False
        assert [c.id for c in storage.list()] == [card.id]


class TestPruning:
    def test_stale_never_run_draft_is_pruned(self, storage):
        card = storage.create(TaskCardCreate(
            name="Old draft", root=_plain_task(), draft=True))
        # Age it past the window by rewriting updated_at directly; the
        # storage API has no back-dating affordance and should not grow one.
        stale = storage.get(card.id)
        stale.updated_at = int(time.time() * 1000) - (30 * 24 * 3600 * 1000)
        storage._write_json(storage._card_file(card.id), stale.model_dump())

        assert storage.prune_stale_drafts() == 1
        assert storage.get(card.id) is None

    def test_stale_draft_that_ran_is_kept(self, storage):
        # Its run records reference the card by id; deleting it would leave
        # that history unresolvable.
        card = storage.create(TaskCardCreate(
            name="Ran once", root=_plain_task(), draft=True))
        storage.record_run(card.id)
        aged = storage.get(card.id)
        aged.updated_at = int(time.time() * 1000) - (30 * 24 * 3600 * 1000)
        storage._write_json(storage._card_file(card.id), aged.model_dump())

        assert storage.prune_stale_drafts() == 0
        assert storage.get(card.id) is not None

    def test_recent_draft_is_kept(self, storage):
        card = storage.create(TaskCardCreate(
            name="Fresh", root=_plain_task(), draft=True))
        assert storage.prune_stale_drafts() == 0
        assert storage.get(card.id) is not None

    def test_deck_card_is_never_pruned(self, storage):
        card = storage.create(TaskCardCreate(name="Deck", root=_plain_task()))
        aged = storage.get(card.id)
        aged.updated_at = int(time.time() * 1000) - (365 * 24 * 3600 * 1000)
        storage._write_json(storage._card_file(card.id), aged.model_dump())
        assert storage.prune_stale_drafts() == 0
        assert storage.get(card.id) is not None


class TestCardLevelScopeSurvivesCreate:
    def test_card_scope_is_persisted(self, storage):
        # Regression: create() built the TaskCard without passing
        # data.scope, so a card whose escalation lived at the CARD level
        # lost it on save.  scope-status then graded a scope the run would
        # not actually request, so the panel and the runtime disagreed.
        card = storage.create(TaskCardCreate(
            name="Card scope",
            root=_plain_task(),
            scope=TaskScope(shell_commands=["pytest"]),
        ))
        stored = storage.get(card.id)
        assert stored is not None and stored.scope is not None
        assert stored.scope.shell_commands == ["pytest"]


# ── API-level seams ───────────────────────────────────────────────────


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_id(ziya_home):
    pid = "test-project-drafts"
    proj_dir = ziya_home / "projects" / pid
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "id": pid,
        "name": "Test",
        "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return pid


@pytest.fixture
def client(ziya_home, project_id):
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.task_cards.get_ziya_home", return_value=ziya_home):
            with patch(
                "app.api.task_cards.get_project_dir",
                return_value=ziya_home / "projects" / project_id,
            ):
                from app.api.task_cards import router

                app = FastAPI()
                app.include_router(router)
                yield TestClient(app), project_id, ziya_home


def _url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/task-cards"


class TestListEndpoint:
    def test_list_excludes_drafts_by_default(self, client):
        c, pid, _ = client
        c.post(_url(pid), json={
            "name": "Draft", "root": _plain_task().model_dump(), "draft": True})
        c.post(_url(pid), json={
            "name": "Deck", "root": _plain_task().model_dump()})
        names = [x["name"] for x in c.get(_url(pid)).json()]
        # Positive half of the assertion matters as much as the negative
        # one: a broken endpoint returning [] would satisfy "no drafts".
        assert names == ["Deck"]

    def test_list_can_include_drafts(self, client):
        c, pid, _ = client
        c.post(_url(pid), json={
            "name": "Draft", "root": _plain_task().model_dump(), "draft": True})
        names = [x["name"] for x in
                 c.get(f"{_url(pid)}?include_drafts=true").json()]
        assert names == ["Draft"]


class TestDraftIsSignable:
    def test_scope_status_mints_a_sign_command_for_a_draft(self, client):
        c, pid, home = client
        created = c.post(_url(pid), json={
            "name": "Draft",
            "root": _escalating_task().model_dump(),
            "draft": True,
        }).json()
        card_id = created["id"]
        block_id = created["root"]["id"]

        st = c.get(f"{_url(pid)}/{card_id}/scope-status").json()
        assert st["anyNeedsSignature"] is True
        rows = {b["blockId"]: b for b in st["blocks"]}
        assert block_id in rows, "the draft's escalating block must be reported"
        # The whole point: a runnable command, which the preview endpoint
        # cannot produce because an unsaved spec has no block ids.
        assert rows[block_id]["signCommand"].startswith("sudo ziya-approve")
        assert card_id in rows[block_id]["signCommand"]

        # And the signer's input exists: the by-id endpoint stages the
        # decrypted scope under "project:card:block".
        staged = json.loads((home / "pending_task_approvals.json").read_text())
        assert f"{pid}:{card_id}:{block_id}" in staged

    def test_promotion_via_update_endpoint_reveals_the_card(self, client):
        c, pid, _ = client
        created = c.post(_url(pid), json={
            "name": "Draft", "root": _plain_task().model_dump(), "draft": True,
        }).json()
        assert c.get(_url(pid)).json() == []
        c.put(f"{_url(pid)}/{created['id']}", json={"draft": False})
        assert [x["id"] for x in c.get(_url(pid)).json()] == [created["id"]]
