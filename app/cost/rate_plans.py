"""
Rate plans: how list prices become the prices a given user actually pays.

A plan receives the usage record and the catalog entry in force (or None)
and returns ``EffectiveRates`` — one price per dimension plus a provenance
label.  Provenance is about the RATE, independent of whether the token
counts are estimated or actual:

    custom        the rate came from a user/plugin table for this model
    default_rule  a plan's fallback rule (e.g. "new Claude → 50% off")
    list          public list price, unmodified
    unknown       no rate available; cost is null, never zero

Resolution order (``resolve_active_plan``):

    1. A plugin ``RatePlanProvider`` whose ``should_apply()`` is true
       (closed-source discount tables such as IMR register here).
    2. The ``plan`` object in ``~/.ziya/pricing.json``.
    3. ``PublicPlan``.

``DiscountTablePlan`` ships in the open core because ``list × (1 − d)`` is
not secret — only the table is.  A plugin supplies the table; a user with
knowledge of their own arrangement can also put one in ``pricing.json``.
"""

from __future__ import annotations

import fnmatch
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.config.pricing import DIMENSIONS, CatalogEntry
from app.utils.logging_utils import logger

PROVENANCES = ("custom", "default_rule", "list", "unknown")


@dataclass
class EffectiveRates:
    unit_prices: Dict[str, float] = field(default_factory=dict)
    provenance: str = "unknown"
    plan_id: str = "public"
    tier_factors: Dict[str, float] = field(default_factory=lambda: {"standard": 1.0})
    # Long-context step-up carried through from the catalog entry so the
    # pricer can apply it after the plan has scaled base rates.
    long_context: Optional[Dict[str, Any]] = None

    @staticmethod
    def unknown(plan_id: str) -> "EffectiveRates":
        return EffectiveRates(unit_prices={}, provenance="unknown", plan_id=plan_id)


class RatePlan(ABC):
    id: str = "abstract"

    @abstractmethod
    def apply(self, record: Any, entry: Optional[CatalogEntry]) -> EffectiveRates:
        """``record`` is a ``UsageRecord`` (duck-typed: provider, model_id,
        region, tier, ts)."""

    def describe(self) -> Dict[str, Any]:
        return {"id": self.id, "type": type(self).__name__}


def _from_entry(entry: CatalogEntry, plan_id: str, scale: float = 1.0,
                provenance: Optional[str] = None) -> EffectiveRates:
    prov = provenance or ("custom" if entry.source == "user" else "list")
    lc = None
    if entry.long_context:
        lc = {
            "above_input_tokens": entry.long_context["above_input_tokens"],
            "unit_prices": {k: v * scale for k, v in entry.long_context.get("unit_prices", {}).items()},
        }
    return EffectiveRates(
        unit_prices={k: v * scale for k, v in entry.unit_prices.items()},
        provenance=prov, plan_id=plan_id,
        tier_factors=dict(entry.tier_factors), long_context=lc,
    )


class PublicPlan(RatePlan):
    """List price, multiplier 1.0.  Default for everyone."""
    id = "public"

    def apply(self, record: Any, entry: Optional[CatalogEntry]) -> EffectiveRates:
        if entry is None:
            return EffectiveRates.unknown(self.id)
        return _from_entry(entry, self.id)


class ZeroPlan(RatePlan):
    """Every dimension costs 0.  For local endpoints, or a provider the
    user is not billed for at all.  Optionally scoped to providers; other
    providers fall through to ``inner``."""
    id = "zero"

    def __init__(self, providers: Optional[List[str]] = None, inner: Optional[RatePlan] = None):
        self.providers = set(providers) if providers else None
        self.inner = inner or PublicPlan()

    def apply(self, record: Any, entry: Optional[CatalogEntry]) -> EffectiveRates:
        if self.providers is not None and record.provider not in self.providers:
            return self.inner.apply(record, entry)
        return EffectiveRates(
            unit_prices={d: 0.0 for d in DIMENSIONS}, provenance="list", plan_id=self.id,
            tier_factors={"standard": 1.0, "priority": 1.0, "flex": 1.0, "batch": 1.0},
        )

    def describe(self) -> Dict[str, Any]:
        return dict(super().describe(), providers=sorted(self.providers) if self.providers else None)


class OverridePlan(RatePlan):
    """Absolute unit rates per ``provider/model_pattern``; negotiated / EDP /
    committed-use.  Unmatched models fall through to ``inner``."""
    id = "override"

    def __init__(self, rates: List[Dict[str, Any]], inner: Optional[RatePlan] = None):
        # Each item: {"provider", "model_pattern", "unit_prices", "tier_factors"?}
        self.rates = rates
        self.inner = inner or PublicPlan()

    def _match(self, record: Any) -> Optional[Dict[str, Any]]:
        for r in self.rates:
            if r.get("provider") == record.provider and fnmatch.fnmatchcase(
                    record.model_id or "", str(r.get("model_pattern", ""))):
                return r
        return None

    def apply(self, record: Any, entry: Optional[CatalogEntry]) -> EffectiveRates:
        r = self._match(record)
        if r is None:
            return self.inner.apply(record, entry)
        tiers = dict(r.get("tier_factors") or (entry.tier_factors if entry else {"standard": 1.0}))
        tiers.setdefault("standard", 1.0)
        return EffectiveRates(
            unit_prices={str(k): float(v) for k, v in (r.get("unit_prices") or {}).items() if v is not None},
            provenance="custom", plan_id=self.id, tier_factors=tiers,
            long_context=None,
        )

    def describe(self) -> Dict[str, Any]:
        return dict(super().describe(), rates=len(self.rates))


class DiscountTablePlan(RatePlan):
    """``list × (1 − discount)`` with fallback rules.

    ``discounts``: {"provider/model_pattern": fraction}  → provenance custom
    ``default_rules``: ordered [{"pattern": "provider/glob", "discount": f}]
                       → provenance default_rule
    ``unlisted_discount``: applied when nothing matches (None = list price)

    Cache and batch dimensions inherit the model's discount: the factor is
    applied uniformly to every unit price and the tier factors are left
    untouched.
    """
    id = "discount_table"

    def __init__(self, discounts: Dict[str, float],
                 default_rules: Optional[List[Dict[str, Any]]] = None,
                 unlisted_discount: Optional[float] = None,
                 plan_id: Optional[str] = None):
        self.discounts = {str(k): float(v) for k, v in (discounts or {}).items()}
        self.default_rules = [
            {"pattern": str(r["pattern"]), "discount": float(r["discount"])}
            for r in (default_rules or []) if "pattern" in r and "discount" in r
        ]
        self.unlisted_discount = float(unlisted_discount) if unlisted_discount is not None else None
        if plan_id:
            self.id = plan_id

    def _discount_for(self, record: Any) -> Tuple[Optional[float], str]:
        key = f"{record.provider}/{record.model_id or ''}"
        for pattern, d in self.discounts.items():
            if fnmatch.fnmatchcase(key, pattern):
                return d, "custom"
        for rule in self.default_rules:
            if fnmatch.fnmatchcase(key, rule["pattern"]):
                return rule["discount"], "default_rule"
        if self.unlisted_discount is not None:
            return self.unlisted_discount, "default_rule"
        return None, "list"

    def apply(self, record: Any, entry: Optional[CatalogEntry]) -> EffectiveRates:
        if entry is None:
            return EffectiveRates.unknown(self.id)
        d, prov = self._discount_for(record)
        scale = 1.0 if d is None else max(0.0, 1.0 - d)
        # A user catalog entry is already custom even at scale 1.0.
        if prov == "list" and entry.source == "user":
            prov = "custom"
        return _from_entry(entry, self.id, scale=scale, provenance=prov)

    def describe(self) -> Dict[str, Any]:
        return dict(super().describe(), discounts=len(self.discounts),
                    default_rules=len(self.default_rules), unlisted_discount=self.unlisted_discount)


def plan_from_spec(spec: Optional[Dict[str, Any]]) -> Optional[RatePlan]:
    """Build a plan from the ``plan`` object of ``pricing.json``.  Returns
    None when the spec is absent or malformed (caller falls back)."""
    if not spec or not isinstance(spec, dict):
        return None
    kind = str(spec.get("type", "")).lower()
    try:
        if kind == "public":
            return PublicPlan()
        if kind == "zero":
            return ZeroPlan(providers=spec.get("providers"))
        if kind == "override":
            return OverridePlan(rates=list(spec.get("rates") or []))
        if kind == "discount_table":
            return DiscountTablePlan(
                discounts=spec.get("discounts") or {},
                default_rules=spec.get("default_rules"),
                unlisted_discount=spec.get("unlisted_discount"),
                plan_id=spec.get("id"),
            )
    except (TypeError, ValueError, KeyError) as e:
        logger.warning(f"pricing: malformed plan spec {spec!r}: {e}")
        return None
    logger.warning(f"pricing: unknown plan type {kind!r}; using public list prices")
    return None


def resolve_active_plan(catalog: Any) -> RatePlan:
    """Plugin provider → pricing.json plan → PublicPlan."""
    try:
        from app.plugins import get_rate_plan_providers
        for provider in get_rate_plan_providers():
            try:
                if provider.should_apply():
                    plan = provider.get_rate_plan()
                    if isinstance(plan, RatePlan):
                        return plan
                    logger.warning(f"pricing: plugin {provider!r} returned a non-RatePlan; skipping")
            except Exception as e:  # noqa: BLE001 - a plugin must not break pricing
                logger.warning(f"pricing: rate plan provider {provider!r} failed: {e}")
    except ImportError:
        pass
    plan = plan_from_spec(getattr(catalog, "plan_spec", None))
    return plan or PublicPlan()
