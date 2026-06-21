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
from .beads import count_open_beads_for_conversation
from ..models.work_item import count_open_work_items

# Per-file mtime cache for ChatStorage.list_summaries().
# Keyed by absolute path string; value is
#   (st_mtime, st_size, ChatSummary|None, group_id|None).
# Self-heals on any write because _write_json renames a temp file
# (new inode, new mtime).  Process-local — ChatStorage is constructed
# per-request so an instance attribute would be useless.  Summaries are
# tiny (~hundreds of bytes) so the cache is unbounded.
_summary_cache: dict = {}

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
    
    @staticmethod
    def strip_empty_assistant_messages(messages):
        """Drop assistant turns that carry no usable content.

        A Bedrock empty-200 completion (seen on opus4.8) gets persisted as a
        blank assistant turn.  Replaying such a turn produces another empty
        completion — a self-perpetuating loop.  We only ever drop *assistant*
        turns with empty/whitespace-only string content and no images; user,
        human, and system turns are never touched, and any assistant turn with
        real text or images is preserved.
        """
        cleaned = []
        for m in messages:
            role = (m.get("role") if isinstance(m, dict) else getattr(m, "role", "")) or ""
            content = (m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or ""
            images = (m.get("images") if isinstance(m, dict) else getattr(m, "images", None))
            if role == "assistant" and not (isinstance(content, str) and content.strip()) and not images:
                continue
            cleaned.append(m)
        return cleaned

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
        n_files = 0
        t_read_total = 0.0
        t_retention_total = 0.0
        t_stat_total = 0.0
        n_hit = 0
        n_miss = 0
        # (group_id, summary) pairs collected in-loop.  Group filter and
        # sort applied once at the end so the hot path stays branch-free
        # for the common case (no group_id).
        built: List[tuple] = []
        files = list(self.chats_dir.glob("*.json"))
        t_glob = time.perf_counter() - t0
        for chat_file in files:
            if chat_file.name.startswith('_'):
                continue
            if chat_file.name.endswith('.bindings.json'):
                continue
            n_files += 1

            try:
                t_s = time.perf_counter()
                st = chat_file.stat()
                t_stat_total += time.perf_counter() - t_s
            except OSError:
                continue

            path_str = str(chat_file)
            cached = _summary_cache.get(path_str)
            if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
                n_hit += 1
                cached_summary = cached[2]
                cached_group_id = cached[3]
                if cached_summary is None:
                    continue
                # Retention is wall-clock-driven; re-check from cached lastActiveAt
                # so an entry cached as "live" earlier gets evicted if expired now.
                t_e = time.perf_counter()
                try:
                    if self.enforcer.is_expired(cached_summary.lastActiveAt / 1000.0, "conversation_data"):
                        logger.info(f"Chat {cached_summary.id} expired per retention policy, removing")
                        self.delete(cached_summary.id)
                        _summary_cache.pop(path_str, None)
                        t_retention_total += time.perf_counter() - t_e
                        continue
                except Exception as e:
                    logger.debug("Retention check failed for cached entry: %s", e)
                t_retention_total += time.perf_counter() - t_e
                built.append((cached_group_id, cached_summary))
                continue
            n_miss += 1

            t_r = time.perf_counter()
            data = self._read_json(chat_file)
            t_read_total += time.perf_counter() - t_r
            if not data:
                _summary_cache[path_str] = (st.st_mtime, st.st_size, None, None)
                continue

            # Expiry check — uses lastActiveAt only, no message validation.
            last_active = data.get('lastActiveAt') or 0
            t_e = time.perf_counter()
            try:
                if self.enforcer.is_expired(last_active / 1000.0, "conversation_data"):
                    chat_id = data.get('id')
                    if chat_id:
                        logger.info(f"Chat {chat_id} expired per retention policy, removing")
                        self.delete(chat_id)
                    # Don't cache — file is being deleted; next glob won't see it.
                    t_retention_total += time.perf_counter() - t_e
                    continue
            except Exception as e:
                # Retention failure is non-fatal; keep the chat rather than dropping it.
                logger.debug("Retention enforcement failed, keeping chat: %s", e)
            t_retention_total += time.perf_counter() - t_e

            chat_group_id = data.get('groupId')
            messages = data.get('messages') or []
            version = data.get('_version') or data.get('lastActiveAt')
            delegate_meta = data.get('delegateMeta')
            open_beads = count_open_beads_for_conversation(data, data.get('id'))
            open_work_items = count_open_work_items(data.get('_work_items'))
            summary = ChatSummary(
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
                openBeadCount=open_beads,
                openWorkItemCount=open_work_items,
                **({'_version': version} if version else {})
            )
            _summary_cache[path_str] = (st.st_mtime, st.st_size, summary, chat_group_id)
            built.append((chat_group_id, summary))

        t_after_loop = time.perf_counter()
        if group_id is not None:
            if group_id == "ungrouped":
                built = [(g, s) for (g, s) in built if g is None]
            else:
                built = [(g, s) for (g, s) in built if g == group_id]
        built.sort(key=lambda gs: gs[1].lastActiveAt, reverse=True)
        summaries = [s for (_g, s) in built]
        n_kept = len(summaries)
        t_sort = time.perf_counter() - t_after_loop
        t_total = time.perf_counter() - t0
        logger.debug(
            f"list_summaries: total={t_total*1000:.0f}ms "
            f"glob={t_glob*1000:.0f}ms "
            f"stat={t_stat_total*1000:.0f}ms "
            f"read={t_read_total*1000:.0f}ms "
            f"retention={t_retention_total*1000:.0f}ms "
            f"sort+filter={t_sort*1000:.0f}ms "
            f"files={n_files} kept={n_kept} "
            f"cache={n_hit}H/{n_miss}M"
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
        # Update cross-project chat index so subsequent bulk-get calls
        # can locate this chat without scanning every project's chats dir.
        try:
            from app.storage import chat_index
            chat_index.on_chat_written(chat_id, self.chats_dir.parent.name)
        except Exception as e:
            logger.debug("chat_index.on_chat_written failed: %s", e)
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
        # Retire any standalone fallback bead file for this conversation so
        # it doesn't outlive the chat.  Runs before the existence check:
        # a fallback file can exist for a chat that never synced to disk
        # (pre-sync beads), and the delete request should clear it either way.
        try:
            from app.storage.beads import remove_fallback_beads
            remove_fallback_beads(chat_id)
        except Exception as e:
            logger.debug("Bead fallback cleanup failed for %s: %s", chat_id, e)
        chat_file = self._chat_file(chat_id)
        if not chat_file.exists():
            return False
        chat_file.unlink()
        # Drop from the cross-project chat index.  Stale entries would
        # self-heal on next lookup, but eager removal saves a failed
        # file-stat per stale lookup.
        try:
            from app.storage import chat_index
            chat_index.on_chat_deleted(chat_id)
        except Exception as e:
            logger.debug("chat_index.on_chat_deleted failed: %s", e)
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
