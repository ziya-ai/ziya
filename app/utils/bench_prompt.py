"""
Bench -> prompt seam (design/capabilities-hub.md, slice 3).

The chat request identifies its project by ``project_root`` (a path), not an
id; ``_inject_task_results`` in app/server.py resolves it the same way.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


def project_id_for_root(project_root: Optional[str]) -> Optional[str]:
    if not project_root:
        return None
    try:
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home
        project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
        return project.id if project else None
    except Exception as e:  # noqa: BLE001 -- prompt assembly must never fail a turn
        logger.debug("bench_prompt: project lookup failed for %s: %s", project_root, e)
        return None


def resolve_bench_prompt(project_root: Optional[str], conversation_id: Optional[str]):
    """PromptInputs for this request, or None when no project resolves."""
    pid = project_id_for_root(project_root)
    if not pid:
        return None
    try:
        from app.services.bench import prompt_inputs
        return prompt_inputs(pid, conversation_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("bench_prompt: resolution failed, using client prompt: %s", e)
        return None


def merge_bench_prompt(
    bench, client_addition: str, client_overrides: Dict[str, Any], client_tool_ids: List[str],
) -> Tuple[str, Dict[str, Any], List[str], Optional[set]]:
    """Combine server-resolved and client-sent skill effects.

    Returns (system_prompt_addition, model_overrides, preferred_tool_ids,
    catalog_names).  Rules, one release only (removed with slice 4's
    migration):

      * no project / bench unresolvable -> client values, catalog unfiltered;
      * bench has NO skill placement at any layer -> not adopted yet; client
        prompt text kept so today's localStorage activations keep working,
        overrides/tool ids taken from the bench only when the client sent
        none (it never does today);
      * bench authoritative -> server prompt text wins; the catalog is
        filtered to the bench's ondemand set, so a skill placed ``off`` is
        offered nowhere.
    """
    if bench is None:
        return client_addition, client_overrides, client_tool_ids, None
    overrides = client_overrides if client_overrides else dict(bench.model_overrides)
    tool_ids = client_tool_ids if client_tool_ids else list(bench.preferred_tool_ids)
    if not bench.authoritative:
        return client_addition, overrides, tool_ids, None
    if client_addition and client_addition.strip() != bench.system_prompt_addition.strip():
        logger.debug("bench_prompt: client systemPromptAddition superseded by bench")
    return bench.system_prompt_addition, overrides, tool_ids, set(bench.catalog_names)
