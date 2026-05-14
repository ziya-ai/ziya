"""
Verifies a task card with a Parallel root (or Parallel nested under
Repeat) round-trips cleanly through storage and matches the executor's
expected shape.  Complements existing tests by covering the tree
configuration that the new frontend ParallelBlockEditor will produce.
"""
import tempfile
from pathlib import Path

from app.models.task_card import Block, TaskCard, TaskCardCreate
from app.storage.task_cards import TaskCardStorage


def _storage():
    tmp = tempfile.mkdtemp(prefix="ziya-pbe-test-")
    return TaskCardStorage(Path(tmp))


def _task(name, instructions="do it"):
    return Block(block_type="task", name=name, instructions=instructions)


def test_parallel_root_roundtrip():
    """A card with a Parallel root and two Task children survives create+get."""
    store = _storage()
    root = Block(
        block_type="parallel",
        name="Fan-out",
        body=[_task("branch A"), _task("branch B")],
    )
    created = store.create(TaskCardCreate(name="Parallel test", root=root))

    fetched = store.get(created.id)
    assert fetched is not None
    assert fetched.root.block_type == "parallel"
    assert fetched.root.name == "Fan-out"
    assert len(fetched.root.body) == 2
    assert all(c.block_type == "task" for c in fetched.root.body)
    # Parallel blocks carry no loop controls
    assert fetched.root.repeat_mode is None
    assert fetched.root.repeat_count is None
    assert fetched.root.repeat_parallel is False


def test_parallel_nested_under_repeat_roundtrip():
    """Repeat → Parallel → Task(s): the trees the UI will let users build."""
    store = _storage()
    inner_parallel = Block(
        block_type="parallel",
        name="Inner fan-out",
        body=[_task("left"), _task("right")],
    )
    repeat_root = Block(
        block_type="repeat",
        name="Loop",
        repeat_mode="count",
        repeat_count=3,
        body=[inner_parallel],
    )
    created = store.create(TaskCardCreate(name="Repeat+Parallel", root=repeat_root))

    fetched = store.get(created.id)
    assert fetched is not None
    assert fetched.root.block_type == "repeat"
    assert len(fetched.root.body) == 1
    inner = fetched.root.body[0]
    assert inner.block_type == "parallel"
    assert len(inner.body) == 2
    assert [c.block_type for c in inner.body] == ["task", "task"]


def test_block_ids_assigned_for_parallel_subtree():
    """TaskCardStorage.create assigns ids to every block in the tree,
    so the block executor's id-keyed state tracking works for Parallels."""
    store = _storage()
    root = Block(
        block_type="parallel",
        name="top",
        body=[_task("a"), _task("b")],
    )
    created = store.create(TaskCardCreate(name="IDs", root=root))
    assert created.root.id  # parallel root gets an id
    assert all(c.id for c in created.root.body)  # children too
    # IDs are distinct
    all_ids = [created.root.id] + [c.id for c in created.root.body]
    assert len(set(all_ids)) == len(all_ids)
