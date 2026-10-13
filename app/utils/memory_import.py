"""Memory import: merge a ``ziya-memory-export`` bundle into a memory store.

The counterpart of app/utils/memory_export.py.  Two callers:

* The Memory Browser's Import side (app/api/memory.py): preview a bundle
  against the user's own store, then apply it.  Same bounds pattern as the
  export: the preview carries a fingerprint of the bundle *and* of the
  store's current state, and ``apply_import`` refuses when either moved.
* ``seed_store`` for headless runs: write a bundle into an empty directory
  that the run then uses as its memory store (``ZIYA_MEMORY_DIR``), with or
  without ``ZIYA_MEMORY_READONLY``.  See app/utils/paths.py.

Dispositions
------------
Each bundle memory is classified against the store:

``new``                 no memory with this id or content; imported.
``identical``           same id, same content; skipped, references keep the id.
``duplicate_content``   different id, same (normalised) content already in
                        the store; skipped, references are redirected to the
                        existing memory.  Off with ``skip_duplicate_content``.
``collision``           same id, different content; resolved by
                        ``on_collision``: ``rename`` (default) gives the
                        incoming memory a fresh id and rewrites every reference
                        to it inside the bundle, ``skip`` drops it, ``overwrite``
                        replaces the stored memory.

Imported memories are tagged ``imported`` (plus ``from:<label>`` when a label
is given), get ``learned_from="imported"``, and carry an ``imported_from``
record naming the bundle's fingerprint, exporter and export time, so a
later export or review can tell borrowed knowledge from the user's own.

Nothing here embeds.  ``MemoryStorage.save_many`` would call the embedding
provider once per memory, which for a 2,000-memory reference set is 2,000
network calls at startup; the store is written in one pass and embeddings
are a separate, explicit backfill (``POST /api/v1/memory/embeddings/backfill``).
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from app.models.memory import MEMORY_LAYERS
from app.utils.memory_export import EXPORT_FORMAT

SUPPORTED_VERSIONS = {1}
IMPORTED_TAG = "imported"
_WS_RE = re.compile(r"\s+")


class ImportOptions(BaseModel):
    model_config = {"extra": "forbid"}

    on_collision: str = Field(
        default="rename", pattern=r"^(rename|skip|overwrite)$",
        description="What to do when a bundle id exists with different content.")
    skip_duplicate_content: bool = Field(
        default=True,
        description="Skip a bundle memory whose content already exists under "
                    "another id; redirect references to the existing one.")
    include_mindmap: bool = Field(
        default=True, description="Merge the bundle's mind-map nodes.")
    source_label: Optional[str] = Field(
        default=None, max_length=64,
        description="Tagged onto every imported memory as from:<label>.")


class ImportPlan(BaseModel):
    """Resolved merge: exactly what ``apply_import`` will write."""
    model_config = {"extra": "forbid"}

    bundle_fingerprint: str
    store_fingerprint: str
    dispositions: Dict[str, str]          # bundle id -> disposition
    id_map: Dict[str, str]                # bundle id -> final store id (absent = dropped)
    counts: Dict[str, int]
    nodes_new: List[str]
    nodes_merged: List[str]
    warnings: List[str]


# -- helpers --------------------------------------------------------------------

def _norm(content: str) -> str:
    return _WS_RE.sub(" ", (content or "").strip().lower())


def load_bundle(obj: Any) -> Dict[str, Any]:
    """Validate the bundle's shape.  Raises ValueError with a reason."""
    if not isinstance(obj, dict):
        raise ValueError("bundle must be a JSON object")
    if obj.get("format") != EXPORT_FORMAT:
        raise ValueError(f"not a {EXPORT_FORMAT} bundle (format={obj.get('format')!r})")
    if obj.get("version") not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported bundle version {obj.get('version')!r}; "
                         f"this build reads {sorted(SUPPORTED_VERSIONS)}")
    mems = obj.get("memories")
    if not isinstance(mems, list):
        raise ValueError("bundle has no memories list")
    seen: Set[str] = set()
    for i, m in enumerate(mems):
        if not isinstance(m, dict) or not m.get("id") or not isinstance(m.get("content"), str):
            raise ValueError(f"memory #{i} lacks id or content")
        if m["id"] in seen:
            raise ValueError(f"bundle repeats memory id {m['id']}")
        seen.add(m["id"])
    nodes = obj.get("mindmap", [])
    if nodes is None:
        nodes = []
    if not isinstance(nodes, list) or any(not isinstance(n, dict) or not n.get("id") for n in nodes):
        raise ValueError("bundle mindmap must be a list of nodes with ids")
    obj["mindmap"] = nodes
    return obj


def bundle_fingerprint(bundle: Dict[str, Any]) -> str:
    h = hashlib.sha256()
    for m in sorted(bundle["memories"], key=lambda m: m["id"]):
        h.update(m["id"].encode("utf-8")); h.update(b"\0")
        h.update(m["content"].encode("utf-8")); h.update(b"\n")
    for n in sorted(bundle.get("mindmap", []), key=lambda n: n["id"]):
        h.update(b"node:"); h.update(n["id"].encode("utf-8")); h.update(b"\n")
    return h.hexdigest()[:16]


def store_fingerprint(rows: List[dict], nodes: Dict[str, dict]) -> str:
    """Digest of what the plan depends on: ids and contents, node ids."""
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: r.get("id") or ""):
        h.update((r.get("id") or "").encode("utf-8")); h.update(b"\0")
        h.update(_norm(r.get("content") or "").encode("utf-8")); h.update(b"\n")
    for nid in sorted(nodes):
        h.update(b"node:"); h.update(nid.encode("utf-8")); h.update(b"\n")
    return h.hexdigest()[:16]


def _fresh_id(taken: Set[str]) -> str:
    while True:
        mid = f"m_{uuid.uuid4().hex[:8]}"
        if mid not in taken:
            return mid


# -- plan -----------------------------------------------------------------------

def plan_import(store, bundle: Dict[str, Any], opts: ImportOptions) -> ImportPlan:
    rows = store._load_memories()
    nodes = store._load_mindmap()
    by_id = {r.get("id"): r for r in rows if r.get("id")}
    by_content: Dict[str, str] = {}
    for r in rows:
        if r.get("id") and r.get("status", "active") != "archived":
            by_content.setdefault(_norm(r.get("content") or ""), r["id"])

    taken: Set[str] = set(by_id)
    dispositions: Dict[str, str] = {}
    id_map: Dict[str, str] = {}
    counts = {"new": 0, "identical": 0, "duplicate_content": 0,
              "collision_renamed": 0, "collision_skipped": 0, "collision_overwritten": 0}
    warnings: List[str] = []

    for m in bundle["memories"]:
        mid, norm = m["id"], _norm(m["content"])
        if mid in by_id:
            if _norm(by_id[mid].get("content") or "") == norm:
                dispositions[mid] = "identical"; id_map[mid] = mid
                counts["identical"] += 1
            elif opts.on_collision == "skip":
                dispositions[mid] = "collision_skipped"
                counts["collision_skipped"] += 1
            elif opts.on_collision == "overwrite":
                dispositions[mid] = "collision_overwritten"; id_map[mid] = mid
                counts["collision_overwritten"] += 1
            else:
                new = _fresh_id(taken); taken.add(new)
                dispositions[mid] = "collision_renamed"; id_map[mid] = new
                counts["collision_renamed"] += 1
        elif opts.skip_duplicate_content and norm in by_content:
            dispositions[mid] = "duplicate_content"; id_map[mid] = by_content[norm]
            counts["duplicate_content"] += 1
        else:
            dispositions[mid] = "new"; id_map[mid] = mid; taken.add(mid)
            counts["new"] += 1
        if m.get("layer") not in MEMORY_LAYERS:
            warnings.append(f"{mid}: unknown layer {m.get('layer')!r}, imported as domain_context")

    nodes_new: List[str] = []
    nodes_merged: List[str] = []
    if opts.include_mindmap:
        for n in bundle.get("mindmap", []):
            (nodes_merged if n["id"] in nodes else nodes_new).append(n["id"])
    counts["nodes_new"] = len(nodes_new)
    counts["nodes_merged"] = len(nodes_merged)
    counts["to_write"] = counts["new"] + counts["collision_renamed"] + counts["collision_overwritten"]

    return ImportPlan(
        bundle_fingerprint=bundle_fingerprint(bundle),
        store_fingerprint=store_fingerprint(rows, nodes),
        dispositions=dispositions, id_map=id_map, counts=counts,
        nodes_new=sorted(nodes_new), nodes_merged=sorted(nodes_merged),
        warnings=warnings,
    )


def preview_rows(bundle: Dict[str, Any], plan: ImportPlan, limit: int = 50) -> List[dict]:
    """Per-memory lines for the preview list."""
    out = []
    for m in bundle["memories"][:limit]:
        out.append({
            "id": m["id"], "final_id": plan.id_map.get(m["id"]),
            "disposition": plan.dispositions.get(m["id"], "new"),
            "layer": m.get("layer"), "content": (m.get("content") or "")[:160],
        })
    return out


# -- apply ----------------------------------------------------------------------

def _map_ids(ids: Any, id_map: Dict[str, str]) -> List[str]:
    return list(dict.fromkeys(id_map[i] for i in (ids or []) if i in id_map))


def apply_import(store, bundle: Dict[str, Any], plan: ImportPlan,
                 opts: ImportOptions, source: Optional[str] = None) -> Dict[str, Any]:
    """Write the plan.  Refuses (ValueError) when the bundle or the store
    no longer matches the plan's fingerprints."""
    rows = store._load_memories()
    nodes = store._load_mindmap()
    if bundle_fingerprint(bundle) != plan.bundle_fingerprint:
        raise ValueError("bundle changed since preview; re-run the preview")
    if store_fingerprint(rows, nodes) != plan.store_fingerprint:
        raise ValueError("memory store changed since preview; re-run the preview")

    id_map = plan.id_map
    final_node_ids: Set[str] = set(nodes)
    if opts.include_mindmap:
        final_node_ids |= {n["id"] for n in bundle.get("mindmap", [])}
    # Which final node holds each imported memory, for scope.domain_node.
    holder: Dict[str, str] = {}
    if opts.include_mindmap:
        for n in bundle.get("mindmap", []):
            for ref in n.get("memory_refs", []) or []:
                if ref in id_map:
                    holder.setdefault(id_map[ref], n["id"])

    imported_from = {
        "fingerprint": plan.bundle_fingerprint,
        "exported_by": bundle.get("exported_by"),
        "exported_at": bundle.get("exported_at"),
        "imported_at": time.strftime("%Y-%m-%d"),
        "source": source,
    }
    extra_tags = [IMPORTED_TAG] + ([f"from:{opts.source_label}"] if opts.source_label else [])

    to_write: Dict[str, dict] = {}
    for m in bundle["memories"]:
        disp = plan.dispositions.get(m["id"])
        if disp not in ("new", "collision_renamed", "collision_overwritten"):
            continue
        fid = id_map[m["id"]]
        scope = dict(m.get("scope") or {})
        dn = scope.get("domain_node")
        if dn not in final_node_ids:
            dn = holder.get(fid)
        rels = {}
        for kind, targets in (m.get("relations") or {}).items():
            kept = _map_ids(targets, id_map)
            if kept:
                rels[kind] = kept
        row = {
            "id": fid,
            "content": m["content"],
            "layer": m.get("layer") if m.get("layer") in MEMORY_LAYERS else "domain_context",
            "tags": list(dict.fromkeys([*(m.get("tags") or []), *extra_tags])),
            "learned_from": "imported",
            "created": m.get("created") or time.strftime("%Y-%m-%d"),
            "last_accessed": time.strftime("%Y-%m-%d"),
            "status": "active",
            "importance": float(m.get("importance", 0.5) or 0.5),
            "scope": {"domain_node": dn, "project_paths": list(scope.get("project_paths") or [])},
            "related": _map_ids(m.get("related"), id_map),
            "relations": rels,
            "imported_from": imported_from,
        }
        if m.get("reference"):
            row["reference"] = m["reference"]
        to_write[fid] = row

    by_idx = {r.get("id"): i for i, r in enumerate(rows)}
    for fid, row in to_write.items():
        if fid in by_idx:
            rows[by_idx[fid]] = row          # collision_overwritten
        else:
            by_idx[fid] = len(rows)
            rows.append(row)
    if to_write:
        store._save_memories(rows)

    if opts.include_mindmap and bundle.get("mindmap"):
        for n in bundle["mindmap"]:
            refs = _map_ids(n.get("memory_refs"), id_map)
            children = [c for c in (n.get("children") or []) if c in final_node_ids]
            links = [c for c in (n.get("cross_links") or []) if c in final_node_ids]
            parent = n.get("parent") if n.get("parent") in final_node_ids else None
            if n["id"] in nodes:
                cur = nodes[n["id"]]
                cur["memory_refs"] = list(dict.fromkeys([*(cur.get("memory_refs") or []), *refs]))
                cur["children"] = list(dict.fromkeys([*(cur.get("children") or []), *children]))
                cur["cross_links"] = list(dict.fromkeys([*(cur.get("cross_links") or []), *links]))
                cur["tags"] = list(dict.fromkeys([*(cur.get("tags") or []), *(n.get("tags") or [])]))
                if not cur.get("parent") and parent:
                    cur["parent"] = parent
            else:
                nodes[n["id"]] = {
                    "id": n["id"], "handle": n.get("handle") or n["id"], "parent": parent,
                    "children": children, "cross_links": links, "memory_refs": refs,
                    "tags": list(n.get("tags") or []), "access_count": 0,
                    "last_accessed": time.strftime("%Y-%m-%d"),
                }
        # Keep parent -> children consistent for nodes whose parent exists.
        for nid, n in nodes.items():
            p = n.get("parent")
            if p and p in nodes and nid not in (nodes[p].get("children") or []):
                nodes[p].setdefault("children", []).append(nid)
        store._save_mindmap(nodes)

    return {
        "imported": len(to_write),
        "counts": plan.counts,
        "nodes_new": plan.nodes_new,
        "nodes_merged": plan.nodes_merged,
        "embeddings_pending": len(to_write),
        "bundle_fingerprint": plan.bundle_fingerprint,
    }


# -- headless seeding -------------------------------------------------------------

def seed_store(memory_dir, bundle_obj: Any, source: Optional[str] = None) -> Dict[str, Any]:
    """Import ``bundle_obj`` into the store at ``memory_dir`` (created if
    absent).  Idempotent: a second seeding of the same bundle writes
    nothing (every memory is ``identical``)."""
    from pathlib import Path
    from app.storage.memory import MemoryStorage
    bundle = load_bundle(bundle_obj)
    store = MemoryStorage(memory_dir=Path(memory_dir))
    opts = ImportOptions()
    plan = plan_import(store, bundle, opts)
    return apply_import(store, bundle, plan, opts, source=source)
