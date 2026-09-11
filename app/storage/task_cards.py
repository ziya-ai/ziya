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

from .base import BaseStorage, contained_path
from ..models.task_card import Block, TaskCard, TaskCardCreate, TaskCardUpdate

logger = logging.getLogger(__name__)


def _assign_block_ids(block_dict: dict, prefix: str = "b", force: bool = False) -> None:
    """Walk a block tree (as a plain dict) and assign IDs where missing.

    ``force=True`` regenerates EVERY id, including ones already set.
    Needed when cloning a card: the source tree already has ids (none
    are missing), so the default fill-only behavior would leave a
    duplicate with byte-identical block ids to its original — a
    collision in ``TaskRunBlockState`` (both cards' runs write into the
    same keyed state) and, more seriously, in the scope-approval
    ledger, which keys an approval by block id alone: an approval
    signed for one card's block would silently authorize the other
    card's unchanged block too.
    """
    if not isinstance(block_dict, dict):
        return
    if force or not block_dict.get("id"):
        block_dict["id"] = f"{prefix}-{uuid.uuid4().hex[:8]}"
    for child in block_dict.get("body", []) or []:
        _assign_block_ids(child, prefix, force=force)


class TaskCardStorage(BaseStorage[TaskCard]):
    """CRUD storage for task cards scoped to a project."""

    def __init__(self, project_dir: Path):
        self.cards_dir = project_dir / "task_cards"
        super().__init__(self.cards_dir)

    def _card_file(self, card_id: str) -> Path:
        return contained_path(self.cards_dir, f"{card_id}.json")

    def get(self, card_id: str) -> Optional[TaskCard]:
        data = self._read_json(self._card_file(card_id))
        if data:
            return TaskCard(**data)
        return None

    def list(self, templates_only: bool = False,
             include_drafts: bool = False) -> List[TaskCard]:
        """List cards, newest first.

        Drafts are EXCLUDED unless asked for.  A draft was persisted only so
        its blocks have ids a signed approval can key on (the proposal
        panel's Sign path); surfacing it here would defeat the point of
        signing without saving to the deck.
        """
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
                    if not include_drafts and getattr(card, "draft", False):
                        # getattr, not attribute access: cards written before
                        # the field existed have no key, and are not drafts.
                        continue
                    cards.append(card)
        return sorted(cards, key=lambda c: c.updated_at, reverse=True)

    def create(
        self, data: TaskCardCreate, source: str = "custom",
        force_new_ids: bool = False,
    ) -> TaskCard:
        """Create a new card.

        ``force_new_ids=True`` regenerates every block id rather than
        only filling missing ones — see ``_assign_block_ids``.  Used by
        ``duplicate()``, whose incoming tree already carries the
        source card's ids.

        ``data.draft`` stores the card UNLISTED: it is persisted (so its
        blocks get ids an approval can key on) but hidden from every deck
        listing until an explicit save promotes it.
        """
        if data.draft:
            # Bounded, self-limiting GC.  Nothing in the UI deletes a draft
            # and none are visible in the deck, so the one thing that
            # creates them is what cleans them up.  Best-effort — a prune
            # failure must never fail the create.
            try:
                self.prune_stale_drafts()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Stale-draft prune failed: {e}")
        card_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        root_dict = data.root.model_dump()
        _assign_block_ids(root_dict, force=force_new_ids)
        card = TaskCard(
            id=card_id,
            name=data.name,
            description=data.description,
            root=root_dict,
            # Was omitted, so a card-level permissions baseline was silently
            # dropped on save (and on every duplicate(), which forwards it).
            # scope-status then graded a scope the run would not request.
            scope=data.scope,
            tags=data.tags,
            is_template=data.is_template,
            draft=data.draft,
            source=source,
            created_at=now,
            updated_at=now,
        )
        self._write_json(self._card_file(card_id), card.model_dump())
        return card

    def prune_stale_drafts(
        self, max_age_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> int:
        """Delete never-run drafts older than ``max_age_ms``; count removed.

        A draft that HAS run is kept regardless of age: its run records
        reference the card by id, and deleting it would leave that history
        unresolvable.
        """
        cutoff = int(time.time() * 1000) - max_age_ms
        removed = 0
        for card in self.list(include_drafts=True):
            if (getattr(card, "draft", False) and card.run_count == 0
                    and card.updated_at < cutoff):
                if self.delete(card.id):
                    removed += 1
        return removed

    def update(self, card_id: str, data: TaskCardUpdate) -> Optional[TaskCard]:
        card = self.get(card_id)
        if not card:
            return None
        # Capture the pre-edit structure + scope so a behaviour-changing
        # edit (block tree or permissions) is told apart from a
        # metadata-only one (name/description/tags/is_template).  Only the
        # former bumps ``version`` and thereby invalidates any prior
        # signing; a rename must not silently re-trigger a re-sign.
        def _dump(v):
            # ``update`` stores scope as a raw dict (it is NOT re-coerced
            # to a model the way ``root`` is below), so the compare must
            # tolerate a pydantic model or a plain dict on either side.
            return v.model_dump() if hasattr(v, "model_dump") else v
        old_root, old_scope = _dump(card.root), _dump(card.scope)
        update_dict = data.model_dump(exclude_unset=True)
        if "root" in update_dict and update_dict["root"]:
            _assign_block_ids(update_dict["root"])
            update_dict["root"] = Block(**update_dict["root"])
        for key, value in update_dict.items():
            # CWE-20 defense-in-depth: only ever set fields the update
            # request model (TaskCardUpdate) itself declares, regardless of
            # what extra-fields policy either model carries. Guards against
            # a future edit to either model (e.g. adding extra="allow")
            # silently becoming an arbitrary-attribute-write primitive here.
            if key in type(data).model_fields:
                setattr(card, key, value)
        # A monotonic bump on a real definition change; an identical
        # re-save (same tree, same scope) leaves the version — and any
        # signature keyed to it — untouched.
        if _dump(card.root) != old_root or _dump(card.scope) != old_scope:
            card.version = (card.version or 1) + 1
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
        """Clone a card; optionally flip template flag.

        The clone gets a FRESH id for every block in the tree, not just
        the card itself — see ``_assign_block_ids`` for why reusing the
        source's block ids is unsafe (state-file collision, and a
        scope approval leaking across cards).
        """
        card = self.get(card_id)
        if not card:
            return None
        return self.create(TaskCardCreate(
            name=f"{card.name} (copy)",
            description=card.description,
            root=card.root,
            scope=card.scope,
            tags=card.tags,
            is_template=as_template,
        ), force_new_ids=True)

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
