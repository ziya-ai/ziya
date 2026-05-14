"""
Contract test for message-ID anchoring behavior (design/task-cards.md).

These tests document the backend contract that the frontend's
message-ID-anchoring feature relies on: TaskBinding.anchor_message_id
is an opaque string that the backend does not parse or validate
against any message store.  The frontend is solely responsible for
keeping message IDs stable and using them as anchors.

If the frontend assigns message IDs at creation time, those IDs can be
passed through as anchor_message_id and later used to attach inline
tiles to specific messages.  This test pins down that:

1. The storage layer accepts any string as an anchor (including
   UUID-like strings), not just null.
2. Anchored bindings round-trip through storage unchanged.
3. The list endpoint returns bindings with their anchors intact so the
   frontend's useTaskBindings hook can build its per-anchor map.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from app.storage.task_bindings import TaskBindingStorage
from app.storage.task_cards import TaskCardStorage
from app.models.task_card import Block, TaskCardCreate


@pytest.fixture
def storage_pair():
    """Fresh per-test storage directories for a single project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        # Ensure the subdirs the storage classes expect exist
        (project_dir / "chats").mkdir(exist_ok=True)
        (project_dir / "task_cards").mkdir(exist_ok=True)

        cards = TaskCardStorage(project_dir)
        bindings = TaskBindingStorage(project_dir)
        yield cards, bindings


def _make_card(cards: TaskCardStorage, name: str = "test-card") -> str:
    card = cards.create(
        TaskCardCreate(
            name=name,
            description="",
            root=Block(block_type="task", name="t", instructions="noop"),
            tags=[],
        ),
    )
    return card.id


class TestAnchorMessageIdRoundTrip:
    """Core contract: anchors are opaque strings the backend doesn't interpret."""

    def test_storage_accepts_uuid_like_anchor(self, storage_pair):
        """Storage layer accepts and persists a UUID-shaped anchor string."""
        cards, bindings = storage_pair
        card_id = _make_card(cards)
        chat_id = "chat-1"

        anchor = str(uuid.uuid4())
        binding = bindings.create(
            chat_id=chat_id,
            card_id=card_id,
            run_id="run-" + str(uuid.uuid4())[:8],
            anchor_message_id=anchor,
        )
        assert binding.anchor_message_id == anchor

        # Round-trip through persistence
        loaded = bindings.list_for_chat(chat_id)
        assert len(loaded) == 1
        assert loaded[0].anchor_message_id == anchor

    def test_storage_accepts_null_anchor(self, storage_pair):
        """Null anchors remain supported for the tail-bucket fallback path."""
        cards, bindings = storage_pair
        card_id = _make_card(cards)
        chat_id = "chat-2"

        binding = bindings.create(
            chat_id=chat_id,
            card_id=card_id,
            run_id="run-null",
            anchor_message_id=None,
        )
        assert binding.anchor_message_id is None

        loaded = bindings.list_for_chat(chat_id)
        assert len(loaded) == 1
        assert loaded[0].anchor_message_id is None

    def test_storage_accepts_arbitrary_string_anchor(self, storage_pair):
        """Backend does not validate anchor format — it's opaque."""
        cards, bindings = storage_pair
        card_id = _make_card(cards)
        chat_id = "chat-3"

        # The frontend's stability contract is its own problem; the
        # backend just stores whatever string it's given.
        for i, anchor in enumerate((
            "msg-123",
            "weird:string/with.chars",
            "x" * 200,
        )):
            binding = bindings.create(
                chat_id=chat_id,
                card_id=card_id,
                run_id=f"run-{i}",
                anchor_message_id=anchor,
            )
            assert binding.anchor_message_id == anchor

        loaded = bindings.list_for_chat(chat_id)
        loaded_anchors = {b.anchor_message_id for b in loaded}
        assert loaded_anchors == {"msg-123", "weird:string/with.chars", "x" * 200}

    def test_multiple_bindings_different_anchors_coexist(self, storage_pair):
        """Anchored and unanchored bindings coexist on the same chat."""
        cards, bindings = storage_pair
        card_id = _make_card(cards)
        chat_id = "chat-4"

        anchor_a = str(uuid.uuid4())
        anchor_b = str(uuid.uuid4())

        bindings.create(
            chat_id=chat_id, card_id=card_id, run_id="run-a",
            anchor_message_id=anchor_a,
        )
        bindings.create(
            chat_id=chat_id, card_id=card_id, run_id="run-b",
            anchor_message_id=anchor_b,
        )
        bindings.create(
            chat_id=chat_id, card_id=card_id, run_id="run-tail",
            anchor_message_id=None,
        )

        loaded = bindings.list_for_chat(chat_id)
        assert len(loaded) == 3
        anchor_set = {b.anchor_message_id for b in loaded}
        assert anchor_set == {anchor_a, anchor_b, None}

    def test_frontend_grouping_simulation(self, storage_pair):
        """
        Simulate what useTaskBindings does: group bindings into a
        map keyed by anchor_message_id, with a '__no_anchor__' bucket
        for nulls.  This is the data shape the inline-tile renderer
        consumes.
        """
        cards, bindings = storage_pair
        card_id = _make_card(cards)
        chat_id = "chat-5"

        msg1, msg2 = str(uuid.uuid4()), str(uuid.uuid4())

        bindings.create(chat_id=chat_id, card_id=card_id,
                        run_id="r1", anchor_message_id=msg1)
        bindings.create(chat_id=chat_id, card_id=card_id,
                        run_id="r2", anchor_message_id=msg1)  # 2nd on same msg
        bindings.create(chat_id=chat_id, card_id=card_id,
                        run_id="r3", anchor_message_id=msg2)
        bindings.create(chat_id=chat_id, card_id=card_id,
                        run_id="r4", anchor_message_id=None)

        loaded = bindings.list_for_chat(chat_id)

        # Replicate the frontend's grouping
        grouped: dict[str, list] = {}
        for b in loaded:
            key = b.anchor_message_id if b.anchor_message_id else "__no_anchor__"
            grouped.setdefault(key, []).append(b)

        assert len(grouped[msg1]) == 2   # two tiles on the same message
        assert len(grouped[msg2]) == 1
        assert len(grouped["__no_anchor__"]) == 1
