"""
Multi-region weighted routing for AWS Bedrock.

Inspired by Diya's modelRouter.ts, this module routes requests across
multiple AWS regions to reduce throttle impact.  Each region is weighted
by a base capacity score that decreases when throttle events are observed
and recovers over a configurable cooldown period.

The router is transparent to the orchestrator — it lives entirely inside
BedrockProvider and only activates when the selected model is available
in multiple regions (as indicated by model_config).

Design principles:
  - Does NOT replace existing retry/backoff logic in StreamingToolExecutor
  - Only provides an alternate region when the primary region is throttled
  - Bedrock cross-region inference profiles (us., eu., global. prefixes)
    allow the same credentials to work across regions
  - Region clients are cached and reused
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

# How long a throttle penalty lasts before the region recovers full weight
_THROTTLE_COOLDOWN_SECS = float(os.environ.get("BEDROCK_REGION_COOLDOWN_SECS", "120"))

# Minimum weight a region can drop to (fraction of base weight).
# Prevents a region from being completely excluded — it may recover soon.
_MIN_WEIGHT_FRACTION = 0.05

# Base weight for each region.  Diya uses tokensPerMinute quotas; we use
# equal weights and let observed throttle events differentiate regions.
_DEFAULT_BASE_WEIGHT = 100

# Bonus multiplier for the user's preferred/configured region so it wins
# ties and is selected more often when no region is throttled.
_PRIMARY_REGION_BONUS = 1.5

# Map region prefixes in model_id dicts to representative AWS regions.
# Cross-region inference profiles (us., eu., global.) route internally
# within AWS, so we pick a representative region for client creation.
_PREFIX_TO_REGIONS: Dict[str, List[str]] = {
    "us": ["us-east-1", "us-west-2", "us-east-2"],
    "eu": ["eu-west-1", "eu-central-1", "eu-west-3"],
    "apac": ["ap-northeast-1", "ap-southeast-1", "ap-southeast-2"],
    "jp": ["ap-northeast-1"],
    "global": ["us-east-1"],  # global profiles work from any region
}


@dataclass
class RegionEndpoint:
    """Tracks routing state for a single Bedrock region."""
    region: str
    model_id: str
    base_weight: float = _DEFAULT_BASE_WEIGHT
    last_throttle_time: float = 0.0
    throttle_count: int = 0
    success_count: int = 0
    _client: Any = field(default=None, repr=False)

    @property
    def effective_weight(self) -> float:
        """Current weight after throttle penalties decay over time."""
        if self.throttle_count == 0:
            return self.base_weight

        elapsed = time.monotonic() - self.last_throttle_time
        if elapsed >= _THROTTLE_COOLDOWN_SECS:
            # Fully recovered
            return self.base_weight

        # Linear recovery from penalized weight back to base
        recovery_fraction = elapsed / _THROTTLE_COOLDOWN_SECS
        # Penalty is proportional to recent throttle count (capped)
        penalty_severity = min(self.throttle_count, 5) / 5.0
        min_weight = self.base_weight * _MIN_WEIGHT_FRACTION
        penalized_weight = self.base_weight * (1.0 - penalty_severity) + min_weight * penalty_severity
        return penalized_weight + (self.base_weight - penalized_weight) * recovery_fraction


class BedrockRegionRouter:
    """Weighted multi-region router for Bedrock API calls.

    Builds a set of RegionEndpoint objects from model_config and selects
    among them using weighted random selection.  Throttled regions have
    reduced weight, causing requests to shift toward healthier regions.

    Usage::

        router = BedrockRegionRouter(model_config, "ziya", "us-east-1")
        endpoint = router.select_endpoint()
        # ... make API call to endpoint.region with endpoint.model_id ...
        if throttled:
            router.report_throttle(endpoint.region)
            alt = router.select_endpoint(exclude=endpoint.region)
    """

    def __init__(
        self,
        model_config: Dict[str, Any],
        aws_profile: str,
        primary_region: str,
    ):
        self._aws_profile = aws_profile
        self._primary_region = primary_region
        self._endpoints: Dict[str, RegionEndpoint] = {}
        self._enabled = False

        self._build_endpoints(model_config, primary_region)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True if there are multiple regions to route across."""
        return self._enabled

    @property
    def regions(self) -> List[str]:
        return list(self._endpoints.keys())

    def select_endpoint(self, exclude: Optional[str] = None) -> Optional[RegionEndpoint]:
        """Pick a region using weighted random selection.

        Parameters
        ----------
        exclude
            Region to exclude (e.g. the one that just throttled).

        Returns
        -------
        RegionEndpoint or None if no eligible regions remain.
        """
        candidates = [
            ep for ep in self._endpoints.values()
            if ep.region != exclude
        ]
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        # Weighted random selection (same algorithm as Diya's modelRouter.ts)
        weights = [ep.effective_weight for ep in candidates]
        total = sum(weights)
        if total <= 0:
            return random.choice(candidates)

        r = random.random() * total
        cumulative = 0.0
        for ep, w in zip(candidates, weights):
            cumulative += w
            if r <= cumulative:
                return ep

        # Fallback (float rounding edge case)
        return candidates[-1]

    def report_throttle(self, region: str) -> None:
        """Record a throttle event for a region, reducing its weight."""
        ep = self._endpoints.get(region)
        if not ep:
            return
        ep.throttle_count += 1
        ep.last_throttle_time = time.monotonic()
        logger.info(
            f"RegionRouter: throttle in {region} "
            f"(count={ep.throttle_count}, effective_weight={ep.effective_weight:.1f})"
        )

    def report_success(self, region: str) -> None:
        """Record a successful response, gradually restoring confidence."""
        ep = self._endpoints.get(region)
        if not ep:
            return
        ep.success_count += 1
        # Decay throttle count on sustained success
        if ep.throttle_count > 0 and ep.success_count % 3 == 0:
            ep.throttle_count = max(0, ep.throttle_count - 1)

    def get_endpoint(self, region: str) -> Optional[RegionEndpoint]:
        """Get the endpoint for a specific region."""
        return self._endpoints.get(region)

    def get_client_for_region(self, region: str) -> Any:
        """Get or create a cached Bedrock client for a region.

        Uses ModelManager's persistent client infrastructure so clients
        are properly wrapped with CustomBedrockClient + ThrottleSafeBedrock.
        """
        ep = self._endpoints.get(region)
        if not ep:
            return None

        if ep._client is not None:
            return ep._client

        try:
            from app.providers.bedrock_client_cache import get_persistent_bedrock_client
            client = get_persistent_bedrock_client(
                aws_profile=self._aws_profile,
                region=region,
                model_id=ep.model_id,
                model_config=None,  # Already validated during router construction
            )
            ep._client = client
            return client
        except Exception as e:
            logger.warning(f"RegionRouter: failed to create client for {region}: {e}")
            return None

    def status(self) -> Dict[str, Any]:
        """Return routing status for diagnostics."""
        return {
            "enabled": self._enabled,
            "primary_region": self._primary_region,
            "endpoints": {
                ep.region: {
                    "model_id": ep.model_id,
                    "base_weight": ep.base_weight,
                    "effective_weight": round(ep.effective_weight, 1),
                    "throttle_count": ep.throttle_count,
                    "success_count": ep.success_count,
                }
                for ep in self._endpoints.values()
            },
        }

    # ------------------------------------------------------------------
    # Internal: endpoint construction from model_config
    # ------------------------------------------------------------------

    def _build_endpoints(self, model_config: Dict[str, Any], primary_region: str) -> None:
        """Populate self._endpoints from model_config."""
        model_id_raw = model_config.get("model_id")
        available_regions = model_config.get("available_regions", [])

        if isinstance(model_id_raw, str):
            # Single model ID — only one region, routing not useful
            self._endpoints[primary_region] = RegionEndpoint(
                region=primary_region,
                model_id=model_id_raw,
                base_weight=_DEFAULT_BASE_WEIGHT,
            )
            self._enabled = False
            return

        if not isinstance(model_id_raw, dict) or len(model_id_raw) < 2:
            # Need at least 2 region prefixes for routing to be useful
            if isinstance(model_id_raw, dict) and model_id_raw:
                prefix, mid = next(iter(model_id_raw.items()))
                self._endpoints[primary_region] = RegionEndpoint(
                    region=primary_region, model_id=mid,
                )
            self._enabled = False
            return

        # Build one endpoint per prefix (us, eu, etc.)
        for prefix, mid in model_id_raw.items():
            if not mid:
                continue

            # Pick a representative region for this prefix
            region = self._pick_region_for_prefix(prefix, available_regions, primary_region)

            bonus = _PRIMARY_REGION_BONUS if region == primary_region else 1.0
            self._endpoints[region] = RegionEndpoint(
                region=region,
                model_id=mid,
                base_weight=_DEFAULT_BASE_WEIGHT * bonus,
            )

        self._enabled = len(self._endpoints) >= 2
        if self._enabled:
            regions_str = ", ".join(f"{r} ({e.model_id})" for r, e in self._endpoints.items())
            logger.info(f"RegionRouter: multi-region enabled — {regions_str}")

    @staticmethod
    def _pick_region_for_prefix(
        prefix: str,
        available_regions: List[str],
        primary_region: str,
    ) -> str:
        """Select the best AWS region for a model_id prefix."""
        candidate_regions = _PREFIX_TO_REGIONS.get(prefix, [])

        # Prefer the user's primary region if it matches this prefix
        if primary_region in candidate_regions:
            return primary_region

        # Prefer a region that's in the model's available_regions list
        for r in candidate_regions:
            if r in available_regions:
                return r

        # Fall back to first candidate, or primary_region
        return candidate_regions[0] if candidate_regions else primary_region
