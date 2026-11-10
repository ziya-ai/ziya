"""The Bench detail drawer edits custom skills (design/capabilities-hub.md,
slice 6 "edit mode", landed in stage 2 of the Skills-tab fold-in).

``item_detail`` for a skill now carries ``skill_id`` and ``editable``.  The
drawer needs the id because the skills REST API is keyed by ``Skill.id``
while the bench is keyed by name; it needs ``editable`` because
``SkillStorage.update``/``delete`` refuse built-in and file-backed skills
(a SKILL.md is edited on disk), and the drawer must not offer an edit form
the server will 403.
"""
from __future__ import annotations

import pytest

from app.models.skill import SkillCreate
from app.services import bench as svc
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage

PID = "proj1"
BUILTIN = "Concise"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    st = SkillStorage(tmp_path / "projects" / PID, TokenService())
    monkeypatch.setattr(svc, "_skill_storage", lambda pid: st)
    monkeypatch.setattr(svc, "_mcp_items", lambda: [])
    return st


def test_custom_skill_detail_is_editable_and_names_its_id(storage):
    s = storage.create(SkillCreate(name="fleet-metrics", description="d", prompt="p" * 40))
    d = svc.item_detail(PID, "skill:fleet-metrics")
    assert d["skill_id"] == s.id
    assert d["editable"] is True
    assert d["source"] == "custom"
    # The id the detail names is the one the REST API accepts.
    assert storage.update(d["skill_id"], __import__("app.models.skill", fromlist=["SkillUpdate"]).SkillUpdate(description="e")).description == "e"


def test_builtin_skill_detail_is_not_editable(storage):
    d = svc.item_detail(PID, f"skill:{BUILTIN}")
    assert d["source"] == "builtin"
    assert d["editable"] is False
    assert d["skill_id"]            # still named, so the drawer can address it read-only
    with pytest.raises(ValueError):
        from app.models.skill import SkillUpdate
        storage.update(d["skill_id"], SkillUpdate(description="x"))
