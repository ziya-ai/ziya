"""
Bench service (design/capabilities-hub.md, slice 3).

Sits between the storage layers and the pure resolver:

  * assembles ``ItemSpec``s (plus display metadata) from the skill catalog and
    the MCP manager — the resolver never touches either;
  * loads the three writable layers: user and project via ``JsonStateStore``
    (slice 1), conversation from the chat record's ``placements`` field;
  * writes a placement after ``validate_placement`` has accepted it, so a
    refused write leaves no trace;
  * exposes ``resolve_bench`` as THE seam the prompt builder reads.  The GET
    route and the prompt path call the same function, which is what makes
    "prompt-builder always-set == GET effective always-set" hold by
    construction rather than by two implementations agreeing.

Layer documents are ``{"placements": {key: state}}``; the wrapper leaves room
for per-layer metadata without a migration.
"""
from __future__ import annotations
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models.activation import ItemSpec, PlacementRejected, ResolveResult, make_key
from app.services.activation import resolve_activation, validate_placement
from app.storage.json_state import JsonStateStore
from app.utils.paths import project_state_file, user_state_file

logger = logging.getLogger(__name__)

STATE_NAME = "bench_placements"


@dataclass
class BenchItem:
    """An ``ItemSpec`` plus what the hub displays about it."""
    spec: ItemSpec
    name: str
    provenance: str            # builtin | project | user | custom | registry
    tokens: int = 0
    health: Dict[str, Any] = field(default_factory=dict)
    # Where it came from, for the "by package" view: a list of paths,
    # outermost first.  More than one when several packages ship it.
    groups: List[List[str]] = field(default_factory=list)


# -- item assembly ---------------------------------------------------------

def _capability_meta(kind: str, name: str, path: Optional[str] = None,
                     config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Plugin-supplied grouping and default for one capability
    (app.plugins.capability_metadata).  Never raises: the MCP half runs on
    the request path."""
    try:
        from app.plugins import capability_metadata
        return capability_metadata(kind, name, path=path, config=config)
    except Exception as e:  # noqa: BLE001
        logger.debug("bench: capability metadata unavailable: %s", e)
        return {"groups": [], "default": None}


def _skill_storage(project_id: str):
    """One seam for the skill records; tests substitute a tmp-dir storage.

    ``SkillStorage.list()`` covers seeded builtins, stored custom skills and
    file-discovered SKILL.md (bodies unloaded); ``get(id)`` loads a body.
    """
    from app.api.skills import get_skill_storage
    return get_skill_storage(project_id)


def _skill_items(project_id: str) -> List[BenchItem]:
    out: List[BenchItem] = []
    seen = set()
    for s in _skill_storage(project_id).list():
        key = make_key("skill", s.name)
        if key in seen:
            continue  # discovery already shadows by name; keep the winner
        seen.add(key)
        out.append(BenchItem(
            spec=ItemSpec(key=key, kind="skill", tier="placeable",
                          discoverable=(s.visibility == "model_discoverable")),
            name=s.name,
            provenance="builtin" if s.isBuiltIn else (s.source or "custom"),
            tokens=s.tokenCount or 0,
            groups=_capability_meta(
                "skill", s.name, path=getattr(s, "skillPath", None))["groups"],
        ))
    return out


# -- authorization gates ----------------------------------------------------
# A placement says where a capability SITS; a gate says whether it may RUN.
# Today the one gate is the rug-pull quarantine (manager.py): the server's
# tool definitions changed since a human approved them, so its tools are
# withheld until someone re-authorizes.  Moving the card never lifts a gate.

QUARANTINE_GATE = {"state": "quarantined",
                   "reason": "Tool definitions changed since they were approved; "
                             "re-authorize to accept the new ones"}


def _quarantined(mgr) -> set:
    return set(getattr(mgr, "_quarantined_servers", None) or ())


def _client_tools(mgr, name: str) -> List[Any]:
    """Raw tools from the server's client; get_all_tools() withholds a
    quarantined server's, so this is how the bench can still list them."""
    client = (getattr(mgr, "clients", None) or {}).get(name)
    return list(getattr(client, "tools", None) or []) if client else []


def _tool_dict(t: Any) -> Dict[str, Any]:
    return {"name": t.name, "description": getattr(t, "description", "") or "",
            "inputSchema": getattr(t, "inputSchema", None) or {}}


def _mcp_items() -> List[BenchItem]:
    out: List[BenchItem] = []
    try:
        from app.mcp.manager import get_mcp_manager
        mgr = get_mcp_manager()
    except Exception as e:
        logger.debug("bench: MCP manager unavailable: %s", e)
        return out
    tools_by_server: Dict[str, List[Dict[str, Any]]] = {}
    try:
        if mgr.is_initialized:
            for t in mgr.get_all_tools():
                srv = getattr(t, "_server_name", None)
                if srv:
                    tools_by_server.setdefault(srv, []).append(_tool_dict(t))
    except Exception as e:
        logger.debug("bench: MCP tools unavailable: %s", e)
    # A quarantined server IS connected; get_all_tools() just withholds it.
    # Without this the card said "not connected" for a server that was
    # connected and blocked.  Its tokens are not counted (see api._view_model).
    quarantined = _quarantined(mgr)
    for srv in quarantined:
        if srv not in tools_by_server:
            tools_by_server[srv] = [_tool_dict(t) for t in _client_tools(mgr, srv)]
    # get_all_tools() keeps permission-disabled tools as inert entries; the
    # request path drops them, so the bench must cost only what is sent.
    perms: Dict[str, Any] = {}
    try:
        from app.mcp.permissions import get_permissions_manager
        perms = get_permissions_manager().get_permissions() or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("bench: permissions unavailable: %s", e)
    default_perm = perms.get("defaults", {}).get("tool", "enabled")
    for name, cfg in sorted((mgr.server_configs or {}).items()):
        all_tools = tools_by_server.get(name, [])
        per = perms.get("servers", {}).get(name, {}).get("tools", {})
        enabled = [t for t in all_tools
                   if per.get(t["name"], {}).get("permission", default_perm) != "disabled"]
        tokens = withheld = 0
        try:
            from app.routes.mcp_routes import count_server_tool_tokens
            tokens = count_server_tool_tokens(enabled)
            if len(enabled) < len(all_tools):
                withheld = count_server_tool_tokens(all_tools) - tokens
        except Exception:
            pass
        spec = _mcp_spec(name, cfg)
        out.append(BenchItem(
            spec=spec,
            name=name,
            provenance="builtin" if spec.tier == "environment" else "user",
            tokens=tokens,
            health={"enabled": cfg.get("enabled", True),
                    "connected": name in tools_by_server,
                    "tool_count": len(all_tools), "enabled_count": len(enabled),
                    "withheld_tokens": withheld,
                    "quarantined": name in quarantined,
                    "gate": dict(QUARANTINE_GATE) if name in quarantined else None},
            groups=_capability_meta("mcp", name, config=cfg)["groups"],
        ))
    return out


def _mcp_spec(name: str, cfg: Dict[str, Any]) -> ItemSpec:
    """One tier rule for the hub view and the per-request tool filter."""
    builtin = bool(cfg.get("builtin", False))
    # A plugin may declare a default (e.g. registry installs sit within
    # reach); environment-tier servers are never placed, so it is not asked.
    default = None if builtin else _capability_meta("mcp", name, config=cfg)["default"]
    return ItemSpec(key=make_key("mcp", name), kind="mcp",
                    tier="environment" if builtin else "placeable", default_state=default)


def _mcp_specs() -> List[ItemSpec]:
    """MCP specs from config alone: no tool listing, no token counting.

    Used on the request path, where ``_mcp_items``' per-server tokenization
    would cost every turn for a number nobody reads there.
    """
    try:
        from app.mcp.manager import get_mcp_manager
        configs = get_mcp_manager().server_configs or {}
    except Exception as e:
        logger.debug("bench: MCP manager unavailable: %s", e)
        return []
    return [_mcp_spec(n, c) for n, c in sorted(configs.items())]


def mcp_effective(project_id: str, conversation_id: Optional[str]) -> Dict[str, str]:
    """``{server: always|ondemand|off}`` for every placeable MCP server.

    Only MCP specs are built: skill discovery walks the disk and is
    irrelevant here, so skill-layer entries resolve as unknown and are
    ignored with ``dropped``.  Within-reach servers are withheld until
    loaded; that rule lives in app/utils/mcp_ondemand.py.
    """
    result = resolve_activation(load_layers(project_id, conversation_id), _mcp_specs())
    return {r.key.partition(":")[2]: r.effective for r in result.items
            if r.kind == "mcp" and r.tier == "placeable"}


def withheld_mcp_servers(project_id: str, conversation_id: Optional[str]) -> set:
    """Shelved MCP server names (``off``), whose tools never reach the model.

    Design rule (capabilities-hub.md, Model): project/conversation ``off``
    keeps the server connected but withholds its tools.  User ``off`` also
    unspawns it (api/bench.py ``_sync_mcp_enabled``) and is included here
    too, so a failed sync cannot leak the tools.
    """
    return {n for n, s in mcp_effective(project_id, conversation_id).items() if s == "off"}


def _builtin_items() -> List[BenchItem]:
    """BUILTIN_TOOL_CATEGORIES as environment items (never placeable).

    ``health.enabled`` is the persisted toggle; a disabled category is still
    listed (so it can be re-enabled from the row) but weighs nothing.
    """
    try:
        from app.mcp.builtin_tools import describe_builtin_categories
        from app.routes.mcp_routes import count_server_tool_tokens
        cats = describe_builtin_categories()
    except Exception as e:  # noqa: BLE001
        logger.debug("bench: builtin categories unavailable: %s", e)
        return []
    out: List[BenchItem] = []
    for c in cats:
        try:
            tokens = count_server_tool_tokens(c["tools"])
        except Exception:
            tokens = 0
        out.append(BenchItem(
            spec=ItemSpec(key=make_key("builtin", c["category"]), kind="builtin", tier="environment"),
            name=c["name"],
            provenance="builtin",
            tokens=tokens,
            health={"enabled": c["enabled"], "default": c["enabled_by_default"],
                    "toggleable": True, "tool_count": len(c["tools"])},
        ))
    return out


def set_environment_enabled(category: str, enabled: bool) -> None:
    """Toggle a builtin category; KeyError for an unknown one."""
    from app.mcp.builtin_tools import set_builtin_category_enabled
    set_builtin_category_enabled(category, enabled)


def set_tool_enabled(server: str, tool: str, enabled: bool) -> None:
    """Per-tool switch on an MCP server (the permissions store's ``disabled``).

    A finer knob than placement: the server stays where it sits, one tool is
    withheld.  ``update_tool_permission`` saves and invalidates the tool cache.
    """
    from app.mcp.permissions import get_permissions_manager
    get_permissions_manager().update_tool_permission(server, tool, "enabled" if enabled else "disabled")


def reauthorize_mcp(server: str, force: bool = False) -> Dict[str, Any]:
    """Lift a rug-pull quarantine: the manager's human-initiated re-auth.
    Returns its result dict verbatim ({success, message} or the refusal with
    blocking_tools / advisory_tools / can_force)."""
    from app.mcp.manager import get_mcp_manager
    mgr = get_mcp_manager()
    if not getattr(mgr, "is_initialized", False):
        return {"success": False, "message": "MCP manager not initialized"}
    return mgr.reauthorize_server(server, force=force)


def _update_info(mgr, name: str, cfg: Dict[str, Any], held_tools: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    """Update policy for the drawer and, for a held server, what changed."""
    try:
        from app.utils import mcp_update_policy as upd
        return upd.describe(upd.state_dir_of(mgr), name, cfg,
                            [_tool_dict(t) for t in held_tools] if held_tools is not None else None)
    except Exception as e:  # noqa: BLE001
        logger.debug("bench: update policy unavailable for %s: %s", name, e)
        return None


def set_update_policy(server: str, policy: str, scope: str = "server") -> None:
    """What a later definition change does: ``ask`` or ``follow``.

    ``scope`` is ``server``, or ``publisher`` for every server installed from
    the same registry provider.  ValueError for a builtin (never gated), an
    unknown policy or scope, or a publisher scope on a server with none.
    """
    from app.mcp.manager import get_mcp_manager
    from app.utils import mcp_update_policy as upd
    mgr = get_mcp_manager()
    cfg = (mgr.server_configs or {}).get(server)
    if cfg is None:
        raise ItemNotFound(f"mcp:{server}")
    if cfg.get("builtin"):
        raise ValueError("built-in servers are not gated")
    state = upd.state_dir_of(mgr)
    if scope == "server":
        upd.set_server_policy(state, server, cfg, policy)
    elif scope == "publisher":
        publisher = upd.publisher_of(cfg)
        if not publisher:
            raise ValueError(f"{server} was not installed from a registry")
        upd.set_publisher_policy(state, publisher, policy)
    else:
        raise ValueError(f"unknown scope: {scope!r}")


# Turning this category off removes get_skill_details, so every within-reach
# skill becomes dead weight in the catalog.  Allowed (it is the user's
# environment) but the row must say so.
SKILLS_CATEGORY = "skills"


def collect_items(project_id: str) -> List[BenchItem]:
    return _skill_items(project_id) + _mcp_items() + _builtin_items()


def _norm_name(name: Any) -> str:
    """``kuiper-confluence-mcp`` and ``kuiperconfluencemcp`` compare equal."""
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def missing_capabilities() -> List[Dict[str, Any]]:
    """MCP servers an installed package needs that are not configured here.

    ``[{"kind": "mcp", "name", "groups": [[package, ...]], "installable"}]``,
    one row per server however many packages want it.  A configured server
    matches on its name or its ``service_id``.  Returns nothing when the
    server list cannot be read: "everything is missing" would be a false
    alarm.
    """
    try:
        from app.plugins import capability_requirements
        wanted = capability_requirements()
    except Exception as e:  # noqa: BLE001
        logger.debug("bench: capability requirements unavailable: %s", e)
        return []
    if not wanted:
        return []
    try:
        from app.mcp.manager import get_mcp_manager
        configs = get_mcp_manager().server_configs or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("bench: MCP manager unavailable: %s", e)
        return []
    have = set()
    for name, cfg in configs.items():
        have.add(_norm_name(name))
        if isinstance(cfg, dict) and cfg.get("service_id"):
            have.add(_norm_name(cfg["service_id"]))
    rows: Dict[str, Dict[str, Any]] = {}
    for w in wanted:
        ident = _norm_name(w["name"])
        if w["kind"] != "mcp" or ident in have:
            continue
        row = rows.setdefault(ident, {"kind": "mcp", "name": w["name"], "groups": [], "installable": False})
        if w["group"] and w["group"] not in row["groups"]:
            row["groups"].append(w["group"])
        row["installable"] = row["installable"] or w["installable"]
    return list(rows.values())


class ItemNotFound(LookupError):
    pass


def item_detail(project_id: str, key: str) -> Dict[str, Any]:
    """What the card back shows, loaded on demand.

    GET /bench stays light (one skill body can be 13K tokens) and the request
    path never pays for it.  Skills load their body via ``storage.get``;
    MCP reports config-level facts and connected tool names, never env/args.
    """
    kind, _, name = key.partition(":")
    if kind == "skill":
        storage = _skill_storage(project_id)
        s = next((s for s in storage.list() if s.name == name), None)
        if s is None:
            raise ItemNotFound(key)
        full = storage.get(s.id) or s
        from app.services.skill_sources import read_provenance, update_available
        prov = read_provenance(full.skillPath)
        return {
            "key": key, "kind": "skill", "name": full.name,
            "description": full.description or "",
            "prompt": full.prompt or "",
            "path": full.skillPath,
            "source": "builtin" if full.isBuiltIn else (full.source or "custom"),
            # The drawer's edit mode writes through the skills REST API, which
            # is keyed by Skill.id; the bench is keyed by name.  Only custom
            # skills are writable: SkillStorage refuses built-in and file-backed.
            "skill_id": full.id,
            "editable": (not full.isBuiltIn) and (full.source or "custom") == "custom",
            "tokens": full.tokenCount or 0,
            "keywords": list(full.keywords or []),
            "provenance_record": prov,
            "update_available": update_available(prov),
        }
    if kind == "mcp":
        try:
            from app.mcp.manager import get_mcp_manager
            mgr = get_mcp_manager()
            cfg = (mgr.server_configs or {}).get(name)
        except Exception as e:
            logger.debug("bench: MCP manager unavailable: %s", e)
            cfg = None
        if cfg is None:
            raise ItemNotFound(key)
        raw: List[Any] = []
        try:
            if mgr.is_initialized:
                raw = sorted((t for t in mgr.get_all_tools() if getattr(t, "_server_name", None) == name),
                             key=lambda t: t.name)
        except Exception as e:
            logger.debug("bench: MCP tools unavailable: %s", e)
        held = name in _quarantined(mgr)
        if held and not raw:
            # Held tools are listed so the user can see what re-authorizing accepts.
            raw = sorted(_client_tools(mgr, name), key=lambda t: t.name)
        tools = [t.name for t in raw]
        # Per-tool rows for the detail drawer: description, own token cost
        # (the estimator the server total uses) and the permission switch from
        # the existing store.  The drawer is where a server's tools are read,
        # so it is where they are judged and toggled.
        default = "enabled"
        per: Dict[str, Any] = {}
        try:
            from app.mcp.permissions import get_permissions_manager
            p = get_permissions_manager().get_permissions() or {}
            default = p.get("defaults", {}).get("tool", "enabled")
            per = p.get("servers", {}).get(name, {}).get("tools", {})
        except Exception as e:  # noqa: BLE001
            logger.debug("bench: permissions unavailable: %s", e)
        rows: List[Dict[str, Any]] = []
        for t in raw:
            desc = getattr(t, "description", "") or ""
            try:
                from app.routes.mcp_routes import count_server_tool_tokens
                tokens = count_server_tool_tokens([{"name": t.name, "description": desc,
                                                    "inputSchema": getattr(t, "inputSchema", None) or {}}])
            except Exception:  # noqa: BLE001
                tokens = 0
            rows.append({"name": t.name, "description": desc, "tokens": tokens,
                         "enabled": per.get(t.name, {}).get("permission", default) != "disabled"})
        perms = {r["name"]: r["enabled"] for r in rows}
        return {
            "key": key, "kind": "mcp", "name": name,
            "description": str(cfg.get("description") or ""),
            "tools": tools, "tool_permissions": perms, "tool_details": rows,
            "enabled": bool(cfg.get("enabled", True)),
            "connected": bool(tools) or held,
            "gate": dict(QUARANTINE_GATE) if held else None,
            "updates": _update_info(mgr, name, cfg, raw if held else None),
            "builtin": bool(cfg.get("builtin", False)),
        }
    if kind == "builtin":
        from app.mcp.builtin_tools import describe_builtin_categories
        c = next((c for c in describe_builtin_categories(include_hidden=True) if c["category"] == name), None)
        if c is None:
            raise ItemNotFound(key)
        return {
            "key": key, "kind": "builtin", "name": c["name"], "description": c["description"],
            "tools": [t["name"] for t in c["tools"]],
            "enabled": c["enabled"], "default": c["enabled_by_default"], "category": name,
        }
    raise ItemNotFound(key)


# -- layers ----------------------------------------------------------------

def _user_store() -> JsonStateStore:
    return JsonStateStore(user_state_file(STATE_NAME))


def _project_store(project_id: str) -> JsonStateStore:
    return JsonStateStore(project_state_file(project_id, STATE_NAME))


def _chat_storage(project_id: str):
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_project_dir
    return ChatStorage(get_project_dir(project_id))


def _conversation_placements(project_id: str, conversation_id: Optional[str]) -> Dict[str, Optional[str]]:
    if not conversation_id:
        return {}
    chat = _chat_storage(project_id).get(conversation_id)
    if chat is None:
        return {}
    return dict(getattr(chat, "placements", None) or {})


def conversation_known(project_id: str, conversation_id: Optional[str]) -> bool:
    """Whether the conversation layer can be WRITTEN for this id.

    ``place`` raises ChatNotFound for an id with no record, and a brand-new
    chat is an empty shell the client never sends to the server.  The view
    model must not offer a lens whose every write would 404 (see the
    conversation-lens test in tests/test_bench_api.py)."""
    return bool(conversation_id) and _chat_storage(project_id).get(conversation_id) is not None


def load_layers(project_id: str, conversation_id: Optional[str]) -> Dict[str, Dict[str, Optional[str]]]:
    return {
        "conversation": _conversation_placements(project_id, conversation_id),
        "project": dict(_project_store(project_id).get("placements", {}) or {}),
        "user": dict(_user_store().get("placements", {}) or {}),
    }


PLACEMENT_VIAS = ("hub", "migration", "palette", "api")


def placement_meta(project_id: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Who/when/how each user- and project-layer placement was written.

    ``{layer: {key: {"via": ..., "at": epoch_seconds}}}``.  Stored beside
    ``placements`` in the layer document so the resolver's input is unchanged.
    The conversation layer lives on the chat record, which has no such field;
    it reports no meta rather than an invented one.
    """
    return {
        "project": dict(_project_store(project_id).get("meta", {}) or {}),
        "user": dict(_user_store().get("meta", {}) or {}),
        "conversation": {},
    }


# -- resolve ---------------------------------------------------------------

def resolve_bench(project_id: str, conversation_id: Optional[str],
                  items: Optional[List[BenchItem]] = None) -> ResolveResult:
    """The one resolution both GET /bench and the prompt builder read."""
    items = collect_items(project_id) if items is None else items
    return resolve_activation(load_layers(project_id, conversation_id), [i.spec for i in items])


def always_skill_names(project_id: str, conversation_id: Optional[str]) -> List[str]:
    """Skill names whose prompt text goes in every turn.

    This is the prompt builder's read of the bench.  It returns names rather
    than prompt text so the caller can load prompt, ``modelOverrides`` and
    ``preferredToolIds`` from the SAME skill records in one place — moving
    one of the three alone would split a skill's effects across two sources.
    """
    result = resolve_bench(project_id, conversation_id)
    return [k.partition(":")[2] for k in result.always_set() if k.startswith("skill:")]


@dataclass
class PromptInputs:
    """Everything the prompt path takes from the bench, assembled in one place.

    Prompt text, ``modelOverrides`` and ``toolIds`` are read from the SAME
    skill records in one pass: the client used to compute all three but only
    the text ever reached the wire (chatApi passes undefined for the other
    two), so a skill's temperature override silently never applied.
    """
    always_names: List[str]
    catalog_names: List[str]
    system_prompt_addition: str
    model_overrides: Dict[str, Any]
    preferred_tool_ids: List[str]
    # True once ANY skill placement is stored at any layer.  Until slice 4
    # migrates ``activeSkillIds`` out of localStorage, a bench with no
    # placements means "not adopted yet", not "everything off", and the
    # client-assembled prompt must keep working.  See bench_prompt.merge.
    authoritative: bool
    # Agent overlay (design/agent-overlay.md): the active agent's prompt,
    # context files and skills.  Kept apart from ``system_prompt_addition``
    # so it reaches the turn whether or not the bench is authoritative.
    agent_addition: str = ""
    active_agent: Optional[str] = None


_OVERRIDE_FIELDS = ("temperature", "maxOutputTokens", "thinkingMode")


def prompt_inputs(project_id: str, conversation_id: Optional[str],
                  items: Optional[List[BenchItem]] = None) -> PromptInputs:
    items = collect_items(project_id) if items is None else items
    result = resolve_activation(load_layers(project_id, conversation_id),
                                [i.spec for i in items])
    skill_items = [r for r in result.items if r.kind == "skill"]
    always = {r.key.partition(":")[2] for r in skill_items if r.effective == "always"}
    catalog = [r.key.partition(":")[2] for r in skill_items if r.effective == "ondemand"]
    authoritative = any(r.origin != "default" for r in skill_items)

    storage = _skill_storage(project_id)
    blocks: List[str] = []
    always_names: List[str] = []
    overrides: Dict[str, Any] = {}
    tool_ids: List[str] = []
    for s in storage.list():
        if s.name not in always or s.name in always_names:
            continue
        # list() leaves discovered bodies unloaded; get() loads them.
        full = storage.get(s.id) or s
        always_names.append(full.name)
        if full.prompt:
            from app.utils.skill_resources import skill_activation_body
            blocks.append(
                f"[Active Skill: {full.name}]\n{skill_activation_body(full)}")
        mo = full.modelOverrides
        if mo is not None:
            for f in _OVERRIDE_FIELDS:
                v = getattr(mo, f, None)
                if v is not None:
                    overrides[f] = v  # last-write-wins, as the client did
        for t in full.toolIds or []:
            if t not in tool_ids:
                tool_ids.append(t)

    # The per-chat additional prompt rode on the same client string; keep it
    # from the record so taking over the string does not drop it.
    if conversation_id:
        chat = _chat_storage(project_id).get(conversation_id)
        extra = getattr(chat, "additionalPrompt", None) if chat else None
        if extra:
            blocks.append(extra)

    # Agent overlay: nothing is stored for it beyond the chat record's
    # ``activeAgent``, so turning the agent off removes all of this.
    agent_addition, active_agent = "", None
    try:
        from app.services.agent_overlay import overlay_for
        overlay = overlay_for(project_id, conversation_id)
    except Exception as e:  # noqa: BLE001 -- an overlay must never fail a turn
        logger.warning("bench: agent overlay unavailable: %s", e)
        overlay = None
    if overlay is not None:
        parts = [overlay.prompt_block]
        # Skills the agent brings that are not already on hand here.
        wanted = [n for n in overlay.skill_names if n not in always_names]
        for s in storage.list():
            if s.name not in wanted:
                continue
            wanted.remove(s.name)
            full = storage.get(s.id) or s
            if full.prompt:
                from app.utils.skill_resources import skill_activation_body
                parts.append(
                    f"[Active Skill: {full.name}]\n{skill_activation_body(full)}")
        agent_addition = "\n\n".join(parts)
        active_agent = overlay.name

    return PromptInputs(
        always_names=always_names,
        catalog_names=catalog,
        system_prompt_addition="\n\n".join(blocks),
        model_overrides=overrides,
        preferred_tool_ids=tool_ids,
        authoritative=authoritative,
        agent_addition=agent_addition,
        active_agent=active_agent,
    )


# -- place -----------------------------------------------------------------

class ConversationRequired(ValueError):
    pass


class ChatNotFound(LookupError):
    pass


def place(project_id: str, conversation_id: Optional[str],
          key: str, layer: str, state: Optional[str],
          items: Optional[List[BenchItem]] = None, via: str = "hub") -> None:
    """Validate then persist one placement.  Raises PlacementRejected (409),
    ConversationRequired (400), ChatNotFound (404).

    ``via`` names the writer (PLACEMENT_VIAS) so the hub can say "pinned by
    migration from your previous settings" instead of an unexplained +1."""
    items = collect_items(project_id) if items is None else items
    validate_placement([i.spec for i in items], key, layer, state)
    if via not in PLACEMENT_VIAS:
        via = "api"

    def _apply(doc: Dict[str, Any]) -> Dict[str, Any]:
        placements = dict(doc.get("placements") or {})
        meta = dict(doc.get("meta") or {})
        if state is None:
            placements.pop(key, None)
            meta.pop(key, None)
        else:
            placements[key] = state
            meta[key] = {"via": via, "at": int(time.time())}
        return {**doc, "placements": placements, "meta": meta}

    if layer == "user":
        _user_store().update(_apply)
    elif layer == "project":
        _project_store(project_id).update(_apply)
    else:  # conversation
        if not conversation_id:
            raise ConversationRequired("conversation layer needs conversation_id")
        storage = _chat_storage(project_id)
        # The chat record has no lock of its own; two rapid drags in the hub
        # would race get -> update and lose one.  Borrow the project store's
        # flock so every placement write in this project is serialised.
        with _project_store(project_id).locked():
            chat = storage.get(conversation_id)
            if chat is None:
                raise ChatNotFound(conversation_id)
            from app.models.chat import ChatUpdate
            new = _apply({"placements": getattr(chat, "placements", None) or {}})["placements"]
            storage.update(conversation_id, ChatUpdate(placements=new))
