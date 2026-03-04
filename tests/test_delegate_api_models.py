"""
Test delegate API request/response models and DelegateManager wiring.

These tests validate the data contracts for T26 (delegate API routes)
without requiring a running server.
"""
import pytest
from app.models.delegate import DelegateSpec, TaskPlan, DelegateMeta, MemoryCrystal


class TestDelegateSpecSerialization:
    """Verify DelegateSpec round-trips through JSON correctly."""

    def test_minimal_spec(self):
        spec = DelegateSpec(
            delegate_id="d1",
            name="OAuth Provider",
        )
        d = spec.model_dump()
        assert d["delegate_id"] == "d1"
        assert d["name"] == "OAuth Provider"
        assert d["dependencies"] == []
        assert d["files"] == []

    def test_full_spec_with_deps(self):
        spec = DelegateSpec(
            delegate_id="d2",
            name="Token Management",
            emoji="🔑",
            scope="Implement token refresh logic",
            files=["app/auth/tokens.py", "app/auth/refresh.py"],
            dependencies=["d1"],
            skill_id="skill-review",
            color="#3b82f6",
        )
        d = spec.model_dump()
        assert d["dependencies"] == ["d1"]
        assert d["files"] == ["app/auth/tokens.py", "app/auth/refresh.py"]
        assert d["emoji"] == "🔑"

    def test_spec_from_dict(self):
        """Simulate what happens when JSON from API request is parsed."""
        raw = {
            "delegate_id": "d3",
            "name": "Test Suite",
            "files": ["tests/test_auth.py"],
            "dependencies": ["d1", "d2"],
        }
        spec = DelegateSpec(**raw)
        assert spec.delegate_id == "d3"
        assert len(spec.dependencies) == 2


class TestTaskPlanSerialization:
    """Verify TaskPlan round-trips through JSON correctly."""

    def test_plan_with_specs(self):
        specs = [
            DelegateSpec(delegate_id="d1", name="A", files=["a.py"]),
            DelegateSpec(delegate_id="d2", name="B", dependencies=["d1"]),
        ]
        plan = TaskPlan(
            name="Auth Refactor",
            description="Refactor authentication system",
            delegate_specs=specs,
            status="running",
            created_at=1000.0,
        )
        d = plan.model_dump()
        assert d["name"] == "Auth Refactor"
        assert len(d["delegate_specs"]) == 2
        assert d["delegate_specs"][1]["dependencies"] == ["d1"]

    def test_plan_from_dict(self):
        """Simulate deserializing a stored TaskPlan from JSON."""
        raw = {
            "name": "Test Plan",
            "delegate_specs": [
                {"delegate_id": "d1", "name": "One"},
            ],
            "status": "planning",
            "created_at": 123.0,
        }
        plan = TaskPlan(**raw)
        assert plan.name == "Test Plan"
        assert len(plan.delegate_specs) == 1
        assert isinstance(plan.delegate_specs[0], DelegateSpec)


class TestDelegateMetaOnChat:
    """Verify DelegateMeta integrates with Chat model."""

    def test_chat_with_delegate_meta(self):
        from app.models.chat import Chat
        chat = Chat(
            id="test-chat",
            title="D1: OAuth Provider",
            messages=[],
            createdAt=1000,
            lastActiveAt=1000,
            delegateMeta=DelegateMeta(
                role="delegate",
                plan_id="plan-1",
                delegate_id="d1",
                status="running",
            ),
        )
        d = chat.model_dump()
        assert d["delegateMeta"]["role"] == "delegate"
        assert d["delegateMeta"]["status"] == "running"

        # Round-trip through dict (simulates JSON storage → read)
        chat2 = Chat(**d)
        assert chat2.delegateMeta is not None
        assert chat2.delegateMeta.role == "delegate"
        assert chat2.delegateMeta.delegate_id == "d1"

    def test_chat_without_delegate_meta(self):
        """Regular conversations should have delegateMeta=None."""
        from app.models.chat import Chat
        chat = Chat(
            id="regular-chat",
            title="Regular Chat",
            messages=[],
            createdAt=1000,
            lastActiveAt=1000,
        )
        assert chat.delegateMeta is None
        d = chat.model_dump()
        assert d["delegateMeta"] is None

    def test_orchestrator_meta(self):
        meta = DelegateMeta(
            role="orchestrator",
            plan_id="plan-1",
            status="running",
        )
        assert meta.role == "orchestrator"
        assert meta.delegate_id is None

    def test_crystal_meta(self):
        meta = DelegateMeta(
            role="delegate",
            plan_id="plan-1",
            delegate_id="d1",
            status="crystal",
            crystal=MemoryCrystal(
                delegate_id="d1",
                task="OAuth Provider",
                summary="Implemented OAuth2 provider.",
                original_tokens=15000,
                crystal_tokens=350,
                created_at=1000.0,
            ),
        )
        d = meta.model_dump()
        assert d["crystal"]["summary"] == "Implemented OAuth2 provider."
        assert d["crystal"]["original_tokens"] == 15000

        # Round-trip
        meta2 = DelegateMeta(**d)
        assert meta2.crystal is not None
        assert meta2.crystal.crystal_tokens == 350


class TestChatGroupWithTaskPlan:
    """Verify ChatGroup round-trips with taskPlan."""

    def test_group_with_task_plan(self):
        from app.models.group import ChatGroup
        group = ChatGroup(
            id="group-1",
            name="⚡ Auth Refactor",
            createdAt=1000,
            taskPlan={
                "name": "Auth Refactor",
                "delegate_specs": [
                    {"delegate_id": "d1", "name": "OAuth"},
                ],
                "status": "running",
                "created_at": 1000.0,
            },
        )
        d = group.model_dump()
        assert d["taskPlan"]["name"] == "Auth Refactor"
        assert len(d["taskPlan"]["delegate_specs"]) == 1

        # Round-trip
        group2 = ChatGroup(**d)
        assert group2.taskPlan is not None
        assert group2.taskPlan["status"] == "running"

    def test_group_without_task_plan(self):
        from app.models.group import ChatGroup
        group = ChatGroup(
            id="group-2",
            name="Regular Folder",
            createdAt=1000,
        )
        assert group.taskPlan is None
