"""
Export/import of task cards — app/storage/task_cards.py (card_to_portable,
build_export, portable_cards_from_payload, TaskCardStorage.import_cards) and
the routes in app/api/task_cards.py (GET /export, GET /{id}/export,
POST /import).

What the portable form must guarantee:
  - deck-local fields (id, run history, version, draft, source) are NOT in
    an export, so a file imports as a fresh card anywhere;
  - block ids ARE exported and are REMINTED on import, with sibling()
    references carried across — an approval keyed on the source deck's
    block id must not authorize anything here;
  - an import is all-or-nothing, and warns (does not refuse) on a name
    collision, since a call block by name resolves the first match.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import TaskCard, TaskCardCreate
from app.storage.task_cards import (
    NON_PORTABLE_CARD_FIELDS, PORTABLE_FORMAT, TaskCardStorage,
    build_export, card_to_portable, portable_cards_from_payload,
)


# ── fixtures (same shape as tests/test_api_task_cards.py) ─────────────

@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-001"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "id": project_id, "name": "Test", "path": "/tmp/x",
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


@pytest.fixture
def storage(tmp_path):
    return TaskCardStorage(tmp_path / "proj")


def _sibling_card_root() -> dict:
    """A group whose second task references the first by id."""
    return {
        "block_type": "group", "name": "G", "id": "root-1",
        "body": [
            {"block_type": "task", "id": "producer-1", "name": "Produce",
             "instructions": "emit a list"},
            {"block_type": "task", "id": "consumer-1", "name": "Consume",
             "instructions": 'use {{sibling("producer-1").summary}}'},
        ],
    }


def _create(storage: TaskCardStorage, name="Card", root=None, **kw) -> TaskCard:
    return storage.create(TaskCardCreate(
        name=name, root=root or {"block_type": "task", "name": "T",
                                 "instructions": "do"},
        **kw,
    ))


def _block_ids(block: dict) -> list:
    out = [block.get("id")]
    for child in block.get("body") or []:
        out.extend(_block_ids(child))
    return out


# ── portable form ─────────────────────────────────────────────────────

class TestPortableForm:
    def test_deck_local_fields_stripped(self, storage):
        card = _create(storage, tags=["folder:ops"], is_template=True)
        storage.record_run(card.id)
        portable = card_to_portable(storage.get(card.id))
        for f in NON_PORTABLE_CARD_FIELDS:
            assert f not in portable, f"{f} leaked into the export"
        # Definition survives.
        assert portable["name"] == "Card"
        assert portable["tags"] == ["folder:ops"]
        assert portable["is_template"] is True
        assert portable["root"]["block_type"] == "task"

    def test_block_ids_are_exported(self, storage):
        # sibling() points at block ids; stripping them would break refs.
        card = _create(storage, root=_sibling_card_root())
        portable = card_to_portable(card)
        assert all(_block_ids(portable["root"]))

    def test_requires_round_trips(self, storage):
        card = _create(storage)
        card.requires = {"skills": ["x"], "mcp_servers": [], "tools": [],
                         "strict": True}
        storage._write_json(storage._card_file(card.id), card.model_dump())
        env = build_export([storage.get(card.id)])
        created, _ = storage.import_cards(env)
        assert created[0].requires is not None
        assert created[0].requires.skills == ["x"]
        assert created[0].requires.strict is True

    def test_envelope_shape(self, storage):
        env = build_export([_create(storage, "A"), _create(storage, "B")])
        assert env["format"] == PORTABLE_FORMAT
        assert env["format_version"] == 1
        assert isinstance(env["exported_at"], int)
        assert [c["name"] for c in env["cards"]] == ["A", "B"]

    def test_envelope_carries_reader_notes(self, storage):
        # The notes are for a reader with no Ziya knowledge, so they must
        # cover the three things the tree leaves implicit.
        env = build_export([_create(storage)])
        about = " ".join(env["about"])
        assert "sibling" in about
        assert "scope" in about
        assert "parallel" in about
        # And the envelope still imports — unknown top-level keys are fine.
        created, _ = storage.import_cards(env)
        assert len(created) == 1

    def test_defaults_and_nulls_omitted(self, storage):
        # A plain model_dump of a 4-block card was ~8 KB with ~190 nulls,
        # and a task block showed schedule_*/repeat_* fields.
        card = _create(storage, root=_sibling_card_root())
        portable = card_to_portable(card)

        def walk(block):
            yield block
            for ch in block.get("body") or []:
                yield from walk(ch)

        for b in walk(portable["root"]):
            assert None not in b.values(), b
            assert b["id"], "block ids must survive default-stripping"
            if b["block_type"] == "task":
                assert not any(k.startswith(("schedule_", "repeat_", "until_"))
                               for k in b), sorted(b)
                assert "required" not in b and "body" not in b
        # Default-valued card fields are gone; set ones stay.
        assert "is_template" not in portable and "description" not in portable
        assert portable["root"]["body"][1]["instructions"].startswith("use ")
        # Positive: a non-default value IS exported.
        esc = _create(storage, "E", root={
            "block_type": "task", "instructions": "x",
            "scope": {"shell_commands": ["make"]}, "on_failure": "stop",
        })
        p = card_to_portable(esc)
        assert p["root"]["scope"] == {"shell_commands": ["make"]}
        assert p["root"]["on_failure"] == "stop"

    def test_stripped_export_round_trips_unchanged(self, storage):
        # Import refills every default: the re-imported card's tree must
        # equal the source tree except for the reminted ids.
        src = _create(storage, root=_sibling_card_root(), tags=["t"],
                      is_template=True)
        created, _ = storage.import_cards(build_export([src]))
        new = created[0]

        def strip_ids(block):
            d = {k: v for k, v in block.items() if k != "id"}
            d["body"] = [strip_ids(c) for c in block.get("body") or []]
            return d

        src_root = src.root.model_dump()
        new_root = new.root.model_dump()
        # The consumer's sibling() text differs only by the reminted id.
        src_root["body"][1]["instructions"] = "REF"
        new_root["body"][1]["instructions"] = "REF"
        assert strip_ids(new_root) == strip_ids(src_root)
        assert new.tags == ["t"] and new.is_template is True

    def test_empty_name_still_importable(self, storage):
        # ``name`` is a default-valued field ("") but TaskCardCreate
        # requires it, so stripping must keep it.
        card = _create(storage, name="")
        portable = card_to_portable(card)
        assert portable["name"] == ""
        created, _ = storage.import_cards(portable)
        assert len(created) == 1


class TestPayloadShapes:
    def test_envelope(self):
        cards = portable_cards_from_payload(
            {"format": PORTABLE_FORMAT, "cards": [{"root": {}}]})
        assert len(cards) == 1

    def test_bare_card(self):
        assert len(portable_cards_from_payload({"name": "x", "root": {}})) == 1

    def test_list_of_cards(self):
        assert len(portable_cards_from_payload([{"root": {}}, {"root": {}}])) == 2

    @pytest.mark.parametrize("bad", [
        "a string", 42, {"nothing": 1},
        {"format": "some-other-format", "cards": [{"root": {}}]},
        {"format": PORTABLE_FORMAT, "format_version": 99, "cards": [{"root": {}}]},
        {"cards": []},
        {"cards": "not a list"},
        {"cards": [{"no_root": True}]},
    ])
    def test_rejected(self, bad):
        with pytest.raises(ValueError):
            portable_cards_from_payload(bad)


# ── environment hints and schema bundle ───────────────────────────────

def _contract_root() -> dict:
    return {
        "block_type": "group", "name": "G", "body": [
            {"block_type": "task", "id": "t1", "name": "Produce", "instructions": "x",
             "scope": {"tools": ["file_read", "mcp_search_jira_issues"],
                       "skills": ["web_research"],
                       "cwd": "/Users/someone/workplace/thing"},
             "output_contract": [{"name": "run", "schema": "project:netscan-run"}]},
        ],
    }


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "proj_root"
    (root / ".ziya" / "schemas").mkdir(parents=True)
    (root / ".ziya" / "schemas" / "netscan-run.json").write_text(json.dumps({
        "type": "object", "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }))
    return root


class TestHints:
    def test_card_needs_derived_from_tree(self, storage, project_root):
        from app.utils.task_card_portable import card_needs
        card = _create(storage, root=_contract_root())
        needs = card_needs(card, str(project_root),
                           servers_by_tool={"mcp_search_jira_issues": "jira"})
        assert needs["tools"] == ["file_read", "mcp_search_jira_issues"]
        assert needs["skills"] == ["web_research"]
        assert needs["mcp_servers"] == ["jira"]
        assert needs["schemas"] == ["project:netscan-run"]
        assert needs["cwd"] == ["/Users/someone/workplace/thing"]
        assert "unresolved_schemas" not in needs

    def test_unresolved_schema_reported_not_fatal(self, storage, tmp_path):
        from app.utils.task_card_portable import card_needs
        card = _create(storage, root=_contract_root())
        needs = card_needs(card, str(tmp_path / "empty"), servers_by_tool={})
        assert needs["unresolved_schemas"] == ["project:netscan-run"]
        # Self-contained card -> no hint at all.
        assert card_needs(_create(storage), None, servers_by_tool={}) == {}

    def test_derived_requires_only_when_author_wrote_none(self, storage):
        from app.utils.task_card_portable import derived_requires
        card = _create(storage, root=_contract_root())
        needs = {"tools": ["a"], "skills": ["s"], "mcp_servers": ["m"], "cwd": ["/x"]}
        req = derived_requires(card, needs)
        assert req == {"skills": ["s"], "tools": ["a"], "mcp_servers": ["m"]}
        assert "strict" not in req, "derived requirements must not refuse a launch"
        card.requires = {"skills": ["authored"]}
        assert derived_requires(card, needs) is None
        assert derived_requires(_create(storage), {"cwd": ["/x"]}) is None

    def test_export_carries_needs_requires_and_schema_bundle(self, storage, project_root, monkeypatch):
        import app.utils.task_card_portable as tp
        monkeypatch.setattr(tp, "tool_server_map", lambda: {"mcp_search_jira_issues": "jira"})
        card = _create(storage, root=_contract_root())
        env = build_export([card], str(project_root))
        pc = env["cards"][0]
        assert pc["needs"]["mcp_servers"] == ["jira"]
        assert pc["requires"] == {"skills": ["web_research"],
                                  "tools": ["file_read", "mcp_search_jira_issues"],
                                  "mcp_servers": ["jira"]}
        assert env["schemas"]["project:netscan-run"]["required"] == ["ok"]
        assert any("needs" in a and "requires" in a for a in env["about"])
        # Without a root: no bundle, the need is reported as unresolved.
        env2 = build_export([card])
        assert "schemas" not in env2
        assert env2["cards"][0]["needs"]["unresolved_schemas"] == ["project:netscan-run"]

    def test_import_restores_schema_and_strips_hint(self, storage, project_root, tmp_path, monkeypatch):
        import app.utils.task_card_portable as tp
        monkeypatch.setattr(tp, "tool_server_map", lambda: {})
        card = _create(storage, root=_contract_root())
        env = build_export([card], str(project_root))
        target = tmp_path / "target_root"
        target.mkdir()
        created, warnings = storage.import_cards(env, project_root=str(target))
        new = created[0]
        written = target / ".ziya" / "schemas" / "netscan-run.json"
        assert written.is_file()
        assert json.loads(written.read_text())["required"] == ["ok"]
        # Hint dropped, derived requires kept as the card's own declaration.
        assert not hasattr(new, "needs") or getattr(new, "needs", None) is None
        assert new.requires is not None and new.requires.strict is False
        assert new.requires.tools == ["file_read", "mcp_search_jira_issues"]
        # The schema arrived in this envelope, so the validator must not
        # flag it as missing.
        assert not any("netscan-run" in w and "does not exist" in w for w in warnings), warnings

    def test_import_keeps_differing_local_schema(self, storage, project_root, tmp_path, monkeypatch):
        import app.utils.task_card_portable as tp
        monkeypatch.setattr(tp, "tool_server_map", lambda: {})
        card = _create(storage, root=_contract_root())
        env = build_export([card], str(project_root))
        target = tmp_path / "target_root"
        (target / ".ziya" / "schemas").mkdir(parents=True)
        local = {"type": "object", "properties": {"other": {"type": "string"}}}
        (target / ".ziya" / "schemas" / "netscan-run.json").write_text(json.dumps(local))
        _created, warnings = storage.import_cards(env, project_root=str(target))
        assert json.loads((target / ".ziya" / "schemas" / "netscan-run.json").read_text()) == local
        assert any("different content" in w and "netscan-run" in w for w in warnings), warnings

    def test_restore_refuses_unsafe_names(self, tmp_path):
        from app.utils.task_card_portable import restore_schemas
        restored, warnings = restore_schemas(
            {"project:../escape": {}, "project:.hidden": {}, "ziya:finding/v1": {}},
            str(tmp_path))
        assert restored == []
        assert len(warnings) == 3
        assert not (tmp_path / "escape.json").exists()
        assert not (tmp_path.parent / "escape.json").exists()


# ── storage import ────────────────────────────────────────────────────

class TestStorageImport:
    def test_remints_block_ids_and_carries_sibling_refs(self, storage):
        src = _create(storage, "Src", root=_sibling_card_root())
        src_ids = set(_block_ids(src.root.model_dump()))

        created, warnings = storage.import_cards(build_export([src]))
        assert len(created) == 1
        new = created[0]
        new_root = new.root.model_dump()
        new_ids = set(_block_ids(new_root))

        # Every id is new — an approval keyed on a source block id must
        # not match any block of the imported card.
        assert new_ids.isdisjoint(src_ids)
        assert all(new_ids)
        # The by-id reference followed the remint.
        producer_id = new_root["body"][0]["id"]
        assert f'sibling("{producer_id}")' in new_root["body"][1]["instructions"]
        # And the imported card is a separate, listed deck entry.
        assert new.id != src.id
        assert new.source == "imported"
        assert new.draft is False
        assert new.run_count == 0 and new.version == 1
        assert {c.id for c in storage.list()} == {src.id, new.id}

    def test_name_collision_warns_not_refuses(self, storage):
        _create(storage, "Nightly")
        created, warnings = storage.import_cards(
            {"name": "nightly", "root": {"block_type": "task",
                                         "instructions": "x"}})
        assert len(created) == 1
        assert any("already exists" in w for w in warnings)

    def test_duplicate_names_within_payload_warn(self, storage):
        root = {"block_type": "task", "instructions": "x"}
        created, warnings = storage.import_cards([
            {"name": "Twin", "root": root}, {"name": "Twin", "root": root},
        ])
        assert len(created) == 2
        assert any("more than one card named" in w for w in warnings)

    def test_all_or_nothing(self, storage):
        good = {"name": "ok", "root": {"block_type": "task", "instructions": "x"}}
        bad = {"name": "broken", "root": {"block_type": "not-a-type"}}
        with pytest.raises(ValueError, match="broken"):
            storage.import_cards([good, bad])
        assert storage.list() == [], "a failed import must write nothing"

    def test_validator_findings_surface_as_warnings(self, storage):
        # A stale sibling() id (no such block) is a launch-time error; an
        # importer should hear about it now, not from a failed run.
        root = {
            "block_type": "task", "name": "T",
            "instructions": 'read {{sibling("nope").summary}}',
        }
        created, warnings = storage.import_cards({"name": "S", "root": root})
        assert len(created) == 1
        assert any("sibling" in w for w in warnings), warnings

    def test_import_ignores_run_history_in_payload(self, storage):
        # A hand-edited file carrying history fields must not smuggle them.
        payload = {
            "id": "forced-id", "name": "H", "run_count": 99, "version": 7,
            "draft": True, "source": "builtin",
            "root": {"block_type": "task", "instructions": "x"},
        }
        created, _ = storage.import_cards(payload)
        c = created[0]
        assert c.id != "forced-id"
        assert c.run_count == 0 and c.version == 1
        assert c.draft is False and c.source == "imported"


# ── API seam ──────────────────────────────────────────────────────────

class TestApi:
    def _mk(self, client, project_id, name="Card", root=None) -> dict:
        r = client.post(f"/api/v1/projects/{project_id}/task-cards", json={
            "name": name,
            "root": root or {"block_type": "task", "name": "T", "instructions": "x"},
        })
        assert r.status_code == 201, r.text
        return r.json()

    def test_deck_export_route_not_shadowed_by_card_id(self, client):
        c, pid = client
        self._mk(c, pid, "A")
        r = c.get(f"/api/v1/projects/{pid}/task-cards/export")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["format"] == PORTABLE_FORMAT
        assert [x["name"] for x in body["cards"]] == ["A"]
        assert "attachment" in r.headers.get("content-disposition", "")

    def test_deck_export_excludes_drafts_unless_named(self, client):
        c, pid = client
        self._mk(c, pid, "Listed")
        r = c.post(f"/api/v1/projects/{pid}/task-cards", json={
            "name": "Hidden", "draft": True,
            "root": {"block_type": "task", "instructions": "x"},
        })
        draft_id = r.json()["id"]
        whole = c.get(f"/api/v1/projects/{pid}/task-cards/export").json()
        assert [x["name"] for x in whole["cards"]] == ["Listed"]
        named = c.get(
            f"/api/v1/projects/{pid}/task-cards/export", params={"ids": draft_id},
        ).json()
        assert [x["name"] for x in named["cards"]] == ["Hidden"]

    def test_export_unknown_id_404(self, client):
        c, pid = client
        r = c.get(f"/api/v1/projects/{pid}/task-cards/export",
                  params={"ids": "nope"})
        assert r.status_code == 404

    def test_single_card_export_and_filename(self, client):
        c, pid = client
        card = self._mk(c, pid, "My Card: v2")
        r = c.get(f"/api/v1/projects/{pid}/task-cards/{card['id']}/export")
        assert r.status_code == 200, r.text
        assert r.json()["cards"][0]["name"] == "My Card: v2"
        cd = r.headers["content-disposition"]
        # Header-safe: no spaces or colons in the token.
        assert 'filename="My-Card-v2.ziya-task-cards.json"' in cd

    def test_export_then_import_round_trip(self, client):
        c, pid = client
        src = self._mk(c, pid, "Src", root=_sibling_card_root())
        env = c.get(f"/api/v1/projects/{pid}/task-cards/{src['id']}/export").json()

        r = c.post(f"/api/v1/projects/{pid}/task-cards/import", json=env)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["count"] == 1
        new = body["cards"][0]
        assert new["id"] != src["id"]
        assert set(_block_ids(new["root"])).isdisjoint(_block_ids(src["root"]))
        # Collision warning: the imported card has the same name.
        assert any("already exists" in w for w in body["warnings"])
        # Outermost surface: the deck now lists both.
        names = sorted(x["name"] for x in
                       c.get(f"/api/v1/projects/{pid}/task-cards").json())
        assert names == ["Src", "Src"]

    def test_import_invalid_is_400_and_writes_nothing(self, client):
        c, pid = client
        r = c.post(f"/api/v1/projects/{pid}/task-cards/import",
                   json={"format": "something-else", "cards": [{"root": {}}]})
        assert r.status_code == 400
        assert "not a task card export" in r.json()["detail"]
        assert c.get(f"/api/v1/projects/{pid}/task-cards").json() == []

    def test_import_preflight_findings_are_warnings(self, client, monkeypatch):
        # The import side's statement of "this card wants tools you do not
        # have" — surfaced at import, not an hour into a run.  The live
        # environment is stubbed so the assertion is about the seam, not
        # about which MCP servers this test host happens to run.
        import app.utils.task_card_preflight as pf
        monkeypatch.setattr(pf, "_live_tool_names", lambda: ["file_read"])
        monkeypatch.setattr(pf, "_live_skill_names", lambda *_a, **_k: [])
        monkeypatch.setattr(pf, "_live_mcp_status", lambda: {})
        c, pid = client
        root = {
            "block_type": "task", "name": "T", "instructions": "x",
            "scope": {"tools": ["file_read", "mcp_search_jira_issues"]},
        }
        r = c.post(f"/api/v1/projects/{pid}/task-cards/import", json={
            "name": "Needy", "root": root,
            "requires": {"mcp_servers": ["jira"]},
        })
        assert r.status_code == 201, r.text
        ws = "\n".join(r.json()["warnings"])
        assert "mcp_search_jira_issues" in ws
        assert "jira" in ws and "not configured" in ws
        # The available tool is not complained about.
        assert "'file_read'" not in ws

    def test_imported_escalation_is_unsigned(self, client):
        # The property the remint exists for: an escalating card arrives
        # with nothing signed, whatever the source deck had approved.
        c, pid = client
        root = {
            "block_type": "task", "name": "T", "instructions": "x",
            "scope": {"paths": [], "tools": [], "skills": [],
                      "shell_commands": ["make"]},
        }
        r = c.post(f"/api/v1/projects/{pid}/task-cards/import",
                   json={"name": "Esc", "root": root})
        assert r.status_code == 201, r.text
        new_id = r.json()["cards"][0]["id"]
        st = c.get(f"/api/v1/projects/{pid}/task-cards/{new_id}/scope-status").json()
        esc = [b for b in st["blocks"] if b["hasEscalation"]]
        assert esc, "the shell grant must register as escalation"
        assert all(b["needsSignature"] for b in esc)
