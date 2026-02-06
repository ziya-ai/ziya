"""
Context storage implementation.
"""
from pathlib import Path
from typing import Optional, List
import uuid
import time

from .base import BaseStorage
from ..models.context import Context, ContextCreate, ContextUpdate
from ..services.token_service import TokenService
from ..services.color_service import generate_color

class ContextStorage(BaseStorage[Context]):
    """Storage for contexts within a project."""
    
    def __init__(self, project_dir: Path, token_service: TokenService):
        self.contexts_dir = project_dir / "contexts"
        self.token_service = token_service
        self.project_path: Optional[str] = None
        super().__init__(self.contexts_dir)
    
    def set_project_path(self, project_path: str) -> None:
        """Set the project path for token calculations."""
        self.project_path = project_path
    
    def _context_file(self, context_id: str) -> Path:
        return self.contexts_dir / f"{context_id}.json"
    
    def get(self, context_id: str) -> Optional[Context]:
        data = self._read_json(self._context_file(context_id))
        return Context(**data) if data else None
    
    def list(self) -> List[Context]:
        contexts = []
        if not self.contexts_dir.exists():
            return contexts
        for context_file in self.contexts_dir.glob("*.json"):
            data = self._read_json(context_file)
            if data:
                contexts.append(Context(**data))
        return sorted(contexts, key=lambda c: c.lastUsedAt, reverse=True)
    
    def create(self, data: ContextCreate) -> Context:
        context_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        
        # Calculate token count
        token_count = 0
        if self.project_path:
            token_count = self.token_service.count_tokens_for_files(
                self.project_path, 
                data.files
            )
        
        context = Context(
            id=context_id,
            name=data.name,
            files=data.files,
            color=generate_color(data.name),
            tokenCount=token_count,
            tokenCountUpdatedAt=now,
            createdAt=now,
            lastUsedAt=now
        )
        
        self._write_json(self._context_file(context_id), context.dict())
        return context
    
    def update(self, context_id: str, data: ContextUpdate) -> Optional[Context]:
        context = self.get(context_id)
        if not context:
            return None
        
        update_dict = data.dict(exclude_unset=True)
        
        # Recalculate tokens if files changed
        if 'files' in update_dict and self.project_path:
            update_dict['tokenCount'] = self.token_service.count_tokens_for_files(
                self.project_path,
                update_dict['files']
            )
            update_dict['tokenCountUpdatedAt'] = int(time.time() * 1000)
        
        # Regenerate color if name changed
        if 'name' in update_dict:
            update_dict['color'] = generate_color(update_dict['name'])
        
        for key, value in update_dict.items():
            setattr(context, key, value)
        
        context.lastUsedAt = int(time.time() * 1000)
        self._write_json(self._context_file(context_id), context.dict())
        return context
    
    def delete(self, context_id: str) -> bool:
        context_file = self._context_file(context_id)
        if not context_file.exists():
            return False
        context_file.unlink()
        return True
    
    def touch(self, context_id: str) -> None:
        """Update lastUsedAt timestamp."""
        context = self.get(context_id)
        if context:
            context.lastUsedAt = int(time.time() * 1000)
            self._write_json(self._context_file(context_id), context.dict())
