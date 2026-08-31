"""Block-id stability across TaskCardStorage.update — the invariant the
signing flow depends on.

Approvals in the signed scope-approval store are keyed by BLOCK ID
(app/utils/scope_approvals._record_path), so any write that renames a
block silently invalidates the signature the operator just obtained: the
gate reads "no_record" and clamps the task to the floor.

``_assign_block_ids`` fills ids only where missing, which makes the
contract:

  * a root that CARRIES ids keeps them  → a signature survives the write
  * a root with ids MISSING gets new ones → a signature is orphaned

The second half is not a bug in storage (a tree with no ids has to get
some), but it is the trap that broke the chat proposal panel: it kept
re-sending the id-less tree parsed out of the model's message on every
Save/Start, so each click renamed every block. These tests pin both
halves so a caller can be reasoned about, and so a future change to the
fill behaviour surfaces here rather than as "signing does nothing".
"""

import tempfile
from pathlib import Path

from app.models.task_card import TaskCardCreate, TaskCardUpdate
from app.storage.task_cards import TaskCardStorage


ESCALATING_TASK = {
    "block_type": "task",
    "name": "push",
    "instructions": "do the thing",
    "scope": {"shell_commands": ["git push"], "paths": [], "tools": [], "skills": []},
}


def _leaf_ids(card) -> list:
    out = []

    def walk(b):
        if getattr(b, "block_type", None) == "task":
            out.append(b.id)
        for c in getattr(b, "body", None) or []:
            walk(c)

    walk(card.root)
    return out


def _storage() -> TaskCardStorage:
    return TaskCardStorage(Path(tempfile.mkdtemp()))


def test_update_preserves_ids_when_the_root_carries_them():
    """The path the proposal panel must use: echo the stored tree back."""
    st = _storage()
    card = st.create(TaskCardCreate(
        name="c", root={"block_type": "group", "body": [dict(ESCALATING_TASK)]}))
    before = _leaf_ids(card)
    assert before and all(before), "create must assign leaf ids"

    # Round-trip the STORED root (ids present), which is what the server
    # returns from create/update.
    updated = st.update(card.id, TaskCardUpdate(root=card.root.model_dump()))
    assert _leaf_ids(updated) == before

    # And again — a second save must not drift either.
    updated2 = st.update(card.id, TaskCardUpdate(root=updated.root.model_dump()))
    assert _leaf_ids(updated2) == before


def test_update_with_an_id_less_root_renames_every_block():
    """The trap: re-sending the model-authored tree mints new ids.

    Not asserting desirable behaviour — asserting the hazard, so any
    caller relying on id stability is provably wrong to send this shape.
    """
    st = _storage()
    spec_root = {"block_type": "group", "body": [dict(ESCALATING_TASK)]}
    card = st.create(TaskCardCreate(name="c", root=spec_root))
    before = _leaf_ids(card)

    reupdated = st.update(card.id, TaskCardUpdate(root=spec_root))
    after = _leaf_ids(reupdated)

    assert len(after) == len(before)
    assert after != before, (
        "an id-less root must be seen to churn ids — if this ever starts "
        "preserving them, the frontend workaround is redundant, not wrong")


def test_ids_survive_an_edit_that_keeps_them():
    """An edit to instructions must not disturb the ids around it."""
    st = _storage()
    card = st.create(TaskCardCreate(
        name="c", root={"block_type": "group", "body": [dict(ESCALATING_TASK)]}))
    before = _leaf_ids(card)

    root = card.root.model_dump()
    root["body"][0]["instructions"] = "edited"
    updated = st.update(card.id, TaskCardUpdate(root=root))

    assert _leaf_ids(updated) == before
    assert updated.root.body[0].instructions == "edited"
