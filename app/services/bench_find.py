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


async def _mcp_column(q: str, n: int) -> Optional[Dict[str, Any]]:
    """The MCP registry as one column, via the existing unified search.  None
    when the registry is unavailable: the view omits the column rather than
    show an empty one that reads as "no such server"."""
    try:
        from app.mcp.registry_manager import get_registry_manager
        results = await get_registry_manager().search_services_by_tools(q)
    except Exception as e:  # noqa: BLE001
        logger.debug("bench find: MCP registry search unavailable: %s", e)
        return None
    have = ss.configured_mcp_servers()
    hits: List[Dict[str, Any]] = []
    for r in results[:n]:
        s = r.service
        hits.append({
            "source": MCP_COLUMN, "id": s.service_id, "name": s.service_name,
            "description": s.service_description or "", "version": "", "requires": [],
            "support_level": getattr(getattr(s, "support_level", None), "value", None),
            "matched": [t.tool_name for t in r.matching_tools],
            "installed": s.service_id in have or s.service_name in have,
            "missing_requires": [],
            "provider": (getattr(s, "provider_metadata", None) or {}).get("provider_id"),
        })
    return {"source": MCP_COLUMN, "name": "MCP registry", "kind": "mcp", "total": len(results), "hits": hits}


async def search(project_id: str, q: str, n: int = 8, source: Optional[str] = None) -> Dict[str, Any]:
    installed = {s.name for s in _installed_skills(project_id)}
    srcs = [s for s in ss.list_sources() if not source or s.identifier == source]
    tasks: List[Any] = [asyncio.to_thread(_column, s, q, n, installed) for s in srcs]
    if not source or source == MCP_COLUMN:
        tasks.append(_mcp_column(q, n))
    cols = await asyncio.gather(*tasks)
    return {"q": q, "per_source": [c for c in cols if c is not None]}
