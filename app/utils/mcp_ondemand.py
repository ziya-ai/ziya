"""
MCP servers within reach (design/mcp-on-demand.md, capabilities-hub slice 9).

A server placed ``ondemand`` stays connected, but its tools are withheld from
the request until the model loads them with ``load_server_tools``.  This
module holds everything about that state that is not executor or storage
code:

  * the LOADED record: ``{server: ["tool", ...] | "*"}`` on the chat record
    (``Chat.loadedMcp``), insertion order = load order.  Written by the
    loader, read by the request filter and the bench view.  Durable: it does
    not depend on a marker surviving in history (tributary folding and
    muting remove tool results), and a fork copies the chat record so it
    inherits the loads.  An in-process sticky copy per conversation is the
    same-process fast path for the turn that loaded.
  * the filter: shelved servers are fully withheld; within-reach servers are
    withheld except for their loaded tools; loaded within-reach servers are
    ordered AFTER on-hand servers, in load order, so a load only ever
    APPENDS to the tool array (a prefix of the array can still cache-hit).
  * the catalog section, from placement + config only.  No connection state
    and no tool count: the system block is the cache prefix, and a server
    connecting after turn 1 would otherwise miss the whole prefix on turn 2.
    Connection problems surface at load time, as a tool result.
  * ``search_tools``: ranks a within-reach server's tools by a query without
    loading anything -- a tool result costs no prefix change, so the model
    can search for free and then load just the tools the task needs.
  * the loader's checks and result text.

Every loaded set is intersected with the request's CURRENT placements, so
moving a card to Shelved or On hand makes its loaded entry inert at once.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from app.utils.logging_utils import logger

LOADER_TOOL = "load_server_tools"
SEARCH_TOOL = "search_server_tools"
MARKER_RE = re.compile(r"\[MCP server loaded: ([^\]\n]+)\]")
ALL = "*"

# server -> list of bare tool names, or ALL
LoadedMap = Dict[str, Union[List[str], str]]

_STICKY_MAX_CONVERSATIONS = 1024
_sticky: Dict[str, LoadedMap] = {}
_sticky_lock = threading.Lock()


def marker(name: str) -> str:
    return f"[MCP server loaded: {name}]"


def _norm(name: str) -> str:
    from app.utils.task_tool_floor import normalize_tool_name
    return normalize_tool_name(name or "")


# -- loaded record -----------------------------------------------------------

def merge_loaded(into: LoadedMap, server: str, tools: Optional[Iterable[str]]) -> LoadedMap:
    """``into`` with ``server`` loaded (cumulatively).  ``tools=None`` means all.
    Returns a new ordered dict; a first load appends, a later one keeps the
    server's position so the tool array stays stable."""
    out: LoadedMap = {k: (v if v == ALL else list(v)) for k, v in into.items()}
    cur = out.get(server)
    if tools is None or cur == ALL:
        out[server] = ALL
        return out
    seen = list(cur or [])
    for t in tools:
        n = _norm(t)
        if n and n not in seen:
            seen.append(n)
    out[server] = seen
    return out


def _coerce(raw: Any) -> LoadedMap:
    out: LoadedMap = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(k, str) or not k:
            continue
        if v == ALL:
            out[k] = ALL
        elif isinstance(v, list):
            out[k] = [str(x) for x in v if isinstance(x, str) and x]
    return out


def _chat_storage(project_id: str):
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_project_dir
    return ChatStorage(get_project_dir(project_id))


def read_record(project_id: Optional[str], conversation_id: Optional[str]) -> LoadedMap:
    """The chat record's ``loadedMcp``; ``{}`` when unknown or unreadable."""
    if not project_id or not conversation_id:
        return {}
    try:
        chat = _chat_storage(project_id).get(conversation_id)
    except Exception as e:  # noqa: BLE001 -- the filter must never fail a turn
        logger.debug(f"loadedMcp read failed: {e}")
        return {}
    return _coerce(getattr(chat, "loadedMcp", None)) if chat is not None else {}


def write_record(project_id: Optional[str], conversation_id: Optional[str],
                 server: str, tools: Optional[Iterable[str]]) -> Optional[LoadedMap]:
    """Merge a load into the chat record.  None when there is no record to
    write (no project, unknown chat): the sticky copy still carries it."""
    if not project_id or not conversation_id:
        return None
    try:
        storage = _chat_storage(project_id)
        from app.services.bench import _project_store
        with _project_store(project_id).locked():     # serialise with placements
            chat = storage.get(conversation_id)
            if chat is None:
                return None
            new = merge_loaded(_coerce(getattr(chat, "loadedMcp", None)), server, tools)
            from app.models.chat import ChatUpdate
            storage.update(conversation_id, ChatUpdate(loadedMcp=new))
            return new
    except Exception as e:  # noqa: BLE001
        logger.warning(f"loadedMcp write failed for {conversation_id}: {e}")
        return None


def remember_loaded(conversation_id: Optional[str], server: str,
                    tools: Optional[Iterable[str]] = None) -> None:
    if not conversation_id or not server:
        return
    with _sticky_lock:
        cur = _sticky.pop(conversation_id, {})
        _sticky[conversation_id] = merge_loaded(cur, server, tools)   # most recent last
        while len(_sticky) > _STICKY_MAX_CONVERSATIONS:
            _sticky.pop(next(iter(_sticky)))


def sticky_loaded(conversation_id: Optional[str]) -> LoadedMap:
    if not conversation_id:
        return {}
    with _sticky_lock:
        return _coerce(_sticky.get(conversation_id, {}))


def forget_conversation(conversation_id: str) -> None:
    with _sticky_lock:
        _sticky.pop(conversation_id, None)


def loaded_map(project_id: Optional[str], conversation_id: Optional[str]) -> LoadedMap:
    """Record ∪ sticky, record order first."""
    out = read_record(project_id, conversation_id)
    for s, v in sticky_loaded(conversation_id).items():
        out = merge_loaded(out, s, None if v == ALL else v)
    return out


def loaded_servers(conversation_id: Optional[str], project_id: Optional[str] = None) -> Set[str]:
    return set(loaded_map(project_id, conversation_id))


def is_loader_tool(name: Optional[str]) -> bool:
    return _norm(name or "") == LOADER_TOOL


def loaded_in_results(tool_results: Iterable[Mapping[str, Any]]) -> Set[str]:
    """Servers a successful ``load_server_tools`` call loaded in these results
    (the executor rebuilds its tool list when non-empty).  A refusal and a
    ``search_server_tools`` result carry no marker."""
    found: Set[str] = set()
    for tr in tool_results or []:
        if is_loader_tool(tr.get("tool_name")):
            parts: List[str] = []
            _flatten(tr.get("result"), parts)
            found |= {m.strip() for p in parts for m in MARKER_RE.findall(p)}
    return found


def _flatten(value: Any, out: List[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k in ("text", "content", "result", "message"):
            if k in value:
                _flatten(value[k], out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _flatten(v, out)


# -- the filter --------------------------------------------------------------

@dataclass
class McpFilter:
    """What the request's tool list must do about MCP servers."""
    withheld: Set[str] = field(default_factory=set)            # no tools at all
    partial: Dict[str, Set[str]] = field(default_factory=dict)  # server -> allowed bare names
    order: List[str] = field(default_factory=list)              # loaded within-reach servers, load order
    # The two placements separately, for the native-deferral path: a shelved
    # server is dropped there too, but a within-reach one is SENT deferred
    # instead of withheld (see native_tool_payload).
    shelved: Set[str] = field(default_factory=set)
    reach: Set[str] = field(default_factory=set)

    def allows(self, server: Optional[str], tool_name: str) -> bool:
        if server is None or server not in self.withheld and server not in self.partial:
            return True
        if server in self.withheld:
            return False
        return _norm(tool_name) in self.partial[server]


def mcp_filter(placements: Mapping[str, str], loaded: LoadedMap,
               scope_servers: Iterable[str] = ()) -> McpFilter:
    """Shelved: withheld.  Within reach: withheld unless loaded (fully, or
    the named tools).  A server named by a task scope counts as fully loaded.
    Loaded entries for servers not within reach are ignored."""
    f = McpFilter()
    scope = set(scope_servers)
    for name, state in placements.items():
        if state == "off":
            f.withheld.add(name)
            f.shelved.add(name)
        elif state == "ondemand":
            f.reach.add(name)
            if name in scope:
                f.order.append(name)
                continue
            got = loaded.get(name)
            if got is None:
                f.withheld.add(name)
            elif got == ALL:
                f.order.append(name)
            else:
                f.partial[name] = {_norm(t) for t in got}
                f.order.append(name)
    # load order, not placement order
    rank = {s: i for i, s in enumerate(loaded)}
    f.order.sort(key=lambda s: rank.get(s, len(rank)))
    return f


def withheld_servers(placements: Mapping[str, str], loaded: Iterable[str]) -> Set[str]:
    """Shelved servers plus unloaded within-reach servers (whole-server view)."""
    have = set(loaded)
    return {n for n, s in placements.items()
            if s == "off" or (s == "ondemand" and n not in have)}


def ondemand_servers(placements: Mapping[str, str]) -> List[str]:
    return sorted(n for n, s in placements.items() if s == "ondemand")


def order_tools(tools: Sequence[Any], server_of, loaded_order: Sequence[str]) -> List[Any]:
    """Builtins (no server) first in their order; then servers not in
    ``loaded_order`` sorted by name; then loaded servers in load order.  A
    load appends; nothing moves."""
    rank = {s: i for i, s in enumerate(loaded_order)}

    def key(ix_t):
        i, t = ix_t
        s = server_of(t)
        if s is None:
            return (0, "", 0, i)
        if s in rank:
            return (2, "", rank[s], i)
        return (1, s, 0, i)
    return [t for _, t in sorted(enumerate(tools), key=key)]


# -- manager access ----------------------------------------------------------

def _manager(mgr=None):
    if mgr is not None:
        return mgr
    from app.mcp.manager import get_mcp_manager
    return get_mcp_manager()


def _one_line(text: str, limit: int = 120) -> str:
    line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _client_tools(mgr, name: str) -> List[Any]:
    client = (getattr(mgr, "clients", None) or {}).get(name)
    return list(getattr(client, "tools", None) or []) if client else []


# -- catalog -----------------------------------------------------------------

def catalog_section(servers: Iterable[str], mgr=None, native: bool = False) -> str:
    """The system-prompt section listing within-reach servers.

    Placement and config only: every input is stable for the life of a
    conversation, so this section never moves the cache prefix.  Nothing
    about connection state, tool counts or what is LOADED goes in it; the
    loader reports those as a tool result.  Empty when skill discovery
    (which owns the loader) is off.

    ``native``: the provider does deferred loading itself (Anthropic tool
    search, see native_tool_search_supported), so the model is told to use
    ``tool_search_tool_regex`` rather than Ziya's loader, which is not in
    its tool list on that path.
    """
    try:
        from app.mcp.builtin_tools import is_builtin_category_enabled
        if not is_builtin_category_enabled("skills"):
            return ""
    except Exception:  # noqa: BLE001
        return ""
    names = sorted(set(servers))
    if not names:
        return ""
    configs: Dict[str, Any] = {}
    try:
        configs = getattr(_manager(mgr), "server_configs", None) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"MCP catalog: manager unavailable: {e}")
    rows = []
    for name in names:
        desc = _one_line((configs.get(name) or {}).get("description") or "")
        rows.append(f"  • {name}" + (f" — {desc}" if desc else ""))
    if native:
        return (
            "\n\n## MCP servers within reach\n\n"
            "These MCP servers are connected, but their tool definitions are deferred:\n"
            f"they are not in your context until you find them with `{TOOL_SEARCH_NAME}`\n"
            "(a regex over tool names, descriptions and argument names; tool names\n"
            "start with `mcp_`).  A found tool is callable at once and stays available\n"
            "for the rest of the conversation; searching costs nothing else.\n\n"
            + "\n".join(rows) + "\n"
        )
    return (
        "\n\n## MCP servers within reach\n\n"
        "These MCP servers are connected, but their tools are not in your tool\n"
        f"list.  `{SEARCH_TOOL}` finds the tools a request needs (it loads\n"
        f"nothing and is free to call); `{LOADER_TOOL}` makes them callable from\n"
        "your next step and for the rest of the conversation.\n\n"
        + "\n".join(rows)
        + f"\n\nEach `{LOADER_TOOL}` call re-sends the tool list, so load every tool the\n"
        "task will need in ONE call: name the tools for a large server, or omit\n"
        "`tools` to load a small one whole.\n"
    )


def catalog_section_for_root(project_root: Optional[str], conversation_id: Optional[str],
                             native: bool = False) -> str:
    from app.utils import bench_prompt
    return catalog_section(ondemand_servers(
        bench_prompt.mcp_placements_for_root(project_root, conversation_id)), native=native)


# -- native deferral (Anthropic tool search) ---------------------------------
#
# The Messages API can hold tool definitions server-side and keep them OUT of
# the model's context until the model finds them with a tool-search tool:
# ``defer_loading: true`` per tool plus a ``tool_search_tool_regex_20251119``
# entry in ``tools``.  The ``tools`` parameter is then identical on every
# request, so a within-reach server costs no prefix miss at all -- neither on
# load (there is no load) nor on the search (the API expands the found
# definitions inline, after the cached prefix).  Where a provider supports
# this, Ziya sends within-reach servers deferred instead of withholding them,
# and drops its own loader/search builtins from the tool list; the rebuild
# path (load_server_tools -> _load_and_prepare_tools again) is the fallback
# for every other provider.  Found blocks (``server_tool_use``,
# ``tool_search_tool_result``) must be echoed back in the assistant turn at
# their original index, as signed thinking blocks are.

TOOL_SEARCH_TYPE = "tool_search_tool_regex_20251119"
TOOL_SEARCH_NAME = "tool_search_tool_regex"
TOOL_SEARCH_ENTRY: Dict[str, Any] = {"type": TOOL_SEARCH_TYPE, "name": TOOL_SEARCH_NAME}
# Response content blocks the provider must hand back unchanged.
PASSBACK_BLOCK_TYPES = frozenset({"server_tool_use", "tool_search_tool_result"})

# Minor is at most two digits and not followed by another: "sonnet-4-20250514"
# is 4.0 with a date, not 4.20250514.
# fable / mythos are the Claude 5 family names on bedrock-mantle (the one
# gateway this was validated on live, 2026-10-25); without them the gate
# refused the very model that works.
_CLAUDE_VERSION_RE = re.compile(
    r"claude[-_](?:opus|sonnet|haiku|fable|mythos)[-_](\d+)(?:[-_.](\d{1,2})(?!\d))?", re.I)


def native_tool_search_supported(model_config: Optional[Mapping[str, Any]], model_id: str) -> bool:
    """Whether the Anthropic request path may send deferred tools.

    An explicit ``supports_tool_search`` in the model config wins; otherwise
    a Claude model at 4.5 or later (the documented floor; Opus 4.1 and
    earlier reject the entry).  Pure, for the model table test."""
    cfg = model_config or {}
    flag = cfg.get("supports_tool_search")
    if flag is not None:
        return bool(flag)
    m = _CLAUDE_VERSION_RE.search(model_id or "")
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2) or 0)
    return (major, minor) >= (4, 5)


def native_search_for(endpoint: Optional[str], model_cfg: Optional[Mapping[str, Any]],
                      model_id: Optional[str] = None) -> bool:
    """Whether a request routed by ``endpoint`` + ``model_cfg`` takes the
    Anthropic Messages path AND a model that accepts deferred tools.  The
    prompt builder and the executor must agree on this, so both call it:
    the catalog must name the search the model actually has."""
    cfg = model_cfg or {}
    anthropic_path = endpoint == "anthropic" or (
        endpoint == "bedrock" and cfg.get("endpoint_override") == "bedrock-mantle"
        and cfg.get("mantle_api") != "openai-responses")
    return anthropic_path and native_tool_search_supported(cfg, model_id or cfg.get("model_id") or "")


def native_tool_payload(pairs: Sequence[Tuple[Any, Dict[str, Any]]], server_of,
                        deferred_servers: Iterable[str]) -> List[Dict[str, Any]]:
    """The ``tools`` array for a native-deferral request.

    ``pairs`` are ``(tool_object, converted_schema)`` in the executor's
    order.  Ziya's own loader and search builtins are dropped (the API's
    search replaces them); tools of ``deferred_servers`` get
    ``defer_loading: true`` and go LAST; the search entry goes first.  The
    result is a pure function of placement + registered tools, so it is the
    same on every request of a conversation."""
    deferred = set(deferred_servers)
    eager: List[Dict[str, Any]] = []
    late: List[Dict[str, Any]] = []
    for tool, schema in pairs:
        if _norm(schema.get("name", "")) in (LOADER_TOOL, SEARCH_TOOL):
            continue
        s = server_of(tool)
        if s is not None and s in deferred:
            late.append({**schema, "defer_loading": True})
        else:
            late_free = dict(schema)
            late_free.pop("defer_loading", None)
            eager.append(late_free)
    return [dict(TOOL_SEARCH_ENTRY)] + eager + late


def is_native_search_request(tools: Optional[Sequence[Mapping[str, Any]]]) -> bool:
    return any((t or {}).get("type") == TOOL_SEARCH_TYPE for t in (tools or []))


# -- search ------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower().replace("_", " "))


def rank_tools(query: str, tools: Iterable[Tuple[str, str, str]], n: int = 10) -> List[Tuple[str, str, str, int]]:
    """``(server, name, description, score)`` for the best ``n`` matches.
    Name-token hits weigh 3, description hits 1, a substring of the name 2.
    Pure, so ranking is testable without a manager."""
    q = [t for t in _tokens(query) if len(t) > 1]
    if not q:
        return []
    scored = []
    for server, name, desc in tools:
        nt, dt = set(_tokens(name)), set(_tokens(desc))
        lname = name.lower()
        score = sum(3 for t in q if t in nt) + sum(1 for t in q if t in dt) \
            + sum(2 for t in q if t in lname and t not in nt)
        if score:
            scored.append((server, name, desc, score))
    scored.sort(key=lambda r: (-r[3], r[0], r[1]))
    return scored[:n]


def search_tools(query: str, server: Optional[str], project_root: Optional[str],
                 conversation_id: Optional[str], mgr=None, n: int = 10) -> Dict[str, Any]:
    """The ``search_server_tools`` tool's body: rank within-reach tools, load
    nothing.  An empty query with a server lists that server's tools."""
    from app.utils import bench_prompt
    placements = bench_prompt.mcp_placements_for_root(project_root, conversation_id)
    reach = ondemand_servers(placements)
    if server and server not in reach:
        state = placements.get(server)
        why = ("is on hand: its tools are already in your tool list" if state == "always"
               else "is shelved" if state == "off" else "is not an MCP server on this bench")
        return {"error": True, "message": f"'{server}' {why}. Within reach: {', '.join(reach) or 'none'}."}
    if not reach:
        return {"error": True, "message": "No MCP servers are within reach on this bench."}
    try:
        mgr = _manager(mgr)
    except Exception as e:  # noqa: BLE001
        return {"error": True, "message": f"MCP manager unavailable: {e}"}
    loaded = loaded_map(bench_prompt.project_id_for_root(project_root), conversation_id)
    pool = [(s, t.name, getattr(t, "description", "") or "")
            for s in ([server] if server else reach) for t in _client_tools(mgr, s)]
    if not (query or "").strip():
        if not server:
            return {"error": True, "message": "Give a query, or a server name to list its tools."}
        rows = [(s, nm, d, 0) for s, nm, d in pool][:50]
        head = f"{len(pool)} tool{'' if len(pool) == 1 else 's'} on {server}"
    else:
        rows = rank_tools(query, pool, n)
        head = f"{len(rows)} match{'' if len(rows) == 1 else 'es'} for {query!r} across {', '.join([server] if server else reach)}"
    if not rows:
        return {"content": f"{head}. Nothing loaded. Try other words, or list a server with an empty query."}

    def flag(s, nm):
        got = loaded.get(s)
        return " (loaded)" if got == ALL or (isinstance(got, list) and _norm(nm) in got) else ""
    lines = [f"  • {s}/{nm}{flag(s, nm)} — {_one_line(d)}" for s, nm, d, _ in rows]
    return {"content": (
        f"{head}. Nothing is loaded by searching.\n" + "\n".join(lines)
        + f"\n\nTo use them: {LOADER_TOOL}(name=<server>, tools=[...]) with every tool the "
        "task needs in one call."
    )}


# -- loader ------------------------------------------------------------------

def load_server(name: str, project_root: Optional[str], conversation_id: Optional[str],
                mgr=None, tools: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Check and perform a load; the ``load_server_tools`` tool's whole body.

    ``tools`` names a subset (bare or ``mcp_``-prefixed); None loads the
    server whole.  Loads are cumulative per conversation.  Success returns
    ``{"content": text}`` whose first line is the marker the executor looks
    for; every refusal returns ``{"error": True, "message": ...}`` with no
    marker.
    """
    from app.utils import bench_prompt
    name = (name or "").strip()
    placements = bench_prompt.mcp_placements_for_root(project_root, conversation_id)
    reach = ondemand_servers(placements)

    def _err(msg: str) -> Dict[str, Any]:
        return {"error": True, "message": msg}

    if not name:
        return _err("name is required. Within reach: " + (", ".join(reach) or "none"))
    state = placements.get(name)
    if state is None:
        return _err(f"'{name}' is not an MCP server on this bench. "
                    f"Within reach: {', '.join(reach) or 'none'}.")
    if state == "always":
        return _err(f"'{name}' is on hand: its tools are already in your tool list. Call them directly.")
    if state == "off":
        return _err(f"'{name}' is shelved, so it is not offered in this conversation. "
                    "Ask the user to move it to Within reach or On hand.")
    if not conversation_id:
        return _err("No conversation to load into; loads are per conversation.")
    try:
        mgr = _manager(mgr)
    except Exception as e:  # noqa: BLE001
        return _err(f"MCP manager unavailable: {e}")
    client = (getattr(mgr, "clients", None) or {}).get(name)
    if client is None or not getattr(client, "is_connected", False):
        return _err(f"'{name}' is not connected. See MCP Server Settings for its logs.")
    if name in (getattr(mgr, "_quarantined_servers", None) or set()):
        return _err(f"'{name}' needs re-authorization: its tool definitions changed since they "
                    "were approved. The user can re-authorize it from the bench.")

    available = list(getattr(client, "tools", None) or [])
    by_norm = {_norm(t.name): t for t in available}
    chosen: Optional[List[str]] = None
    if tools:
        wanted = [_norm(t) for t in tools if (t or "").strip()]
        unknown = [t for t in wanted if t not in by_norm]
        if unknown:
            return _err(f"{name} has no tool named {', '.join(unknown)}. "
                        f"Use {SEARCH_TOOL}(server='{name}') to list its tools.")
        chosen = wanted

    project_id = bench_prompt.project_id_for_root(project_root)
    remember_loaded(conversation_id, name, chosen)
    rec = write_record(project_id, conversation_id, name, chosen)
    try:
        from app.utils.capability_usage import record_mcp_use
        record_mcp_use(name)
    except Exception:  # noqa: BLE001
        pass
    now = (rec or sticky_loaded(conversation_id)).get(name)
    shown = available if now == ALL else [by_norm[n] for n in (now or []) if n in by_norm]
    lines = [f"  • mcp_{t.name} — {_one_line(getattr(t, 'description', '') or '')}" for t in shown]
    scope = "all" if now == ALL else f"{len(shown)} of {len(available)}"
    logger.info(f"🧰 MCP_LOADED: {name} ({scope} tools) for conversation {conversation_id}"
                + ("" if rec is not None else " (no chat record; this process only)"))
    return {"content": (
        f"{marker(name)}\n\n"
        f"{scope} tools from {name} are callable from your next step and for the rest "
        "of this conversation:\n" + ("\n".join(lines) or "  (none listed)")
    )}
