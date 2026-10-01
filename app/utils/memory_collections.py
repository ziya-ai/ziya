"""Provenance collections for mind-map placement.

A memory's provenance is the project it was learned in
(``scope.project_paths[0]``).  Gating placement on the raw project path is
wrong whenever one topic spans several repositories: a topic split across
three repos gives each repo a small share of the topic's own node, so the
gate judges the topic's memories incompatible with the node named after
them, while a single large project sitting in that node passes.  That
drained the topic out of its node and reinforced the gravity well.

A *collection* is a set of projects inferred, from the mind-map alone, to
hold the same body of knowledge: two projects join when their memories
sit together in the same SMALL nodes.  Small nodes are used because large
nodes are exactly where unrelated memories accumulate; evidence is
weighted by ``min(count_p, count_q)`` per node so a single stray memory
cannot fuse two projects.  No names, remotes or user input are consulted.

Everything here is a pure function over the plain-dict mind-map
(``MemoryStorage._load_mindmap()``) and a ``memory id -> project`` map.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

# Nodes with more members than this are not used as co-location evidence.
# Matches maintenance.CELL_DIVISION_THRESHOLD: a node this size is due to
# divide, i.e. it is already considered too broad to be one topic.
EVIDENCE_MAX_NODE_MEMBERS = 12
# Summed min(count_p, count_q) over small nodes needed to join p and q.
MIN_PAIR_WEIGHT = 2
# A project is evidence in a node only when it holds at least this share
# of the node's members.  Without it, single strays in several different
# nodes add up to MIN_PAIR_WEIGHT and fuse unrelated projects (seen on
# the live store: one stray Kuiper memory in each of two Ziya nodes).
EVIDENCE_MIN_SHARE = 0.25
# Catch-all roots that exist only for stragglers.  Never a destination for
# rebalance or split: pulling a memory out of a real topic node into the
# catch-all loses its topic.  Must match organizer._GENERAL_ROOT_ID
# (asserted by tests/test_memory_collections.py; not imported to avoid a
# storage -> organizer import cycle).
CATCH_ALL_NODE_IDS = frozenset({"domain_general"})
# Smallest group of a foreign collection worth splitting out of a node.
# Matches organizer.MIN_NODE_OCCUPANCY (not imported: organizer imports
# storage, which imports this module).
MIN_SPLIT_GROUP = 2


def infer_collections(nodes: Dict[str, dict],
                      proj_by_id: Dict[str, str],
                      max_node_members: int = EVIDENCE_MAX_NODE_MEMBERS,
                      min_pair_weight: int = MIN_PAIR_WEIGHT,
                      min_share: float = EVIDENCE_MIN_SHARE) -> Dict[str, str]:
    """Return ``project -> collection key`` for every project in
    ``proj_by_id``.  The key is the lexicographically smallest project in
    the collection, so it is stable across runs."""
    projects = sorted(set(proj_by_id.values()))
    parent = {p: p for p in projects}

    def find(p: str) -> str:
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    weight: Counter = Counter()
    for ndata in nodes.values():
        known = [proj_by_id[r] for r in ndata.get("memory_refs", []) if r in proj_by_id]
        if not known or len(known) > max_node_members:
            continue
        counts = Counter(known)
        ps = sorted(p for p, n in counts.items() if n / len(known) >= min_share)
        for i, p in enumerate(ps):
            for q in ps[i + 1:]:
                weight[(p, q)] += min(counts[p], counts[q])
    for (p, q), w in weight.items():
        if w >= min_pair_weight:
            rp, rq = find(p), find(q)
            if rp != rq:
                parent[max(rp, rq)] = min(rp, rq)
    return {p: find(p) for p in projects}


def keys_by_memory_id(proj_by_id: Dict[str, str],
                      coll_of_project: Dict[str, str]) -> Dict[str, str]:
    """``memory id -> collection key``."""
    return {mid: coll_of_project.get(p, p) for mid, p in proj_by_id.items()}


def memory_keys(project_paths: Iterable[str],
                coll_of_project: Dict[str, str]) -> set:
    """Collection keys for a memory's ``scope.project_paths``."""
    return {coll_of_project.get(p, p) for p in (project_paths or [])}


def _descendants(nid: str, nodes: Dict[str, dict]) -> List[str]:
    out, stack, seen = [], list(nodes.get(nid, {}).get("children", [])), {nid}
    while stack:
        c = stack.pop()
        if c in seen or c not in nodes:
            continue
        seen.add(c)
        out.append(c)
        stack.extend(nodes[c].get("children", []))
    return out


def owner_collection(nid: str, nodes: Dict[str, dict],
                     key_by_id: Dict[str, str],
                     min_members: int = MIN_SPLIT_GROUP) -> Optional[str]:
    """The collection a node belongs to.

    A node's children were carved out of its own cluster by cell division,
    so when its descendants hold at least ``min_members`` memories of known
    provenance their plurality decides; this keeps a topic's root from
    being claimed by whatever flooded into it later.  Otherwise the
    plurality of the node's own members decides.  Ties break by key.
    """
    def plurality(refs: Iterable[str]) -> Optional[str]:
        c = Counter(key_by_id[r] for r in refs if r in key_by_id)
        if not c:
            return None
        return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    desc_refs = [r for d in _descendants(nid, nodes) for r in nodes[d].get("memory_refs", [])]
    if sum(1 for r in desc_refs if r in key_by_id) >= min_members:
        return plurality(desc_refs)
    return plurality(nodes.get(nid, {}).get("memory_refs", []))


def plan_collection_splits(nodes: Dict[str, dict],
                           key_by_id: Dict[str, str],
                           min_group: int = MIN_SPLIT_GROUP
                           ) -> List[Tuple[str, str, List[str]]]:
    """``(node_id, foreign_collection, member_ids)`` for every group of at
    least ``min_group`` members whose collection is not the node's owner.
    Smaller groups stay (the soft gate: a lone memory of another project
    is tolerated rather than stranded)."""
    plans = []
    for nid in sorted(nodes):
        owner = owner_collection(nid, nodes, key_by_id, min_group)
        if owner is None:
            continue
        groups: Dict[str, List[str]] = defaultdict(list)
        own = 0
        for r in nodes[nid].get("memory_refs", []):
            k = key_by_id.get(r)
            if k == owner:
                own += 1
            elif k is not None:
                groups[k].append(r)
        for k in sorted(groups):
            # Only a flood leaves: a group that outnumbers the owner's own
            # members on this node.  A minority stays, because collection
            # inference can miss a small repo of the same topic (on the
            # live store np-switchd and qos never co-locate with
            # switch-mgr in a small node), and splitting it out would
            # strand real topic knowledge in a machine-named root.
            if len(groups[k]) >= min_group and len(groups[k]) > own:
                plans.append((nid, k, groups[k]))
    return plans


def derive_tags(tag_lists: Iterable[Iterable[str]], max_tags: int = 6) -> List[str]:
    """Tags characteristic of a group: those on at least two members (or
    the most frequent when none repeat), most frequent first."""
    c = Counter(t.lower() for tags in tag_lists for t in set(tags))
    ranked = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
    shared = [t for t, n in ranked if n >= 2]
    return (shared or [t for t, _ in ranked])[:max_tags]


def member_node_tags(tag_lists: List[Iterable[str]], min_share: float = 0.5,
                     max_tags: int = 12) -> Optional[List[str]]:
    """Part C of the gravity-well fix: a node's tags derived from its members.

    Node tags only ever accreted (organizer merges, enrichment, division all
    union; nothing prunes), so a root reached 513 tags on 28 members and out-
    scored every other node on raw overlap while its accreted tags blocked
    cell division.  Here a node's tags are the tags carried by at least
    ``min_share`` of its members (most frequent first, capped), falling back
    to ``derive_tags`` when no tag is that widely shared.  None when the node
    has no members with tags, so an empty node keeps the tags that let it be
    seeded.  Off by default (MemoryStorage.NODE_TAGS_FROM_MEMBERS); the
    replay harness enables it as an ablation arm and sweeps ``min_share``.
    """
    lists = [list(t) for t in tag_lists if t]
    if not lists:
        return None
    c = Counter(t.lower() for tags in lists for t in set(tags))
    shared = [t for t, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
              if n / len(lists) >= min_share]
    return (shared or derive_tags(lists, max_tags))[:max_tags]


def handle_from_tags(tags: List[str], n: int = 3) -> str:
    """A readable handle built from the group's own tags, no model call."""
    words = [re.sub(r"[-_]+", " ", t).strip().title() for t in tags[:n] if t.strip()]
    return " · ".join(words) or "Unsorted"
