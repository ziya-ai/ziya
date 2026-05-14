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
        last_active_epoch = chat.lastActiveAt / 1000.0
        return self.enforcer.is_expired(last_active_epoch, "conversation_data")
    
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
            if chat_file.name.endswith('.bindings.json'):
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
            if chat_file.name.endswith('.bindings.json'):
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
        """List chats without messages for performance.

        Reads each chat file as a raw dict and pulls only the summary fields,
        skipping Pydantic validation of the full ``messages`` array.  On a
        project with several hundred chats the old path (``self.list()`` →
        ``Chat(**data)`` for every file) was the dominant backend cost on
        ``GET /api/v1/projects/{pid}/chats`` (~3 s for 850 chats).  Skipping
        message validation drops it by an order of magnitude because the
        ``Chat`` model validates every entry in ``messages: List[Message]``.
        """
        t0 = time.perf_counter()
        summaries = []
        if not self.chats_dir.exists():
            return summaries

        raw_entries: List[dict] = []
        n_files = 0
        n_kept = 0
        t_read_total = 0.0
        t_retention_total = 0.0
        files = list(self.chats_dir.glob("*.json"))
        t_glob = time.perf_counter() - t0
        for chat_file in files:
            if chat_file.name.startswith('_'):
                continue
            if chat_file.name.endswith('.bindings.json'):
                continue
            n_files += 1

            t_r = time.perf_counter()
            data = self._read_json(chat_file)
            t_read_total += time.perf_counter() - t_r
            if not data:
                continue

            # Expiry check — uses lastActiveAt only, no message validation needed.
            last_active = data.get('lastActiveAt') or 0
            t_e = time.perf_counter()
            try:
                if self.enforcer.is_expired(last_active / 1000.0, "conversation_data"):
                    chat_id = data.get('id')
                    if chat_id:
                        logger.info(f"Chat {chat_id} expired per retention policy, removing")
                        self.delete(chat_id)
                    t_retention_total += time.perf_counter() - t_e
                    continue
            except Exception:
                # If retention enforcement fails, keep the chat — better than dropping it.
                pass
            t_retention_total += time.perf_counter() - t_e

            # Filter by group if requested
            chat_group_id = data.get('groupId')
            if group_id is not None:
                if group_id == "ungrouped" and chat_group_id is None:
                    raw_entries.append(data)
                elif chat_group_id == group_id:
                    raw_entries.append(data)
            else:
                raw_entries.append(data)

        t_after_loop = time.perf_counter()
        # Sort by lastActiveAt descending (matches self.list()'s ordering)
        raw_entries.sort(key=lambda d: d.get('lastActiveAt') or 0, reverse=True)
        t_sort = time.perf_counter() - t_after_loop

        t_build_start = time.perf_counter()
        for data in raw_entries:
            messages = data.get('messages') or []
            chat_group_id = data.get('groupId')
            version = data.get('_version') or data.get('lastActiveAt')
            delegate_meta = data.get('delegateMeta')
            summaries.append(ChatSummary(
                id=data['id'],
                title=data.get('title') or '',
                groupId=chat_group_id,
                contextIds=data.get('contextIds') or [],
                skillIds=data.get('skillIds') or [],
                additionalFiles=data.get('additionalFiles') or [],
                messageCount=len(messages) if isinstance(messages, list) else 0,
                createdAt=data.get('createdAt') or 0,
                lastActiveAt=data.get('lastActiveAt') or 0,
                delegateMeta=delegate_meta,
                **({'_version': version} if version else {})
            ))
            n_kept += 1
        t_build = time.perf_counter() - t_build_start
        t_total = time.perf_counter() - t0
        logger.info(
            f"list_summaries: total={t_total*1000:.0f}ms "
            f"glob={t_glob*1000:.0f}ms "
            f"read={t_read_total*1000:.0f}ms "
            f"retention={t_retention_total*1000:.0f}ms "
            f"sort={t_sort*1000:.0f}ms "
            f"build={t_build*1000:.0f}ms "
            f"files={n_files} kept={n_kept}"
        )
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
        
        d = chat.model_dump()
        d["_version"] = now
        self._write_json(self._chat_file(chat_id), d)
        return chat
    
    def update(self, chat_id: str, data: ChatUpdate) -> Optional[Chat]:
        chat = self.get(chat_id)
        if not chat:
            return None
        
        update_dict = data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(chat, key, value)
        chat.lastActiveAt = int(time.time() * 1000)
        d = chat.model_dump()
        d["_version"] = int(time.time() * 1000)
        self._write_json(self._chat_file(chat_id), d)
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
        d = chat.model_dump()
        d["_version"] = int(time.time() * 1000)
        self._write_json(self._chat_file(chat_id), d)
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
