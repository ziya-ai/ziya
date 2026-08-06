"""
Project API endpoints.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import os

from ..storage.chats import ChatStorage
from ..models.project import Project, ProjectCreate, ProjectUpdate, ProjectListItem, StartupInfo
from ..storage.projects import ProjectStorage
from ..utils.paths import get_ziya_home

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

def get_project_storage() -> ProjectStorage:
    return ProjectStorage(get_ziya_home())


# ── Project templates ────────────────────────────────────────────────────
# Registered BEFORE the /{project_id} routes below: FastAPI matches in
# declaration order, so a later /templates would be swallowed by
# /{project_id} and arrive as a project lookup for the id "templates".

class TemplateListResponse(BaseModel):
    """Templates plus the current default, in one round trip.

    Combined because the create dialog needs both to render a single line
    of UI, and two calls would let it paint a stale default.
    """
    templates: List[dict]
    defaultTemplateId: Optional[str] = None


class DetectTemplateResponse(BaseModel):
    templateId: str
    # The marker file that produced the match, so the UI can say
    # "detected from pyproject.toml" rather than asking for trust.
    marker: Optional[str] = None
    detected: bool = False


class SetDefaultTemplateRequest(BaseModel):
    # Explicit null clears the preference.
    templateId: Optional[str] = None


class SnapshotTemplateRequest(BaseModel):
    """Save an existing project's settings as a reusable template.

    The authoring UX is a snapshot rather than a template editor: configure
    one project the way you want, then name it.
    """
    id: str
    name: str
    description: str = ""
    detectMarkers: List[str] = []


@router.get("/templates", response_model=TemplateListResponse)
async def list_project_templates():
    """List built-in and user templates, plus the default preference."""
    from ..utils.template_store import all_templates, get_default_template_id
    return TemplateListResponse(
        templates=[t.model_dump() for t in all_templates()],
        defaultTemplateId=get_default_template_id(),
    )


@router.get("/templates/detect", response_model=DetectTemplateResponse)
async def detect_project_template(path: str):
    """Sniff a directory for template markers, without creating anything.

    Lets the create dialog show its detection before the user commits.
    """
    from ..utils.project_templates import detect_template
    from ..utils.template_store import all_templates
    resolved = os.path.abspath(os.path.expanduser(path)) if path else path
    got = detect_template(resolved, templates=all_templates())
    return DetectTemplateResponse(
        templateId=got.template_id, marker=got.marker, detected=got.detected,
    )


@router.put("/templates/default", response_model=TemplateListResponse)
async def set_default_project_template(data: SetDefaultTemplateRequest):
    """Set or clear the default template for new projects."""
    from ..utils.template_store import (
        all_templates, get_default_template_id, get_template,
        set_default_template_id,
    )
    if data.templateId and get_template(data.templateId) is None:
        raise HTTPException(status_code=404, detail="Template not found")
    set_default_template_id(data.templateId)
    return TemplateListResponse(
        templates=[t.model_dump() for t in all_templates()],
        defaultTemplateId=get_default_template_id(),
    )


@router.delete("/templates/{template_id}")
async def delete_project_template(template_id: str):
    """Delete a user template.  Built-ins cannot be deleted.

    Safe for projects created from it: apply-once means they already own
    their settings, and templateId is only provenance.
    """
    from ..utils.template_store import delete_user_template
    try:
        if not delete_user_template(template_id):
            raise HTTPException(status_code=404, detail="Template not found")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"deleted": True, "id": template_id}


@router.get("", response_model=List[ProjectListItem])
async def list_projects():
    """List all known projects."""
    storage = get_project_storage()
    projects = storage.list_deduped()
    from app.context import get_project_root
    cwd = get_project_root()
    ziya_home = get_ziya_home()
    projects_dir = ziya_home / "projects"
    
    # Add flag for current working directory
    result = []
    for p in projects:
        result.append(ProjectListItem(
            id=p.id,
            name=p.name,
            path=p.path,
            lastAccessedAt=p.lastAccessedAt,
            isCurrentWorkingDirectory=(p.path == cwd),
            conversationCount=_count_chats(projects_dir / p.id / "chats"),
        ))
    
    return result


def _count_chats(chats_dir) -> int:
    """Count chat JSON files in a project's chats directory (excluding internal files)."""
    from pathlib import Path
    d = Path(chats_dir)
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.suffix == '.json' and not f.name.startswith('_'))

@router.get("/current", response_model=Project)
async def get_current_project():
    """Get or create project for current working directory."""
    storage = get_project_storage()
    from app.context import get_project_root
    cwd = get_project_root()
    
    project = storage.get_by_path(cwd)
    if not project:
        # Auto-create project for current directory
        project = storage.create(ProjectCreate(path=cwd))
    else:
        # Update access time
        storage.touch(project.id)
    
    return project

@router.get("/last-accessed", response_model=Project)
async def get_last_accessed_project():
    """Return the most recently accessed project across all projects."""
    storage = get_project_storage()
    projects = storage.list()
    if not projects:
        # No projects at all — fall back to creating one for cwd
        cwd = os.getcwd()
        return storage.create(ProjectCreate(path=cwd))
    # list() is already sorted by lastAccessedAt descending
    best = projects[0]
    storage.touch(best.id)
    return best

@router.get("/startup", response_model=StartupInfo)
async def get_startup_info():
    """Return startup-directory context for new web sessions.

    Pure read: unlike /current and /last-accessed this never creates a
    project. The frontend uses ``explicit`` to decide whether the startup
    directory should win over a browser's remembered last project, and
    ``hasAnyProjects``/``rootProject`` to decide whether to show the
    first-run project picker.
    """
    storage = get_project_storage()
    # Read the startup root directly from the env (not get_project_root(),
    # which would honor an X-Project-Root header — absent at first boot).
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
    root = os.path.abspath(os.path.expanduser(root))
    explicit = os.environ.get("ZIYA_EXPLICIT_ROOT") == "true"
    return StartupInfo(
        root=root,
        explicit=explicit,
        hasAnyProjects=len(storage.list_deduped()) > 0,
        rootProject=storage.get_by_path(root),
    )

@router.post("", response_model=Project)
async def create_project(data: ProjectCreate):
    """Create or get existing project for a path."""
    # If path is provided, resolve it to absolute
    if data.path:
        data.path = os.path.abspath(os.path.expanduser(data.path))
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


@router.post("/{project_id}/save-as-template")
async def save_project_as_template(project_id: str, data: SnapshotTemplateRequest):
    """Snapshot this project's settings as a reusable template.

    Only the templatable subset is captured (see
    project_templates.TEMPLATABLE_SETTINGS_KEYS) — a template must not
    carry a project's identity or its context-id references, which mean
    nothing in a different project.

    Unlike the built-in Software Development preset, a snapshot DOES carry
    writePolicy if the source project had one: the user explicitly chose to
    save these settings, so there is no silent widening of permission.
    """
    storage = get_project_storage()
    project = storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from ..utils.project_templates import (
        TEMPLATABLE_SETTINGS_KEYS, ProjectTemplate,
    )
    from ..utils.template_store import save_user_template

    raw = project.settings.model_dump()
    captured = {
        k: v for k, v in raw.items()
        if k in TEMPLATABLE_SETTINGS_KEYS and v not in (None, [], {})
        # Context ids are per-project record ids; carrying them into a
        # template would seed dangling references in every project it is
        # later applied to.
        and k != "defaultContextIds"
    }
    try:
        tpl = save_user_template(ProjectTemplate(
            id=data.id, name=data.name, description=data.description,
            detectMarkers=data.detectMarkers, settings=captured,
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return tpl.model_dump()
