"""Tests for task card storage."""

import pytest
from pathlib import Path

from app.models.task_card import (
    Block, TaskScope, TaskCardCreate, TaskCardUpdate,
)
from app.storage.task_cards import TaskCardStorage


def _simple_task(name: str = "leaf") -> Block:
    return Block(
        block_type="task",
        name=name,
        instructions="do something",
        scope=TaskScope(tools=["render_diagram"]),
    )


def _loop(body=None) -> Block:
    return Block(
        block_type="repeat",
        name="loop",
        repeat_mode="count",
        repeat_count=3,
        repeat_parallel=True,
        body=body or [_simple_task()],
    )


@pytest.fixture
def storage(tmp_path):
    return TaskCardStorage(tmp_path)


class TestCRUD:
    def test_create_and_get(self, storage):
        card = storage.create(TaskCardCreate(
            name="Test Card",
            description="A test",
            root=_simple_task("Spec Gen"),
        ))
        assert card.id
        assert card.name == "Test Card"
        assert card.created_at > 0

        retrieved = storage.get(card.id)
        assert retrieved is not None
        assert retrieved.name == "Test Card"
        assert retrieved.root.name == "Spec Gen"

    def test_get_missing(self, storage):
        assert storage.get("nonexistent") is None

    def test_list_empty(self, storage):
        assert storage.list() == []

    def test_list_returns_all(self, storage):
        storage.create(TaskCardCreate(name="Card A", root=_simple_task()))
        storage.create(TaskCardCreate(name="Card B", root=_simple_task()))
        assert len(storage.list()) == 2

    def test_list_templates_only(self, storage):
        storage.create(TaskCardCreate(name="Regular", root=_simple_task()))
        storage.create(TaskCardCreate(
            name="Template", root=_simple_task(), is_template=True,
        ))
        templates = storage.list(templates_only=True)
        assert len(templates) == 1
        assert templates[0].name == "Template"

    def test_update(self, storage):
        card = storage.create(TaskCardCreate(
            name="Original", root=_simple_task(),
        ))
        updated = storage.update(card.id, TaskCardUpdate(name="Renamed"))
        assert updated.name == "Renamed"
        assert updated.updated_at >= card.updated_at

    def test_update_preserves_unspecified_fields(self, storage):
        card = storage.create(TaskCardCreate(
            name="X", description="keep", root=_simple_task(),
        ))
        updated = storage.update(card.id, TaskCardUpdate(name="Y"))
        assert updated.description == "keep"

    def test_update_root_reassigns_block_ids(self, storage):
        card = storage.create(TaskCardCreate(name="X", root=_simple_task()))
        new_root = _simple_task("leaf2")
        updated = storage.update(card.id, TaskCardUpdate(root=new_root))
        assert updated.root.name == "leaf2"
        assert updated.root.id  # IDs assigned

    def test_update_nonexistent(self, storage):
        assert storage.update("nope", TaskCardUpdate(name="X")) is None

    def test_delete(self, storage):
        card = storage.create(TaskCardCreate(name="Doomed", root=_simple_task()))
        assert storage.delete(card.id) is True
        assert storage.get(card.id) is None

    def test_delete_nonexistent(self, storage):
        assert storage.delete("nope") is False


class TestDuplicate:
    def test_duplicate_preserves_tree(self, storage):
        original = storage.create(TaskCardCreate(
            name="Original", root=_loop(), tags=["x"],
        ))
        clone = storage.duplicate(original.id)
        assert clone.name == "Original (copy)"
        assert clone.id != original.id
        assert clone.root.repeat_count == 3
        assert clone.tags == ["x"]

    def test_duplicate_regenerates_every_block_id(self, storage):
        # A collision here means one card's scope-approval or run-state
        # record silently governs the other card's block too — see
        # _assign_block_ids in app/storage/task_cards.py.
        original = storage.create(TaskCardCreate(
            name="Original", root=_loop(),
        ))
        clone = storage.duplicate(original.id)

        def _ids(block) -> set[str]:
            out = {block.id}
            for child in block.body or []:
                out |= _ids(child)
            return out

        original_ids = _ids(original.root)
        clone_ids = _ids(clone.root)
        assert len(original_ids) > 1, "fixture should have a nested block"
        assert original_ids.isdisjoint(clone_ids)
        # Same shape and content, only the identities differ.
        assert clone.root.block_type == original.root.block_type
        assert clone.root.repeat_count == original.root.repeat_count
        assert len(clone.root.body) == len(original.root.body)

    def test_duplicate_rewrites_sibling_references_to_the_new_ids(self, storage):
        """The remint is correct (see the test above) but it used to leave
        every ``sibling("old-id")`` in the clone pointing at a block that
        exists only in the ORIGINAL.  At run time the reference rendered
        empty and a for_each fan-out ran zero iterations; the launch
        validator now catches it, but a duplicate should not need
        catching."""
        from app.utils.task_card_validation import validate_card_tree
        root = Block(block_type="group", id="g", name="Pipeline", body=[
            Block(block_type="task", id="plan", name="Plan",
                  instructions="emit the roster"),
            Block(block_type="repeat", id="fan", name="Fan out",
                  repeat_mode="for_each",
                  repeat_for_each_source='{{sibling("plan").outputs.roster.docs}}',
                  body=[Block(block_type="task", id="one", name="One",
                              instructions="audit {{item}} per {{sibling(\'plan\')}}")]),
        ])
        original = storage.create(TaskCardCreate(name="Original", root=root))
        # Positive control: the original resolves cleanly.
        assert validate_card_tree(original.root).errors == []

        clone = storage.duplicate(original.id)
        new_plan_id = clone.root.body[0].id
        assert new_plan_id != "plan"
        fan = clone.root.body[1]
        assert fan.repeat_for_each_source == (
            f'{{{{sibling("{new_plan_id}").outputs.roster.docs}}}}'
        )
        # Nested field, single-quoted reference: quote style preserved.
        assert fan.body[0].instructions == (
            f"audit {{{{item}}}} per {{{{sibling('{new_plan_id}')}}}}"
        )
        # The outermost surface: the clone is launchable as-is.
        assert validate_card_tree(clone.root).errors == []
        # The original is untouched.
        reread = storage.get(original.id)
        assert reread.root.body[1].repeat_for_each_source == (
            '{{sibling("plan").outputs.roster.docs}}'
        )

    def test_duplicate_leaves_references_to_foreign_ids_alone(self, storage):
        """A reference to a block that is not in this tree (a calling
        card's block, shared via the run registry) must survive the
        remint verbatim rather than being rewritten or dropped."""
        root = Block(block_type="task", id="t", name="T",
                     instructions='see {{sibling("caller-block")}}')
        card = storage.create(TaskCardCreate(name="C", root=root))
        clone = storage.duplicate(card.id)
        assert clone.root.instructions == 'see {{sibling("caller-block")}}'

    def test_plain_save_does_not_rewrite_references(self, storage):
        """Fill-only id assignment (create/update without force) records
        no remap, so explicit references are never touched — the id
        contract the skill text promises authors."""
        root = Block(block_type="group", id="", name="G", body=[
            Block(block_type="task", id="plan", name="P", instructions="x"),
            Block(block_type="task", id="", name="Q",
                  instructions='{{sibling("plan")}}'),
        ])
        card = storage.create(TaskCardCreate(name="S", root=root))
        assert card.root.body[0].id == "plan"
        assert card.root.body[1].id  # filled in
        assert card.root.body[1].instructions == '{{sibling("plan")}}'

    def test_storage_and_validator_sibling_regexes_agree(self):
        """Both modules recognise a reference independently; if they
        drift, the validator would accept a form the rewrite skips (or
        vice versa) and a duplicate would dangle again."""
        from app.storage import task_cards as st
        from app.utils import task_card_validation as va
        samples = [
            'sibling("a-1")', "sibling('a-1')", 'sibling( "a-1" )',
            '{{sibling("a-1").outputs.x}}', 'sibling(a-1)', 'sibling("")',
        ]
        for s in samples:
            got_st = [m.group(2) for m in st._SIBLING_REF_RE.finditer(s)]
            got_va = [m.group(1) for m in va._SIBLING_REF_RE.finditer(s)]
            assert got_st == got_va, s

    def test_duplicate_as_template(self, storage):
        card = storage.create(TaskCardCreate(name="Task", root=_simple_task()))
        t = storage.duplicate(card.id, as_template=True)
        assert t.is_template is True

    def test_duplicate_nonexistent(self, storage):
        assert storage.duplicate("nope") is None


class TestBlockIdAssignment:
    def test_nested_blocks_all_get_ids(self, storage):
        tree = _loop(body=[_simple_task("a"), _loop(body=[_simple_task("b")])])
        card = storage.create(TaskCardCreate(name="nested", root=tree))
        assert card.root.id
        assert card.root.body[0].id
        assert card.root.body[1].id
        assert card.root.body[1].body[0].id

    def test_existing_ids_preserved(self, storage):
        tree = _simple_task("keep")
        tree.id = "preexisting-id"
        card = storage.create(TaskCardCreate(name="keep", root=tree))
        assert card.root.id == "preexisting-id"


class TestRunRecording:
    def test_record_run_bumps_counters(self, storage):
        card = storage.create(TaskCardCreate(name="X", root=_simple_task()))
        assert card.run_count == 0
        assert card.last_run_at is None
        updated = storage.record_run(card.id)
        assert updated.run_count == 1
        assert updated.last_run_at is not None

    def test_record_run_nonexistent(self, storage):
        assert storage.record_run("nope") is None
