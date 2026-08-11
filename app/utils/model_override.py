"""Per-request model override support (per-conversation / per-project pins).

The frontend resolves a pinned model (conversation pin -> project pin ->
none) and sends it as ``modelSelection: {"model": <alias>}`` on the chat
request.  These helpers keep validation and prompt-metadata patching in
one place:

- validate_model_override(): the pin must name a model on some endpoint
  this install is permitted to use, and pass the user's personal
  allowlist -- a per-request override must not become an allowlist bypass
  for the gates /api/set-model enforces.
- resolve_override_endpoint(): which endpoint owns a pinned model. Model
  aliases are globally unique across endpoints (asserted by
  tests/test_model_override_validation.py), so the endpoint is derivable
  from the model name alone and pins need no endpoint of their own.
- apply_model_info_override(): patches the model_info dict used by the
  prompt system so family-specific prompt extensions follow the pinned
  model rather than the global default.

Pins are deliberately not persisted anywhere on the backend; the global
model (``/api/set-model``) remains the server default.
"""
import os
from typing import Any, Dict, Optional


def resolve_override_endpoint(model: str) -> Optional[str]:
    """Return the endpoint that defines ``model``, or None if unknown.

    Prefers the active endpoint when it defines the model, so a pin that
    happens to name the running endpoint's model never routes elsewhere.
    """
    from app.agents.models import ModelManager

    active = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    if model in ModelManager.MODEL_CONFIGS.get(active, {}):
        return active
    for endpoint, models in ModelManager.MODEL_CONFIGS.items():
        if model in models:
            return endpoint
    return None


def _permitted_endpoints() -> Optional[list]:
    """Enterprise endpoint allowlist, or None when unrestricted."""
    if os.environ.get("ZIYA_ALLOW_ALL_ENDPOINTS") == "1":
        return None
    try:
        from app.plugins import get_allowed_endpoints
        return get_allowed_endpoints()
    except (ImportError, RuntimeError, OSError):
        return None
def validate_model_override(model: Optional[str], endpoint: str) -> Optional[str]:
    """Validate a per-request model pin.

    ``endpoint`` is the running endpoint. A pin naming a model on a
    DIFFERENT endpoint is accepted: the streaming executor takes an
    ``endpoint_override`` (already used by Task Card scopes), so the pin can
    be honored without touching global state. It must still name a model on
    an endpoint the enterprise policy permits and that has credentials,
    since those are the gates /api/set-model enforces.

    Returns an error string when the override is not allowed, or None
    when it is valid.
    """
    from app.agents.models import ModelManager

    if not model or not isinstance(model, str):
        return "modelSelection.model must be a non-empty string"

    # Fail closed on a bogus caller endpoint. Resolving the model's OWN
    # endpoint below made this argument unchecked, so a garbage/unset
    # ZIYA_ENDPOINT silently validated as if it were the model's endpoint --
    # losing the "is the running endpoint real?" signal the pre-cross-endpoint
    # version got for free from the empty-dict lookup.
    if endpoint not in ModelManager.MODEL_CONFIGS:
        return f"Endpoint '{endpoint}' is not a configured endpoint"

    target = resolve_override_endpoint(model)
    if target is None:
        return f"Model '{model}' is not defined on any configured endpoint"

    if target != endpoint:
        permitted = _permitted_endpoints()
        if permitted is not None and target not in permitted:
            return (
                f"Model '{model}' belongs to endpoint '{target}', "
                f"which is not permitted by policy"
            )
        try:
            from app.utils.provider_detection import get_availability
            if not get_availability().get(target, True):
                return (
                    f"Model '{model}' requires endpoint '{target}', "
                    f"which has no credentials configured"
                )
        except (ImportError, RuntimeError, OSError):
            pass  # Availability unknown — let the provider fail loudly

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

    ``endpoint`` follows the pinned model too. Looking the family up on the
    OLD endpoint returns None for any cross-endpoint pin (the alias isn't in
    that endpoint's config), which silently dropped family prompt extensions.
    """
    from app.agents.models import ModelManager

    endpoint = resolve_override_endpoint(model_override) or model_info.get("endpoint")
    patched = dict(model_info)
    patched["model_name"] = model_override
    patched["endpoint"] = endpoint
    cfg = ModelManager.MODEL_CONFIGS.get(endpoint, {}).get(model_override) or {}
    patched["model_family"] = cfg.get("family")
    return patched
