"""
Find view service (design/capabilities-hub.md, slice 8b).

Browse is a QUERY, not a shelf.  The sources behind the bench hold tens of
thousands of skills and MCP servers, so nothing here enumerates a catalog
into a response, and there is no curated default (too many users across too
many domains to maintain one).  Two entry points:

  * ``home``   -- what the Find view shows before a query is typed.  Only
    panels derived from THIS bench: skills here whose ``requires`` name an
    MCP server the config lacks; installed skills whose source now reports a
    different version; skills in the *enumerable* sources whose requirements
    the servers already on hand satisfy.
  * ``search`` -- fans ``q`` out to every skill source and the MCP registry
    concurrently and returns one column per source: top-``n`` hits plus the
    source's own total, so the view can say "+M more" and link out.

A registry's support level rides on a hit as ``support_level`` -- a badge,
never a filter or a section.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.activation import make_key
from app.services import bench as bench_service
from app.services import skill_sources as ss
from app.services.skill_discovery import parse_skill_md

logger = logging.getLogger(__name__)

# Column id of the MCP registry; its hits link to the registry panel rather
# than installing through the skill path.
MCP_COLUMN = "mcp-registry"


def _installed_skills(project_id: str) -> List[Any]:
    return list(bench_service._skill_storage(project_id).list())


def _requires_of(skill: Any) -> List[str]:
    """``requires`` from the provenance record, else from the SKILL.md
    frontmatter: a hand-authored skill has no provenance but may still name
    the server it needs."""
    prov = ss.read_provenance(skill.skillPath)
    if prov is not None and prov.get("requires") is not None:
        return list(prov.get("requires") or [])
    if skill.skillPath:
        p = Path(skill.skillPath)
        md = p if p.name == "SKILL.md" else p / "SKILL.md"
        parsed = parse_skill_md(md) if md.is_file() else None
        if parsed:
            return ss.parse_requires(parsed[0].get("requires"))
    return []


def _hit(l: ss.SkillListing, installed: set) -> Dict[str, Any]:
    d = ss.listing_dict(l)
    # A package hit (AIM) counts as installed when one of its matched skills is.
    d["installed"] = l.name in installed or any(m in installed for m in l.matched)
    d["missing_requires"] = ss.missing_requirements(l.requires)
    return d


def home(project_id: str) -> Dict[str, Any]:
    installed = _installed_skills(project_id)
    names = {s.name for s in installed}
    needs: List[Dict[str, Any]] = []
    updates: List[Dict[str, Any]] = []
    for s in installed:
        key = make_key("skill", s.name)
        missing = ss.missing_requirements(_requires_of(s))
        if missing:
            needs.append({"key": key, "name": s.name, "missing": missing})
        prov = ss.read_provenance(s.skillPath)
        if prov and ss.update_available(prov):
            src = ss.get_source(prov.get("source", ""))
            updates.append({
                "key": key, "name": s.name, "source": prov.get("source"), "id": prov.get("remote_id"),
                "installed_version": prov.get("version"),
                "current_version": src.current_version(prov.get("remote_id", "")) if src else None,
            })
    pairs: List[Dict[str, Any]] = []
    for src in ss.list_sources():
        if not getattr(src, "enumerable", True):
            continue                       # a registry is queried, never listed
        try:
            listings = src.list()
        except Exception as e:  # noqa: BLE001
            logger.warning("bench find: source %s failed: %s", src.identifier, e)
            continue
        for l in listings:
            wants = [r for r in l.requires if r.startswith("mcp:")]
            if not wants or l.name in names or ss.missing_requirements(l.requires):
                continue
            pairs.append(_hit(l, names))
    return {"needs": needs, "updates": updates, "pairs_with": pairs}


def _column(src: ss.SkillSourceProvider, q: str, n: int, installed: set) -> Dict[str, Any]:
    col: Dict[str, Any] = {"source": src.identifier, "name": src.name, "kind": src.kind, "total": None, "hits": []}
    try:
        r = src.search(q, n)
    except Exception as e:  # noqa: BLE001
        logger.warning("bench find: source %s search failed: %s", src.identifier, e)
        col["error"] = str(e)[:200]
        return col
    col["hits"] = [_hit(l, installed) for l in r.hits[:n]]
    col["total"] = r.total if r.total is not None else len(r.hits)
    return col


# The registry's server-side search inspects only the first page or so of
# each provider's catalogue, so a server far down the list is never found by
# name.  The whole unified catalogue is matched here instead, as the registry
# panel does in the browser.
CATALOG_MAX = 10000
_TOOL_HIT = 2


def _match_rank(needle: str, s: Any) -> Optional[int]:
    """How a server's own text matches the query: 0 when its id or name IS
    the query, 1 when either contains it, 3 when only the description or a
    tag does, None when nothing does.  2 is a tool-search hit."""
    sid = str(getattr(s, "service_id", "") or "").lower()
    name = str(getattr(s, "service_name", "") or "").lower()
    if needle in (sid, name):
        return 0
    if needle in sid or needle in name:
        return 1
    if needle in str(getattr(s, "service_description", "") or "").lower():
        return 3
    if any(needle in str(t).lower() for t in (getattr(s, "tags", None) or [])):
        return 3
    return None


def _configured_idents() -> set:
    """Normalised names and registry ids of the configured MCP servers.  An
    install may store a server under another name than the registry's, so a
    hit also counts as installed when a config entry carries its id."""
    have = {bench_service._norm_name(n) for n in ss.configured_mcp_servers()}
    try:
        from app.mcp.manager import get_mcp_manager
        for cfg in (get_mcp_manager().server_configs or {}).values():
            if isinstance(cfg, dict) and cfg.get("service_id"):
                have.add(bench_service._norm_name(cfg["service_id"]))
    except Exception as e:  # noqa: BLE001
        logger.debug("bench find: MCP config unavailable: %s", e)
    return have


def _registry_choices(mgr: Any, off: set) -> List[Dict[str, Any]]:
    """Every available registry as ``{id, name, selected}``; empty when the
    manager cannot say (the column then carries no chips and filters nothing)."""
    try:
        ids = list(mgr.get_available_providers())
    except Exception as e:  # noqa: BLE001
        logger.debug("bench find: registry list unavailable: %s", e)
        return []
    out: List[Dict[str, Any]] = []
    for pid in ids:
        name = pid
        try:
            name = getattr(mgr.provider_registry.get_provider(pid), "name", None) or pid
        except Exception:  # noqa: BLE001
            pass
        out.append({"id": pid, "name": name, "selected": pid not in off})
    return out


async def _mcp_column(q: str, n: int, registries_off: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """The MCP registries as one column: servers whose id, name, description
    or tags contain the query, plus the registries' tool-search hits, best
    name match first.  Registries named in ``registries_off`` are not asked.
    The column lists every registry with whether it was asked, so the view
    can offer them as switches.  None when the registry is unavailable: the
    view omits the column rather than show an empty one that reads as "no
    such server"."""
    try:
        from app.mcp.registry_manager import get_registry_manager
        mgr = get_registry_manager()
    except Exception as e:  # noqa: BLE001
        logger.debug("bench find: MCP registry search unavailable: %s", e)
        return None

    off = set(registries_off or [])
    choices = _registry_choices(mgr, off)
    column: Dict[str, Any] = {"source": MCP_COLUMN, "name": "MCP registry", "kind": "mcp",
                              "total": 0, "hits": [], "registries": choices}
    # No filter when nothing is switched off, so the shared caches are reused.
    provider_filter = [c["id"] for c in choices if c["selected"]] if off and choices else None
    if provider_filter is not None and not provider_filter:
        return column                      # every registry is off: nothing to ask

    async def _try(call):
        try:
            return await call()
        except Exception as e:  # noqa: BLE001
            logger.debug("bench find: MCP registry call failed: %s", e)
            return None

    catalog, tool_hits = await asyncio.gather(
        _try(lambda: mgr.get_available_services(max_results=CATALOG_MAX, provider_filter=provider_filter)),
        _try(lambda: mgr.search_services_by_tools(q, provider_filter=provider_filter)))
    if catalog is None and tool_hits is None:
        return None
    needle = q.strip().lower()
    found: Dict[str, List[Any]] = {}          # service id -> [rank, service, matched tools]
    for s in catalog or []:
        rank = _match_rank(needle, s)
        if rank is not None:
            found.setdefault(s.service_id, [rank, s, []])
    for r in tool_hits or []:
        s = r.service
        row = found.get(s.service_id)
        if row is None:
            rank = _match_rank(needle, s)
            row = found[s.service_id] = [_TOOL_HIT if rank is None else rank, s, []]
        row[0] = min(row[0], _TOOL_HIT)
        if not row[2]:
            row[2] = [t.tool_name for t in r.matching_tools]
    ordered = sorted(found.values(), key=lambda row: row[0])   # stable: registry order within a rank
    have = _configured_idents()
    hits: List[Dict[str, Any]] = []
    for _rank, s, matched in ordered[:n]:
        meta = getattr(s, "provider_metadata", None) or {}
        providers = [p for p in (meta.get("available_in") or [meta.get("provider_id")]) if p]
        hits.append({
            "source": MCP_COLUMN, "id": s.service_id, "name": s.service_name,
            "description": s.service_description or "", "version": "", "requires": [],
            "support_level": getattr(getattr(s, "support_level", None), "value", None),
            "matched": matched,
            "tags": [str(t) for t in (getattr(s, "tags", None) or [])][:8],
            "installed": (bench_service._norm_name(s.service_id) in have
                          or bench_service._norm_name(s.service_name) in have),
            "missing_requires": [],
            "provider": ", ".join(providers) or None,
        })
    column["total"] = len(ordered)
    column["hits"] = hits
    return column


async def search(project_id: str, q: str, n: int = 8, source: Optional[str] = None,
                 registries_off: Optional[List[str]] = None) -> Dict[str, Any]:
    installed = {s.name for s in _installed_skills(project_id)}
    srcs = [s for s in ss.list_sources() if not source or s.identifier == source]
    tasks: List[Any] = [asyncio.to_thread(_column, s, q, n, installed) for s in srcs]
    if not source or source == MCP_COLUMN:
        tasks.append(_mcp_column(q, n, registries_off))
    cols = await asyncio.gather(*tasks)
    return {"q": q, "per_source": [c for c in cols if c is not None]}
