#!/usr/bin/env python3
"""Diff two competitive-landscape depth runs, partitioned by cause.

WHY A PLAIN DIFF IS USELESS HERE
--------------------------------
A score moving from 3 to 4 has three possible causes and they demand opposite
reactions:

  REAL      the tool changed.  They shipped something, or we did.  This is the
            only class that belongs in a "how are we doing" report.
  EVIDENCE  we looked harder.  The cell's evidence tier moved (D->B, say), so
            the number moved because our knowledge improved, not because the
            world did.  71% of the first run's competitor cells were C or D
            tier and 35% were D, so on any re-audit this class will be LARGE.
            Unpartitioned, it swamps REAL and the report reads as constant
            competitive churn that is not happening.
  COVERAGE  the cell appeared or vanished, or its absence reason changed.  Not
            a finding at all -- roster wobble, budget, or a gap in an earlier
            phase.

A fourth class is not a delta but a refusal:

  SCHEMA    the two runs do not share the dimension.  Either the id is absent
            from one registry, or it is present in both with a different
            ``name_hash``, meaning the axis was reworded under a stable id.
            These cells are reported as INCOMPARABLE rather than diffed --
            silently aligning them is how a re-audit invents changes.

The first run cannot be an input to this tool: only ~5% of its 2,135 cells
carry a dimension that a second run could align to.  It is the scoping
exercise that produced the registry; run 2 is the first baseline.

USAGE
-----
    python3 scripts/complandscape_diff.py OLD_RUN_DIR NEW_RUN_DIR [--root DIR]
                                          [--json OUT] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from complandscape_registry import (  # noqa: E402
    ABSENCE_REASONS, NO_SIGNAL_REASONS, REAL_SIGNAL_REASONS, REGISTRY_FILENAME,
)

#: Delta classes, in the order a reader should care about them.
REAL = "real_change"
EVIDENCE = "evidence_change"
COVERAGE = "coverage_change"
SCHEMA = "incomparable_schema"
UNCHANGED = "unchanged"

#: Evidence tiers ordered strongest-first, for deciding whether a tier moved
#: "up" (we learned more) or "down" (a prior citation did not hold up).
TIER_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_run(run_dir: str) -> Dict[str, dict]:
    """Load a run directory keyed by capability_id."""
    out: Dict[str, dict] = {}
    if not os.path.isdir(run_dir):
        return out
    for name in sorted(os.listdir(run_dir)):
        if not name.endswith(".json"):
            continue
        try:
            doc = _read_json(os.path.join(run_dir, name))
        except (ValueError, OSError):
            continue
        cap = doc.get("capability_id")
        if cap:
            out[cap] = doc
    return out


def index_cells(run: Dict[str, dict]) -> Dict[Tuple[str, str, str], dict]:
    """Flatten a run into ``{(capability, dimension_id, tool): cell}``.

    Ziya is indexed under the tool name ``ziya`` from each dimension's own
    ``ziya`` block, so our side is diffed by the identical machinery -- a
    separate path for our own numbers is how a report ends up applying a
    kinder standard to itself.
    """
    cells: Dict[Tuple[str, str, str], dict] = {}
    for cap, doc in run.items():
        for dim in doc.get("dimensions") or []:
            did = dim.get("dimension_id")
            if not did:
                continue
            ziya = dim.get("ziya")
            if isinstance(ziya, dict):
                cells[(cap, did, "ziya")] = {
                    "status": ziya.get("status", "scored"),
                    "score": ziya.get("score"),
                    "evidence_tier": ziya.get("evidence_tier"),
                    "as_of": ziya.get("as_of"),
                    "citation": ziya.get("citation"),
                }
            for cell in dim.get("competitors") or []:
                tool = cell.get("tool")
                if tool:
                    cells[(cap, did, tool)] = cell
    return cells


def dimension_fingerprints(registry: dict) -> Dict[str, str]:
    """``{dimension_id: name_hash}`` for schema-comparability checks."""
    out: Dict[str, str] = {}
    for cap in (registry.get("capabilities") or {}).values():
        for dim in cap.get("dimensions") or []:
            did = dim.get("dimension_id")
            if did:
                out[did] = dim.get("name_hash")
    return out


def classify(old: Optional[dict], new: Optional[dict]) -> Tuple[str, str]:
    """Classify one cell's transition. Returns ``(class, human reason)``."""
    if old is None and new is None:
        return UNCHANGED, "absent from both"
    if old is None:
        return COVERAGE, f"new cell (status {(new or {}).get('status')})"
    if new is None:
        return COVERAGE, f"cell dropped (was {old.get('status')})"

    old_status = old.get("status", "scored")
    new_status = new.get("status", "scored")

    if old_status != "scored" or new_status != "scored":
        if old_status == new_status:
            return UNCHANGED, f"still {old_status}"
        # A move between not-scored states, or into/out of scoring, is a
        # coverage event -- but only one direction is a genuine finding, and
        # naming it keeps the two from being averaged together.
        if old_status in NO_SIGNAL_REASONS and new_status in REAL_SIGNAL_REASONS:
            return COVERAGE, f"resolved: {old_status} -> {new_status} (now real signal)"
        if old_status in REAL_SIGNAL_REASONS and new_status in NO_SIGNAL_REASONS:
            return COVERAGE, f"REGRESSED to no-signal: {old_status} -> {new_status}"
        return COVERAGE, f"{old_status} -> {new_status}"

    old_score, new_score = old.get("score"), new.get("score")
    old_tier, new_tier = old.get("evidence_tier"), new.get("evidence_tier")
    tier_moved = old_tier != new_tier

    if old_score == new_score:
        if tier_moved:
            return EVIDENCE, f"score {old_score} held; tier {old_tier} -> {new_tier}"
        return UNCHANGED, f"score {old_score}, tier {old_tier}"

    delta = f"{old_score} -> {new_score}"
    if tier_moved:
        direction = (
            "stronger" if TIER_RANK.get(new_tier, 0) > TIER_RANK.get(old_tier, 0)
            else "weaker"
        )
        return EVIDENCE, (
            f"{delta} but evidence became {direction} ({old_tier} -> {new_tier}) "
            f"-- attribute to knowledge, not to the tool"
        )
    return REAL, f"{delta} at unchanged tier {old_tier}"


def diff_runs(
    old_run: Dict[str, dict],
    new_run: Dict[str, dict],
    old_registry: dict,
    new_registry: dict,
) -> Dict[str, Any]:
    """Partition every cell transition between two runs."""
    old_cells = index_cells(old_run)
    new_cells = index_cells(new_run)
    old_fp = dimension_fingerprints(old_registry)
    new_fp = dimension_fingerprints(new_registry)

    buckets: Dict[str, List[dict]] = defaultdict(list)
    counts: Counter = Counter()

    for key in sorted(set(old_cells) | set(new_cells)):
        cap, did, tool = key
        # Schema gate first: a reworded axis under a stable id would otherwise
        # be diffed as though it measured the same thing.
        if did not in old_fp or did not in new_fp:
            kind, reason = SCHEMA, "dimension absent from one registry"
        elif old_fp[did] != new_fp[did]:
            kind, reason = SCHEMA, "dimension name changed under a stable id"
        else:
            kind, reason = classify(old_cells.get(key), new_cells.get(key))
        counts[kind] += 1
        if kind != UNCHANGED:
            buckets[kind].append({
                "capability_id": cap,
                "dimension_id": did,
                "tool": tool,
                "reason": reason,
                "old": old_cells.get(key),
                "new": new_cells.get(key),
            })

    verdict_flips: List[dict] = []
    for cap, new_doc in new_run.items():
        old_doc = old_run.get(cap)
        if not old_doc:
            continue
        if old_doc.get("verdict") != new_doc.get("verdict"):
            # A flip driven entirely by evidence/coverage cells is NOT a
            # competitive movement, so the supporting classes are counted
            # beside it rather than left for the reader to assume.
            support = Counter()
            for kind in (REAL, EVIDENCE, COVERAGE, SCHEMA):
                support[kind] = sum(
                    1 for row in buckets[kind] if row["capability_id"] == cap
                )
            verdict_flips.append({
                "capability_id": cap,
                "old_verdict": old_doc.get("verdict"),
                "new_verdict": new_doc.get("verdict"),
                "old_confidence": old_doc.get("confidence"),
                "new_confidence": new_doc.get("confidence"),
                "supporting_cells": dict(support),
                "driven_by_real_change": support[REAL] > 0,
            })

    total = sum(counts.values())
    comparable = total - counts[SCHEMA]
    return {
        "totals": {
            "cells_considered": total,
            "comparable": comparable,
            "comparable_fraction": round(comparable / total, 3) if total else 0.0,
            **{k: counts[k] for k in (REAL, EVIDENCE, COVERAGE, SCHEMA, UNCHANGED)},
        },
        "registry_versions": {
            "old": old_registry.get("registry_version"),
            "new": new_registry.get("registry_version"),
        },
        "verdict_flips": verdict_flips,
        REAL: buckets[REAL],
        EVIDENCE: buckets[EVIDENCE],
        COVERAGE: buckets[COVERAGE],
        SCHEMA: buckets[SCHEMA],
    }


def render(report: Dict[str, Any], limit: int = 25) -> str:
    """Human-readable summary, REAL changes first."""
    totals = report["totals"]
    lines: List[str] = []
    versions = report["registry_versions"]
    lines.append(f"registry: {versions['old']} -> {versions['new']}")
    lines.append(
        f"cells considered {totals['cells_considered']}  "
        f"comparable {totals['comparable']} "
        f"({100 * totals['comparable_fraction']:.0f}%)"
    )
    if totals["comparable_fraction"] < 0.9:
        lines.append(
            "  WARNING: under 90% of cells are comparable. The two runs do not "
            "share a schema; treat the deltas below as provisional."
        )
    lines.append("")
    lines.append(f"  REAL      (tool changed)        : {totals[REAL]}")
    lines.append(f"  EVIDENCE  (we looked harder)    : {totals[EVIDENCE]}")
    lines.append(f"  COVERAGE  (cell came/went)      : {totals[COVERAGE]}")
    lines.append(f"  SCHEMA    (incomparable)        : {totals[SCHEMA]}")
    lines.append(f"  unchanged                       : {totals[UNCHANGED]}")

    if report["verdict_flips"]:
        lines.append("")
        lines.append("VERDICT FLIPS")
        for flip in report["verdict_flips"]:
            mark = "REAL" if flip["driven_by_real_change"] else "no real cell moved"
            lines.append(
                f"  {flip['capability_id']}: {flip['old_verdict']} -> "
                f"{flip['new_verdict']}  [{mark}] {flip['supporting_cells']}"
            )

    for kind, title in (
        (REAL, "REAL CHANGES -- the only class that is competitive news"),
        (EVIDENCE, "EVIDENCE CHANGES -- our knowledge moved, not the tool"),
        (COVERAGE, "COVERAGE CHANGES -- not findings"),
        (SCHEMA, "INCOMPARABLE -- schema drift, not diffed"),
    ):
        rows = report[kind]
        if not rows:
            continue
        lines.append("")
        lines.append(f"{title} ({len(rows)})")
        for row in rows[:limit]:
            lines.append(
                f"  {row['tool']:<14} {row['dimension_id']}\n"
                f"      {row['reason']}"
            )
        if len(rows) > limit:
            lines.append(f"  ... {len(rows) - limit} more")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old_run")
    parser.add_argument("new_run")
    parser.add_argument("--root", default=None)
    parser.add_argument("--old-registry", default=None)
    parser.add_argument("--new-registry", default=None)
    parser.add_argument("--json", default=None, help="write the full report here")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args(argv)

    root = args.root or os.path.join(os.getcwd(), ".ziya", "complandscape")
    default_registry = os.path.join(root, REGISTRY_FILENAME)
    old_registry = _read_json(args.old_registry or default_registry)
    new_registry = _read_json(args.new_registry or default_registry)

    report = diff_runs(
        load_run(args.old_run), load_run(args.new_run), old_registry, new_registry,
    )
    print(render(report, limit=args.limit))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
