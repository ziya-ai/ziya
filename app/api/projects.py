"""
Project API endpoints.
"""
from fastapi import APIRouter, HTTPException
from typing import List
import os

from ..models.project import Project, ProjectCreate, ProjectUpdate, ProjectListItem
from ..storage.projects import ProjectStorage
from ..utils.paths import get_ziya_home

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

def get_project_storage() -> ProjectStorage:
    return ProjectStorage(get_ziya_home())

@router.get("", response_model=List[ProjectListItem])
async def list_projects():
    """List all known projects."""
    storage = get_project_storage()
    projects = storage.list()
    cwd = os.getcwd()
    
    # Add flag for current working directory
    result = []
    for p in projects:
        result.append(ProjectListItem(
            id=p.id,
            name=p.name,
            path=p.path,
            lastAccessedAt=p.lastAccessedAt,
            isCurrentWorkingDirectory=(p.path == cwd)
        ))
    
    return result

@router.get("/current", response_model=Project)
async def get_current_project():
    """Get or create project for current working directory."""
    storage = get_project_storage()
    cwd = os.getcwd()
    
    project = storage.get_by_path(cwd)
    if not project:
        # Auto-create project for current directory
        project = storage.create(ProjectCreate(path=cwd))
    else:
        # Update access time
        storage.touch(project.id)
    
    return project

@router.post("", response_model=Project)
async def create_project(data: ProjectCreate):
    """Create or get existing project for a path."""
    storage = get_project_storage()
    return storage.create(data)

@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str):
    """Get a specific project."""
    storage = get_project_storage()
    project = storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    storage.touch(project_id)
    return project

@router.put("/{project_id}", response_model=Project)
async def update_project(project_id: str, data: ProjectUpdate):
    """Update project metadata."""
    storage = get_project_storage()
    project = storage.update(project_id, data)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and all its data."""
    storage = get_project_storage()
    if not storage.delete(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": True, "id": project_id}
