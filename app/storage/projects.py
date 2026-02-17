"""
Project storage implementation.
"""
from pathlib import Path
from typing import Optional, List
import uuid
import time

from .base import BaseStorage
from ..models.project import Project, ProjectCreate, ProjectUpdate, ProjectSettings

class ProjectStorage(BaseStorage[Project]):
    """Storage for projects."""
    
    def __init__(self, ziya_home: Path):
        self.ziya_home = ziya_home
        self.projects_dir = ziya_home / "projects"
        super().__init__(self.projects_dir)
    
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
        for project in self.list():
            if project.path == path:
                return project
        return None
    
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
    
    def create(self, data: ProjectCreate) -> Project:
        """Create a new project or return existing one for the path."""
        # Check if project already exists for this path (only if path is provided)
        if data.path:
            existing = self.get_by_path(data.path)
            if existing:
                # Update lastAccessedAt
                self.touch(existing.id)
                return existing
        
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
        return project
    
    def update(self, project_id: str, data: ProjectUpdate) -> Optional[Project]:
        project = self.get(project_id)
        if not project:
            return None
        
        update_dict = data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(project, key, value)
        
        project.lastAccessedAt = int(time.time() * 1000)
        self._write_json(self._project_file(project_id), project.model_dump())
        return project
    
    def delete(self, project_id: str) -> bool:
        project_dir = self._project_dir(project_id)
        if not project_dir.exists():
            return False
        
        import shutil
        shutil.rmtree(project_dir)
        return True
    
    def touch(self, project_id: str) -> None:
        """Update lastAccessedAt timestamp."""
        project = self.get(project_id)
        if project:
            project.lastAccessedAt = int(time.time() * 1000)
            self._write_json(self._project_file(project_id), project.model_dump())
