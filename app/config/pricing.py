"""
List-price catalog for model usage.

Design: ``design/CostModel.md``.  Summary of the rules this module obeys:

* Prices live here as DATA, not in ``MODEL_CONFIGS``.  They change on a
  different cadence and by different people than capability flags, and one
  entry (a ``model_pattern`` glob) serves several aliases and regional ids.
* Keys are ``provider`` (the billing endpoint: bedrock / anthropic / openai /
  google / zai / meta / local / openrouter) + resolved ``model_id`` glob +
  optional ``region``.  The same weights cost different amounts on Bedrock
  than on Anthropic-direct, so aliases are never priced.
* Entries are DATED (``effective_from`` / ``effective_to``).  A usage record
  is priced against the entry in force at ``record.ts``, so history reprices
  correctly when a card revises and a corrected entry heals old rows.  The
  catalog is therefore append-only in spirit: supersede, do not edit.
* Never guess.  A model with no matching entry prices as ``unknown``; a
  dimension a matching entry does not list prices as ``unknown`` for that
  dimension.  Zero is a real price (local models) and is spelled ``0.0``.

User overlay: ``~/.ziya/pricing.json`` (``ZIYA_HOME`` honoured) with the
same entry shape.  User entries win over built-ins when both match.  This
is where internal transfer prices, negotiated rates, and prices for models
newer than this file go.  Shape::

    {
      "revision": "internal-2026-09",
      "entries": [
        {"provider": "bedrock", "model_pattern": "*anthropic.claude-opus-4-7*",
         "region": null, "effective_from": "2026-06-01",
         "unit_prices": {"input": 5.0, "output": 25.0,
                         "cache_read": 0.5, "cache_write_5m": 6.25},
         "tier_factors": {"standard": 1.0, "flex": 0.5}}
      ],
      "plan": {"type": "public"}            # see app/cost/rate_plans.py
    }

Unit prices are USD per million tokens for token dimensions and USD per
unit for the non-token dimensions (``cache_storage_token_hours``,
``provisioned_unit_hours``, ``image_input``).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.logging_utils import logger

# Canonical usage dimensions.  Providers map native usage fields onto
# these in app/cost/meter.py; anything not in this tuple is preserved under
# its native name and priced as unknown.
DIMENSIONS: Tuple[str, ...] = (
    "input",                     # uncached prompt tokens
    "output",                    # completion tokens (includes reasoning
                                 #   for every provider Ziya ships today)
    "cache_read",                # prompt-cache hits
    "cache_write_5m",            # default-TTL cache creation
    "cache_write_1h",            # extended-TTL cache creation
    "cache_storage_token_hours", # Gemini explicit caching (storage-billed)
    "reasoning",                 # recorded separately; see pricer fold rule
    "image_input",               # where billed per image, not per token
    "provisioned_unit_hours",    # Bedrock provisioned throughput
)

TOKEN_DIMENSIONS = frozenset({
    "input", "output", "cache_read", "cache_write_5m", "cache_write_1h",
    "reasoning",
})

SERVICE_TIERS: Tuple[str, ...] = ("standard", "priority", "flex", "batch")

# Revision of the BUILT-IN table below.  Bump when a price changes.  The
# effective catalog version reported on every priced line combines this
# with a content hash of the user overlay (see PricingCatalog.version).
CATALOG_REVISION = "2026-09-04"

PRICING_FILENAME = "pricing.json"


@dataclass(frozen=True)
class CatalogEntry:
    provider: str
    model_pattern: str
    unit_prices: Dict[str, float]
    region: Optional[str] = None
    effective_from: str = "2020-01-01"
    effective_to: Optional[str] = None
    tier_factors: Dict[str, float] = field(default_factory=lambda: {"standard": 1.0})
    # Request-size step-up (Anthropic 1M context, Gemini >200k): when the
    # request's total prompt tokens exceed ``above_input_tokens`` these
    # unit prices replace the base ones for the whole request.
    long_context: Optional[Dict[str, Any]] = None
    # builtin | user — feeds cost provenance (list vs custom).
    source: str = "builtin"
    note: str = ""

    def matches(self, provider: str, model_id: str, region: Optional[str], ts: float) -> bool:
        if self.provider != provider:
            return False
        if not fnmatch.fnmatchcase(model_id or "", self.model_pattern):
            return False
        if self.region is not None and self.region != region:
            return False
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        if day < self.effective_from:
            return False
        if self.effective_to is not None and day > self.effective_to:
            return False
        return True

    def specificity(self) -> Tuple[int, int, str]:
        """Sort key: user before builtin, region-specific before global,
        later effective_from before earlier."""
        return (
            1 if self.source == "user" else 0,
            1 if self.region is not None else 0,
            self.effective_from,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider, "model_pattern": self.model_pattern,
            "region": self.region, "effective_from": self.effective_from,
            "effective_to": self.effective_to, "unit_prices": dict(self.unit_prices),
            "tier_factors": dict(self.tier_factors), "long_context": self.long_context,
            "source": self.source, "note": self.note,
        }


def _entry_from_dict(d: Dict[str, Any], source: str) -> Optional[CatalogEntry]:
    """Validate one JSON entry; return None (and log) if unusable."""
    try:
        provider = str(d["provider"])
        pattern = str(d["model_pattern"])
        prices = d.get("unit_prices") or {}
        if not isinstance(prices, dict):
            raise ValueError("unit_prices must be an object")
        clean_prices: Dict[str, float] = {}
        for k, v in prices.items():
            if v is None:
                continue  # explicit null = unknown for that dimension
            clean_prices[str(k)] = float(v)
        tiers = d.get("tier_factors") or {"standard": 1.0}
        tiers = {str(k): float(v) for k, v in tiers.items()}
        tiers.setdefault("standard", 1.0)
        eff_from = str(d.get("effective_from") or "2020-01-01")
        eff_to = d.get("effective_to")
        for s in (eff_from, eff_to):
            if s is not None:
                date.fromisoformat(s)  # raises on bad format
        lc = d.get("long_context")
        if lc is not None:
            if not isinstance(lc, dict) or "above_input_tokens" not in lc:
                raise ValueError("long_context needs above_input_tokens")
            lc = {
                "above_input_tokens": int(lc["above_input_tokens"]),
                "unit_prices": {str(k): float(v) for k, v in (lc.get("unit_prices") or {}).items() if v is not None},
            }
        return CatalogEntry(
            provider=provider, model_pattern=pattern, unit_prices=clean_prices,
            region=d.get("region"), effective_from=eff_from,
            effective_to=str(eff_to) if eff_to else None, tier_factors=tiers,
            long_context=lc, source=source, note=str(d.get("note", "")),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"pricing: ignoring malformed {source} catalog entry {d!r}: {e}")
        return None


# ─── Built-in public list prices ─────────────────────────────────────────────
# USD per million tokens.  Public on-demand rates as published on the
# providers' pricing pages.  ONLY rates known with confidence are listed;
# models absent here price as unknown by design — add them to
# ~/.ziya/pricing.json rather than guessing here.  See Docs/CostAccounting.md
# for the list of shipped models that are currently unpriced.

def _anthropic_card(inp, out, cr, cw5, cw1, note="") -> Dict[str, Any]:
    return {
        "unit_prices": {"input": inp, "output": out, "cache_read": cr,
                        "cache_write_5m": cw5, "cache_write_1h": cw1},
        "tier_factors": {"standard": 1.0, "batch": 0.5},
        "note": note,
    }


_CLAUDE_CARDS = [
    # (patterns, card)
    (["*claude-opus-4-1*", "*claude-opus-4-2025*"], _anthropic_card(15.0, 75.0, 1.5, 18.75, 30.0)),
    (["*claude-opus-4-5*"], _anthropic_card(5.0, 25.0, 0.5, 6.25, 10.0)),
    (["*claude-sonnet-4-5*", "*claude-sonnet-4-2025*", "*claude-3-7-sonnet*", "*claude-3-5-sonnet*"],
     dict(_anthropic_card(3.0, 15.0, 0.30, 3.75, 6.0),
          long_context={"above_input_tokens": 200_000,
                        "unit_prices": {"input": 6.0, "output": 22.5, "cache_read": 0.60,
                                        "cache_write_5m": 7.5, "cache_write_1h": 12.0}})),
    (["*claude-haiku-4-5*"], _anthropic_card(1.0, 5.0, 0.10, 1.25, 2.0)),
    (["*claude-3-5-haiku*"], _anthropic_card(0.80, 4.0, 0.08, 1.0, 1.6)),
    (["*claude-3-haiku*"], _anthropic_card(0.25, 1.25, 0.03, 0.30, 0.50)),
    (["*claude-3-sonnet*"], _anthropic_card(3.0, 15.0, 0.30, 3.75, 6.0, "legacy")),
]


def _expand(provider: str, cards: Iterable[Tuple[List[str], Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for patterns, card in cards:
        for p in patterns:
            out.append(dict(card, provider=provider, model_pattern=p))
    return out


def _simple(inp, out, cache_read=None, tiers=None, note="", **extra) -> Dict[str, Any]:
    prices = {"input": inp, "output": out}
    if cache_read is not None:
        prices["cache_read"] = cache_read
    d = {"unit_prices": prices, "tier_factors": tiers or {"standard": 1.0}, "note": note}
    d.update(extra)
    return d


_OPENAI_TIERS = {"standard": 1.0, "priority": 2.0, "flex": 0.5, "batch": 0.5}

BUILTIN_CATALOG: List[Dict[str, Any]] = (
    # Claude on Anthropic direct and on Bedrock (same public list).  Bedrock
    # ids carry us./eu./global. prefixes and -vN:0 suffixes; the globs absorb
    # both.  Bedrock "batch" is 0.5 like Anthropic's Batch API.
    _expand("anthropic", _CLAUDE_CARDS)
    + _expand("bedrock", _CLAUDE_CARDS)
    # Amazon Nova.  Cache-read rates deliberately omitted (unknown).
    + _expand("bedrock", [
        (["*amazon.nova-micro*"], _simple(0.035, 0.14)),
        (["*amazon.nova-lite*"], _simple(0.06, 0.24)),
        (["*amazon.nova-pro*"], _simple(0.80, 3.20)),
        (["*amazon.nova-premier*"], _simple(2.50, 12.50)),
        (["*deepseek.r1*"], _simple(1.35, 5.40)),
        (["*openai.gpt-oss-120b*"], _simple(0.15, 0.60)),
        (["*openai.gpt-oss-20b*"], _simple(0.07, 0.30)),
        (["*meta.llama3-3-70b*"], _simple(0.72, 0.72)),
        (["*meta.llama4-maverick*"], _simple(0.24, 0.97)),
        (["*meta.llama4-scout*"], _simple(0.17, 0.66)),
        (["*qwen.qwen3-coder-480b*"], _simple(0.22, 1.80)),
    ])
    + _expand("openai", [
        (["gpt-5", "gpt-5-2*"], _simple(1.25, 10.0, 0.125, _OPENAI_TIERS)),
        (["gpt-5-mini*"], _simple(0.25, 2.0, 0.025, _OPENAI_TIERS)),
        (["gpt-5-nano*"], _simple(0.05, 0.40, 0.005, _OPENAI_TIERS)),
        (["gpt-4.1", "gpt-4.1-2*"], _simple(2.0, 8.0, 0.50, _OPENAI_TIERS)),
        (["gpt-4.1-mini*"], _simple(0.40, 1.60, 0.10, _OPENAI_TIERS)),
        (["gpt-4.1-nano*"], _simple(0.10, 0.40, 0.025, _OPENAI_TIERS)),
        (["gpt-4o", "gpt-4o-2*"], _simple(2.50, 10.0, 1.25, _OPENAI_TIERS)),
        (["gpt-4o-mini*"], _simple(0.15, 0.60, 0.075, _OPENAI_TIERS)),
        (["o3", "o3-2*"], _simple(2.0, 8.0, 0.50, _OPENAI_TIERS)),
        (["o3-mini*"], _simple(1.10, 4.40, 0.55, _OPENAI_TIERS)),
        (["o4-mini*"], _simple(1.10, 4.40, 0.275, _OPENAI_TIERS)),
        (["o1", "o1-2*"], _simple(15.0, 60.0, 7.50, _OPENAI_TIERS)),
    ])
    + _expand("google", [
        (["gemini-2.5-pro*"], _simple(
            1.25, 10.0, 0.31, {"standard": 1.0, "batch": 0.5},
            long_context={"above_input_tokens": 200_000,
                          "unit_prices": {"input": 2.50, "output": 15.0, "cache_read": 0.625}})),
        (["gemini-2.5-flash-lite*"], _simple(0.10, 0.40, 0.01, {"standard": 1.0, "batch": 0.5})),
        (["gemini-2.5-flash*"], _simple(0.30, 2.50, 0.03, {"standard": 1.0, "batch": 0.5})),
        (["gemini-2.0-flash-lite*"], _simple(0.075, 0.30, None, {"standard": 1.0, "batch": 0.5})),
        (["gemini-2.0-flash*"], _simple(0.10, 0.40, 0.025, {"standard": 1.0, "batch": 0.5})),
        (["gemini-3-pro*"], _simple(
            2.0, 12.0, 0.20, {"standard": 1.0, "batch": 0.5},
            long_context={"above_input_tokens": 200_000,
                          "unit_prices": {"input": 4.0, "output": 18.0, "cache_read": 0.40}},
            note="preview pricing")),
    ])
    + _expand("zai", [
        (["glm-4.6*", "glm-4.5", "glm-4.5-2*"], _simple(0.60, 2.20, 0.11)),
        (["glm-4.5-air*"], _simple(0.20, 1.10, 0.03)),
        (["glm-4.5-flash*"], _simple(0.0, 0.0, 0.0)),
    ])
    # Local inference: a real price of zero.
    + [{"provider": "local", "model_pattern": "*",
        "unit_prices": {d: 0.0 for d in DIMENSIONS}, "tier_factors": {"standard": 1.0},
        "note": "local inference"}]
)


# ─── Catalog ─────────────────────────────────────────────────────────────────

def pricing_file() -> Path:
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / PRICING_FILENAME


def _read_user_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"pricing: ignoring {path}: could not read or parse it ({e}).")
        return {}
    if not isinstance(data, dict):
        logger.warning(f"pricing: ignoring {path}: expected a JSON object.")
        return {}
    return data


class PricingCatalog:
    """Built-in entries plus the user overlay, with dated lookup."""

    def __init__(self, entries: List[CatalogEntry], builtin_revision: str,
                 user_revision: Optional[str], user_hash: str, plan_spec: Optional[Dict[str, Any]]):
        self.entries = entries
        self.builtin_revision = builtin_revision
        self.user_revision = user_revision
        self.user_hash = user_hash
        # Raw ``plan`` object from the user file, consumed by
        # app.cost.rate_plans.resolve_active_plan.
        self.plan_spec = plan_spec

    @property
    def version(self) -> str:
        """Identifier stamped on every priced line so a figure can always be
        traced to the exact table that produced it."""
        if self.user_hash:
            user = self.user_revision or self.user_hash
            return f"builtin:{self.builtin_revision}+user:{user}"
        return f"builtin:{self.builtin_revision}"

    def lookup(self, provider: str, model_id: str, region: Optional[str], ts: float) -> Optional[CatalogEntry]:
        """Most specific entry in force at ``ts``, or None (= unknown)."""
        candidates = [e for e in self.entries if e.matches(provider, model_id, region, ts)]
        if not candidates:
            # Regional Bedrock ids sometimes arrive without a region even
            # though the entry is region-scoped; nothing to do — unknown.
            return None
        candidates.sort(key=lambda e: e.specificity(), reverse=True)
        return candidates[0]

    def describe(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "builtin_revision": self.builtin_revision,
            "user_revision": self.user_revision,
            "builtin_entries": sum(1 for e in self.entries if e.source == "builtin"),
            "user_entries": sum(1 for e in self.entries if e.source == "user"),
            "user_file": str(pricing_file()),
        }


def build_catalog(user_file: Optional[Path] = None,
                  builtin: Optional[List[Dict[str, Any]]] = None) -> PricingCatalog:
    """Assemble a catalog.  Both arguments exist for tests; production
    callers use :func:`get_catalog`."""
    entries: List[CatalogEntry] = []
    for d in (builtin if builtin is not None else BUILTIN_CATALOG):
        e = _entry_from_dict(d, "builtin")
        if e is not None:
            entries.append(e)

    path = user_file if user_file is not None else pricing_file()
    raw = _read_user_file(path)
    user_hash = ""
    user_rev: Optional[str] = None
    plan_spec: Optional[Dict[str, Any]] = None
    if raw:
        user_hash = hashlib.sha256(
            json.dumps(raw, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        rev = raw.get("revision")
        user_rev = str(rev) if rev else None
        user_entries = raw.get("entries") or []
        if not isinstance(user_entries, list):
            logger.warning(f"pricing: {path}: 'entries' must be a list; ignoring.")
            user_entries = []
        for d in user_entries:
            if isinstance(d, dict):
                e = _entry_from_dict(d, "user")
                if e is not None:
                    entries.append(e)
        plan = raw.get("plan")
        if isinstance(plan, dict):
            plan_spec = plan
        logger.info(
            f"pricing: loaded {path} (revision={user_rev or user_hash}, "
            f"entries={sum(1 for e in entries if e.source == 'user')})"
        )
    return PricingCatalog(entries, CATALOG_REVISION, user_rev, user_hash, plan_spec)


_catalog: Optional[PricingCatalog] = None
_catalog_mtime: Optional[float] = None


def get_catalog() -> PricingCatalog:
    """Process-wide catalog.  Re-reads ``pricing.json`` when its mtime
    changes so a hand-edit takes effect without a restart; the built-in
    table is import-time constant."""
    global _catalog, _catalog_mtime
    path = pricing_file()
    try:
        mtime: Optional[float] = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    if _catalog is None or mtime != _catalog_mtime:
        _catalog = build_catalog(path)
        _catalog_mtime = mtime
    return _catalog


def reset_catalog_cache() -> None:
    """Test hook."""
    global _catalog, _catalog_mtime
    _catalog = None
    _catalog_mtime = None
