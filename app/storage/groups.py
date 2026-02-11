"""
Chat group storage implementation.
"""
from pathlib import Path
from typing import Optional, List
import uuid
import time

from ..models.group import ChatGroup, ChatGroupCreate, ChatGroupUpdate, ChatGroupsFile

class ChatGroupStorage:
    """Storage for chat groups within a project."""
    
    def __init__(self, project_dir: Path):
        self.chats_dir = project_dir / "chats"
        self.groups_file = self.chats_dir / "_groups.json"
        self.chats_dir.mkdir(parents=True, exist_ok=True)
    
    def _read_groups_file(self) -> ChatGroupsFile:
        """Read the groups file, creating if necessary."""
        if not self.groups_file.exists():
            return ChatGroupsFile(version=1, groups=[])
        
        try:
            import json
            with open(self.groups_file, 'r') as f:
                data = json.load(f)
                return ChatGroupsFile(**data)
        except Exception:
            return ChatGroupsFile(version=1, groups=[])
    
    def _write_groups_file(self, groups_file: ChatGroupsFile) -> None:
        """Write the groups file atomically."""
        import json
        temp_path = self.groups_file.with_suffix('.tmp')
        with open(temp_path, 'w') as f:
            json.dump(groups_file.model_dump(), f, indent=2)
        temp_path.rename(self.groups_file)
    
    def get(self, group_id: str) -> Optional[ChatGroup]:
        groups_file = self._read_groups_file()
        for group in groups_file.groups:
            if group.id == group_id:
                return group
        return None
    
    def list(self) -> List[ChatGroup]:
        groups_file = self._read_groups_file()
        return sorted(groups_file.groups, key=lambda g: g.order)
    
    def create(self, data: ChatGroupCreate) -> ChatGroup:
        groups_file = self._read_groups_file()
        
        group_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        
        # Assign next order
        max_order = max([g.order for g in groups_file.groups], default=-1)
        
        group = ChatGroup(
            id=group_id,
            name=data.name,
            defaultContextIds=data.defaultContextIds or [],
            defaultSkillIds=data.defaultSkillIds or [],
            collapsed=False,
            order=max_order + 1,
            createdAt=now
        )
        
        groups_file.groups.append(group)
        self._write_groups_file(groups_file)
        return group
    
    def update(self, group_id: str, data: ChatGroupUpdate) -> Optional[ChatGroup]:
        groups_file = self._read_groups_file()
        
        for i, group in enumerate(groups_file.groups):
            if group.id == group_id:
                update_dict = data.model_dump(exclude_unset=True)
                for key, value in update_dict.items():
                    setattr(group, key, value)
                groups_file.groups[i] = group
                self._write_groups_file(groups_file)
                return group
        
        return None
    
    def delete(self, group_id: str) -> bool:
        groups_file = self._read_groups_file()
        original_count = len(groups_file.groups)
        groups_file.groups = [g for g in groups_file.groups if g.id != group_id]
        
        if len(groups_file.groups) < original_count:
            self._write_groups_file(groups_file)
            return True
        return False
    
    def reorder(self, ordered_ids: List[str]) -> List[ChatGroup]:
        """Reorder groups according to the provided list of IDs."""
        groups_file = self._read_groups_file()
        
        # Create a mapping of id -> group
        group_map = {g.id: g for g in groups_file.groups}
        
        # Rebuild list in new order
        reordered = []
        for i, group_id in enumerate(ordered_ids):
            if group_id in group_map:
                group = group_map[group_id]
                group.order = i
                reordered.append(group)
        
        groups_file.groups = reordered
        self._write_groups_file(groups_file)
        return reordered
