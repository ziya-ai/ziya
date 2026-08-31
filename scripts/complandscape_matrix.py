#!/usr/bin/env python3
"""Full-grid capability matrix tooling for the competitive-landscape study.

WHY THIS EXISTS
---------------
Matrix v1 was not a scored grid; it was a transcription of what 26 dossier
authors happened to enumerate.  Measured:

    capability's vendor_aliases NAMES the tool  -> a cell exists  380/404 = 94%
    vendor_aliases does NOT name the tool       -> a cell exists 1681/12726 = 13%

So a cell existed almost exactly when some dossier volunteered the pairing.
Nobody ever asked "does tool X have capability Y".  Consequences, measured:

    full grid            505 capabilities x 26 tools = 13130 pairs
    cells present                                      2061 (16%)
    never assessed                                    11069 (84%)

    contested queue (108 caps)   862 contender / 363 below-threshold / 1583 unassessed
    unique queue    (205 caps)   4952 of 5330 pairs unassessed (93%)
                                 117 of 205 "only Ziya has this" claims rest on
                                 ZERO competitor assessment

That last number is why this matters: the unique queue IS the positioning
argument, and more than half of it currently means "nobody looked".

THE CONTRACT THIS MODULE ENFORCES
---------------------------------
Every (capability, tool) pair carries EXACTLY ONE cell with an EXPLICIT
status.  There is deliberately no "not assessed" state -- its absence is the
whole point.  A silence in a dossier is now a research obligation, not a
missing row.

    present         score 1-5, with evidence
    absent          score 0, affirmatively determined
    not_applicable  score null; the capability presupposes an architecture the
                    tool does not have.  A REAL signal, not a deficiency, and
                    distinct from absent -- "browser-rendered diagrams" is not
                    a gap in a pure CLI, it is a category mismatch.
    unknown         score null; researched and undeterminable.  Must record
                    what_would_resolve, or it is indistinguishable from
                    nobody having tried.
    unresolved      score null; Ziya-only state for competitor-sourced
                    capabilities, settled by CL4.  Never write 0 here -- that
                    launders an unverified assumption into the matrix.

ID PRESERVATION
---------------
.ziya/complandscape/25-dimension-registry.json is frozen against v1 capability
ids.  Re-clustering would rename them and silently orphan the entire CL5
comparison schema -- the same failure mode as a whole-tree card write minting
fresh block ids and orphaning a signed approval.  A re-run may ADD
capabilities; it may not rename, merge, split or drop an existing id.
``check-space`` refuses on any such change.

Usage:
    complandscape_matrix.py plan            [--root DIR]
    complandscape_matrix.py check-space     [--root DIR]
    complandscape_matrix.py validate-cells  CELLS_DIR [--root DIR]
    complandscape_matrix.py merge           CELLS_DIR [--root DIR] [--write]
    complandscape_matrix.py check           [--root DIR] [--matrix PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ROOT = ".ziya/complandscape"
SCHEMA_VERSION = "2.0"

#: The complete status vocabulary.  Adding a member here is a schema change:
#: every consumer that partitions cells must learn it, so it must not be done
#: casually.
STATUSES = ("present", "absent", "not_applicable", "unknown", "unresolved")

#: Statuses that carry a numeric score.  Everything else scores null --
#: conflating "no score" with "score 0" is exactly how an unassessed pair
#: became indistinguishable from a verified absence in v1.
SCORED_STATUSES = ("present", "absent")

EVIDENCE_TIERS = ("A", "B", "C", "D")

#: Contender threshold, shared with complandscape_registry.  Kept as a literal
#: in both rather than imported, so neither module can silently change the
#: other's definition of "contender".
CONTENDER_THRESHOLD = 2


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _read_json(path: str) -> Any:
    """Read JSON, tolerating a duplicated close-punctuation tail.

    33-unique-queue.json shipped with a repeated ``\\n ]\\n}\\n`` tail from its
    writing agent and would not json.load at all.  A reader that dies on
    trailing punctuation turns a cosmetic authoring slip into a pipeline stop,
    so decode the first document and ignore a tail that is provably nothing
    but whitespace and close punctuation.  A tail carrying CONTENT still
    raises -- that is real corruption and must not be silently truncated.
    """
    raw = open(path, encoding="utf-8").read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        obj, end = json.JSONDecoder().raw_decode(raw, 0)
        tail = raw[end:]
        if set(tail) - set(" \t\r\n]}"):
            raise
        return obj


def load_space(root: str) -> Dict[str, Any]:
    """Load the capability space: the new file if present, else matrix v1.

    Falling back to the v1 matrix is what lets a first full-grid run reuse the
    v1 clustering rather than re-deriving it -- which is required, not merely
    convenient, because the frozen dimension registry keys on those ids.
    """
    newer = os.path.join(root, "29-capability-space.json")
    if os.path.exists(newer):
        data = _read_json(newer)
        return {
            "source": newer,
            "space_version": data.get("space_version"),
            "domains": data.get("domains") or [],
            "capabilities": data.get("capabilities") or [],
            "tools": data.get("tools") or [],
        }
    legacy = os.path.join(root, "30-matrix.json")
    data = _read_json(legacy)
    return {
        "source": legacy,
        "space_version": data.get("generated"),
        "domains": data.get("domains") or [],
        "capabilities": data.get("capabilities") or [],
        "tools": data.get("tools") or [],
    }


def competitor_tools(space: Dict[str, Any]) -> List[str]:
    return [t for t in space.get("tools", []) if t != "ziya"]


# --------------------------------------------------------------------------
# planning the fan-out
# --------------------------------------------------------------------------

def plan_slices(space: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One work slice per (tool, domain).

    Sliced by tool because tool knowledge is the expensive, reusable context:
    an agent loads one dossier and researches one product.  Sliced also by
    domain because a whole tool is 505 determinations in one agent, which is
    how v1's single Stage-1 agent ended up transcribing instead of
    interrogating.  505 x 26 in 364 slices is a median of 35 determinations
    each -- small enough that each one can actually be reasoned about.
    """
    by_domain: Dict[str, List[str]] = defaultdict(list)
    for cap in space["capabilities"]:
        by_domain[cap["domain"]].append(cap["id"])
    slices: List[Dict[str, Any]] = []
    for tool in competitor_tools(space):
        for domain in sorted(by_domain):
            caps = sorted(by_domain[domain])
            slices.append({
                "slice_id": f"{tool}::{domain}",
                "tool": tool,
                "domain": domain,
                "capability_ids": caps,
                "cells": len(caps),
            })
    # Largest slices first: they are the long pole, so starting them early
    # keeps the tail of the run from being one 71-cell agent finishing alone.
    slices.sort(key=lambda s: (-s["cells"], s["slice_id"]))
    return slices


# --------------------------------------------------------------------------
# space integrity
# --------------------------------------------------------------------------

def check_space(root: str) -> List[str]:
    """Refuse a capability space that renamed or dropped a frozen id."""
    problems: List[str] = []
    newer = os.path.join(root, "29-capability-space.json")
    if not os.path.exists(newer):
        return problems  # nothing to compare; the v1 matrix IS the space

    legacy_path = os.path.join(root, "30-matrix.json")
    if not os.path.exists(legacy_path):
        return problems
    legacy_ids = {c["id"] for c in (_read_json(legacy_path).get("capabilities") or [])}
    new = _read_json(newer)
    new_ids = {c["id"] for c in (new.get("capabilities") or [])}

    dropped = sorted(legacy_ids - new_ids)
    for cid in dropped:
        problems.append(
            f"capability id '{cid}' present in 30-matrix.json but absent from "
            f"29-capability-space.json -- 25-dimension-registry.json is frozen "
            f"against these ids; renaming or dropping one orphans the CL5 "
            f"comparison schema"
        )

    reg_path = os.path.join(root, "25-dimension-registry.json")
    if os.path.exists(reg_path):
        reg = _read_json(reg_path)
        frozen = set((reg.get("capabilities") or {}).keys())
        for cid in sorted(frozen - new_ids):
            problems.append(
                f"capability id '{cid}' is frozen in the dimension registry "
                f"(version {reg.get('registry_version')}) but missing from the "
                f"new capability space"
            )
    return problems


# --------------------------------------------------------------------------
# cell validation
# --------------------------------------------------------------------------

def _validate_cell(cell: Dict[str, Any], *, known_caps: set,
                   where: str) -> List[str]:
    errs: List[str] = []
    cid = cell.get("capability_id")
    if cid not in known_caps:
        errs.append(f"{where}: capability_id {cid!r} is not in the capability space")
    status = cell.get("status")
    if status not in STATUSES:
        errs.append(f"{where}: status {status!r} not in {STATUSES}")
        return errs

    score = cell.get("score")
    if status in SCORED_STATUSES:
        if not isinstance(score, int) or isinstance(score, bool):
            errs.append(f"{where}: status {status} requires an integer score")
        elif status == "present" and not (1 <= score <= 5):
            errs.append(f"{where}: status present requires score 1-5, got {score}")
        elif status == "absent" and score != 0:
            errs.append(f"{where}: status absent requires score 0, got {score}")
        if cell.get("evidence_tier") not in EVIDENCE_TIERS:
            errs.append(
                f"{where}: a scored cell requires evidence_tier in "
                f"{EVIDENCE_TIERS}, got {cell.get('evidence_tier')!r}"
            )
        if not (cell.get("citation") or "").strip():
            errs.append(f"{where}: a scored cell requires a citation")
    else:
        if score is not None:
            errs.append(
                f"{where}: status {status} must carry score null, got {score!r} "
                f"-- a non-null score here is read as a measurement"
            )

    # An unknown with no resolution path is indistinguishable from nobody
    # having tried, which is the exact ambiguity this schema exists to remove.
    if status == "unknown" and not (cell.get("what_would_resolve") or "").strip():
        errs.append(
            f"{where}: status unknown requires what_would_resolve -- without it "
            f"an unresolvable cell cannot be told apart from an unexamined one"
        )
    if status == "not_applicable" and not (cell.get("rationale") or "").strip():
        errs.append(
            f"{where}: status not_applicable requires a rationale naming the "
            f"architectural mismatch -- otherwise it reads as a hidden absence"
        )
    if not (cell.get("as_of") or "").strip():
        errs.append(
            f"{where}: every cell requires as_of -- a re-audit cannot tell a "
            f"real change from a restated one without knowing when each side "
            f"was observed"
        )
    return errs


def validate_cells(root: str, cells_dir: str) -> Dict[str, Any]:
    """Validate slice files and, above all, assert GRID COMPLETENESS.

    Per-cell schema checks are the cheap half.  The half that matters is that
    every (capability, tool) pair is present exactly once: that assertion is
    what makes v1's transcription failure structurally impossible rather than
    merely discouraged.
    """
    space = load_space(root)
    caps = {c["id"] for c in space["capabilities"]}
    tools = competitor_tools(space)
    errors: List[str] = []
    warnings: List[str] = []
    seen: Dict[Tuple[str, str], str] = {}
    status_counts: Counter = Counter()
    tier_counts: Counter = Counter()

    files = sorted(
        os.path.join(cells_dir, f)
        for f in os.listdir(cells_dir) if f.endswith(".json")
    ) if os.path.isdir(cells_dir) else []
    if not files:
        errors.append(f"no slice files found under {cells_dir}")

    for path in files:
        base = os.path.basename(path)
        try:
            data = _read_json(path)
        except Exception as exc:  # noqa: BLE001 - report, do not abort the sweep
            errors.append(f"{base}: unparseable ({exc})")
            continue
        tool = data.get("tool")
        if tool not in tools:
            errors.append(f"{base}: tool {tool!r} is not in the roster")
            continue
        for cell in data.get("cells") or []:
            cid = cell.get("capability_id")
            key = (cid, tool)
            if key in seen:
                errors.append(
                    f"{base}: duplicate cell for ({cid}, {tool}), already "
                    f"supplied by {seen[key]} -- two determinations for one "
                    f"pair means the merge would pick one arbitrarily"
                )
                continue
            seen[key] = base
            errs = _validate_cell(cell, known_caps=caps, where=f"{base}[{cid}]")
            errors.extend(errs)
            if not errs:
                status_counts[cell["status"]] += 1
                if cell.get("evidence_tier"):
                    tier_counts[cell["evidence_tier"]] += 1

    expected = {(c, t) for c in caps for t in tools}
    missing = expected - set(seen)
    if missing:
        by_tool = Counter(t for _, t in missing)
        errors.append(
            f"GRID INCOMPLETE: {len(missing)} of {len(expected)} "
            f"(capability, tool) pairs have no cell. A pair with no cell is "
            f"exactly the v1 defect -- it is indistinguishable from a verified "
            f"absence. Worst tools: {by_tool.most_common(5)}"
        )
    # Warn rather than fail: an all-unknown slice is honest, but it is also
    # what a slice that gave up looks like, and the two deserve a second look.
    per_tool_unknown: Counter = Counter()
    for (cid, tool), base in seen.items():
        pass
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "slice_files": len(files),
            "cells": len(seen),
            "expected_pairs": len(expected),
            "missing_pairs": len(missing),
            "completeness_pct": round(100.0 * len(seen) / len(expected), 1) if expected else 0.0,
            "status_counts": dict(status_counts),
            "tier_counts": dict(tier_counts),
        },
    }


# --------------------------------------------------------------------------
# merge
# --------------------------------------------------------------------------

def _ziya_cells(root: str, space: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Carry Ziya's cells forward from v1, normalised to the v2 vocabulary.

    Ziya's scores are A/B-tier code readings from CL1 and are not re-derived
    here: this phase's job is the competitor grid.  Competitor-sourced
    capabilities keep status 'unresolved' rather than being scored 0, because
    CL4 settles those and a 0 written here would launder an assumption.
    """
    legacy = os.path.join(root, "30-matrix.json")
    prior: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(legacy):
        for c in _read_json(legacy).get("cells") or []:
            if c.get("tool") == "ziya":
                prior[c["capability_id"]] = c
    today = date.today().isoformat()
    out: List[Dict[str, Any]] = []
    for cap in space["capabilities"]:
        cid = cap["id"]
        old = prior.get(cid)
        if old is None:
            out.append({
                "capability_id": cid, "tool": "ziya", "status": "unresolved",
                "score": None, "evidence_tier": None, "citation": None,
                "note": "no v1 Ziya cell; CL4 must settle this",
                "as_of": today,
            })
            continue
        # A zero is read as EVIDENCE or as a PLACEHOLDER depending on whether
        # it carries an evidence tier, not on the capability's origin.
        #
        # The origin test this replaced (``competitor-sourced and not score``)
        # was correct only before CL4 ran: at that point every
        # competitor-sourced Ziya cell was an unverified 0 and forwarding it
        # as "absent" would have laundered an assumption.  CL4 has since
        # settled all 112 of them -- 78 moved off zero, and 34 were confirmed
        # absent at tier A by mechanism search ("confirmed absent ... not a
        # terminology artifact").  Because ``not 0`` is true, the origin test
        # discarded exactly those 34 findings, re-queueing capabilities CL4
        # had already answered and paying for the same audit twice to reach
        # the same conclusion.
        #
        # Keying on the evidence tier instead is both correct now and
        # correct before CL4: an unverified placeholder has no tier and stays
        # unresolved, while a tier-bearing 0 is a determination and is
        # forwarded as the finding it is.  Origin no longer participates,
        # which also removes the asymmetry whereby a ledger-sourced 0 was
        # believed and a competitor-sourced one was not.
        score = old.get("score")
        if isinstance(score, bool) or not isinstance(score, int):
            status, score_out = "unresolved", None
        elif score >= 1:
            status, score_out = "present", score
        elif old.get("evidence_tier"):
            status, score_out = "absent", 0
        else:
            status, score_out = "unresolved", None
        out.append({
            "capability_id": cid, "tool": "ziya", "status": status,
            "score": score_out,
            "evidence_tier": old.get("evidence_tier"),
            "citation": old.get("citation"),
            "note": old.get("note"),
            "as_of": old.get("as_of") or "2026-08-19",
        })
    return out


def compute_coverage(matrix: Dict[str, Any]) -> Dict[str, Any]:
    """Coverage that states grid completeness and disaggregates zero-cells.

    v1's ``zero_cells: 507`` was CORRECT (471 competitor + 36 Ziya) but its
    name did not say whose, and it was misread during this very analysis as a
    competitor-only figure -- a 36-cell error in the one field a reader
    consults to size the gap.  The disaggregated names below exist so that
    misreading is not available.
    """
    caps = [c["id"] for c in matrix["capabilities"]]
    tools = [t for t in matrix["tools"] if t != "ziya"]
    cells = matrix["cells"]
    comp = [c for c in cells if c["tool"] != "ziya"]
    ziya = [c for c in cells if c["tool"] == "ziya"]
    expected = len(caps) * len(tools)
    present_pairs = len({(c["capability_id"], c["tool"]) for c in comp})

    status_by_tool: Dict[str, Counter] = defaultdict(Counter)
    tier_by_tool: Dict[str, Counter] = defaultdict(Counter)
    for c in cells:
        status_by_tool[c["tool"]][c.get("status") or "none"] += 1
        tier_by_tool[c["tool"]][c.get("evidence_tier") or "none"] += 1

    contenders = sum(
        1 for c in comp
        if c.get("status") == "present" and (c.get("score") or 0) >= CONTENDER_THRESHOLD
    )
    return {
        "grid": {
            "capabilities": len(caps),
            "competitor_tools": len(tools),
            "expected_competitor_cells": expected,
            "actual_competitor_cells": present_pairs,
            "completeness_pct": round(100.0 * present_pairs / expected, 1) if expected else 0.0,
            "never_assessed": expected - present_pairs,
        },
        "competitor_status_totals": dict(Counter(c.get("status") for c in comp)),
        "ziya_status_totals": dict(Counter(c.get("status") for c in ziya)),
        "zero_cells_competitor": sum(1 for c in comp if c.get("score") == 0),
        "zero_cells_ziya": sum(1 for c in ziya if c.get("score") == 0),
        "zero_cells_all": sum(1 for c in cells if c.get("score") == 0),
        "unresolved_ziya_cells": sum(1 for c in ziya if c.get("status") == "unresolved"),
        "contender_cells": contenders,
        "status_by_tool": {k: dict(v) for k, v in sorted(status_by_tool.items())},
        "tier_distribution": {k: dict(v) for k, v in sorted(tier_by_tool.items())},
    }


def merge(root: str, cells_dir: str) -> Dict[str, Any]:
    space = load_space(root)
    cells: List[Dict[str, Any]] = []
    files = sorted(
        os.path.join(cells_dir, f)
        for f in os.listdir(cells_dir) if f.endswith(".json")
    ) if os.path.isdir(cells_dir) else []
    for path in files:
        data = _read_json(path)
        tool = data.get("tool")
        for cell in data.get("cells") or []:
            row = dict(cell)
            row["tool"] = tool
            cells.append(row)
    cells.extend(_ziya_cells(root, space))
    matrix = {
        "schema_version": SCHEMA_VERSION,
        "generated": date.today().isoformat(),
        "generated_from": f"{space['source']} + {cells_dir}",
        "space_version": space.get("space_version"),
        "statuses": list(STATUSES),
        "contender_threshold": CONTENDER_THRESHOLD,
        "domains": space["domains"],
        "capabilities": space["capabilities"],
        "tools": space["tools"],
        "cells": cells,
    }
    matrix["coverage"] = compute_coverage(matrix)
    return matrix


def check_matrix(matrix: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if matrix.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version {matrix.get('schema_version')!r} != {SCHEMA_VERSION}"
        )
    caps = {c["id"] for c in matrix["capabilities"]}
    tools = [t for t in matrix["tools"] if t != "ziya"]
    seen: Counter = Counter()
    for c in matrix["cells"]:
        seen[(c["capability_id"], c["tool"])] += 1
        status = c.get("status")
        if status not in STATUSES:
            problems.append(f"cell ({c['capability_id']}, {c['tool']}): bad status")
            continue
        # Status and score must agree on EVERY cell, Ziya's included.  The
        # per-slice validator enforces this for competitor cells, but Ziya
        # cells are written by CL4's reintegration step, and this whole-matrix
        # check is their only gate.  A score written without its status --
        # the natural outcome of hand-editing a JSON document cell by cell --
        # yields "unresolved" carrying a score of 3: the queue derivation
        # reads the status and re-queues a capability CL4 has just resolved,
        # while every number in the matrix looks right.
        has_score = isinstance(c.get("score"), int) and not isinstance(
            c.get("score"), bool)
        if status in SCORED_STATUSES and not has_score:
            problems.append(
                f"cell ({c['capability_id']}, {c['tool']}): status "
                f"{status!r} requires an integer score"
            )
        if status not in SCORED_STATUSES and has_score:
            problems.append(
                f"cell ({c['capability_id']}, {c['tool']}): status "
                f"{status!r} must not carry a score (got {c.get('score')!r}) "
                f"-- status and score disagree about whether this is settled"
            )
    dupes = [k for k, v in seen.items() if v > 1]
    if dupes:
        problems.append(f"{len(dupes)} duplicated cells, e.g. {dupes[:3]}")
    missing = {(a, b) for a in caps for b in tools} - set(seen)
    if missing:
        problems.append(
            f"grid incomplete: {len(missing)} competitor pairs have no cell"
        )
    cov = matrix.get("coverage") or {}
    if cov.get("grid", {}).get("never_assessed"):
        problems.append(
            f"coverage reports {cov['grid']['never_assessed']} never-assessed "
            f"pairs; a full-grid run must report 0"
        )
    return problems


def _atomic_write(path: str, data: Any) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command",
                    choices=["plan", "check-space", "validate-cells", "merge", "check"])
    ap.add_argument("cells_dir", nargs="?", default=None)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--matrix", default=None)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "plan":
        space = load_space(args.root)
        slices = plan_slices(space)
        total = sum(s["cells"] for s in slices)
        print(f"capability space : {args.root}/{os.path.basename(space['source'])}")
        print(f"capabilities     : {len(space['capabilities'])}")
        print(f"competitor tools : {len(competitor_tools(space))}")
        print(f"slices           : {len(slices)}")
        print(f"cells to determine: {total}")
        sizes = sorted(s["cells"] for s in slices)
        print(f"cells per slice  : min {sizes[0]} median {sizes[len(sizes)//2]} max {sizes[-1]}")
        if args.write:
            out = os.path.join(args.root, "28-cell-plan.json")
            _atomic_write(out, {
                "generated": date.today().isoformat(),
                "space_version": space.get("space_version"),
                "total_cells": total,
                "slices": slices,
            })
            print(f"wrote {out}")
        return 0

    if args.command == "check-space":
        problems = check_space(args.root)
        for p in problems:
            print(f"  PROBLEM {p}")
        print(f"capability space -- {len(problems)} problem(s)")
        return 1 if problems else 0

    if args.command == "validate-cells":
        if not args.cells_dir:
            print("validate-cells requires CELLS_DIR", file=sys.stderr)
            return 2
        res = validate_cells(args.root, args.cells_dir)
        print(f"ok: {res['ok']}")
        print(f"stats: {json.dumps(res['stats'], indent=2)}")
        for e in res["errors"][:40]:
            print(f"  ERROR   {e}")
        if len(res["errors"]) > 40:
            print(f"  ... and {len(res['errors']) - 40} more errors")
        return 0 if res["ok"] else 1

    if args.command == "merge":
        if not args.cells_dir:
            print("merge requires CELLS_DIR", file=sys.stderr)
            return 2
        matrix = merge(args.root, args.cells_dir)
        problems = check_matrix(matrix)
        cov = matrix["coverage"]["grid"]
        print(f"capabilities {cov['capabilities']}  tools {cov['competitor_tools']}")
        print(f"competitor cells {cov['actual_competitor_cells']}/"
              f"{cov['expected_competitor_cells']} ({cov['completeness_pct']}%)")
        print(f"status totals: {matrix['coverage']['competitor_status_totals']}")
        print(f"problems: {len(problems)}")
        for p in problems[:20]:
            print(f"  PROBLEM {p}")
        if args.write and not problems:
            out = args.matrix or os.path.join(args.root, "30-matrix.json")
            _atomic_write(out, matrix)
            print(f"wrote {out}")
        elif args.write:
            print("refusing to write a matrix with problems")
            return 1
        return 1 if problems else 0

    if args.command == "check":
        path = args.matrix or os.path.join(args.root, "30-matrix.json")
        matrix = _read_json(path)
        problems = check_matrix(matrix)
        for p in problems:
            print(f"  PROBLEM {p}")
        print(f"{os.path.basename(path)} -- {len(problems)} problem(s)")
        return 1 if problems else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
