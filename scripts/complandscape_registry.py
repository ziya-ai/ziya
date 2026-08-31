#!/usr/bin/env python3
"""Freeze the competitive-landscape comparison schema so runs can be diffed.

WHY THIS EXISTS
---------------
The first CL5 run produced 2,135 scored cells that cannot be compared to a
re-run.  A cell's key is ``(capability_id, dimension_id, tool)``; the first
two thirds were stable and the middle third did not exist.  Depth agents were
handed a prose list of ``measurable_dimensions`` as a *suggestion* and authored
their own: measured against the queue, 52% of the 601 dimensions they scored
were novel, 43% reworded, 5% verbatim.  So only ~101 of 2,135 cells could be
aligned to a second run.  Dimensions are the AXES of the comparison -- they are
schema, not findings -- and regenerating them per run means regenerating the
comparison itself.

This module freezes that schema into ``25-dimension-registry.json``:

  * every dimension gets a stable ``dimension_id`` plus a ``name_hash``, so a
    later edit to a dimension's wording is *detectable* rather than silently
    re-aligning two different axes onto one id;
  * every capability's contender list is derived MECHANICALLY from the matrix
    instead of being prose.  37% of the queue's contender tokens contained a
    placeholder ("many", "all", "various") and 16 capabilities named no tool at
    all, which is what forced depth agents to invent a roster -- and they
    reached for whoever had the most public documentation (cursor +9,
    claude-code +7, cline +6), i.e. availability bias;
  * the three ways a tool can be absent from a comparison are separated, since
    they are not the same claim (see ABSENCE_REASONS).

THE 56% BLIND SPOT
------------------
Deriving contenders from the matrix exposes something the prose hid: of the
108x26 contested grid, only 31% of pairs carry a matrix cell scoring >=2 and
13% carry an explicit 0-or-1, while **56% have no matrix cell at all**.  A
missing cell is not a zero -- explicit zeros are recorded (471 of them) -- it
means CL2/CL3 never assessed that pair.  The registry records that per
capability as ``blind_spot`` so a re-audit reports its own coverage instead of
presenting an unassessed pair as evidence of absence.

USAGE
-----
    python3 scripts/complandscape_registry.py build   [--root DIR] [--write]
    python3 scripts/complandscape_registry.py check   [--root DIR]
    python3 scripts/complandscape_registry.py validate-run RUN_DIR [--root DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: Why a (dimension, tool) cell carries no score.  These are NOT
#: interchangeable and collapsing them is the defect this enum exists to stop:
#: a chart that renders every one of them as a blank invites the reader to
#: treat "we never checked" as "they don't have it".
ABSENCE_REASONS: Dict[str, str] = {
    "scored": "A numeric score is present. Not an absence.",
    "below_threshold": (
        "The matrix scores this tool 0-1 on the capability, so it is not a "
        "contender on any of its dimensions. REAL SIGNAL: they lack it."
    ),
    "not_applicable": (
        "The axis is meaningless for this tool's architecture (e.g. "
        "'cross-provider normalization' for a single-provider native tool). "
        "REAL SIGNAL, but not a deficiency -- do not average it as a zero."
    ),
    "unknown": (
        "The tool has the capability but its behaviour on THIS axis cannot be "
        "determined from available evidence. NO SIGNAL. Record what would "
        "resolve it (usually hands-on use)."
    ),
    "not_assessed": (
        "The agent did not reach this cell -- budget, time, or a truncated "
        "roster. NO SIGNAL, and an honest record of the run's own limits. The "
        "first run's repeat_max clip left 48 capabilities in exactly this "
        "state with nothing recording it."
    ),
    "not_in_matrix": (
        "CL3 never scored this (capability, tool) pair, so contention was "
        "never established. NO SIGNAL. 56% of the contested grid is in this "
        "state; it is a coverage gap in an EARLIER phase, not a CL5 finding."
    ),
}

#: Absence reasons that carry real information about the competitor.
REAL_SIGNAL_REASONS = frozenset({"below_threshold", "not_applicable"})

#: Absence reasons that mean "we do not know", and must never be read as a
#: competitive finding in a report or a diff.
NO_SIGNAL_REASONS = frozenset({"unknown", "not_assessed", "not_in_matrix"})

VERDICTS = frozenset({"ZIYA_AHEAD", "PARITY", "ZIYA_BEHIND", "INDETERMINATE"})
CONFIDENCES = frozenset({"high", "medium", "low"})
EVIDENCE_TIERS = frozenset({"A", "B", "C", "D"})

#: Matrix score at or above which a tool is a contender on a capability.
#: Mirrors 32-contested-queue.json's own stated definition.
CONTENDER_THRESHOLD = 2

#: Jaccard token overlap at or above which a declared and a harvested
#: dimension are treated as the same axis and merged.  Tuned by measurement
#: over the real corpus: the median declared-to-harvested best match is 0.38,
#: and 0.35 folds 54% of declared axes into a harvested one as an alias while
#: leaving genuinely distinct ones separate.  Raising it to 0.55 left the
#: registry at 957 dimensions -- restatements counted twice.
MERGE_SIMILARITY = 0.35

#: A declared axis with no harvested counterpart is NOT frozen into the
#: registry.  The harvested set is what an agent chose to score AFTER reading
#: the implementing code, so an axis it passed over is a candidate, not an
#: omission -- and freezing 154 unvetted axes would inflate every run's cost
#: for cells nobody judged worth scoring once.  They are parked per capability
#: as ``dimension_candidates`` for review into the next registry version,
#: following the same probation shape the memory system uses.
FREEZE_UNMATCHED_DECLARED = False

SCHEMA_VERSION = 2
REGISTRY_FILENAME = "25-dimension-registry.json"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[^a-z0-9]+")

#: Tokens that carry no discriminating meaning when comparing two dimension
#: names, so including them inflates similarity between unrelated axes.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "vs", "per", "for", "to", "in", "on",
    "is", "are", "it", "its", "with", "by", "at", "from", "that", "this",
    "ziya", "competitor", "competitors", "tool", "tools",
})


_PAREN_RE = re.compile(r"\([^)]*\)")
_TRAILING_NOTE_RE = re.compile(r"\s(?:--|—|-{2,})\s.*$")


def axis_head(text: str) -> str:
    """The axis NAME with its value annotation stripped.

    Declared dimension strings routinely carry the answer inline -- "#
    languages indexed (Ziya ~25 via tree-sitter+py+ts)", "strategies
    (edge-based + name-fallback) — Ziya=2".  Those parentheticals are values,
    not part of the axis's identity, and left in place they dominate the token
    set: the example above scored 0.20 against the harvested "# languages
    indexed by the background build", which is plainly the same axis.  Stripping
    them is what lets a restatement be recognised as one.
    """
    stripped = _PAREN_RE.sub(" ", text or "")
    stripped = _TRAILING_NOTE_RE.sub("", stripped)
    return stripped.strip()


def tokens(text: str) -> frozenset:
    """Meaning-bearing lowercase tokens of ``text``, annotations removed."""
    raw = _WORD_RE.sub(" ", axis_head(text).lower()).split()
    return frozenset(t for t in raw if t and t not in _STOPWORDS)


def jaccard(a: str, b: str) -> float:
    """Token-set overlap of two names, 0.0 when either is empty."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def slugify(text: str, *, max_words: int = 6) -> str:
    """A short readable slug, used as the human-facing half of a dimension id.

    Readable rather than a bare hash because an agent has to cite it back
    correctly and a human has to read it in a diff.  Stability comes from the
    registry freezing it, not from the slug being derivable -- which is why
    ``name_hash`` is stored alongside.
    """
    words = [w for w in _WORD_RE.sub(" ", (text or "").lower()).split() if w]
    keep = [w for w in words if w not in _STOPWORDS] or words
    return "-".join(keep[:max_words]) or "unnamed"


def name_hash(text: str) -> str:
    """Content fingerprint of a dimension name, for drift detection."""
    return hashlib.sha256((text or "").strip().encode()).hexdigest()[:12]


def canonical_tool(label: str, roster: Iterable[str]) -> Optional[str]:
    """Map a free-text tool label onto a roster id, or None.

    The first run used 96 distinct labels for 26 tools -- ``aider (repo map)``,
    ``cline (list_code_definition_names)``, ``claude-ai (Artifacts)`` -- so a
    naive exact match reported 23% of comparisons as using a completely
    different tool set when they were the same tools differently spelled.
    Longest-match-first so ``claude-code`` is not captured by ``claude-ai``.
    """
    text = (label or "").strip().lower()
    if not text:
        return None
    best: Optional[str] = None
    for tool in sorted(roster, key=len, reverse=True):
        needle = tool.lower()
        if needle in text or needle.replace("-", " ") in text:
            best = tool
            break
    return best


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# Contender derivation
# --------------------------------------------------------------------------

def derive_contenders(
    matrix_cells_by_cap: Dict[str, Dict[str, dict]],
    capability_id: str,
    roster: Sequence[str],
) -> Dict[str, Any]:
    """Partition the roster for one capability into the three absence states.

    Mechanical and therefore reproducible: no prose, no placeholders, and the
    blind spot is counted rather than hidden.
    """
    cells = matrix_cells_by_cap.get(capability_id, {})
    contenders: List[Dict[str, Any]] = []
    below: List[Dict[str, Any]] = []
    blind: List[str] = []
    for tool in roster:
        cell = cells.get(tool)
        if cell is None:
            blind.append(tool)
            continue
        score = cell.get("score") or 0
        record = {
            "tool": tool,
            "matrix_score": score,
            "matrix_evidence_tier": cell.get("evidence_tier"),
        }
        if score >= CONTENDER_THRESHOLD:
            contenders.append(record)
        else:
            below.append(record)
    ranked = sorted(contenders, key=lambda r: (-r["matrix_score"], r["tool"]))
    # Rank is the run's scoring ORDER, so a budget-limited run covers the
    # strongest contenders first and what it drops is predictable rather than
    # whichever tools the agent happened to research.
    for position, record in enumerate(ranked, start=1):
        record["rank"] = position
    return {
        "contenders": ranked,
        "below_threshold": sorted(below, key=lambda r: r["tool"]),
        "blind_spot": sorted(blind),
        "coverage": {
            "roster": len(roster),
            "contenders": len(contenders),
            "below_threshold": len(below),
            "not_in_matrix": len(blind),
            # The fraction of the roster CL3 actually judged.  A capability
            # whose contender list looks short may simply be under-assessed,
            # and this is the number that says which.
            "assessed_fraction": round(
                (len(contenders) + len(below)) / len(roster), 3
            ) if roster else 0.0,
        },
    }


# --------------------------------------------------------------------------
# Dimension harvesting and merging
# --------------------------------------------------------------------------

def harvest_dimensions(depth_dir: str) -> Dict[str, List[dict]]:
    """Collect the dimensions the first run actually scored, per capability.

    The first run's dimensions are BETTER than the queue's declared ones --
    they were authored after reading the implementing code, which is why 52%
    were novel.  Harvesting them is how the run's analytic value is kept while
    its unalignable scores are discarded.
    """
    out: Dict[str, List[dict]] = {}
    if not os.path.isdir(depth_dir):
        return out
    for name in sorted(os.listdir(depth_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(depth_dir, name)
        try:
            doc = _read_json(path)
        except (ValueError, OSError):
            continue
        cap = doc.get("capability_id") or name[:-5]
        found = []
        for dim in doc.get("dimensions") or []:
            dim_name = (dim.get("name") or "").strip()
            if not dim_name:
                continue
            found.append({
                "name": dim_name,
                "why_it_matters": (dim.get("why_it_matters") or "").strip(),
                "provenance": "harvested_run1",
            })
        if found:
            out[cap] = found
    return out


def merge_dimension_sets(
    declared: Sequence[str],
    harvested: Sequence[dict],
) -> Tuple[List[dict], List[dict]]:
    """Fold declared axes into the harvested set; park the leftovers.

    Returns ``(frozen, candidates)``.  Harvested wins as the canonical name
    (post-code-reading and more precise); a declared restatement is retained as
    an alias, following the ledger's own ``aliases`` convention so terminology
    reconciliation stays possible.

    A declared axis with no harvested match becomes a CANDIDATE rather than a
    frozen dimension -- see FREEZE_UNMATCHED_DECLARED.  When there is no
    harvested set at all (a capability run 1 never reached) the declared axes
    are frozen, since something must be scoreable.
    """
    frozen: List[dict] = []
    for item in harvested:
        frozen.append({
            "name": item["name"],
            "why_it_matters": item.get("why_it_matters", ""),
            "aliases": [],
            "provenance": item.get("provenance", "harvested_run1"),
        })

    candidates: List[dict] = []
    for decl in declared:
        text = (decl or "").strip()
        if not text:
            continue
        best_idx, best_score = -1, 0.0
        for idx, existing in enumerate(frozen):
            score = jaccard(text, existing["name"])
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx >= 0 and best_score >= MERGE_SIMILARITY:
            aliases = frozen[best_idx]["aliases"]
            if text not in aliases:
                aliases.append(text)
            if frozen[best_idx]["provenance"] == "harvested_run1":
                frozen[best_idx]["provenance"] = "both"
        elif harvested and not FREEZE_UNMATCHED_DECLARED:
            candidates.append({
                "name": text,
                "provenance": "declared_queue",
                "best_match_similarity": round(best_score, 3),
                "status": "awaiting_review",
            })
        else:
            # No harvested set to defer to: freeze it or the capability has
            # nothing to score.
            frozen.append({
                "name": text,
                "why_it_matters": "",
                "aliases": [],
                "provenance": "declared_queue",
            })
    return frozen, candidates


def assign_dimension_ids(capability_id: str, dims: Sequence[dict]) -> List[dict]:
    """Freeze a stable id and a name fingerprint onto each dimension."""
    out: List[dict] = []
    used: Counter = Counter()
    for dim in dims:
        base = slugify(dim["name"])
        used[base] += 1
        # A numeric suffix only where two axes on ONE capability slugify
        # identically; without it the second would overwrite the first and
        # silently merge two distinct axes.
        suffix = "" if used[base] == 1 else f"-{used[base]}"
        out.append({
            "dimension_id": f"{capability_id}::{base}{suffix}",
            "name": dim["name"],
            "name_hash": name_hash(dim["name"]),
            "why_it_matters": dim.get("why_it_matters", ""),
            "aliases": dim.get("aliases", []),
            "provenance": dim.get("provenance", "harvested_run1"),
            "kind": classify_kind(dim["name"]),
        })
    return out


def classify_kind(name: str) -> str:
    """Advisory hint about what kind of answer an axis wants.

    Not load-bearing -- the agent still decides -- but a countable axis
    ("# of provider families normalized") and a behavioural one ("recovers
    from a malformed hunk") warrant different evidence, and saying so up front
    is cheaper than discovering it per agent.
    """
    low = (name or "").lower()
    if "#" in low or re.search(r"\b(count|number of|how many)\b", low):
        return "count"
    if re.search(r"\b(present|supported|exists|yes/no|has )\b", low):
        return "boolean"
    if re.search(r"\b(tiers?|levels?|granularity|scale)\b", low):
        return "ordinal"
    return "behavioral"


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build_registry(root: str) -> Dict[str, Any]:
    """Assemble the frozen registry from the existing study artifacts."""
    matrix = _read_json(os.path.join(root, "30-matrix.json"))
    queue = _read_json(os.path.join(root, "32-contested-queue.json"))

    roster = [t for t in matrix.get("tools", []) if t != "ziya"]
    cells_by_cap: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for cell in matrix.get("cells", []):
        cells_by_cap[cell["capability_id"]][cell["tool"]] = cell

    harvested = harvest_dimensions(os.path.join(root, "50-depth"))

    capabilities: Dict[str, Any] = {}
    for entry in queue.get("contested", []):
        cap = entry["id"]
        dims, candidates = merge_dimension_sets(
            entry.get("measurable_dimensions") or [],
            harvested.get(cap) or [],
        )
        derived = derive_contenders(cells_by_cap, cap, roster)
        frozen_dims = assign_dimension_ids(cap, dims)
        capabilities[cap] = {
            "capability_id": cap,
            "domain": entry.get("domain"),
            "ziya_ledger_score": entry.get("ziya_score"),
            "ziya_evidence": entry.get("ziya_evidence"),
            "dimensions": frozen_dims,
            # Parked, not frozen: reviewed into a MINOR version bump rather
            # than silently inflating this run's scope.
            "dimension_candidates": candidates,
            **derived,
            # Full grid for this capability. Where it exceeds the run's budget
            # the agent scores contenders in rank order and records the rest as
            # not_assessed -- a deliberate, recorded cap rather than a silent
            # clip, which is the whole lesson of the 108-into-60 roster
            # truncation.
            "cell_budget_hint": {
                "dimensions": len(frozen_dims),
                "contenders": len(derived["contenders"]),
                "full_grid": len(frozen_dims) * len(derived["contenders"]),
            },
        }

    total_dims = sum(len(c["dimensions"]) for c in capabilities.values())
    total_cells = sum(
        len(c["dimensions"]) * len(c["contenders"]) for c in capabilities.values()
    )
    registry = {
        "schema_version": SCHEMA_VERSION,
        # Bump the MINOR when dimensions are added, the MAJOR when any existing
        # dimension_id is renamed or removed -- the diff tool refuses to
        # compare cells across a major bump rather than mis-aligning them.
        "registry_version": "1.0.0",
        "frozen_at": date.today().isoformat(),
        "generated_from": [
            "30-matrix.json (contenders, mechanically derived)",
            "32-contested-queue.json (declared dimensions)",
            "50-depth/*.json (run-1 harvested dimensions)",
        ],
        "roster": roster,
        "contender_threshold": CONTENDER_THRESHOLD,
        "absence_reasons": ABSENCE_REASONS,
        "verdicts": sorted(VERDICTS),
        "confidences": sorted(CONFIDENCES),
        "evidence_tiers": sorted(EVIDENCE_TIERS),
        "totals": {
            "capabilities": len(capabilities),
            "dimensions": total_dims,
            "scoreable_competitor_cells": total_cells,
        },
        "capabilities": capabilities,
    }
    return registry


# --------------------------------------------------------------------------
# Check
# --------------------------------------------------------------------------

def check_registry(registry: Dict[str, Any]) -> List[str]:
    """Structural problems that would make a run un-diffable. Empty == good."""
    problems: List[str] = []
    caps = registry.get("capabilities") or {}
    if not caps:
        problems.append("registry has no capabilities")

    seen_ids: Counter = Counter()
    for cap_id, cap in caps.items():
        if cap.get("capability_id") != cap_id:
            problems.append(f"{cap_id}: capability_id disagrees with its key")
        dims = cap.get("dimensions") or []
        if not dims:
            problems.append(f"{cap_id}: no dimensions -- nothing to compare")
        for dim in dims:
            did = dim.get("dimension_id")
            seen_ids[did] += 1
            if not did or "::" not in str(did):
                problems.append(f"{cap_id}: malformed dimension_id {did!r}")
            if not dim.get("name"):
                problems.append(f"{did}: dimension has no name")
            # The whole point of storing the hash: a later edit to the name
            # without a version bump would silently re-point an id at a
            # different axis, and every diff across it would be wrong.
            elif dim.get("name_hash") != name_hash(dim["name"]):
                problems.append(
                    f"{did}: name_hash does not match name -- the dimension was "
                    f"edited without re-freezing; bump registry_version MAJOR"
                )
        for record in cap.get("contenders") or []:
            if (record.get("matrix_score") or 0) < CONTENDER_THRESHOLD:
                problems.append(
                    f"{cap_id}: {record.get('tool')} listed as a contender at "
                    f"score {record.get('matrix_score')}"
                )
    for did, count in seen_ids.items():
        if count > 1:
            problems.append(f"duplicate dimension_id across capabilities: {did} x{count}")
    return problems


# --------------------------------------------------------------------------
# Validate a depth run against the registry
# --------------------------------------------------------------------------

def validate_run(registry: Dict[str, Any], run_dir: str) -> Dict[str, Any]:
    """Confirm a run's output can actually be diffed against another.

    Run this BEFORE trusting a run's numbers. The first run would have failed
    every check here, which is the point: the failure was invisible until a
    second run was attempted.
    """
    caps = registry.get("capabilities") or {}
    errors: List[str] = []
    warnings: List[str] = []
    stats = Counter()
    proposals: List[dict] = []

    if not os.path.isdir(run_dir):
        return {"ok": False, "errors": [f"run dir not found: {run_dir}"],
                "warnings": [], "stats": {}, "dimension_proposals": []}

    files = [f for f in sorted(os.listdir(run_dir)) if f.endswith(".json")]
    for name in files:
        path = os.path.join(run_dir, name)
        try:
            doc = _read_json(path)
        except (ValueError, OSError) as exc:
            errors.append(f"{name}: unreadable ({exc})")
            continue
        stats["files"] += 1
        cap_id = doc.get("capability_id")
        cap = caps.get(cap_id)
        if cap is None:
            errors.append(f"{name}: capability_id {cap_id!r} is not in the registry")
            continue
        if doc.get("registry_version") != registry.get("registry_version"):
            errors.append(
                f"{name}: built against registry_version "
                f"{doc.get('registry_version')!r}, registry is "
                f"{registry.get('registry_version')!r}"
            )
        if doc.get("verdict") not in VERDICTS:
            errors.append(f"{name}: verdict {str(doc.get('verdict'))[:40]!r} off-vocabulary")
        if doc.get("confidence") not in CONFIDENCES:
            errors.append(f"{name}: confidence {doc.get('confidence')!r} off-vocabulary")

        known = {d["dimension_id"]: d for d in cap["dimensions"]}
        contender_tools = {r["tool"] for r in cap["contenders"]}
        seen_dims = set()
        for dim in doc.get("dimensions") or []:
            did = dim.get("dimension_id")
            if did not in known:
                errors.append(
                    f"{name}: dimension_id {did!r} is not in the registry -- an "
                    f"invented axis cannot be diffed; use dimension_proposals"
                )
                continue
            seen_dims.add(did)
            stats["dimensions"] += 1
            for cell in dim.get("competitors") or []:
                stats["cells"] += 1
                tool = cell.get("tool")
                status = cell.get("status")
                if status not in ABSENCE_REASONS:
                    errors.append(f"{name}/{did}/{tool}: status {status!r} off-vocabulary")
                    continue
                stats[f"status:{status}"] += 1
                if status == "scored":
                    if not isinstance(cell.get("score"), int):
                        errors.append(f"{name}/{did}/{tool}: scored with no integer score")
                    if cell.get("evidence_tier") not in EVIDENCE_TIERS:
                        errors.append(
                            f"{name}/{did}/{tool}: evidence_tier "
                            f"{cell.get('evidence_tier')!r} off-vocabulary"
                        )
                    # Undated scores are what make an evidence-vs-real delta
                    # impossible to attribute; the first run had 0 of 2,135.
                    if not cell.get("as_of"):
                        errors.append(f"{name}/{did}/{tool}: scored cell has no as_of date")
                if tool not in contender_tools and status == "scored":
                    warnings.append(
                        f"{name}/{did}: scored {tool}, which the matrix does not "
                        f"list as a contender (score <2 or unassessed)"
                    )
        missing = set(known) - seen_dims
        if missing:
            warnings.append(
                f"{name}: {len(missing)} registry dimension(s) not reported at all "
                f"-- record them explicitly as not_assessed: "
                f"{sorted(missing)[:3]}"
            )
        for proposal in doc.get("dimension_proposals") or []:
            proposals.append({"capability_id": cap_id, **proposal})

    unreported = set(caps) - {
        _read_json(os.path.join(run_dir, f)).get("capability_id")
        for f in files
        if _safe_json(os.path.join(run_dir, f)) is not None
    }
    if unreported:
        warnings.append(
            f"{len(unreported)} registry capabilities produced no file -- these are "
            f"not_assessed, not findings: {sorted(unreported)[:5]}"
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": dict(stats),
        "dimension_proposals": proposals,
    }


def _safe_json(path: str) -> Optional[Any]:
    try:
        return _read_json(path)
    except (ValueError, OSError):
        return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _default_root() -> str:
    return os.path.join(os.getcwd(), ".ziya", "complandscape")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "check", "validate-run"])
    parser.add_argument("run_dir", nargs="?", default=None)
    parser.add_argument("--root", default=None, help="complandscape dir")
    parser.add_argument("--write", action="store_true", help="persist the registry")
    args = parser.parse_args(argv)
    root = args.root or _default_root()

    if args.command == "build":
        registry = build_registry(root)
        problems = check_registry(registry)
        totals = registry["totals"]
        print(f"capabilities        : {totals['capabilities']}")
        print(f"frozen dimensions   : {totals['dimensions']}")
        print(f"scoreable cells     : {totals['scoreable_competitor_cells']}")
        blind = sum(
            c["coverage"]["not_in_matrix"] for c in registry["capabilities"].values()
        )
        assessed = sum(
            c["coverage"]["contenders"] + c["coverage"]["below_threshold"]
            for c in registry["capabilities"].values()
        )
        print(f"matrix pairs assessed: {assessed}   never assessed: {blind}")
        print(f"structural problems : {len(problems)}")
        for problem in problems[:20]:
            print(f"  - {problem}")
        if args.write:
            out = os.path.join(root, REGISTRY_FILENAME)
            tmp = out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(registry, handle, indent=2, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, out)
            print(f"wrote {out}")
        return 1 if problems else 0

    if args.command == "check":
        registry = _read_json(os.path.join(root, REGISTRY_FILENAME))
        problems = check_registry(registry)
        print(f"registry_version {registry.get('registry_version')} -- "
              f"{len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1 if problems else 0

    registry = _read_json(os.path.join(root, REGISTRY_FILENAME))
    result = validate_run(registry, args.run_dir or "")
    print(f"ok: {result['ok']}")
    print(f"stats: {result['stats']}")
    for err in result["errors"][:30]:
        print(f"  ERROR   {err}")
    for warn in result["warnings"][:15]:
        print(f"  WARNING {warn}")
    if result["dimension_proposals"]:
        print(f"  {len(result['dimension_proposals'])} dimension proposal(s) for the "
              f"NEXT registry version")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
