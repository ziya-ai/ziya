"""Tests for task card data models (the block tree)."""

import pytest
from app.models.task_card import (
    Block, TaskScope, ScopeEntry, Artifact, ArtifactPart,
    TaskCard, TaskCardCreate, TaskCardUpdate, TaskCardRun,
    merge_scopes, find_scope_chain,
)


class TestTaskScope:
    def test_empty(self):
        scope = TaskScope()
        assert scope.paths == []
        assert scope.cwd is None
        assert scope.tools == []
        assert scope.skills == []

    def test_populated(self):
        scope = TaskScope(
            paths=[
                ScopeEntry(
                    path="app/services/diagram_renderer.py",
                    is_dir=False, read=True, context=True,
                ),
            ],
            tools=["render_diagram", "file_write"],
            skills=["code-review"],
        )
        assert len(scope.paths) == 1
        assert scope.paths[0].context is True
        assert scope.paths[0].write is False
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

    def test_card_scope_defaults_none(self):
        card = TaskCard(id="tc-2", name="X", description="", root=self._tree())
        assert card.scope is None

    def test_card_scope_round_trip(self):
        card = TaskCard(
            id="tc-3", name="X", description="", root=self._tree(),
            scope=TaskScope(tools=["file_read"]),
        )
        data = card.model_dump()
        restored = TaskCard(**data)
        assert restored.scope.tools == ["file_read"]

    def test_create_and_update_accept_scope(self):
        req = TaskCardCreate(
            name="X", root=self._tree(),
            scope=TaskScope(shell_commands=["pytest"]),
        )
        assert req.scope.shell_commands == ["pytest"]
        upd = TaskCardUpdate(scope=TaskScope(tools=["file_read"]))
        dumped = upd.model_dump(exclude_unset=True)
        assert dumped["scope"]["tools"] == ["file_read"]


class TestMergeScopes:
    """Deck / card / ancestor-block hierarchy — additive-only merge."""

    def test_all_none_returns_none(self):
        assert merge_scopes(None, None, None) is None

    def test_single_scope_passthrough_equivalent(self):
        s = TaskScope(tools=["file_read"])
        merged = merge_scopes(None, s, None)
        assert merged.tools == ["file_read"]

    def test_tools_skills_shell_commands_union_deduped(self):
        a = TaskScope(tools=["file_read"], skills=["debug_mode"],
                       shell_commands=["pytest"])
        b = TaskScope(tools=["file_read", "file_write"], skills=["web_research"],
                       shell_commands=["pytest", "make test"])
        merged = merge_scopes(a, b)
        assert merged.tools == ["file_read", "file_write"]
        assert merged.skills == ["debug_mode", "web_research"]
        assert merged.shell_commands == ["pytest", "make test"]

    def test_paths_merged_by_path_key_union_of_flags(self):
        a = TaskScope(paths=[ScopeEntry(path="src/", is_dir=True, read=True, write=False)])
        b = TaskScope(paths=[ScopeEntry(path="src/", is_dir=True, read=False, write=True)])
        merged = merge_scopes(a, b)
        assert len(merged.paths) == 1
        entry = merged.paths[0]
        # Union: a leaf-only "read" layer + an ancestor-only "write" layer
        # produce a path that is both readable and writable.
        assert entry.read is True
        assert entry.write is True

    def test_distinct_paths_both_kept(self):
        a = TaskScope(paths=[ScopeEntry(path="a.py")])
        b = TaskScope(paths=[ScopeEntry(path="b.py")])
        merged = merge_scopes(a, b)
        assert {e.path for e in merged.paths} == {"a.py", "b.py"}

    def test_later_layer_cannot_downgrade_earlier_write_grant(self):
        """A more specific (later) layer that only grants read must not
        silently strip an earlier (ancestor) layer's write grant for the
        same path — union, not overwrite."""
        deck = TaskScope(paths=[ScopeEntry(path="out/", is_dir=True, write=True)])
        leaf = TaskScope(paths=[ScopeEntry(path="out/", is_dir=True, read=True, write=False)])
        merged = merge_scopes(deck, leaf)
        assert merged.paths[0].write is True

    def test_cwd_most_specific_wins(self):
        deck = TaskScope(cwd="/deck")
        card = TaskScope(cwd="/card")
        leaf = TaskScope()  # no cwd — must not clobber card's
        merged = merge_scopes(deck, card, leaf)
        assert merged.cwd == "/card"

    def test_cwd_falls_back_when_innermost_is_none(self):
        deck = TaskScope(cwd="/deck")
        merged = merge_scopes(deck, None)
        assert merged.cwd == "/deck"

    def test_none_layers_skipped(self):
        a = TaskScope(tools=["file_read"])
        merged = merge_scopes(None, a, None, None)
        assert merged.tools == ["file_read"]

    def test_order_is_root_to_leaf_for_docs_but_union_is_order_independent(self):
        """Union semantics mean swapping layer order produces the same
        grant set (only cwd and same-path-flag precedence are order
        sensitive, covered by dedicated tests above)."""
        a = TaskScope(tools=["x"])
        b = TaskScope(tools=["y"])
        assert set(merge_scopes(a, b).tools) == set(merge_scopes(b, a).tools)


class TestFindScopeChain:
    def test_root_is_target(self):
        root = Block(block_type="task", id="root", scope=TaskScope(tools=["a"]))
        chain = find_scope_chain(root, "root")
        assert chain == [root.scope]

    def test_nested_target_returns_root_to_leaf_chain(self):
        leaf = Block(block_type="task", id="leaf", scope=TaskScope(tools=["leaf-tool"]))
        mid = Block(block_type="repeat", id="mid", scope=TaskScope(tools=["mid-tool"]),
                    body=[leaf])
        root = Block(block_type="group", id="root", scope=None, body=[mid])
        chain = find_scope_chain(root, "leaf")
        assert chain == [None, mid.scope, leaf.scope]

    def test_missing_id_returns_none(self):
        root = Block(block_type="task", id="root")
        assert find_scope_chain(root, "nonexistent") is None

    def test_sibling_not_matched_does_not_pollute_chain(self):
        a = Block(block_type="task", id="a", scope=TaskScope(tools=["a-tool"]))
        b = Block(block_type="task", id="b", scope=TaskScope(tools=["b-tool"]))
        root = Block(block_type="parallel", id="root", body=[a, b])
        chain = find_scope_chain(root, "b")
        assert chain == [None, b.scope]
        assert chain[-1].tools == ["b-tool"]
