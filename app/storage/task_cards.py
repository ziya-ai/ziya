"""
Task card storage — file-per-card under a project directory.

Follows the same pattern as SkillStorage: JSON files managed
through BaseStorage, which provides file locking and optional
ALE encryption.
"""

import time
import uuid
import logging
from pathlib import Path
from typing import Optional, List

from .base import BaseStorage
from ..models.task_card import Block, TaskCard, TaskCardCreate, TaskCardUpdate

logger = logging.getLogger(__name__)


def _assign_block_ids(block_dict: dict, prefix: str = "b") -> None:
    """Walk a block tree (as a plain dict) and assign IDs where missing."""
    if not isinstance(block_dict, dict):
        return
    if not block_dict.get("id"):
        block_dict["id"] = f"{prefix}-{uuid.uuid4().hex[:8]}"
    for child in block_dict.get("body", []) or []:
        _assign_block_ids(child, prefix)


class TaskCardStorage(BaseStorage[TaskCard]):
    """CRUD storage for task cards scoped to a project."""

    def __init__(self, project_dir: Path):
        self.cards_dir = project_dir / "task_cards"
        super().__init__(self.cards_dir)

    def _card_file(self, card_id: str) -> Path:
        return self.cards_dir / f"{card_id}.json"

    def get(self, card_id: str) -> Optional[TaskCard]:
        data = self._read_json(self._card_file(card_id))
        if data:
            return TaskCard(**data)
        return None

    def list(self, templates_only: bool = False) -> List[TaskCard]:
        cards: List[TaskCard] = []
        if self.cards_dir.exists():
            for card_file in self.cards_dir.glob("*.json"):
                data = self._read_json(card_file)
                if data:
                    try:
                        card = TaskCard(**data)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Skipping corrupt task card {card_file}: {e}")
                        continue
                    if templates_only and not card.is_template:
                        continue
                    cards.append(card)
        return sorted(cards, key=lambda c: c.updated_at, reverse=True)

    def create(self, data: TaskCardCreate) -> TaskCard:
        card_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        root_dict = data.root.model_dump()
        _assign_block_ids(root_dict)
        card = TaskCard(
            id=card_id,
            name=data.name,
            description=data.description,
            root=root_dict,
            tags=data.tags,
            is_template=data.is_template,
            source="custom",
            created_at=now,
            updated_at=now,
        )
        self._write_json(self._card_file(card_id), card.model_dump())
        return card

    def update(self, card_id: str, data: TaskCardUpdate) -> Optional[TaskCard]:
        card = self.get(card_id)
        if not card:
            return None
        update_dict = data.model_dump(exclude_unset=True)
        if "root" in update_dict and update_dict["root"]:
            _assign_block_ids(update_dict["root"])
            update_dict["root"] = Block(**update_dict["root"])
        for key, value in update_dict.items():
            setattr(card, key, value)
        card.updated_at = int(time.time() * 1000)
        self._write_json(self._card_file(card_id), card.model_dump())
        return card

    def delete(self, card_id: str) -> bool:
        card_file = self._card_file(card_id)
        if not card_file.exists():
            return False
        card_file.unlink()
        return True

    def duplicate(self, card_id: str, as_template: bool = False) -> Optional[TaskCard]:
        """Clone a card; optionally flip template flag."""
        card = self.get(card_id)
        if not card:
            return None
        return self.create(TaskCardCreate(
            name=f"{card.name} (copy)",
            description=card.description,
            root=card.root,
            tags=card.tags,
            is_template=as_template,
        ))

    def record_run(self, card_id: str) -> Optional[TaskCard]:
        """Bump run_count and last_run_at for a card."""
        card = self.get(card_id)
        if not card:
            return None
        card.last_run_at = int(time.time() * 1000)
        card.run_count += 1
        card.updated_at = card.last_run_at
        self._write_json(self._card_file(card_id), card.model_dump())
        return card
