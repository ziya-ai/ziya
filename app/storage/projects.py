"""
Project storage implementation.
"""
from pathlib import Path
from typing import Optional, List
import uuid
import time

from .base import BaseStorage
from ..models.project import Project, ProjectCreate, ProjectUpdate, ProjectSettings
from ..utils.logging_utils import logger

def _normalize_path(path: str) -> str:
    """Normalize a filesystem path for consistent comparison."""
    if not path:
        return path
    return str(Path(path).resolve())

class ProjectStorage(BaseStorage[Project]):
    """Storage for projects."""
    
    def __init__(self, ziya_home: Path):
        self.ziya_home = ziya_home
        self.projects_dir = ziya_home / "projects"
        super().__init__(self.projects_dir)
    
    # ── Path index for O(1) lookups ──────────────────────────────────
    def _index_file(self) -> Path:
        return self.projects_dir / "_path_index.json"

    def _load_index(self) -> dict:
        """Load path→project_id index. Returns empty dict on any error."""
        try:
            data = self._read_json(self._index_file())
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_index(self, index: dict) -> None:
        self._write_json(self._index_file(), index)

    def _rebuild_index(self) -> dict:
        """Full rebuild: scan all project dirs and return a fresh index."""
        index: dict = {}
        if not self.projects_dir.exists():
            return index
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            pf = project_dir / "project.json"
            if not pf.exists():
                continue
            data = self._read_json(pf)
            if data and data.get("path"):
                key = _normalize_path(data["path"])
                if key:
                    index[key] = data["id"]
        self._save_index(index)
        return index

    def _index_set(self, path: str, project_id: str) -> None:
        """Add/update a single entry in the path index."""
        key = _normalize_path(path)
        if not key:
            return
        index = self._load_index()
        index[key] = project_id
        self._save_index(index)

    def _index_remove(self, path: str) -> None:
        """Remove a single entry from the path index."""
        key = _normalize_path(path)
        if not key:
            return
        index = self._load_index()
        index.pop(key, None)
        self._save_index(index)

    def _project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id
    
    def _project_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"
    
    def get(self, project_id: str) -> Optional[Project]:
        data = self._read_json(self._project_file(project_id))
        if not data:
            return None
        # Ensure settings exists
        if 'settings' not in data:
            data['settings'] = {'defaultContextIds': [], 'defaultSkillIds': []}
        return Project(**data)
    
    def get_by_path(self, path: str) -> Optional[Project]:
        """Find project by working directory path."""
        normalized = _normalize_path(path)
        # Fast path: use the index
        index = self._load_index()
        pid = index.get(normalized)
        if pid:
            project = self.get(pid)
            if project and _normalize_path(project.path) == normalized:
                return project
        # Index miss or stale — fall back to full scan (and rebuild index)
        logger.debug("Path index miss for %s, rebuilding", normalized)
        index = self._rebuild_index()
        pid = index.get(normalized)
        if pid:
            return self.get(pid)
        return None  # Genuinely not found
    
    def list(self) -> List[Project]:
        projects = []
        if not self.projects_dir.exists():
            return projects
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir():
                project_file = project_dir / "project.json"
                if project_file.exists():
                    data = self._read_json(project_file)
                    if data:
                        # Ensure settings exists
                        if 'settings' not in data:
                            data['settings'] = {'defaultContextIds': [], 'defaultSkillIds': []}
                        projects.append(Project(**data))
        return sorted(projects, key=lambda p: p.lastAccessedAt, reverse=True)
    
    def list_deduped(self) -> List[Project]:
        """List projects, collapsing duplicates that share the same path.

        Keeps the most recently accessed entry for each normalized path."""
        seen: dict[str, Project] = {}
        for project in self.list():
            key = _normalize_path(project.path) or project.id
            if key not in seen or project.lastAccessedAt > seen[key].lastAccessedAt:
                seen[key] = project
        return sorted(seen.values(), key=lambda p: p.lastAccessedAt, reverse=True)

    def create(self, data: ProjectCreate) -> Project:
        """Create a new project or return existing one for the path."""
        # Check if project already exists for this path (only if path is provided)
        if data.path:
            existing = self.get_by_path(data.path)
            if existing:
                # Update lastAccessedAt
                self.touch(existing.id)
                return existing
        
        # Normalize the path before persisting
        if data.path:
            data.path = _normalize_path(data.path)
        
        project_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        
        # Default name is the directory basename
        name = data.name
        if not name:
            if data.path:
                name = Path(data.path).name or "Unnamed Project"
            else:
                name = "Unnamed Project"
        
        path = data.path or ""
        
        project = Project(
            id=project_id,
            name=name,
            path=path,
            createdAt=now,
            lastAccessedAt=now,
            settings=ProjectSettings(defaultContextIds=[], defaultSkillIds=[])
        )
        
        # Create project directory structure
        project_dir = self._project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "contexts").mkdir(exist_ok=True)
        (project_dir / "skills").mkdir(exist_ok=True)
        (project_dir / "chats").mkdir(exist_ok=True)
        
        self._write_json(self._project_file(project_id), project.model_dump())

        # Update path index
        if project.path:
            self._index_set(project.path, project_id)
        return project
    
    def update(self, project_id: str, data: ProjectUpdate) -> Optional[Project]:
        project = self.get(project_id)
        if not project:
            return None
        
        old_path = project.path
        update_dict = data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            if key == 'settings' and value is not None:
                # Merge settings instead of replacing — preserves fields the
                # caller didn't include (e.g. defaultContextIds when only
                # contextManagement is being updated).
                existing = project.settings.model_dump()
                for sk, sv in value.items():
                    if sv is not None:
                        existing[sk] = sv
                from app.models.project import ProjectSettings
                project.settings = ProjectSettings(**existing)
            elif key == 'path' and value is not None:
                project.path = _normalize_path(value) if value else ""
            else:
                setattr(project, key, value)
        
        project.lastAccessedAt = int(time.time() * 1000)
        self._write_json(self._project_file(project_id), project.model_dump())

        # Update path index if path changed
        if project.path != old_path:
            if old_path:
                self._index_remove(old_path)
            if project.path:
                self._index_set(project.path, project_id)

        return project
    
    def delete(self, project_id: str) -> bool:
        project_dir = self._project_dir(project_id)
        if not project_dir.exists():
            return False
        
        # Remove from path index before deleting
        project = self.get(project_id)
        if project and project.path:
            self._index_remove(project.path)

        import shutil
        shutil.rmtree(project_dir)
        return True
    
    def touch(self, project_id: str) -> None:
        """Update lastAccessedAt timestamp."""
        project = self.get(project_id)
        if project:
            project.lastAccessedAt = int(time.time() * 1000)
            self._write_json(self._project_file(project_id), project.model_dump())
