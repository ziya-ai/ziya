"""
Delegate orchestration data models.

Canonical backend definitions for delegate-related structures.
Frontend TypeScript equivalents: frontend/src/types/delegate.ts

Cross-document references:
- design/newux-context.md: DelegateMeta, DelegateSpec, TaskPlan,
  MemoryCrystal, SwarmBudget, ChatGroup extensions
- design/conversation-graph-tracker.md: graph node types, status mapping
"""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Literal


class FileChange(BaseModel):
    """A file modification made by a delegate."""
    model_config = {"extra": "allow"}

    path: str
    action: str  # 'created' | 'modified' | 'deleted'
    line_delta: str = ""  # e.g. "+48 -12" or "(new, 245 lines)"


class MemoryCrystal(BaseModel):
    """
    Compacted summary of a completed delegate's conversation.

    Produced by CompactionEngine when stream_with_tools exhausts.
    Typically 300-500 tokens replacing 10,000-30,000 token conversations.

    Phase A (deterministic, zero LLM cost):
        files_changed, decisions, exports, tool_stats
    Phase B (one cheap LLM call, ≤200 token output):
        summary
    """
    model_config = {"extra": "allow"}

    delegate_id: str
    task: str
    summary: str = ""
    files_changed: List[FileChange] = []
    decisions: List[str] = []
    exports: Dict[str, str] = {}
    tool_stats: Dict[str, int] = {}
    original_tokens: int = 0
    crystal_tokens: int = 0
    created_at: float = 0.0
    retroactive_review: Optional[str] = None  # 'preserved' | 'extended' | 'discarded'


class DelegateSpec(BaseModel):
    """Specification for a single delegate within a TaskPlan."""
    model_config = {"extra": "allow"}

    delegate_id: str
    conversation_id: Optional[str] = None
    name: str
    emoji: str = "🔵"
    scope: str = ""
    files: List[str] = []
    dependencies: List[str] = []
    skill_id: Optional[str] = None
    color: str = ""
    project_root: Optional[str] = None  # Captured at request time; used in background task


class DelegateMeta(BaseModel):
    """
    Delegate metadata stored on a Chat object.

    Absent for regular conversations. When present, marks this
    conversation as an orchestrator or delegate thread in a TaskPlan.
    """
    model_config = {"extra": "allow"}

    role: str  # 'orchestrator' | 'delegate'
    plan_id: str
    delegate_id: Optional[str] = None
    delegate_spec: Optional[DelegateSpec] = None
    status: str = "proposed"
    crystal: Optional[MemoryCrystal] = None
    context_id: Optional[str] = None
    skill_id: Optional[str] = None


class TaskPlan(BaseModel):
    """
    Task plan metadata stored on a ChatGroup (folder).

    Absent for regular folders. When present, marks this folder
    as a TaskPlan with orchestrator + delegate conversations.
    """
    model_config = {"extra": "allow"}

    name: str
    description: str = ""
    orchestrator_id: Optional[str] = None
    source_conversation_id: Optional[str] = None
    parent_plan_id: Optional[str] = None      # If spawned by a delegate in another plan
    parent_delegate_id: Optional[str] = None   # Which delegate in the parent plan spawned this
    delegate_specs: List[DelegateSpec] = []
    crystals: List[MemoryCrystal] = []
    task_list: List["SwarmTask"] = []
    status: str = "planning"
    task_graph: Optional[Dict[str, Any]] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None


class DelegateBudget(BaseModel):
    """Token budget for a single delegate."""
    model_config = {"extra": "allow"}

    status: str = "proposed"
    active_tokens: int = 0
    original_tokens: Optional[int] = None
    estimated_tokens: Optional[int] = None


class SwarmBudget(BaseModel):
    """Aggregate token budget across all delegates in a TaskPlan."""
    model_config = {"extra": "allow"}

    model_limit: int = 200000
    system_prompt_tokens: int = 0
    orchestrator_tokens: int = 0
    delegates: Dict[str, DelegateBudget] = {}
    total_active: int = 0
    total_freed: int = 0
    headroom: int = 0


class SwarmTask(BaseModel):
    """A task on the shared swarm task list, visible to all delegates."""
    model_config = {"extra": "allow"}

    task_id: str
    title: str
    status: str = "open"  # 'open' | 'claimed' | 'done' | 'blocked'
    claimed_by: Optional[str] = None  # delegate_id
    added_by: str = ""  # delegate_id that created it ("" = orchestrator)
    summary: Optional[str] = None  # completion summary
    created_at: float = 0.0
    completed_at: Optional[float] = None
    tags: List[str] = []
