"""
Pure activation resolver (design/capabilities-hub.md, slice 2).

``resolve_activation(layers, items)`` takes the stored placements of every
writable layer and the installed items, and returns each item's effective
placement and where it came from.  No I/O, no storage, no knowledge of skills
or MCP beyond the ``ItemSpec`` the caller built — which is what lets the same
behaviour be asserted from ``tests/fixtures/activation_cases.json`` by both
this module and its jest mirror (``frontend/src/utils/activation.ts``).

Rules, all from the design note:
  * layers are first-set-wins: conversation -> project -> user -> default;
  * defaults: discoverable skill => ondemand, user-selectable => off,
    MCP => always;
  * environment-tier items are never placed — always ``always``, origin
    ``environment`` — and any stored entry for them is reported dropped;
  * a stored key that is not installed here is dropped and reported, so the
    UI can say *not installed here* rather than silently losing it;
  * MCP ``ondemand`` is rejected on write and ignored on read until slice 9.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional

from app.models.activation import (
    PLACEMENT_STATES, WRITABLE_LAYERS,
    REASON_ENVIRONMENT, REASON_INVALID_LAYER, REASON_INVALID_STATE,
    REASON_MCP_ONDEMAND, REASON_UNKNOWN_KEY,
    DroppedEntry, ItemSpec, PlacementRejected, ResolveResult, ResolvedItem,
)

Layers = Mapping[str, Mapping[str, Optional[str]]]


def default_placement(item: ItemSpec) -> str:
    if item.tier == "environment":
        return "always"
    if item.kind == "mcp":
        return "always"
    return "ondemand" if item.discoverable else "off"


def _rejection_reason(item: Optional[ItemSpec], state: Optional[str]) -> Optional[str]:
    """Why ``state`` cannot be stored for ``item``; None when it can.

    Shared by the write-time validator and the read-time resolver so a value
    that would be refused on PUT is also ignored if it is already on disk
    (a file hand-edited, or written by a build that predates a rule).
    """
    if item is None:
        return REASON_UNKNOWN_KEY
    if item.tier == "environment":
        return REASON_ENVIRONMENT
    if state is None:
        return None  # clearing a placement is always allowed
    if state not in PLACEMENT_STATES:
        return REASON_INVALID_STATE
    if item.kind == "mcp" and state == "ondemand":
        return REASON_MCP_ONDEMAND
    return None


def validate_placement(
    items: Iterable[ItemSpec], key: str, layer: str, state: Optional[str],
) -> None:
    """Raise PlacementRejected if ``(key, layer, state)`` may not be written.

    The API maps the exception to 409.  Checked BEFORE the storage write so a
    refused placement leaves no trace.
    """
    if layer not in WRITABLE_LAYERS:
        raise PlacementRejected(REASON_INVALID_LAYER, key, f"not a writable layer: {layer}")
    by_key = {i.key: i for i in items}
    reason = _rejection_reason(by_key.get(key), state)
    if reason is not None:
        raise PlacementRejected(reason, key)


def resolve_activation(layers: Layers, items: Iterable[ItemSpec]) -> ResolveResult:
    """Resolve every item's effective placement across the layers.

    ``layers`` maps layer name -> {key -> state}.  Missing layers and missing
    keys mean inherit.  Layers not in WRITABLE_LAYERS are ignored: only the
    three real layers can hold a placement, and ``default`` is computed.
    Item order is preserved so the UI is stable between GETs.
    """
    item_list = list(items)
    by_key: Dict[str, ItemSpec] = {i.key: i for i in item_list}
    dropped: List[DroppedEntry] = []

    # Pass 1: validate every stored entry once, so the report is complete even
    # for keys that never reach an item (unknown keys) and so an entry that
    # is refused does not participate in first-set-wins below.
    honoured: Dict[str, Dict[str, str]] = {layer: {} for layer in WRITABLE_LAYERS}
    for layer in WRITABLE_LAYERS:
        for key, state in (layers.get(layer) or {}).items():
            if state is None:
                continue  # explicit inherit; nothing stored
            reason = _rejection_reason(by_key.get(key), state)
            if reason is not None:
                dropped.append(DroppedEntry(key=key, layer=layer, reason=reason))
                continue
            honoured[layer][key] = state

    resolved: List[ResolvedItem] = []
    for item in item_list:
        default = default_placement(item)
        placements = {layer: honoured[layer].get(item.key) for layer in WRITABLE_LAYERS}
        if item.tier == "environment":
            effective, origin = "always", "environment"
        else:
            effective, origin = default, "default"
            for layer in WRITABLE_LAYERS:
                if placements[layer] is not None:
                    effective, origin = placements[layer], layer
                    break
        resolved.append(ResolvedItem(
            key=item.key, kind=item.kind, tier=item.tier,
            placements=placements, effective=effective, origin=origin,
            default=default,
        ))
    return ResolveResult(items=resolved, dropped=dropped)
