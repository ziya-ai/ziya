"""Targeted ``edits`` for task_card_write (app/utils/task_card_edits.py).

Attribution (2026-10-09): a model reconfiguring a ~100 KB nightly card
to set ``scope.cwd`` on every task shelled the card to /tmp, stripped
defaults with a second script, and was about to paste 64 KB back as the
``root`` argument -- which the scope guard would then have refused,
because any scope difference (cwd included) read as a permissions
change.  Two defects: no targeted write, and a guard stricter than the
signing hash it protects.

Layer 1 (pure applier) runs against written code.  Layer 2 (the tool
seam) asserts that task_card_write actually accepts ``edits``, routes
the merged tree through the guard, and saves -- it fails until the
app/mcp/tools/task_card_tools.py change is applied.
"""
import json
import time
import uuid

import pytest

from app.utils.task_card_edits import apply_block_edits, escalation_changes


def _card_root():
    return {
        "block_type": "group", "id": "g", "name": "", "body": [
            {"block_type": "state", "id": "s", "name": "preamble",
             "state_context": "You are in /old/root", "body": []},
            {"block_type": "task", "id": "t1", "name": "one",
             "instructions": "do one", "body": [],
             "scope": {"shell_commands": ["pytest"]}},
            {"block_type": "task", "id": "t2", "name": "two",
             "instructions": "do two", "body": []},
        ],
    }


class TestApplier:
    def test_by_type_sets_cwd_on_every_task_and_nothing_else(self):
        root = _card_root()
        new, touched, problems = apply_block_edits(
            root, [{"block_type": "task", "set": {"scope.cwd": "svc"}}])
        assert problems == [] and touched == 2
        t1, t2 = new["body"][1], new["body"][2]
        assert t1["scope"] == {"shell_commands": ["pytest"], "cwd": "svc"}
        assert t2["scope"] == {"cwd": "svc"}
        assert "scope" not in new["body"][0]          # the state block untouched
        assert root["body"][2].get("scope") is None   # input not mutated

    def test_by_id_rewords_one_field(self):
        new, touched, problems = apply_block_edits(
            _card_root(), [{"block_id": "s", "set": {"state_context": "cd first"}}])
        assert problems == [] and touched == 1
        assert new["body"][0]["state_context"] == "cd first"
        assert new["body"][1]["instructions"] == "do one"

    @pytest.mark.parametrize("key", ["id", "body", "block_type", "scope"])
    def test_structural_fields_are_refused(self, key):
        root = _card_root()
        new, touched, problems = apply_block_edits(
            root, [{"block_id": "t1", "set": {key: "x"}}])
        assert len(problems) == 1 and key in problems[0]
        assert touched == 0 and new is root       # original returned untouched

    def test_any_scope_field_is_settable_and_validated(self):
        new, touched, problems = apply_block_edits(_card_root(), [
            {"block_id": "t2", "set": {"scope.tools": ["file_read"],
                                       "scope.model_tier": "small",
                                       "scope.shell_timeout_secs": 900}},
        ])
        assert problems == [] and touched == 1
        assert new["body"][2]["scope"] == {
            "tools": ["file_read"], "model_tier": "small", "shell_timeout_secs": 900}
        # The existing grant on t1 is untouched by an edit aimed at t2.
        assert new["body"][1]["scope"] == {"shell_commands": ["pytest"]}

    def test_bad_scope_value_and_unknown_scope_field_are_problems(self):
        root = _card_root()
        _, touched, p1 = apply_block_edits(
            root, [{"block_id": "t1", "set": {"scope.shell_timeout_secs": "soon"}}])
        assert touched == 0 and p1 and "shell_timeout_secs" in p1[0]
        _, _, p2 = apply_block_edits(
            root, [{"block_id": "t1", "set": {"scope.sudo": True}}])
        assert p2 and "not a TaskScope field" in p2[0]

    def test_whole_scope_replacement_is_refused_with_pointer_to_dotted_keys(self):
        _, _, problems = apply_block_edits(
            _card_root(), [{"block_id": "t1", "set": {"scope": {"tools": ["x"]}}}])
        assert problems and "scope.<field>" in problems[0]


class TestEscalationChanges:
    """The rule the tool enforces on a merged tree: never widen."""

    def test_unchanged_and_non_privilege_edits_report_nothing(self):
        old = _card_root()
        new, _, _ = apply_block_edits(old, [
            {"block_type": "task", "set": {"scope.tools": ["file_read"],
                                           "scope.cwd": "svc"}}])
        assert escalation_changes(old, new) == ([], [])

    def test_new_shell_command_is_widening(self):
        old = _card_root()
        new, _, _ = apply_block_edits(old, [
            {"block_id": "t1", "set": {"scope.shell_commands": ["pytest", "rm"]}}])
        widened, narrowed = escalation_changes(old, new)
        assert len(widened) == 1 and "'t1'" in widened[0] and "rm" in widened[0]
        assert narrowed == []

    def test_new_out_of_floor_writable_path_is_widening_but_floor_path_is_not(self):
        old = _card_root()
        safe, _, _ = apply_block_edits(old, [
            {"block_id": "t2", "set": {"scope.paths": [
                {"path": ".ziya/out", "write": True}]}}])
        assert escalation_changes(old, safe) == ([], [])
        wide, _, _ = apply_block_edits(old, [
            {"block_id": "t2", "set": {"scope.paths": [
                {"path": "src", "write": True}]}}])
        widened, _ = escalation_changes(old, wide)
        assert len(widened) == 1 and "src" in widened[0]

    def test_grant_on_container_counts_for_its_leaves(self):
        old = _card_root()
        new, _, _ = apply_block_edits(old, [
            {"block_id": "g", "set": {"scope.shell_commands": ["rm"]}}])
        widened, _ = escalation_changes(old, new)
        # Both leaf tasks inherit the container's new grant.
        assert {w.split("'")[1] for w in widened} == {"t1", "t2"}

    def test_narrowing_is_allowed_and_reported(self):
        root = _card_root()
        root["body"][1]["scope"] = {"shell_commands": ["pytest", "rm"]}
        new, _, _ = apply_block_edits(root, [
            {"block_id": "t1", "set": {"scope.shell_commands": ["rm"]}}])
        assert escalation_changes(root, new) == ([], ["t1"])

    def test_dropping_every_grant_needs_no_resign(self):
        old = _card_root()
        new, _, _ = apply_block_edits(old, [
            {"block_id": "t1", "set": {"scope.shell_commands": []}}])
        assert escalation_changes(old, new) == ([], [])

    def test_unknown_field_and_unmatched_selector_are_problems(self):
        _, _, p1 = apply_block_edits(_card_root(), [{"block_id": "t1", "set": {"instrucions": "x"}}])
        assert p1 and "spelling" in p1[0]
        _, _, p2 = apply_block_edits(_card_root(), [{"block_id": "nope", "set": {"name": "x"}}])
        assert p2 and "no block matches" in p2[0]
        _, _, p3 = apply_block_edits(_card_root(), [{"set": {"name": "x"}}])
        assert p3 and "block_id or block_type" in p3[0]

    def test_any_problem_applies_nothing(self):
        root = _card_root()
        new, touched, problems = apply_block_edits(root, [
            {"block_id": "t2", "set": {"instructions": "changed"}},
            {"block_id": "t1", "set": {"id": "stolen"}},
        ])
        assert problems and touched == 0
        assert new["body"][2]["instructions"] == "do two"


# ── Layer 2: the tool seam ──────────────────────────────────────

def _make_env(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    projects_dir.mkdir(parents=True)
    project_id = "p_" + uuid.uuid4().hex[:8]
    project_dir = projects_dir / project_id
    project_dir.mkdir()
    (project_dir / "project.json").write_text(json.dumps({
        "id": project_id, "name": "Test",
        "path": str(project_root.resolve()),
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): project_id}))
    (project_dir / "task_cards").mkdir()
    (project_dir / "chats").mkdir()
    return {"project_root": str(project_root.resolve()),
            "ziya_home": ziya_home, "project_dir": project_dir}


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _make_env(tmp_path)
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: e["ziya_home"])
    from app.context import set_project_root
    set_project_root(e["project_root"])
    return e


def _make_card(env):
    from app.storage.task_cards import TaskCardStorage
    from app.models.task_card import TaskCardCreate, Block
    storage = TaskCardStorage(env["project_dir"])
    card = storage.create(TaskCardCreate(name="Nightly", root=Block(**_card_root())))
    return storage, card


@pytest.mark.asyncio
async def test_tool_edits_set_cwd_on_all_tasks_without_resending_root(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    storage, card = _make_card(env)
    out = await TaskCardWriteTool().execute(card_id=card.id, edits=[
        {"block_type": "task", "set": {"scope.cwd": "svc"}},
        {"block_id": "s", "set": {"state_context": "cd first"}},
    ])
    assert out.get("success") is True, out
    assert out["blocks_edited"] == 3
    reloaded = storage.get(card.id)
    assert reloaded.root.body[0].state_context == "cd first"
    assert [b.scope.cwd for b in reloaded.root.body[1:]] == ["svc", "svc"]
    # The shell grant and its id survived: the approval key is intact.
    assert reloaded.root.body[1].id == "t1"
    assert reloaded.root.body[1].scope.shell_commands == ["pytest"]


@pytest.mark.asyncio
async def test_tool_edits_cannot_widen_a_grant(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    storage, card = _make_card(env)
    out = await TaskCardWriteTool().execute(card_id=card.id, edits=[
        {"block_id": "t1", "set": {"scope.shell_commands": ["pytest", "rm"]}},
    ])
    assert out.get("error") is True
    assert storage.get(card.id).root.body[1].scope.shell_commands == ["pytest"]
    # The refusal teaches the mechanism rather than just saying no: the
    # card file is not the floor, signing is, so there is no side door.
    msg = out["message"]
    assert "rm" in msg and "ziya-approve" in msg and "gains nothing" in msg


@pytest.mark.asyncio
async def test_tool_edits_set_non_privilege_scope_fields(env):
    """tools/model_tier/shell_timeout_secs are settings the signing hash
    does not cover; refusing them is what sent a model to python3."""
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    storage, card = _make_card(env)
    out = await TaskCardWriteTool().execute(card_id=card.id, edits=[
        {"block_id": "t1", "set": {"scope.tools": ["file_read"],
                                   "scope.model_tier": "small",
                                   "scope.shell_timeout_secs": 900}},
    ])
    assert out.get("success") is True, out
    assert "resign_required" not in out
    s = storage.get(card.id).root.body[1].scope
    assert (s.tools, s.model_tier, s.shell_timeout_secs) == (["file_read"], "small", 900)
    assert s.shell_commands == ["pytest"]   # the grant and its hash untouched


@pytest.mark.asyncio
async def test_tool_edits_narrow_a_grant_and_report_resign(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    from app.storage.task_cards import TaskCardStorage
    from app.models.task_card import TaskCardCreate, Block
    root = _card_root()
    root["body"][1]["scope"] = {"shell_commands": ["pytest", "rm"]}
    storage = TaskCardStorage(env["project_dir"])
    card = storage.create(TaskCardCreate(name="N", root=Block(**root)))
    out = await TaskCardWriteTool().execute(card_id=card.id, edits=[
        {"block_id": "t1", "set": {"scope.shell_commands": ["rm"]}},
    ])
    assert out.get("success") is True, out
    assert out["resign_required"] == ["t1"]
    assert "re-sign" in out["message"]
    assert storage.get(card.id).root.body[1].scope.shell_commands == ["rm"]


@pytest.mark.asyncio
async def test_tool_edits_set_scope_skills_on_one_block(env):
    """Attribution (2026-10-13): a model needed to add document_authoring
    to two stages of a ~114 KB card, read the tool description as
    "scope.cwd only", and concluded it had to reserialize the whole root
    it could not even read in full.  skills is not in the signing hash."""
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    storage, card = _make_card(env)
    out = await TaskCardWriteTool().execute(card_id=card.id, edits=[
        {"block_id": "t2", "set": {"scope.skills": ["document_authoring"]}},
    ])
    assert out.get("success") is True, out
    reloaded = storage.get(card.id).root
    assert reloaded.body[2].scope.skills == ["document_authoring"]
    # Neighbouring block and its grant are untouched.
    assert reloaded.body[1].scope.shell_commands == ["pytest"]
    assert not reloaded.body[1].scope.skills


def test_tool_description_does_not_claim_scope_is_cwd_only():
    """The stale 'other scope fields are not editable' text is what the
    model obeyed; the description must advertise scope.<field>."""
    from app.mcp.tools.task_card_tools import TaskCardWriteInput, TaskCardWriteTool
    edits_doc = TaskCardWriteInput.model_fields["edits"].description
    assert "not editable" not in edits_doc
    assert "scope.<field>" in edits_doc and "skills" in edits_doc
    assert "scope.<field>" in TaskCardWriteTool().description


@pytest.mark.asyncio
async def test_read_block_id_returns_only_that_subtree(env):
    from app.mcp.tools.task_card_tools import TaskCardReadTool
    _, card = _make_card(env)
    out = await TaskCardReadTool().execute(card_id=card.id, block_id="t1")
    assert out.get("success") is True, out
    assert out["block"]["id"] == "t1"
    assert out["block"]["instructions"] == "do one"
    assert "card" not in out          # the whole tree is not returned
    missing = await TaskCardReadTool().execute(card_id=card.id, block_id="nope")
    assert missing.get("error") is True and "nope" in missing["message"]


@pytest.mark.asyncio
async def test_read_outline_lists_ids_without_bodies_of_text(env):
    from app.mcp.tools.task_card_tools import TaskCardReadTool
    _, card = _make_card(env)
    out = await TaskCardReadTool().execute(card_id=card.id, outline=True)
    assert out.get("success") is True, out
    tree = out["outline"]
    assert tree["id"] == "g"
    ids = [c["id"] for c in tree["children"]]
    assert ids == ["s", "t1", "t2"]
    t1 = tree["children"][1]
    assert t1["scope"] == {"shell_commands": ["pytest"]}
    assert "instructions" not in t1   # only a short preview
    assert t1["preview"] == "do one"


@pytest.mark.asyncio
async def test_tool_refuses_root_and_edits_together(env):
    from app.mcp.tools.task_card_tools import TaskCardWriteTool
    _, card = _make_card(env)
    out = await TaskCardWriteTool().execute(
        card_id=card.id, root=_card_root(),
        edits=[{"block_id": "s", "set": {"name": "x"}}])
    assert out.get("error") is True
