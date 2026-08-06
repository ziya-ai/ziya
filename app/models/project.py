"""
Project data models.
"""
from pydantic import BaseModel
from typing import List, Optional
from app.models.task_card import TaskScope

class WritePolicy(BaseModel):
    """Per-project write policy overrides."""
    safe_write_paths: List[str] = []
    allowed_write_patterns: List[str] = []
    allowed_interpreters: List[str] = []
    always_blocked: List[str] = []

class ContextManagementSettings(BaseModel):
    """Per-project automatic context management settings."""
    auto_add_diff_files: bool = True
    # Per-file token cap for automatically added context files.
    # Files larger than this are never auto-added.  0 disables the limit.
    auto_add_token_limit: int = 12500

class ProjectSettings(BaseModel):
    defaultContextIds: List[str] = []
    defaultSkillIds: List[str] = []
    writePolicy: Optional[WritePolicy] = None
    contextManagement: Optional[ContextManagementSettings] = None
    externalPaths: List[str] = []
    # Saved project-wide model pin (alias string).  Outermost model
    # scope — inherited by conversations/folders with no more-specific
    # pin.  None = follow the server global model.
    modelPreference: Optional[str] = None
    # Deck-level (project-wide) Task Card permissions baseline.  Merged
    # additively with each card's own scope and every block's ancestor
    # chain (see app.models.task_card.merge_scopes and
    # app/agents/block_executor.py) — this is the outermost layer.
    taskScope: Optional[TaskScope] = None
    # Which project template seeded these settings, recorded at creation.
    # PROVENANCE ONLY — deliberately NOT consulted when reading settings.
    # Template semantics are apply-once: the values above were stamped in
    # at creation and the project owns them from then on.  This exists so
    # "why is this skill on by default?" is answerable, and so a future
    # explicit "re-apply template" action has something to key off.
    # None means the project predates templates or was created without one.
    templateId: Optional[str] = None

class Project(BaseModel):
    id: str
    name: str
    path: str
    createdAt: int
    lastAccessedAt: int
    settings: ProjectSettings

class ProjectCreate(BaseModel):
    path: Optional[str] = None
    name: Optional[str] = None
    # Explicit template choice from the create dialog.  Omit to let the
    # server autodetect from the directory's build markers, then fall back
    # to the user's default-template preference.  See
    # app.utils.project_templates.resolve_template_id for precedence.
    templateId: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    settings: Optional[ProjectSettings] = None

class ProjectListItem(BaseModel):
    """Project with additional computed fields for list view."""
    id: str
    name: str
    path: str
    lastAccessedAt: int
    isCurrentWorkingDirectory: bool = False
    conversationCount: int = 0
class StartupInfo(BaseModel):
    """Startup-directory context used to seed new web sessions."""
    # Absolute path the server was started in (or --root/--directory).
    root: str
    # True when --root/--directory was passed explicitly on the command line.
    explicit: bool
    hasAnyProjects: bool
    # Project already rooted at ``root``, if one exists (never auto-created).
    rootProject: Optional[Project] = None
