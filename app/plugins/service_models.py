"""
Built-in service model provider implementations.

These are concrete providers that enterprise plugins can instantiate
and register with a single line in their register() function:

    from app.plugins import register_service_model_provider
    from app.plugins.service_models import NovaGroundingProvider
    register_service_model_provider(NovaGroundingProvider())

Community users get the same tools via the builtin_tools registry
(enabled_by_default=True).  Enterprise plugins use these providers
to guarantee the tools are enabled even if a user has set the env
var ZIYA_ENABLE_NOVA_GROUNDING=false.
"""

from typing import Set, Dict, Any
from app.plugins.interfaces import ServiceModelProvider


class NovaGroundingProvider(ServiceModelProvider):
    """
    Enables Nova Web Grounding for enterprise deployments.

    When registered, ensures the nova_grounding builtin tool category
    is always enabled regardless of user-level env var overrides.
    Optionally configures the grounding model and region.
    """

    provider_id = "nova-grounding"
    priority = 10

    def __init__(
        self,
        model: str = "nova-2-lite",
        region: str = "us-east-1",
    ):
        self._model = model
        self._region = region

    def get_enabled_service_tools(self) -> Set[str]:
        return {"nova_grounding"}

    def get_service_model_config(self) -> Dict[str, Any]:
        return {
            "nova_grounding": {
                "model": self._model,
                "region": self._region,
            }
        }

    def should_apply(self) -> bool:
        return True
