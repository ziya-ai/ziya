"""
Tests for delegate data models (Layer 0).

Verifies all Pydantic models, backward compatibility with existing
Chat/ChatGroup models, and JSON serialization round-trips.
"""
import json
import pytest
from app.models.delegate import (
    FileChange, MemoryCrystal, DelegateSpec, DelegateMeta,
    TaskPlan, DelegateBudget, SwarmBudget,
)
from app.models.chat import Chat, Message
from app.models.group import ChatGroup


class TestFileChange:
    def test_fields(self):
        fc = FileChange(path="src/main.py", action="created", line_delta="(new, 50 lines)")
        assert fc.path == "src/main.py"
        assert fc.action == "created"
        assert fc.line_delta == "(new, 50 lines)"

    def test_defaults(self):
        fc = FileChange(path="x.py", action="modified")
        assert fc.line_delta == ""


class TestMemoryCrystal:
    def test_defaults(self):
        mc = MemoryCrystal(delegate_id="D1", task="Auth")
        assert mc.summary == ""
        assert mc.files_changed == []
        assert mc.decisions == []
        assert mc.exports == {}
        assert mc.tool_stats == {}
        assert mc.original_tokens == 0
        assert mc.crystal_tokens == 0
        assert mc.retroactive_review is None

    def test_all_fields(self):
        mc = MemoryCrystal(
            delegate_id="D2",
            task="Token Management",
            summary="Implemented JWT tokens.",
            files_changed=[FileChange(path="auth/tokens.py", action="created", line_delta="(new, 200 lines)")],
            decisions=["Used RS256 for signing"],
            exports={"TokenManager": "auth.tokens.TokenManager"},
            tool_stats={"file_write": 3, "run_shell_command": 1},
            original_tokens=18000,
            crystal_tokens=400,
            created_at=1700000000.0,
            retroactive_review="preserved",
        )
        assert mc.delegate_id == "D2"
        assert len(mc.files_changed) == 1
        assert mc.files_changed[0].path == "auth/tokens.py"
        assert mc.exports["TokenManager"] == "auth.tokens.TokenManager"
        assert mc.retroactive_review == "preserved"

    def test_json_roundtrip(self):
        mc = MemoryCrystal(
            delegate_id="D1", task="Auth",
            summary="Did stuff",
            files_changed=[FileChange(path="a.py", action="modified", line_delta="+10 -2")],
            decisions=["Chose X over Y"],
            original_tokens=5000, crystal_tokens=300,
        )
        d = json.loads(json.dumps(mc.model_dump(), default=str))
        mc2 = MemoryCrystal(**d)
        assert mc2.delegate_id == mc.delegate_id
        assert mc2.summary == mc.summary
        assert mc2.files_changed[0].path == "a.py"


class TestDelegateSpec:
    def test_defaults(self):
        ds = DelegateSpec(delegate_id="D1", name="OAuth Provider")
        assert ds.emoji == "🔵"
        assert ds.scope == ""
        assert ds.files == []
        assert ds.dependencies == []
        assert ds.skill_id is None
        assert ds.conversation_id is None

    def test_full_spec(self):
        ds = DelegateSpec(
            delegate_id="D3", name="Test Suite",
            emoji="⏳", scope="Write pytest tests",
            files=["tests/test_auth.py"],
            dependencies=["D1", "D2"],
            skill_id="skill-review",
            color="#3b82f6",
        )
        assert ds.dependencies == ["D1", "D2"]
        assert ds.files == ["tests/test_auth.py"]


class TestDelegateMeta:
    def test_defaults(self):
        dm = DelegateMeta(role="delegate", plan_id="plan-1")
        assert dm.status == "proposed"
        assert dm.crystal is None
        assert dm.delegate_id is None

    def test_with_crystal(self):
        crystal = MemoryCrystal(delegate_id="D1", task="Auth", summary="Done")
        dm = DelegateMeta(
            role="delegate", plan_id="p1",
            delegate_id="D1", status="completed",
            crystal=crystal,
        )
        assert dm.crystal.summary == "Done"


class TestTaskPlan:
    def test_defaults(self):
        tp = TaskPlan(name="Auth Refactor")
        assert tp.status == "planning"
        assert tp.delegate_specs == []
        assert tp.crystals == []
        assert tp.orchestrator_id is None

    def test_with_specs_and_crystals(self):
        tp = TaskPlan(
            name="Auth Refactor",
            description="Refactor auth to OAuth2",
            orchestrator_id="orch-1",
            delegate_specs=[
                DelegateSpec(delegate_id="D1", name="Provider"),
                DelegateSpec(delegate_id="D2", name="Tokens"),
            ],
            crystals=[
                MemoryCrystal(delegate_id="D1", task="Provider", summary="Created provider"),
            ],
            status="executing",
        )
        assert len(tp.delegate_specs) == 2
        assert len(tp.crystals) == 1
        assert tp.crystals[0].delegate_id == "D1"


class TestSwarmBudget:
    def test_structure(self):
        sb = SwarmBudget(
            model_limit=200000,
            system_prompt_tokens=30000,
            orchestrator_tokens=5000,
            delegates={
                "D1": DelegateBudget(status="completed", active_tokens=340, original_tokens=18000),
                "D2": DelegateBudget(status="running", active_tokens=12000),
            },
            total_active=12340,
            total_freed=17660,
            headroom=157660,
        )
        assert sb.delegates["D1"].original_tokens == 18000
        assert sb.delegates["D2"].status == "running"
        assert sb.headroom == 157660


class TestChatBackwardCompat:
    """Chat model must work identically with and without delegate fields."""

    def test_chat_without_delegate_meta(self):
        c = Chat(id="c1", title="Regular chat", createdAt=1, lastActiveAt=1)
        assert c.delegateMeta is None

    def test_chat_with_delegate_meta(self):
        c = Chat(
            id="c2", title="Delegate conv", createdAt=1, lastActiveAt=1,
            delegateMeta=DelegateMeta(role="delegate", plan_id="p1", status="running"),
        )
        assert c.delegateMeta.role == "delegate"
        assert c.delegateMeta.status == "running"

    def test_chat_json_roundtrip_with_delegate_meta(self):
        dm = DelegateMeta(
            role="delegate", plan_id="p1", delegate_id="D1", status="completed",
            crystal=MemoryCrystal(delegate_id="D1", task="Auth", summary="Done"),
        )
        c = Chat(id="c3", title="test", createdAt=1, lastActiveAt=1, delegateMeta=dm)
        j = json.dumps(c.model_dump(), default=str)
        c2 = Chat(**json.loads(j))
        assert c2.delegateMeta.crystal.summary == "Done"

    def test_old_json_without_delegate_fields(self):
        """Existing persisted JSON data must still deserialize."""
        old = '{"id":"x","title":"old","createdAt":1,"lastActiveAt":1,"messages":[]}'
        c = Chat(**json.loads(old))
        assert c.delegateMeta is None
        assert c.id == "x"


class TestChatGroupBackwardCompat:
    """ChatGroup must work with and without new fields."""

    def test_group_without_new_fields(self):
        g = ChatGroup(id="g1", name="fold", createdAt=1)
        assert g.taskPlan is None
        assert g.systemInstructions is None
        assert g.updatedAt is None

    def test_group_with_task_plan(self):
        g = ChatGroup(
            id="g2", name="🎯 Auth Refactor", createdAt=1,
            systemInstructions="Refactor auth to OAuth2",
            taskPlan={"name": "Auth Refactor", "status": "planning", "delegate_specs": []},
        )
        assert g.taskPlan["name"] == "Auth Refactor"
        assert g.systemInstructions == "Refactor auth to OAuth2"

    def test_old_group_json(self):
        old = '{"id":"g1","name":"fold","createdAt":1,"collapsed":false,"order":0}'
        g = ChatGroup(**json.loads(old))
        assert g.taskPlan is None
        assert g.systemInstructions is None
