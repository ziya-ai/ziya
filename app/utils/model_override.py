"""Per-request model override support (per-conversation / per-project pins).

The frontend resolves a pinned model (conversation pin -> project pin ->
none) and sends it as ``modelSelection: {"model": <alias>}`` on the chat
request.  These helpers keep validation and prompt-metadata patching in
one place:

- validate_model_override(): the pin must name a model available on the
  *active* endpoint (slice 1: no cross-endpoint pinning) and pass the
  user's personal allowlist -- a per-request override must not become an
  allowlist bypass for the gates /api/set-model enforces.
- apply_model_info_override(): patches the model_info dict used by the
  prompt system so family-specific prompt extensions follow the pinned
  model rather than the global default.

Pins are deliberately not persisted anywhere on the backend; the global
model (``/api/set-model``) remains the server default.
"""
from typing import Any, Dict, Optional


def validate_model_override(model: Optional[str], endpoint: str) -> Optional[str]:
    """Validate a per-request model pin.

    Returns an error string when the override is not allowed, or None
    when it is valid.
    """
    from app.agents.models import ModelManager

    if not model or not isinstance(model, str):
        return "modelSelection.model must be a non-empty string"

    endpoint_models = ModelManager.MODEL_CONFIGS.get(endpoint, {})
    if model not in endpoint_models:
        return f"Model '{model}' is not available on endpoint '{endpoint}'"

    # Personal model allowlist (~/.ziya/models.json) -- the same gate
    # /api/set-model enforces.  Import at call time so tests (and runtime
    # config reloads) can substitute the accessor.
    try:
        from app.config.models_config import get_user_allowed_models
        allowed = get_user_allowed_models()
        if allowed is not None and model not in allowed:
            return f"Model '{model}' is not in your allowed model list"
    except (ImportError, RuntimeError, OSError):
        pass  # Allowlist unavailable -- same leniency as set_model

    return None


def apply_model_info_override(model_info: Dict[str, Any], model_override: str) -> Dict[str, Any]:
    """Return a copy of ``model_info`` re-pointed at the overridden model.

    ``model_family`` drives which family prompt extensions are applied; it
    must follow the pinned model or, e.g., a Gemini pin would run with
    Claude-family instructions.  The family is looked up fresh from the
    model config (and may be None, matching get_model_info_from_config's
    behavior for models with no family tag).  The input dict is not
    mutated.
    """
    from app.agents.models import ModelManager

    endpoint = model_info.get("endpoint")
    patched = dict(model_info)
    patched["model_name"] = model_override
    cfg = ModelManager.MODEL_CONFIGS.get(endpoint, {}).get(model_override) or {}
    patched["model_family"] = cfg.get("family")
    return patched
