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


# -- item assembly ---------------------------------------------------------

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
        ))
    return out


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
                    tools_by_server.setdefault(srv, []).append(
                        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema})
    except Exception as e:
        logger.debug("bench: MCP tools unavailable: %s", e)
    for name, cfg in sorted((mgr.server_configs or {}).items()):
        tokens = 0
        try:
            from app.routes.mcp_routes import count_server_tool_tokens
            tokens = count_server_tool_tokens(tools_by_server.get(name, []))
        except Exception:
            pass
        builtin = bool(cfg.get("builtin", False))
        out.append(BenchItem(
            spec=ItemSpec(key=make_key("mcp", name), kind="mcp",
                          tier="environment" if builtin else "placeable"),
            name=name,
            provenance="builtin" if builtin else "user",
            tokens=tokens,
            health={"enabled": cfg.get("enabled", True),
                    "connected": name in tools_by_server},
        ))
    return out


def collect_items(project_id: str) -> List[BenchItem]:
    return _skill_items(project_id) + _mcp_items()


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


def load_layers(project_id: str, conversation_id: Optional[str]) -> Dict[str, Dict[str, Optional[str]]]:
    return {
        "conversation": _conversation_placements(project_id, conversation_id),
        "project": dict(_project_store(project_id).get("placements", {}) or {}),
        "user": dict(_user_store().get("placements", {}) or {}),
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
            blocks.append(f"[Active Skill: {full.name}]\n{full.prompt}")
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

    return PromptInputs(
        always_names=always_names,
        catalog_names=catalog,
        system_prompt_addition="\n\n".join(blocks),
        model_overrides=overrides,
        preferred_tool_ids=tool_ids,
        authoritative=authoritative,
    )


# -- place -----------------------------------------------------------------

class ConversationRequired(ValueError):
    pass


class ChatNotFound(LookupError):
    pass


def place(project_id: str, conversation_id: Optional[str],
          key: str, layer: str, state: Optional[str],
          items: Optional[List[BenchItem]] = None) -> None:
    """Validate then persist one placement.  Raises PlacementRejected (409),
    ConversationRequired (400), ChatNotFound (404)."""
    items = collect_items(project_id) if items is None else items
    validate_placement([i.spec for i in items], key, layer, state)

    def _apply(doc: Dict[str, Any]) -> Dict[str, Any]:
        placements = dict(doc.get("placements") or {})
        if state is None:
            placements.pop(key, None)
        else:
            placements[key] = state
        return {**doc, "placements": placements}

    if layer == "user":
        _user_store().update(_apply)
    elif layer == "project":
        _project_store(project_id).update(_apply)
    else:  # conversation
        if not conversation_id:
            raise ConversationRequired("conversation layer needs conversation_id")
        storage = _chat_storage(project_id)
        chat = storage.get(conversation_id)
        if chat is None:
            raise ChatNotFound(conversation_id)
        from app.models.chat import ChatUpdate
        new = _apply({"placements": getattr(chat, "placements", None) or {}})["placements"]
        storage.update(conversation_id, ChatUpdate(placements=new))
