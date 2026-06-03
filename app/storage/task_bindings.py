"""
Task binding storage — per-chat list of bindings.

Layout: chats/{chat_id}.bindings.json is a JSON array of TaskBinding
records for that chat.  Most chats have 0 or 1 bindings; a
fuzz-testing heavy chat might have 5-10.  A single file per chat
keeps read/write atomic and mirrors the existing chat file layout.

This does NOT inherit from BaseStorage — BaseStorage is one-entity-
per-file whereas a binding file is a list.  We reuse BaseStorage's
read/write JSON helpers by subclassing and ignoring the CRUD methods.
"""

import json
import time
import uuid
import logging
from pathlib import Path
from typing import List, Optional

from .base import BaseStorage
from ..models.task_binding import TaskBinding

logger = logging.getLogger(__name__)


class TaskBindingStorage(BaseStorage[TaskBinding]):
    """Per-chat list storage for task bindings.

    Files live alongside chat files: chats/{chat_id}.bindings.json
    """

    def __init__(self, project_dir: Path):
        self.chats_dir = project_dir / "chats"
        super().__init__(self.chats_dir)

    def _bindings_file(self, chat_id: str) -> Path:
        return self.chats_dir / f"{chat_id}.bindings.json"


    def _read_json_list(self, filepath):
        """Read a JSON array from filepath, handling encryption transparently."""
        if not filepath.exists():
            return []
        try:
            raw = filepath.read_bytes()
            if not raw:
                return []
            try:
                from app.utils.encryption import is_encrypted, get_encryptor
                if is_encrypted(raw):
                    raw = get_encryptor().decrypt(raw)
            except Exception:
                pass
            result = json.loads(raw)
            if not isinstance(result, list):
                logger.error(f"Expected JSON array in {filepath}, got {type(result).__name__}")
                return []
            return result
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")
            return []

    def list_for_chat(self, chat_id: str) -> List[TaskBinding]:
        path = self._bindings_file(chat_id)
        data = self._read_json_list(path)
        out: List[TaskBinding] = []
        for row in data:
            try:
                out.append(TaskBinding(**row))
            except (ValueError, TypeError) as e:
                logger.warning(f"Skipping corrupt task binding in {path}: {e}")
        return out

    def get(self, chat_id: str, binding_id: str) -> Optional[TaskBinding]:
        for b in self.list_for_chat(chat_id):
            if b.id == binding_id:
                return b
        return None

    def create(
        self, chat_id: str, card_id: str, run_id: str,
        anchor_message_id: Optional[str] = None,
    ) -> TaskBinding:
        binding = TaskBinding(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            card_id=card_id,
            run_id=run_id,
            anchor_message_id=anchor_message_id,
            created_at=int(time.time() * 1000),
        )
        existing = self.list_for_chat(chat_id)
        existing.append(binding)
        self._write_json(
            self._bindings_file(chat_id),
            [b.model_dump() for b in existing],
        )
        return binding

    def delete(self, chat_id: str, binding_id: str) -> bool:
        existing = self.list_for_chat(chat_id)
        remaining = [b for b in existing if b.id != binding_id]
        if len(remaining) == len(existing):
            return False
        if not remaining:
            # Remove the file entirely when no bindings remain.
            path = self._bindings_file(chat_id)
            if path.exists():
                path.unlink()
        else:
            self._write_json(
                self._bindings_file(chat_id),
                [b.model_dump() for b in remaining],
            )
        return True

    def update_run_id(self, chat_id: str, binding_id: str, new_run_id: str) -> bool:
        """Point an existing binding at a new run (used by goal resume).

        Returns True if the binding was found and updated, False otherwise.
        """
        existing = self.list_for_chat(chat_id)
        found = False
        for b in existing:
            if b.id == binding_id:
                b.run_id = new_run_id
                found = True
                break
        if not found:
            return False
        self._write_json(
            self._bindings_file(chat_id),
            [b.model_dump() for b in existing],
        )
        return True

    # BaseStorage abstract methods we don't use — task bindings are
    # always accessed via their chat.  Keep stubs to satisfy the ABC.
    def list(self) -> List[TaskBinding]:  # pragma: no cover
        raise NotImplementedError("use list_for_chat(chat_id) instead")

    def update(self, _id, _data) -> Optional[TaskBinding]:  # pragma: no cover
        raise NotImplementedError("bindings are immutable pointers")
