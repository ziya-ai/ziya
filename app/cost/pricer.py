"""
price(record) -> CostLine.

Cost is derived at read time from the catalog entry in force at the
record's timestamp and the active rate plan; nothing here is stored.

Rules
-----
* Dimensions with a count but no rate make the line PARTIAL: the known
  part is summed, ``unpriced_dims`` names the rest, and ``is_partial`` is
  set.  A partial figure is not a guess — it is labelled as incomplete —
  and it beats a null that hides 95% of a Nova bill because one cache
  dimension is unlisted.
* No entry at all → ``effective_cost`` None, provenance ``unknown``.
* ``reasoning`` is informational: every provider Ziya ships reports
  reasoning tokens INSIDE ``output``, so pricing it again would double
  count.  An entry may set ``"reasoning_additive": true`` in its unit
  prices object (value ignored) to opt into additive billing.
* Service tier: the record's tier must appear in the entry's
  ``tier_factors`` or the line is unknown — a tier we have no factor for
  is not "standard", it is unpriced.
* Long-context step-up applies to the whole request when total prompt
  tokens (input + cache_read + cache writes) exceed the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config.pricing import TOKEN_DIMENSIONS, CatalogEntry, PricingCatalog
from app.cost.rate_plans import EffectiveRates, RatePlan

_PROMPT_DIMS = ("input", "cache_read", "cache_write_5m", "cache_write_1h")


@dataclass
class CostLine:
    record_id: str
    list_cost: Optional[float]
    effective_cost: Optional[float]
    catalog_version: str
    plan_id: str
    provenance: str
    is_partial: bool = False
    unpriced_dims: List[str] = field(default_factory=list)
    # Per-dimension effective rate actually applied (after plan scaling,
    # tier factor and long-context selection), for hover/inspection.
    rates_applied: Dict[str, float] = field(default_factory=dict)
    tier: str = "standard"
    tier_factor: Optional[float] = None
    long_context_applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id, "list_cost": self.list_cost,
            "effective_cost": self.effective_cost, "catalog_version": self.catalog_version,
            "plan_id": self.plan_id, "provenance": self.provenance,
            "is_partial": self.is_partial, "unpriced_dims": list(self.unpriced_dims),
            "rates_applied": dict(self.rates_applied), "tier": self.tier,
            "tier_factor": self.tier_factor, "long_context_applied": self.long_context_applied,
        }


def _billable_dims(dims: Dict[str, int], additive_reasoning: bool) -> Dict[str, int]:
    out = {k: int(v) for k, v in dims.items() if v}
    if not additive_reasoning:
        out.pop("reasoning", None)
    return out


def _sum(dims: Dict[str, int], rates: Dict[str, float], factor: float) -> tuple[float, List[str]]:
    total = 0.0
    missing: List[str] = []
    for dim, count in dims.items():
        rate = rates.get(dim)
        if rate is None:
            missing.append(dim)
            continue
        if dim in TOKEN_DIMENSIONS:
            total += count / 1_000_000.0 * rate * factor
        else:
            total += count * rate * factor
    return total, missing


def _select_rates(rates: Dict[str, float], long_context: Optional[Dict[str, Any]],
                  dims: Dict[str, int]) -> tuple[Dict[str, float], bool]:
    if not long_context:
        return rates, False
    prompt = sum(dims.get(d, 0) for d in _PROMPT_DIMS)
    if prompt > int(long_context.get("above_input_tokens", 0)):
        merged = dict(rates)
        merged.update(long_context.get("unit_prices") or {})
        return merged, True
    return rates, False


def price_record(record: Any, catalog: PricingCatalog, plan: RatePlan) -> CostLine:
    """``record`` is a ``UsageRecord`` (duck-typed: id, ts, provider,
    model_id, region, tier, dims)."""
    entry: Optional[CatalogEntry] = catalog.lookup(
        record.provider, record.model_id, record.region, record.ts)
    eff: EffectiveRates = plan.apply(record, entry)
    tier = getattr(record, "tier", None) or "standard"

    if eff.provenance == "unknown" and not eff.unit_prices:
        return CostLine(
            record_id=record.id, list_cost=None, effective_cost=None,
            catalog_version=catalog.version, plan_id=eff.plan_id,
            provenance="unknown", is_partial=False,
            unpriced_dims=sorted(k for k, v in record.dims.items() if v),
            tier=tier,
        )

    factor = eff.tier_factors.get(tier)
    if factor is None:
        return CostLine(
            record_id=record.id, list_cost=None, effective_cost=None,
            catalog_version=catalog.version, plan_id=eff.plan_id,
            provenance="unknown", is_partial=False,
            unpriced_dims=sorted(k for k, v in record.dims.items() if v),
            tier=tier, tier_factor=None,
        )

    additive = bool(eff.unit_prices.get("reasoning_additive")) or bool(
        entry and entry.unit_prices.get("reasoning_additive"))
    dims = _billable_dims(record.dims, additive)
    eff_rates = {k: v for k, v in eff.unit_prices.items() if k != "reasoning_additive"}
    eff_rates, lc_applied = _select_rates(eff_rates, eff.long_context, dims)
    effective, missing = _sum(dims, eff_rates, factor)

    list_cost: Optional[float] = None
    if entry is not None:
        list_rates = {k: v for k, v in entry.unit_prices.items() if k != "reasoning_additive"}
        list_rates, _ = _select_rates(list_rates, entry.long_context, dims)
        list_factor = entry.tier_factors.get(tier)
        if list_factor is not None:
            list_cost, _ = _sum(dims, list_rates, list_factor)

    return CostLine(
        record_id=record.id,
        list_cost=round(list_cost, 8) if list_cost is not None else None,
        effective_cost=round(effective, 8),
        catalog_version=catalog.version, plan_id=eff.plan_id,
        provenance=eff.provenance, is_partial=bool(missing),
        unpriced_dims=sorted(missing),
        rates_applied={d: eff_rates[d] * factor for d in dims if d in eff_rates},
        tier=tier, tier_factor=factor, long_context_applied=lc_applied,
    )
