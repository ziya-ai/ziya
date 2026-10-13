"""Per-request region for a model that may differ from the process's model.

The server keeps ONE session region (ModelManager._state['aws_region'] /
AWS_REGION), resolved for the process-wide model (``--model``).  A
conversation can pin a different model, and StreamingToolExecutor used to
send that pinned model to the session region unchanged.  Observed
2026-10-07: the session model opus5.5 was running in eu-west-1 (it has an
``eu`` profile), a conversation pinned to fable5.1 (``us``/``global`` profiles
only, available in us-east-1/us-west-2) was sent to eu-west-1 as well, and
Bedrock rejected it there because that region's data-retention switch had
never been set.

The clamp is deliberately narrow so EU users of models with an ``eu``
profile keep their region: a region is replaced only when the model has no
inference profile for that geography and the region is not in the model's
declared ``available_regions``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _geo(region: str) -> str:
    return region.split("-", 1)[0] if region else ""


def region_for_model(region: Optional[str], model_config: Optional[Dict[str, Any]]) -> Optional[str]:
    """The region to invoke *model_config*'s model from, given the session *region*."""
    if not region or not model_config:
        return region
    available = model_config.get("available_regions") or []
    if not available or region in available:
        return region
    model_id = model_config.get("model_id")
    if isinstance(model_id, dict):
        if model_id.get(_geo(region)):
            return region          # the model has a profile for this geography
    elif not model_config.get("region_restricted"):
        return region              # plain id with an advisory region list
    preferred = model_config.get("preferred_region")
    return preferred if preferred in available else available[0]
