"""
Delegate orchestration API endpoints.

Provides launch, status, and cancel for TaskPlan delegate workflows.
These routes are the HTTP layer over DelegateManager.

Registered in server.py alongside existing API routers.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any

from ..models.delegate import DelegateSpec, TaskPlan, SwarmBudget
from ..utils.paths import get_project_dir
from ..utils.logging_utils import logger

router = APIRouter(tags=["delegates"])


# ── Request models ────────────────────────────────────────────────────

class LaunchDelegatesRequest(BaseModel):
    """Request body for launching a TaskPlan."""
    model_config = {"extra": "allow"}

    name: str = Field(..., description="Task plan name")
    description: str = Field("", description="Task description")
    delegate_specs: List[DelegateSpec] = Field(
        ..., description="List of delegate specifications"
    )


class LaunchDelegatesResponse(BaseModel):
    """Response from launching a TaskPlan."""
    model_config = {"extra": "allow"}

    success: bool
    plan_id: str
    group_id: str
    orchestrator_id: str
    conversation_ids: Dict[str, str]
    context_ids: Dict[str, str]
    message: str = ""


class DelegateStatusResponse(BaseModel):
    """Response with current delegate status."""
    model_config = {"extra": "allow"}

    plan_id: str
    name: str
    status: str
    delegates: Dict[str, Any]
    running_count: int
    crystal_count: int
    total_delegates: int


# ── Routes ────────────────────────────────────────────────────────────

@router.post(
    "/api/v1/projects/{project_id}/groups/{group_id}/launch-delegates",
    response_model=LaunchDelegatesResponse,
)
async def launch_delegates(
    project_id: str,
    group_id: str,
    data: LaunchDelegatesRequest,
):
    """
    Launch a TaskPlan — create delegate conversations and start execution.

    The group_id is currently unused (the manager creates its own folder),
    but reserved for future use where a user may pre-create a folder.
    """
    try:
        from ..agents.delegate_manager import get_delegate_manager

        project_dir = get_project_dir(project_id)
        manager = get_delegate_manager(project_id, project_dir)

        result = await manager.launch_plan(
            name=data.name,
            description=data.description,
            delegate_specs=data.delegate_specs,
        )

        logger.info(
            f"🚀 API: Launched TaskPlan '{data.name}' with "
            f"{len(data.delegate_specs)} delegates"
        )

        return LaunchDelegatesResponse(
            success=True,
            plan_id=result["plan_id"],
            group_id=result["group_id"],
            orchestrator_id=result["orchestrator_id"],
            conversation_ids=result["conversation_ids"],
            context_ids=result["context_ids"],
            message=f"Launched {len(data.delegate_specs)} delegates",
        )
    except Exception as e:
        logger.error(f"❌ API: Failed to launch delegates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/api/v1/projects/{project_id}/groups/{group_id}/delegate-status",
)
async def get_delegate_status(project_id: str, group_id: str):
    """
    Get current status of all delegates in a TaskPlan.

    Returns status per delegate, crystal counts, and running counts.
    """
    try:
        from ..agents.delegate_manager import get_delegate_manager

        project_dir = get_project_dir(project_id)
        manager = get_delegate_manager(project_id, project_dir)

        # Find the plan by scanning (plans are keyed by plan_id, not group_id)
        for plan_id in list(manager._plans.keys()):
            status = manager.get_plan_status(plan_id)
            if status:
                return status

        raise HTTPException(status_code=404, detail="No active TaskPlan found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ API: Failed to get delegate status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/api/v1/projects/{project_id}/groups/{group_id}/cancel-delegates",
)
async def cancel_delegates(project_id: str, group_id: str):
    """Cancel all running delegates in the TaskPlan."""
    try:
        from ..agents.delegate_manager import get_delegate_manager

        project_dir = get_project_dir(project_id)
        manager = get_delegate_manager(project_id, project_dir)

        # Find and cancel the active plan
        cancelled = False
        for plan_id in list(manager._plans.keys()):
            await manager.cancel_plan(plan_id)
            cancelled = True

        if not cancelled:
            raise HTTPException(
                status_code=404, detail="No active TaskPlan to cancel"
            )

        return {"success": True, "message": "All delegates cancelled"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ API: Failed to cancel delegates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/api/v1/projects/{project_id}/groups/{group_id}/swarm-budget",
)
async def get_swarm_budget(project_id: str, group_id: str):
    """Get SwarmBudget (token accounting) for a TaskPlan."""
    try:
        from ..agents.delegate_manager import get_delegate_manager

        project_dir = get_project_dir(project_id)
        manager = get_delegate_manager(project_id, project_dir)

        for plan_id in list(manager._plans.keys()):
            budget = manager.get_swarm_budget(plan_id)
            if budget:
                return budget.model_dump()

        raise HTTPException(status_code=404, detail="No active TaskPlan found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ API: Failed to get swarm budget: {e}")
        raise HTTPException(status_code=500, detail=str(e))
