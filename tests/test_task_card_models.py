"""Tests for task card data models (the block tree)."""

import pytest
from app.models.task_card import (
    Block, TaskScope, Artifact, ArtifactPart,
    TaskCard, TaskCardCreate, TaskCardUpdate, TaskCardRun,
)


class TestTaskScope:
    def test_empty(self):
        scope = TaskScope()
        assert scope.files == []
        assert scope.tools == []
        assert scope.skills == []

    def test_populated(self):
        scope = TaskScope(
            files=["app/services/diagram_renderer.py"],
            tools=["render_diagram", "file_write"],
            skills=["code-review"],
        )
        assert len(scope.files) == 1
        assert "render_diagram" in scope.tools


class TestTaskBlock:
    def test_minimal(self):
        block = Block(block_type="task", name="Spec Gen")
        assert block.block_type == "task"
        assert block.body == []
        assert block.instructions is None

    def test_with_instructions_and_scope(self):
        block = Block(
            block_type="task",
            name="Verifier",
            instructions="Render and classify.",
            scope=TaskScope(tools=["render_diagram"]),
            emoji="✅",
        )
        assert block.instructions == "Render and classify."
        assert block.scope.tools == ["render_diagram"]
        assert block.emoji == "✅"


class TestRepeatBlock:
    def test_count_loop(self):
        block = Block(
            block_type="repeat",
            name="Fuzz loop",
            repeat_mode="count",
            repeat_count=5,
            repeat_parallel=True,
            body=[Block(block_type="task", name="iter body")],
        )
        assert block.repeat_count == 5
        assert block.repeat_parallel is True
        assert len(block.body) == 1

    def test_until_loop(self):
        block = Block(
            block_type="repeat",
            name="Retry",
            repeat_mode="until",
            repeat_max=3,
            repeat_propagate="last",
            repeat_until="classification == 'pass'",
        )
        assert block.repeat_mode == "until"
        assert block.repeat_max == 3
        assert block.repeat_propagate == "last"


class TestBlockRecursion:
    def test_nested_tree(self):
        inner_task = Block(
            block_type="task",
            name="Verify",
            instructions="Render and classify.",
        )
        retry = Block(
            block_type="repeat",
            name="Retry-until-pass",
            repeat_mode="until",
            repeat_max=3,
            repeat_propagate="last",
            body=[inner_task],
        )
        generator = Block(
            block_type="task",
            name="Spec Generator",
            instructions="Generate random spec.",
        )
        outer = Block(
            block_type="repeat",
            name="Fuzz",
            repeat_mode="count",
            repeat_count=5,
            repeat_parallel=True,
            body=[generator, retry],
        )
        # Round trip the deep tree
        data = outer.model_dump()
        restored = Block(**data)
        assert restored.block_type == "repeat"
        assert restored.repeat_count == 5
        assert len(restored.body) == 2
        assert restored.body[1].block_type == "repeat"
        assert restored.body[1].body[0].name == "Verify"


class TestArtifact:
    def test_empty_artifact(self):
        a = Artifact()
        assert a.summary == ""
        assert a.outputs == []

    def test_artifact_with_parts(self):
        a = Artifact(
            summary="5 iterations complete",
            decisions=["use log scale", "skip empty dataset"],
            outputs=[
                ArtifactPart(part_type="text", text="3 passed, 2 failed"),
                ArtifactPart(part_type="data", data={"pass": 3, "fail": 2}),
            ],
            tokens=12400,
            tool_calls=18,
        )
        assert a.summary == "5 iterations complete"
        assert len(a.outputs) == 2
        assert a.outputs[1].data["fail"] == 2


class TestTaskCard:
    def _tree(self):
        return Block(
            block_type="repeat",
            name="Diagram Fuzz",
            repeat_mode="count",
            repeat_count=5,
            repeat_parallel=True,
            body=[Block(block_type="task", name="Gen", instructions="Generate.")],
        )

    def test_create_shape(self):
        req = TaskCardCreate(
            name="Diagram Fuzz Test",
            description="Fuzz the renderer",
            root=self._tree(),
            tags=["testing", "diagrams"],
        )
        assert req.name == "Diagram Fuzz Test"
        assert req.is_template is False
        assert req.root.repeat_count == 5

    def test_round_trip(self):
        card = TaskCard(
            id="tc-1",
            name="Diagram Fuzz Test",
            description="",
            root=self._tree(),
            tags=["testing"],
            created_at=1000,
            updated_at=1000,
        )
        data = card.model_dump()
        restored = TaskCard(**data)
        assert restored.root.block_type == "repeat"
        assert restored.root.body[0].instructions == "Generate."

    def test_run_defaults(self):
        run = TaskCardRun()
        assert run.source_conversation_id is None
        assert run.parameter_overrides == {}

    def test_update_partial(self):
        upd = TaskCardUpdate(name="renamed")
        dumped = upd.model_dump(exclude_unset=True)
        assert dumped == {"name": "renamed"}
