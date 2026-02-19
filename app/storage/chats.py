"""
Chat storage implementation.
"""
from pathlib import Path
from typing import Optional, List
import uuid
import time

from app.utils.logging_utils import logger

from .base import BaseStorage
from ..models.chat import Chat, ChatCreate, ChatUpdate, ChatSummary, Message

class ChatStorage(BaseStorage[Chat]):
    """Storage for chats within a project."""
    
    def __init__(self, project_dir: Path):
        self.chats_dir = project_dir / "chats"
        super().__init__(self.chats_dir)
        self._enforcer = None

    @property
    def enforcer(self):
        """Lazy-load the retention enforcer to avoid import cycles."""
        if self._enforcer is None:
            from app.plugins.data_retention import get_retention_enforcer
            self._enforcer = get_retention_enforcer()
        return self._enforcer

    def _is_chat_expired(self, chat: Chat) -> bool:
        """Check whether a chat has exceeded the retention policy TTL."""
        # Use lastActiveAt (milliseconds) — convert to epoch seconds
        created_epoch = chat.createdAt / 1000.0
        return self.enforcer.is_expired(created_epoch, "conversation_data")
    
    def _chat_file(self, chat_id: str) -> Path:
        return self.chats_dir / f"{chat_id}.json"
    
    def get(self, chat_id: str) -> Optional[Chat]:
        data = self._read_json(self._chat_file(chat_id))
        if not data:
            return None
        chat = Chat(**data)
        if self._is_chat_expired(chat):
            logger.info(f"Chat {chat_id} expired per retention policy, removing")
            self.delete(chat_id)
            return None
        return chat
    
    def list(self, group_id: Optional[str] = None) -> List[Chat]:
        """List chats, optionally filtered by group."""
        chats = []
        if not self.chats_dir.exists():
            return chats
        
        for chat_file in self.chats_dir.glob("*.json"):
            # Skip _groups.json
            if chat_file.name.startswith('_'):
                continue
            
            data = self._read_json(chat_file)
            if data:
                chat = Chat(**data)
                if self._is_chat_expired(chat):
                    logger.info(f"Chat {chat.id} expired per retention policy, removing")
                    self.delete(chat.id)
                    continue
                # Filter by group if specified
                if group_id is not None:
                    if group_id == "ungrouped" and chat.groupId is None:
                        chats.append(chat)
                    elif chat.groupId == group_id:
                        chats.append(chat)
                else:
                    chats.append(chat)
        
        return sorted(chats, key=lambda c: c.lastActiveAt, reverse=True)

    def purge_expired(self) -> int:
        """
        Remove all chats that have exceeded the retention policy.

        Returns the number of chats purged.
        """
        purged = 0
        if not self.chats_dir.exists():
            return purged
        for chat_file in self.chats_dir.glob("*.json"):
            if chat_file.name.startswith('_'):
                continue
            data = self._read_json(chat_file)
            if data:
                chat = Chat(**data)
                if self._is_chat_expired(chat):
                    logger.info(f"Purging expired chat {chat.id}")
                    chat_file.unlink()
                    purged += 1
        return purged
    
    def list_summaries(self, group_id: Optional[str] = None) -> List[ChatSummary]:
        """List chats without messages for performance."""
        summaries = []
        for chat in self.list(group_id):
            # Extract _version from extra fields for polling comparisons
            chat_extra = chat.model_dump()
            version = chat_extra.get('_version')
            summaries.append(ChatSummary(
                id=chat.id,
                title=chat.title,
                groupId=chat.groupId,
                contextIds=chat.contextIds,
                skillIds=chat.skillIds,
                additionalFiles=chat.additionalFiles,
                messageCount=len(chat.messages),
                createdAt=chat.createdAt,
                lastActiveAt=chat.lastActiveAt,
                **({'_version': version} if version else {})
            ))
        return summaries
    
    def create(self, data: ChatCreate, default_context_ids: Optional[List[str]] = None, default_skill_ids: Optional[List[str]] = None) -> Chat:
        chat_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        
        # Use provided contexts or defaults
        context_ids = data.contextIds if data.contextIds is not None else (default_context_ids or [])
        skill_ids = data.skillIds if data.skillIds is not None else (default_skill_ids or [])
        
        chat = Chat(
            id=chat_id,
            title=data.title or "New conversation",
            groupId=data.groupId,
            contextIds=context_ids,
            skillIds=skill_ids,
            additionalFiles=data.additionalFiles or [],
            additionalPrompt=data.additionalPrompt,
            messages=[],
            createdAt=now,
            lastActiveAt=now
        )
        
        self._write_json(self._chat_file(chat_id), chat.model_dump())
        return chat
    
    def update(self, chat_id: str, data: ChatUpdate) -> Optional[Chat]:
        chat = self.get(chat_id)
        if not chat:
            return None
        
        update_dict = data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(chat, key, value)
        
        chat.lastActiveAt = int(time.time() * 1000)
        self._write_json(self._chat_file(chat_id), chat.model_dump())
        return chat
    
    def delete(self, chat_id: str) -> bool:
        chat_file = self._chat_file(chat_id)
        if not chat_file.exists():
            return False
        chat_file.unlink()
        return True
    
    def add_message(self, chat_id: str, message: Message) -> Optional[Chat]:
        """Add a message to a chat."""
        chat = self.get(chat_id)
        if not chat:
            return None
        
        chat.messages.append(message)
        chat.lastActiveAt = int(time.time() * 1000)
        self._write_json(self._chat_file(chat_id), chat.model_dump())
        return chat
    
    def remove_context_from_all_chats(self, context_id: str) -> None:
        """Remove a context from all chats that reference it."""
        for chat in self.list():
            if context_id in chat.contextIds:
                chat.contextIds.remove(context_id)
                self._write_json(self._chat_file(chat.id), chat.model_dump())
    
    def remove_skill_from_all_chats(self, skill_id: str) -> None:
        """Remove a skill from all chats that reference it."""
        for chat in self.list():
            if skill_id in chat.skillIds:
                chat.skillIds.remove(skill_id)
                self._write_json(self._chat_file(chat.id), chat.model_dump())
    
    def touch(self, chat_id: str) -> None:
        """Update lastActiveAt timestamp."""
        chat = self.get(chat_id)
        if chat:
            chat.lastActiveAt = int(time.time() * 1000)
            self._write_json(self._chat_file(chat_id), chat.model_dump())
