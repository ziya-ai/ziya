"""What the MCP status heading reports: tokens actually sent for MCP, split
by bench placement.

The status route used to report every enabled tool of every connected server
as "in context".  A within-reach server sends one catalog line until its
tools are loaded, and a shelved server sends nothing, so that figure was the
weight of everything installed, not of what a request carries.

``breakdown`` is pure.  It consumes the same ``McpFilter`` the request path
builds (``bench_prompt.mcp_filter_for_root``), so the heading and the tool
list cannot classify a server differently.

Buckets:

``on_hand``          schemas of servers placed on hand (or not placeable)
``loaded``           within-reach tools this conversation has loaded
``reach_summaries``  the "MCP servers within reach" system-prompt section
``instructions``     instruction estimate for the current model
``deferred``         within-reach schemas sent with ``defer_loading`` on a
                     model with native tool search.  The API holds them out
                     of the model's context (design/mcp-on-demand.md:
                     identical requests billed input=82 with two deferred
                     tools present), so they are reported but NOT added to
                     ``sent_total``.  What the model has since found is the
                     API's state and is not known here.
``not_sent``         ``reach_unloaded``, ``shelved``, ``disabled``
``per_server``       ``{name: {placement, sent, held}}`` for the server rows;
                     placement is on_hand | loaded | partial | reach |
                     deferred | shelved | disabled
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from app.utils.logging_utils import logger


def _bucket() -> Dict[str, int]:
    return {"tokens": 0, "servers": 0, "tools": 0}


def _add(bucket: Dict[str, int], tokens: int, tools: int) -> None:
    bucket["tokens"] += tokens
    bucket["servers"] += 1
    bucket["tools"] += tools


def breakdown(
    servers: Mapping[str, Mapping[str, Any]],
    filt: Any,
    *,
    deferral: bool = False,
    catalog_tokens: int = 0,
    instruction_tokens: int = 0,
) -> Dict[str, Any]:
    """Split connected servers' tool tokens by what a request does with them.

    ``servers``: ``{name: {"enabled": bool, "total": int, "enabled_tokens":
    int, "tools": {enabled tool name: tokens}}}``.  ``filt`` is an
    ``McpFilter`` (``shelved``, ``reach``, ``withheld``, ``partial``,
    ``allows``); None means no bench information, so everything is on hand.
    """
    shelved = set(getattr(filt, "shelved", ()) or ())
    reach = set(getattr(filt, "reach", ()) or ())
    withheld = set(getattr(filt, "withheld", ()) or ())
    partial = getattr(filt, "partial", None) or {}

    on_hand, loaded, deferred = _bucket(), _bucket(), _bucket()
    not_sent = {"reach_unloaded": 0, "shelved": 0, "disabled": 0}

    # Per server: where it sits, what of it is sent, and what is held back
    # (unloaded, deferred or shelved).  The panel's server rows read this.
    per_server: Dict[str, Dict[str, Any]] = {}

    for name, s in servers.items():
        total = int(s.get("total") or 0)
        if not s.get("enabled", True):
            not_sent["disabled"] += total
            per_server[name] = {"placement": "disabled", "sent": 0, "held": total}
            continue
        enabled = int(s.get("enabled_tokens") or 0)
        tools: Mapping[str, int] = s.get("tools") or {}
        not_sent["disabled"] += max(0, total - enabled)
        if name in shelved:
            not_sent["shelved"] += enabled
            per_server[name] = {"placement": "shelved", "sent": 0, "held": enabled}
        elif name in reach:
            if deferral:
                _add(deferred, enabled, len(tools))
                per_server[name] = {"placement": "deferred", "sent": 0, "held": enabled}
            elif name in withheld:
                not_sent["reach_unloaded"] += enabled
                per_server[name] = {"placement": "reach", "sent": 0, "held": enabled}
            elif name in partial:
                got = {t: n for t, n in tools.items() if filt.allows(name, t)}
                part = min(enabled, sum(int(n or 0) for n in got.values()))
                if got:
                    _add(loaded, part, len(got))
                not_sent["reach_unloaded"] += enabled - part
                per_server[name] = {"placement": "partial" if got else "reach",
                                    "sent": part, "held": enabled - part}
            else:
                _add(loaded, enabled, len(tools))
                per_server[name] = {"placement": "loaded", "sent": enabled, "held": 0}
        else:
            _add(on_hand, enabled, len(tools))
            per_server[name] = {"placement": "on_hand", "sent": enabled, "held": 0}

    summaries = {"tokens": int(catalog_tokens) if reach else 0, "servers": len(reach)}
    sent_total = (on_hand["tokens"] + loaded["tokens"] + summaries["tokens"]
                  + int(instruction_tokens))
    return {
        "deferral": bool(deferral),
        "on_hand": on_hand,
        "loaded": loaded,
        "reach_summaries": summaries,
        "deferred": deferred,
        "instructions": {"tokens": int(instruction_tokens)},
        "sent_total": sent_total,
        "not_sent": not_sent,
        "per_server": per_server,
    }


def _as_str(value: Any) -> str:
    """A model id may be a per-region dict; any one value identifies the model."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for v in value.values():
            if isinstance(v, str):
                return v
    return ""


def deferral_for_current_model() -> bool:
    """Whether the current endpoint + model send within-reach tools deferred.

    The same gate the prompt builder uses (``mcp_ondemand.native_search_for``).
    False on any error: an unknown model is reported as not deferring.
    """
    try:
        from app.agents.models import ModelManager
        from app.config.env_registry import ziya_env
        from app.utils.mcp_ondemand import native_search_for
        endpoint = ziya_env("ZIYA_ENDPOINT")
        alias = ModelManager.get_model_alias()
        cfg = ModelManager.get_model_config(endpoint, alias) or {}
        model_id = _as_str(ModelManager.get_model_id()) or _as_str(cfg.get("model_id"))
        return bool(native_search_for(endpoint, cfg, model_id))
    except Exception as e:  # noqa: BLE001 -- a status read must not fail on model state
        logger.debug("mcp_token_breakdown: deferral unknown: %s", e)
        return False


def breakdown_for_request(
    servers: Mapping[str, Mapping[str, Any]],
    project_root: Optional[str],
    conversation_id: Optional[str],
    *,
    count_text: Callable[[str], int],
    instruction_tokens: int = 0,
    deferral: Optional[bool] = None,
) -> Dict[str, Any]:
    """``breakdown`` for the status route: resolves the bench filter, the
    deferral gate and the catalog section for this project and conversation.

    Never raises.  When the bench cannot be resolved the result has
    ``scope: "none"`` and every enabled server on hand, which is what the
    request path does in the same situation (it fails open).
    """
    filt = None
    scope = "none"
    catalog_tokens = 0
    if deferral is None:
        deferral = deferral_for_current_model()
    try:
        from app.utils import bench_prompt
        if bench_prompt.project_id_for_root(project_root):
            filt = bench_prompt.mcp_filter_for_root(project_root, conversation_id)
            scope = "conversation" if conversation_id else "project"
            if getattr(filt, "reach", None):
                from app.utils.mcp_ondemand import catalog_section_for_root
                section = catalog_section_for_root(project_root, conversation_id, native=deferral)
                catalog_tokens = int(count_text(section)) if section else 0
    except Exception as e:  # noqa: BLE001 -- the heading degrades, the modal still opens
        logger.warning("mcp_token_breakdown: bench unavailable, reporting all on hand: %s", e)
        filt, scope, catalog_tokens = None, "none", 0
    out = breakdown(servers, filt, deferral=bool(deferral), catalog_tokens=catalog_tokens,
                    instruction_tokens=instruction_tokens)
    out["scope"] = scope
    out["conversation_id"] = conversation_id if scope == "conversation" else None
    return out
