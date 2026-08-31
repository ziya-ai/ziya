#!/usr/bin/env python3
"""Resolve the competitive-landscape corpus and compute its authoritative facts.

WHY THIS EXISTS
---------------
Each phase of the study now writes into a run-versioned directory
(``<base>/<run_id>/``) with the current run id in ``<base>/CURRENT_RUN``.
CL6 -- the synthesis phase -- was written before versioning existed and reads
the *unversioned* directories, which are the FIRST run's output.  Left alone
it would read run-1 data, produce a well-formed report full of superseded
numbers, and nothing anywhere would error.  That is the same failure shape as
the CL3 transcription defect: correct output, wrong input, no signal.

So resolution is mechanical rather than a path an agent types.  ``resolve``
answers "which directory is phase X's current output" and says loudly when it
had to fall back to a legacy unversioned directory.

The second job is ground truth.  CL6's verifier was asked to "recompute the
counts and flag every discrepancy" by hand across ~450 files, which is both
expensive and unfalsifiable -- a verifier that miscounts agrees with a report
that miscounts.  ``facts`` computes those counts once, from the resolved
corpus, so the verifier compares two numbers instead of deriving one.

The third job is provenance.  A report is a claim about a specific corpus
state, and prose cannot be diffed.  ``check-report`` does NOT attempt to parse
arbitrary prose -- it requires the report to carry a machine-readable
provenance block declaring which run of each phase it synthesized and the
headline counts it used, then verifies that declaration against ``facts``.  A
report that declares nothing fails; a report whose declaration disagrees with
the data fails with the specific field named.

Commands
--------
  resolve       print the resolved corpus (run dirs, legacy fallbacks, problems)
  facts         print the authoritative fact block as JSON
  check-report  verify a report's provenance block against the facts

Exit status is non-zero when a blocking problem is found, so a task can gate
on it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ROOT = ".ziya/complandscape"

#: Phase output directories.  ``base`` holds ``CURRENT_RUN`` plus one
#: subdirectory per run; ``legacy_glob`` matches the pre-versioning layout
#: where records sat loose in the base directory.
PHASE_DIRS: Dict[str, Dict[str, str]] = {
    "cells": {"base": "30-cells", "legacy_glob": "*.json",
              "note": "CL3 per-slice cell determinations"},
    "reintegration": {"base": "50-reintegration", "legacy_glob": "*.json",
                      "legacy_base": "40-reintegration",
                      "note": "CL4 Stage A + disposition records"},
    "depth": {"base": "50-depth", "legacy_glob": "*.json",
              "note": "CL5 head-to-head depth records"},
}

#: Single-file artifacts every phase after CL3 depends on.
CORE_FILES = (
    "00-method.md",
    "19-ziya-ledger.json",
    "30-matrix.json",
    "31-gap-queue.json",
    "32-contested-queue.json",
    "33-unique-queue.json",
    "25-dimension-registry.json",
)

MATRIX_SCHEMA = "2.0"

#: The provenance block a report must carry, and which fact each field is
#: checked against.  Keeping this table explicit is what makes the check
#: falsifiable: a field nobody verifies is documentation, not a check.
PROVENANCE_FIELDS: Dict[str, str] = {
    "matrix_schema_version": "matrix.schema_version",
    "grid_never_assessed": "matrix.grid.never_assessed",
    "registry_version": "registry.registry_version",
    "gap_queue_entries": "queues.gap",
    "contested_queue_entries": "queues.contested",
    "unique_queue_entries": "queues.unique",
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _try_json(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """Read JSON, returning (data, error) rather than raising.

    A corpus check must be able to report "this file is unparseable" as a
    finding; raising would abort the whole audit on one bad file.
    """
    try:
        return _read_json(path), None
    except FileNotFoundError:
        return None, "missing"
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"unparseable: {exc}"


def _dig(obj: Any, dotted: str) -> Any:
    """Fetch a dotted path out of nested dicts, or None."""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _queue_entries(data: Any) -> List[Any]:
    """Pull the entry list out of a queue file.

    The three queue files use three different key names, and a queue may also
    be a bare list.  Guessing by shape rather than by name keeps this working
    if a later phase renames one.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("gaps", "contested", "unique", "entries", "items"):
        val = data.get(key)
        if isinstance(val, list):
            return val
    # Fall back to the longest list value present.
    lists = [v for v in data.values() if isinstance(v, list)]
    return max(lists, key=len) if lists else []


def _entry_id(entry: Any) -> Optional[str]:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id") or entry.get("capability_id")
    return None


# --------------------------------------------------------------------------
# resolve
# --------------------------------------------------------------------------

def resolve_phase(root: str, phase: str) -> Dict[str, Any]:
    """Locate one phase's current output directory.

    Resolution order, and the reason for it:
      1. ``<base>/CURRENT_RUN`` naming an existing subdirectory -- the
         versioned, intended case.
      2. Loose ``*.json`` in ``<base>`` (or ``legacy_base``) -- the
         pre-versioning layout.  Returned, but flagged ``legacy`` so a caller
         can refuse it.  Silently accepting this is exactly how a synthesis
         phase ends up describing a superseded run.
      3. Nothing -- the phase has not produced output.
    """
    spec = PHASE_DIRS[phase]
    base = os.path.join(root, spec["base"])
    out: Dict[str, Any] = {
        "phase": phase, "base": base, "dir": None,
        "run_id": None, "legacy": False, "files": 0, "problems": [],
    }

    pointer = os.path.join(base, "CURRENT_RUN")
    if os.path.exists(pointer):
        try:
            with open(pointer, "r", encoding="utf-8") as fh:
                run_id = fh.read().strip()
        except OSError as exc:
            out["problems"].append(f"CURRENT_RUN unreadable: {exc}")
            run_id = ""
        if run_id:
            cand = os.path.join(base, run_id)
            if os.path.isdir(cand):
                out.update({"dir": cand, "run_id": run_id})
                out["files"] = len([f for f in os.listdir(cand)
                                   if f.endswith(".json")])
                if out["files"] == 0:
                    out["problems"].append(
                        f"run directory {run_id} exists but holds no JSON "
                        f"records -- the fan-out wrote nowhere"
                    )
                return out
            out["problems"].append(
                f"CURRENT_RUN names {run_id!r} but {cand} is not a directory"
            )
        else:
            out["problems"].append("CURRENT_RUN is empty")

    # Legacy fallback.  Only bases the spec actually declares are considered:
    # joining an absent legacy_base onto the root yields the corpus root
    # itself, which holds the queue and matrix files, so a phase with no
    # output would "resolve" to the root and report 8 files it does not own.
    candidates = [base]
    if spec.get("legacy_base"):
        candidates.append(os.path.join(root, spec["legacy_base"]))
    for legacy_base in candidates:
        if not os.path.isdir(legacy_base):
            continue
        loose = [f for f in os.listdir(legacy_base) if f.endswith(".json")]
        if loose:
            out.update({"dir": legacy_base, "legacy": True,
                        "files": len(loose)})
            out["problems"].append(
                f"NO CURRENT_RUN: falling back to the unversioned directory "
                f"{legacy_base}, which is an EARLIER run's output. Any "
                f"synthesis built on it describes superseded data."
            )
            return out

    out["problems"].append(
        f"no output found: neither {base}/CURRENT_RUN nor loose records"
    )
    return out


def resolve_corpus(root: str) -> Dict[str, Any]:
    """Resolve every phase directory plus the core single-file artifacts."""
    phases = {p: resolve_phase(root, p) for p in PHASE_DIRS}
    core: Dict[str, Any] = {}
    for name in CORE_FILES:
        path = os.path.join(root, name)
        entry: Dict[str, Any] = {"path": path, "exists": os.path.exists(path)}
        if entry["exists"] and name.endswith(".json"):
            _, err = _try_json(path)
            if err:
                entry["problem"] = err
        core[name] = entry

    problems: List[str] = []
    for name, entry in core.items():
        if not entry["exists"]:
            problems.append(f"core artifact missing: {name}")
        elif entry.get("problem"):
            problems.append(f"core artifact {name}: {entry['problem']}")
    for phase, info in phases.items():
        for p in info["problems"]:
            problems.append(f"[{phase}] {p}")

    return {"root": root, "phases": phases, "core": core, "problems": problems}


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------

def _matrix_facts(root: str) -> Dict[str, Any]:
    data, err = _try_json(os.path.join(root, "30-matrix.json"))
    if err or not isinstance(data, dict):
        return {"error": err or "not an object"}
    cells = data.get("cells") or []
    comp = [c for c in cells if c.get("tool") != "ziya"]
    ziya = [c for c in cells if c.get("tool") == "ziya"]
    caps = {c["id"] for c in data.get("capabilities") or [] if "id" in c}
    tools = [t for t in data.get("tools") or [] if t != "ziya"]
    have = {(c.get("capability_id"), c.get("tool")) for c in comp}
    full = len(caps) * len(tools)
    return {
        "schema_version": data.get("schema_version"),
        "capabilities": len(caps),
        "competitor_tools": len(tools),
        "competitor_cells": len(comp),
        "ziya_cells": len(ziya),
        "competitor_status_totals": dict(Counter(
            c.get("status") for c in comp)),
        "ziya_status_totals": dict(Counter(c.get("status") for c in ziya)),
        "grid": {
            "full": full,
            "present": len(have),
            "never_assessed": max(0, full - len(have)),
        },
        "contender_cells": sum(
            1 for c in comp
            if c.get("status") == "present" and (c.get("score") or 0) >= 2),
    }


def _queue_facts(root: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, name in (("gap", "31-gap-queue.json"),
                      ("contested", "32-contested-queue.json"),
                      ("unique", "33-unique-queue.json")):
        data, err = _try_json(os.path.join(root, name))
        if err:
            out[key] = None
            out[f"{key}_error"] = err
            continue
        out[key] = len(_queue_entries(data))
    return out


def _registry_facts(root: str) -> Dict[str, Any]:
    data, err = _try_json(os.path.join(root, "25-dimension-registry.json"))
    if err or not isinstance(data, dict):
        return {"error": err or "not an object"}
    caps = data.get("capabilities") or {}
    return {
        "registry_version": data.get("registry_version"),
        "frozen_at": data.get("frozen_at"),
        "capabilities": len(caps),
        "dimensions": sum(len(v.get("dimensions") or []) for v in caps.values()),
        "contenders": sum(len(v.get("contenders") or []) for v in caps.values()),
    }


def _reintegration_facts(info: Dict[str, Any]) -> Dict[str, Any]:
    d = info.get("dir")
    if not d:
        return {"error": "unresolved"}
    stage_a, disp = {}, {}
    unparseable: List[str] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        rec, err = _try_json(os.path.join(d, fn))
        if err:
            unparseable.append(fn)
            continue
        if fn.endswith("-stageA.json"):
            stage_a[fn[:-len("-stageA.json")]] = rec
        elif fn.endswith("-disposition.json"):
            disp[fn[:-len("-disposition.json")]] = rec
    return {
        "run_id": info.get("run_id"),
        "legacy": info.get("legacy", False),
        "stage_a_files": len(stage_a),
        "disposition_files": len(disp),
        "paired": len(set(stage_a) & set(disp)),
        "orphan_dispositions": sorted(set(disp) - set(stage_a)),
        "verdicts": dict(Counter(
            (r or {}).get("verdict") for r in stage_a.values())),
        "dispositions": dict(Counter(
            (r or {}).get("disposition") for r in disp.values())),
        "carried_forward": sum(
            1 for r in stage_a.values() if (r or {}).get("carried_forward")),
        "unparseable": unparseable,
    }


def _depth_facts(info: Dict[str, Any]) -> Dict[str, Any]:
    d = info.get("dir")
    if not d:
        return {"error": "unresolved"}
    files, verdicts, confid = 0, Counter(), Counter()
    cells = 0
    statuses: Counter = Counter()
    reg_versions: Counter = Counter()
    unparseable: List[str] = []
    caps: List[str] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        rec, err = _try_json(os.path.join(d, fn))
        if err:
            unparseable.append(fn)
            continue
        files += 1
        caps.append((rec or {}).get("capability_id") or fn[:-5])
        verdicts[(rec or {}).get("verdict")] += 1
        confid[(rec or {}).get("confidence")] += 1
        reg_versions[(rec or {}).get("registry_version")] += 1
        for dim in (rec or {}).get("dimensions") or []:
            for c in dim.get("competitors") or []:
                cells += 1
                statuses[c.get("status")] += 1
    return {
        "run_id": info.get("run_id"),
        "legacy": info.get("legacy", False),
        "files": files,
        "capabilities": sorted(caps),
        "competitor_cells": cells,
        "cell_statuses": dict(statuses),
        "verdicts": dict(verdicts),
        "confidences": dict(confid),
        "registry_versions": dict(reg_versions),
        "unparseable": unparseable,
    }


def corpus_facts(root: str) -> Dict[str, Any]:
    """Compute the authoritative fact block for the resolved corpus."""
    corpus = resolve_corpus(root)
    facts: Dict[str, Any] = {
        "root": root,
        "resolved": {p: {"run_id": i["run_id"], "dir": i["dir"],
                         "legacy": i["legacy"], "files": i["files"]}
                     for p, i in corpus["phases"].items()},
        "matrix": _matrix_facts(root),
        "queues": _queue_facts(root),
        "registry": _registry_facts(root),
        "reintegration": _reintegration_facts(corpus["phases"]["reintegration"]),
        "depth": _depth_facts(corpus["phases"]["depth"]),
    }
    facts["problems"] = corpus["problems"] + _consistency_problems(root, facts)
    facts["blocking"] = [p for p in facts["problems"]
                         if p.startswith(("BLOCKING", "[cells]", "[depth]",
                                          "[reintegration]"))]
    return facts


def _consistency_problems(root: str, facts: Dict[str, Any]) -> List[str]:
    """Cross-artifact problems that no single file can reveal.

    These are the ones that silently produce a coherent-looking report about
    a corpus that does not hang together -- a depth run scored against a
    registry that has since been rebuilt, contested entries with no depth
    record, unique claims resting on unresolved cells.
    """
    problems: List[str] = []
    matrix = facts["matrix"]
    reg = facts["registry"]
    depth = facts["depth"]

    if matrix.get("schema_version") != MATRIX_SCHEMA:
        problems.append(
            f"BLOCKING: matrix schema_version {matrix.get('schema_version')!r} "
            f"!= {MATRIX_SCHEMA}; the grid predates full-grid determination, so "
            f"an absent competitor cell means 'never assessed', not 'lacks it'"
        )
    never = _dig(matrix, "grid.never_assessed")
    if never:
        problems.append(
            f"BLOCKING: {never} competitor pairs have no cell; every "
            f"'only Ziya has this' and 'no competitor ships this' claim "
            f"inherits that gap silently"
        )

    # A depth corpus is only comparable to the registry it was scored against.
    reg_v = reg.get("registry_version")
    seen = {v for v in (depth.get("registry_versions") or {}) if v}
    if seen and reg_v and seen != {reg_v}:
        problems.append(
            f"BLOCKING: depth records were scored against registry "
            f"version(s) {sorted(seen)} but the registry is now {reg_v!r}; "
            f"their dimension ids may no longer mean the same thing"
        )
    if depth.get("files") and not seen:
        problems.append(
            "depth records carry no registry_version, so they cannot be "
            "aligned to a comparison schema -- they are a snapshot, not a "
            "baseline"
        )

    # Contested coverage: every contested entry should have a depth record.
    cq, err = _try_json(os.path.join(root, "32-contested-queue.json"))
    if not err:
        want = {_entry_id(e) for e in _queue_entries(cq)} - {None}
        have = set(depth.get("capabilities") or [])
        missing = sorted(want - have)
        if missing:
            problems.append(
                f"{len(missing)} contested capabilities have no depth record "
                f"(e.g. {missing[:3]}); their head-to-head is unquantified"
            )

    # Reintegration pairing.
    rei = facts["reintegration"]
    if rei.get("orphan_dispositions"):
        problems.append(
            f"BLOCKING: {len(rei['orphan_dispositions'])} dispositions have no "
            f"Stage A record (e.g. {rei['orphan_dispositions'][:3]}); a "
            f"disposition without its audit is unsourced"
        )
    for key in ("reintegration", "depth"):
        bad = facts[key].get("unparseable") or []
        if bad:
            problems.append(
                f"BLOCKING: {len(bad)} unparseable {key} record(s): {bad[:3]}")

    # Legacy fallbacks are blocking for synthesis specifically.
    for phase, info in facts["resolved"].items():
        if info.get("legacy"):
            problems.append(
                f"BLOCKING: [{phase}] resolved to an unversioned legacy "
                f"directory; synthesis would describe an earlier run"
            )
    return problems


# --------------------------------------------------------------------------
# check-report
# --------------------------------------------------------------------------

PROVENANCE_RE = re.compile(
    r"<!--\s*corpus-provenance\s*(\{.*?\})\s*-->", re.S)


def extract_provenance(report_text: str) -> Tuple[Optional[dict], Optional[str]]:
    """Pull the machine-readable provenance block out of a report.

    Deliberately NOT a prose parser.  A report is a claim about a specific
    corpus state; asking a regex to infer that from narrative text would
    produce confident wrong answers.  Instead the report declares its inputs
    in one HTML comment -- invisible when rendered, and unambiguous here.
    """
    m = PROVENANCE_RE.search(report_text or "")
    if not m:
        return None, (
            "no corpus-provenance block found; the report does not state "
            "which corpus it describes, so its numbers cannot be attributed "
            "to a run"
        )
    try:
        return json.loads(m.group(1)), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"corpus-provenance block is not valid JSON: {exc}"


def check_report(root: str, report_path: str) -> Dict[str, Any]:
    """Verify a report's declared provenance against the corpus facts."""
    result: Dict[str, Any] = {"ok": False, "errors": [], "warnings": [],
                              "checked": {}}
    try:
        with open(report_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        result["errors"].append(f"cannot read report: {exc}")
        return result

    prov, err = extract_provenance(text)
    if err:
        result["errors"].append(err)
        return result

    facts = corpus_facts(root)

    # Declared run ids must match what actually resolved.
    for phase in PHASE_DIRS:
        declared = (prov.get("runs") or {}).get(phase)
        actual = facts["resolved"][phase]["run_id"]
        result["checked"][f"runs.{phase}"] = {"declared": declared,
                                              "actual": actual}
        if declared != actual:
            result["errors"].append(
                f"runs.{phase}: report declares {declared!r} but the corpus "
                f"resolves to {actual!r} -- the report describes a different "
                f"run than the one on disk"
            )

    # Declared headline counts must match the data.
    for field, dotted in PROVENANCE_FIELDS.items():
        declared = prov.get(field)
        actual = _dig(facts, dotted)
        result["checked"][field] = {"declared": declared, "actual": actual}
        if declared is None:
            result["errors"].append(
                f"{field}: not declared; it is one of the numbers every "
                f"conclusion rests on")
        elif declared != actual:
            result["errors"].append(
                f"{field}: report says {declared!r}, data says {actual!r}")

    for p in facts["blocking"]:
        result["errors"].append(f"corpus: {p}")
    for p in facts["problems"]:
        if p not in facts["blocking"]:
            result["warnings"].append(f"corpus: {p}")

    result["ok"] = not result["errors"]
    return result


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def _print_resolve(corpus: Dict[str, Any]) -> None:
    for phase, info in corpus["phases"].items():
        tag = "LEGACY" if info["legacy"] else (info["run_id"] or "-")
        print(f"  {phase:16s} {tag:20s} files={info['files']:4d}  "
              f"{info['dir'] or '(unresolved)'}")
    missing = [n for n, e in corpus["core"].items() if not e["exists"]]
    print(f"  core artifacts   {len(corpus['core']) - len(missing)}"
          f"/{len(corpus['core'])} present"
          + (f"  MISSING: {missing}" if missing else ""))
    if corpus["problems"]:
        print(f"\n  {len(corpus['problems'])} problem(s):")
        for p in corpus["problems"]:
            print(f"    {p}")
    else:
        print("\n  no problems")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command",
                    choices=("resolve", "facts", "check-report"))
    ap.add_argument("report", nargs="?", help="report path for check-report")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args(argv)

    if args.command == "resolve":
        corpus = resolve_corpus(args.root)
        _print_resolve(corpus)
        blocking = [p for p in corpus["problems"]
                    if "unversioned" in p or "no output found" in p]
        return 1 if blocking else 0

    if args.command == "facts":
        facts = corpus_facts(args.root)
        print(json.dumps(facts, indent=2, sort_keys=True))
        return 1 if facts["blocking"] else 0

    report = args.report or os.path.join(args.root, "REPORT.md")
    res = check_report(args.root, report)
    print(f"ok: {res['ok']}")
    for e in res["errors"]:
        print(f"  ERROR   {e}")
    for w in res["warnings"]:
        print(f"  WARN    {w}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
