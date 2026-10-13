"""Memory export: select a bounded slice of the memory store for another user.

design/structured-memory-system.md: "Memory files are local, never
transmitted, never shared without explicit user action."  This module is
that explicit action.  Everything here is a pure function over a
``MemoryStorage`` so the API layer (app/api/memory.py) stays thin and the
selection logic is testable without HTTP.

Flow
----
1. ``export_options(store)`` — what can be selected: the domain tree with
   per-node counts, the projects memories are scoped to, how many are
   unscoped, and the layers present.
2. ``plan_export(store, selection)`` — resolve a selection into the exact
   set of memories, with per-reason counts of what was held back, a
   leak scan of the content, and a fingerprint of the id set.
3. ``build_bundle(store, selection, plan)`` — the file.  Every memory is
   rebuilt from a field whitelist (never ``model_dump``): conversation
   ids, retrieval telemetry and absolute project paths are dropped or
   replaced unless the caller opts in.

Bounds
------
A memory is exported only when it passes every gate in order: status,
layer, domain, project.  Project *exclusion* is checked before
*inclusion*, so a memory scoped to both an included and an excluded
project is held back.  The fingerprint lets the API refuse an export
whose preview no longer matches the store.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from app.models.memory import MEMORY_LAYERS, MEMORY_STATUSES

EXPORT_FORMAT = "ziya-memory-export"
EXPORT_VERSION = 1

# Fields a memory keeps in a bundle.  Anything not listed is dropped, so a
# new telemetry field added to ``Memory`` later cannot leak by default.
_MEMORY_PUBLIC_FIELDS = (
    "id", "content", "layer", "tags", "learned_from", "created", "status",
    "importance", "scope", "related", "relations", "reference",
)
# Kept only with ``strip_provenance=False``.  These name the exporting
# user's conversations and usage history, not the knowledge itself.
_MEMORY_PROVENANCE_FIELDS = (
    "last_accessed", "last_retrieved_at", "retrieval_loaded_count",
    "retrieval_used_count", "corroborations", "corroborated_by",
    "interference_score", "learned_from_conversation", "learned_from_message",
)
_NODE_PUBLIC_FIELDS = ("id", "handle", "parent", "children", "cross_links",
                       "memory_refs", "tags")

# Leak scan.  Credential shapes come from the shadow redactor so there is
# one vetted pattern set in the codebase; the two below are export-specific:
# a path under a home directory names the exporting user's machine, and an
# address is personal data another user has no need of.
_HOME_PATH_RE = re.compile(r"(?:/Users|/home|[A-Za-z]:\\Users)/[^/\\\s'\"`]+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_REDACTED_KIND_RE = re.compile(r"\[REDACTED:([^\]]+)\]")
_SNIPPET_CHARS = 120


class ExportSelection(BaseModel):
    """What to export.  Empty lists mean "no restriction" on that axis."""
    model_config = {"extra": "forbid"}

    domains: List[str] = Field(
        default_factory=list,
        description="Mind-map node ids.  A node selects its whole subtree.  "
                    "Empty selects every memory, placed or not.")
    include_projects: List[str] = Field(
        default_factory=list,
        description="Project paths (scope.project_paths).  Empty = all.")
    exclude_projects: List[str] = Field(
        default_factory=list,
        description="Project paths to hold back.  Wins over include.")
    include_unscoped: bool = Field(
        default=True,
        description="Export memories with no project scope.")
    layers: List[str] = Field(default_factory=list, description="Empty = all.")
    statuses: List[str] = Field(default_factory=lambda: ["active"])
    strip_provenance: bool = Field(
        default=True,
        description="Drop conversation ids, retrieval telemetry and access "
                    "dates.")
    anonymize_paths: bool = Field(
        default=True,
        description="Replace scope.project_paths with their last path "
                    "segment.")
    include_mindmap: bool = Field(
        default=True,
        description="Include the mind-map nodes that hold exported memories.")


class ExportPlan(BaseModel):
    """Resolved selection: exactly what ``build_bundle`` will write."""
    model_config = {"extra": "forbid"}

    memory_ids: List[str]
    fingerprint: str
    total: int
    held_back: Dict[str, int]
    counts: Dict[str, Any]
    warnings: List[Dict[str, Any]]
    node_ids: List[str]


# -- helpers --------------------------------------------------------------------

def project_label(path: str) -> str:
    """Last path segment; mirrors projectLabel() in MemoryBrowser.tsx."""
    if not path:
        return ""
    parts = [p for p in re.split(r"[\\/]+", path.rstrip("\\/")) if p]
    return parts[-1] if parts else path


def fingerprint_ids(ids: Iterable[str]) -> str:
    """Stable digest of an id set; equal sets give equal fingerprints."""
    joined = "\n".join(sorted(set(ids)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _subtree(nodes: Dict[str, dict], roots: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    stack = [r for r in roots if r in nodes]
    while stack:
        nid = stack.pop()
        if nid in out:
            continue
        out.add(nid)
        stack.extend(c for c in nodes[nid].get("children", []) if c in nodes)
    return out


def _root_of(nodes: Dict[str, dict]) -> Dict[str, str]:
    """node id -> its root ancestor id (cycle-safe)."""
    cache: Dict[str, str] = {}
    for nid in nodes:
        seen: List[str] = []
        cur = nid
        while cur in nodes and cur not in cache and cur not in seen:
            seen.append(cur)
            parent = nodes[cur].get("parent")
            if not parent or parent not in nodes:
                break
            cur = parent
        root = cache.get(cur, cur)
        for s in seen:
            cache[s] = root
    return cache


def _nodes_by_memory(nodes: Dict[str, dict]) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for nid, n in nodes.items():
        for ref in n.get("memory_refs", []) or []:
            out.setdefault(ref, set()).add(nid)
    return out


def scan_memory(row: dict) -> List[Dict[str, Any]]:
    """Leak scan of one memory.  Returns one warning per finding kind."""
    from app.shadow.redaction import redact_text

    text_parts = [row.get("content") or ""]
    ref = row.get("reference") or {}
    if isinstance(ref, dict) and ref.get("uri"):
        text_parts.append(str(ref["uri"]))
    text = "\n".join(text_parts)
    found: List[Tuple[str, str]] = []

    redacted = redact_text(text)
    if redacted != text:
        for kind in dict.fromkeys(_REDACTED_KIND_RE.findall(redacted)):
            found.append((f"credential:{kind}", ""))
    for m in _HOME_PATH_RE.finditer(text):
        found.append(("local_path", m.group(0)))
        break
    for m in _EMAIL_RE.finditer(text):
        found.append(("email", m.group(0)))
        break

    out = []
    for kind, match in found:
        snippet = (row.get("content") or "")[:_SNIPPET_CHARS]
        out.append({"memory_id": row.get("id"), "kind": kind,
                    "match": match, "snippet": snippet})
    return out


# -- options --------------------------------------------------------------------

def export_options(store) -> Dict[str, Any]:
    """Selectable axes with counts.  Counts use the default status filter
    (active) so they match what a default selection would export."""
    rows = [r for r in store._load_memories() if r.get("status", "active") == "active"]
    ids = {r.get("id") for r in rows}
    nodes = store._load_mindmap()

    def node_entry(nid: str) -> Dict[str, Any]:
        n = nodes[nid]
        members = {ref for sub in _subtree(nodes, [nid])
                   for ref in nodes[sub].get("memory_refs", []) if ref in ids}
        return {
            "id": nid,
            "handle": n.get("handle", ""),
            "count": len(members),
            "children": [node_entry(c) for c in n.get("children", []) if c in nodes],
        }

    roots = [nid for nid, n in nodes.items()
             if not n.get("parent") or n.get("parent") not in nodes]
    domains = sorted((node_entry(r) for r in roots),
                     key=lambda d: (-d["count"], d["handle"]))

    placed = {ref for n in nodes.values() for ref in n.get("memory_refs", []) if ref in ids}

    by_project: Dict[str, int] = {}
    unscoped = 0
    by_layer: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for r in store._load_memories():
        by_status[r.get("status", "active")] = by_status.get(r.get("status", "active"), 0) + 1
    for r in rows:
        by_layer[r.get("layer", "unknown")] = by_layer.get(r.get("layer", "unknown"), 0) + 1
        pp = (r.get("scope") or {}).get("project_paths") or []
        if not pp:
            unscoped += 1
        for p in pp:
            by_project[p] = by_project.get(p, 0) + 1
    projects = sorted(
        ({"path": p, "label": project_label(p), "count": c} for p, c in by_project.items()),
        key=lambda d: (-d["count"], d["label"]))

    return {
        "total": len(rows),
        "domains": domains,
        "unplaced": len(ids - placed),
        "projects": projects,
        "unscoped": unscoped,
        "layers": [{"layer": l, "count": by_layer[l]} for l in MEMORY_LAYERS if l in by_layer],
        "statuses": [{"status": s, "count": by_status[s]} for s in MEMORY_STATUSES if s in by_status],
    }


# -- plan -----------------------------------------------------------------------

def _select_rows(store, sel: ExportSelection
                 ) -> Tuple[List[dict], Dict[str, int], Dict[str, dict], Dict[str, str]]:
    """Apply the gates.  Returns (rows, held_back, nodes, domain_of_memory)."""
    nodes = store._load_mindmap()
    node_of = _nodes_by_memory(nodes)
    root_of = _root_of(nodes)
    allowed_nodes = _subtree(nodes, sel.domains) if sel.domains else None
    exclude = set(sel.exclude_projects)
    include = set(sel.include_projects)
    layers = set(sel.layers)
    statuses = set(sel.statuses or ["active"])

    held: Dict[str, int] = {
        "status": 0, "layer": 0, "outside_domains": 0,
        "excluded_project": 0, "project_not_included": 0, "unscoped": 0,
    }
    out: List[dict] = []
    domain_of: Dict[str, str] = {}
    for r in store._load_memories():
        mid = r.get("id")
        if not mid:
            continue
        if r.get("status", "active") not in statuses:
            held["status"] += 1
            continue
        if layers and r.get("layer") not in layers:
            held["layer"] += 1
            continue
        holding = set(node_of.get(mid, ()))
        dn = (r.get("scope") or {}).get("domain_node")
        if dn and dn in nodes:
            holding.add(dn)
        if allowed_nodes is not None and not (holding & allowed_nodes):
            held["outside_domains"] += 1
            continue
        pp = (r.get("scope") or {}).get("project_paths") or []
        if not pp:
            if not sel.include_unscoped:
                held["unscoped"] += 1
                continue
        else:
            if exclude and any(p in exclude for p in pp):
                held["excluded_project"] += 1
                continue
            if include and not any(p in include for p in pp):
                held["project_not_included"] += 1
                continue
        out.append(r)
        roots = {root_of.get(n, n) for n in holding}
        domain_of[mid] = sorted(roots)[0] if roots else ""
    return out, held, nodes, domain_of


def _exported_nodes(nodes: Dict[str, dict], memory_ids: Set[str]) -> Set[str]:
    """Nodes holding an exported memory, plus their ancestors so the
    bundle's tree is connected."""
    keep: Set[str] = set()
    for nid, n in nodes.items():
        if memory_ids & set(n.get("memory_refs", []) or []):
            cur: Optional[str] = nid
            seen: Set[str] = set()
            while cur and cur in nodes and cur not in seen:
                seen.add(cur)
                keep.add(cur)
                cur = nodes[cur].get("parent")
    return keep


def plan_export(store, sel: ExportSelection, sample_size: int = 0
                ) -> Tuple[ExportPlan, List[dict]]:
    """Resolve ``sel``.  Returns the plan and the first ``sample_size``
    selected rows (sanitized, for a preview list)."""
    rows, held, nodes, domain_of = _select_rows(store, sel)
    ids = [r["id"] for r in rows]
    id_set = set(ids)

    by_layer: Dict[str, int] = {}
    by_domain: Dict[str, int] = {}
    by_project: Dict[str, int] = {}
    warnings: List[Dict[str, Any]] = []
    for r in rows:
        by_layer[r.get("layer", "unknown")] = by_layer.get(r.get("layer", "unknown"), 0) + 1
        d = domain_of.get(r["id"], "")
        by_domain[d] = by_domain.get(d, 0) + 1
        pp = (r.get("scope") or {}).get("project_paths") or []
        for p in (pp or [""]):
            by_project[p] = by_project.get(p, 0) + 1
        warnings.extend(scan_memory(r))
    if not sel.strip_provenance and rows:
        warnings.append({"memory_id": None, "kind": "provenance_included",
                         "match": "", "snippet":
                         "Conversation ids and usage telemetry will be included."})
    if not sel.anonymize_paths and any(by_project.get(p) for p in by_project if p):
        warnings.append({"memory_id": None, "kind": "project_paths_included",
                         "match": "", "snippet":
                         "Absolute project paths from this machine will be included."})

    node_ids = sorted(_exported_nodes(nodes, id_set)) if sel.include_mindmap else []
    plan = ExportPlan(
        memory_ids=ids,
        fingerprint=fingerprint_ids(ids),
        total=len(ids),
        held_back={k: v for k, v in held.items() if v},
        counts={
            "by_layer": by_layer,
            "by_domain": [
                {"id": d, "handle": (nodes.get(d) or {}).get("handle", "") if d else "(unplaced)",
                 "count": c}
                for d, c in sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0]))],
            "by_project": [
                {"path": p, "label": project_label(p) if p else "(unscoped)", "count": c}
                for p, c in sorted(by_project.items(), key=lambda kv: (-kv[1], kv[0]))],
            "nodes": len(node_ids),
        },
        warnings=warnings,
        node_ids=node_ids,
    )
    sample = [sanitize_memory(r, sel, id_set) for r in rows[:sample_size]] if sample_size else []
    return plan, sample


# -- bundle ---------------------------------------------------------------------

def sanitize_memory(row: dict, sel: ExportSelection, exported_ids: Set[str]) -> dict:
    """Rebuild a memory from the whitelist.  Relations and ``related`` are
    pruned to ids inside the export so the bundle has no dangling refs."""
    out: Dict[str, Any] = {}
    for f in _MEMORY_PUBLIC_FIELDS:
        if f in row and row[f] is not None:
            out[f] = row[f]
    scope = dict(row.get("scope") or {})
    pp = scope.get("project_paths") or []
    if sel.anonymize_paths:
        pp = list(dict.fromkeys(project_label(p) for p in pp if p))
    out["scope"] = {"domain_node": scope.get("domain_node"), "project_paths": pp}
    out["related"] = [r for r in (row.get("related") or []) if r in exported_ids]
    rels = {}
    for kind, targets in (row.get("relations") or {}).items():
        kept = [t for t in (targets or []) if t in exported_ids]
        if kept:
            rels[kind] = kept
    out["relations"] = rels
    if not sel.strip_provenance:
        for f in _MEMORY_PROVENANCE_FIELDS:
            if f in row and row[f] is not None:
                out[f] = row[f]
    return out


def _sanitize_node(n: dict, kept_nodes: Set[str], exported_ids: Set[str]) -> dict:
    out = {f: n.get(f) for f in _NODE_PUBLIC_FIELDS if f in n}
    out["parent"] = n.get("parent") if n.get("parent") in kept_nodes else None
    out["children"] = [c for c in n.get("children", []) or [] if c in kept_nodes]
    out["cross_links"] = [c for c in n.get("cross_links", []) or [] if c in kept_nodes]
    out["memory_refs"] = [r for r in n.get("memory_refs", []) or [] if r in exported_ids]
    return out


def build_bundle(store, sel: ExportSelection, plan: ExportPlan,
                 exported_by: Optional[str] = None) -> Dict[str, Any]:
    """The export file.  Re-resolves the selection and refuses (ValueError)
    if the store no longer matches ``plan.fingerprint``."""
    rows, _held, nodes, _dom = _select_rows(store, sel)
    ids = [r["id"] for r in rows]
    if fingerprint_ids(ids) != plan.fingerprint:
        raise ValueError("memory store changed since preview; re-run the preview")
    id_set = set(ids)
    kept_nodes = set(plan.node_ids) & set(nodes) if sel.include_mindmap else set()
    try:
        from importlib.metadata import version as _pkg_version
        ziya_version = _pkg_version("ziya")
    except Exception:
        ziya_version = None
    # The header must honour anonymization too: the selection echo and the
    # per-project counts name raw paths from this machine.
    selection = sel.model_dump()
    counts = dict(plan.counts)
    if sel.anonymize_paths:
        selection["include_projects"] = [project_label(p) for p in sel.include_projects]
        selection["exclude_projects"] = [project_label(p) for p in sel.exclude_projects]
        counts["by_project"] = [
            {"label": c["label"], "count": c["count"]} for c in plan.counts.get("by_project", [])]
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exported_by": exported_by,
        "ziya_version": ziya_version,
        "selection": selection,
        "fingerprint": plan.fingerprint,
        "counts": counts,
        "mindmap": [_sanitize_node(nodes[nid], kept_nodes, id_set) for nid in sorted(kept_nodes)],
        "memories": [sanitize_memory(r, sel, id_set) for r in rows],
    }


def export_filename(plan: ExportPlan, when: Optional[float] = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(when))
    return f"ziya-memory-export-{stamp}-{plan.total}mem-{plan.fingerprint[:8]}.json"


# -- Save-to-path ------------------------------------------------------------
# A browser download lands wherever the browser's default is and the page is
# never told where, so the user cannot find the file.  The server writes the
# bundle to a location the user typed instead and returns the absolute path.

def default_export_dir() -> str:
    """Prefilled save folder: ~/Downloads when it exists, else home."""
    import os
    from pathlib import Path
    home = Path(os.path.expanduser("~"))
    downloads = home / "Downloads"
    return str(downloads if downloads.is_dir() else home)


def resolve_save_path(raw: str, filename: str):
    """Absolute file path for a user-chosen save location.

    An existing directory, or a value ending in a path separator, gets
    ``filename`` appended.  A file path without a ``.json`` suffix gets one.
    A relative value is taken relative to the home directory (the server's
    cwd is an implementation detail the user cannot see).  Raises
    ValueError when the location is empty or its folder does not exist.
    """
    import os
    from pathlib import Path
    s = (raw or "").strip()
    if not s:
        raise ValueError("No save location given.")
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        p = Path(os.path.expanduser("~")) / p
    if s.endswith(("/", os.sep)) or p.is_dir():
        p = p / filename
    elif p.suffix.lower() != ".json":
        p = p.with_name(p.name + ".json")
    p = p.parent.resolve() / p.name
    if not p.parent.is_dir():
        raise ValueError(f"Folder does not exist: {p.parent}")
    return p


def write_export_file(path, content: str, overwrite: bool = False):
    """Write ``content`` to ``path`` atomically.  Raises FileExistsError
    when the file exists and ``overwrite`` is False."""
    import os
    from pathlib import Path
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path
