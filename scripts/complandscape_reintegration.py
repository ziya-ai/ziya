#!/usr/bin/env python3
"""CL4 reintegration tooling: carry-forward planning, validation, application.

Phase 4 re-audits Ziya's own source for every apparent gap the matrix
reports, on the premise that a gap is often a terminology artifact rather
than a missing capability.  It is a two-stage body per capability:

  Stage A  audits ZIYA'S CODE            -> verdict FOUND | PARTIAL | ABSENT
  Stage B  disposes given what RIVALS do -> LEDGER_CORRECTION | BUILD_CANDIDATE
                                            | DELIBERATE_NON_GOAL

That split is what makes carry-forward possible, and it is asymmetric.  A
Stage A verdict is a statement about Ziya's source at a point in time: it
does not depend on the competitor grid, so a complete grid does not
invalidate it.  A Stage B disposition weighs the gap against what
competitors actually deliver -- competitor_prevalence, centrality, whether
closing it is worth it -- so a grid that grew from 16% to 100% coverage
DOES invalidate it.  Carrying Stage A forward and re-deriving Stage B
therefore saves the expensive half without preserving a stale judgment.

Three defects in the first run motivate the rest of this module.

1. NO PROVENANCE.  Across 43 distinct keys in 225 reintegration records
   there is not one date, commit or as_of field, so nothing could judge
   whether a prior audit was still true.  Carry-forward here does not
   guess: it re-checks that every evidence path the prior audit cited
   still EXISTS.  A vanished path means the audit described code that is
   gone, which is the staleness case that matters; unchanged paths mean
   the finding is at least still locatable.  New records must carry
   ``as_of`` and ``audited_at_commit`` so the next run can do better.

2. STAGE B IMPROVISED.  27 dispositions were hand-quarantined as
   "corrupted".  They parse fine.  What is wrong is visible in one of
   their own fields: ``stage_a_verdict`` reads "PARTIAL (reconstructed) --
   the Stage A file was ABSENT ... Stage B therefore performed the
   second-look code audit itself".  With its Stage A record missing, Stage
   B did the audit itself and wrote a disposition that is indistinguishable
   from a paired one.  Three independent checks here catch that: the pair
   must exist, ``stage_a_verdict`` must be a bare vocabulary word (not a
   400-character essay), and it must EQUAL the paired Stage A's verdict.

3. FREELY-AUTHORED SCHEMA.  Stage A records carry 32 distinct keys of
   which only two are universal; one file spells "fraction present" four
   different ways (``percent_present``, ``what_is_present_pct``,
   ``stage_a_percent_present``, ``fraction_present_if_partial``).  The
   required field sets below are taken from what 110+ of 112 files already
   agreed on, so conformance is a floor the phase already meets rather
   than a new burden.

``apply-dispositions`` exists because the matrix's Ziya cells carry a
``status`` alongside ``score`` in the v2 schema, and the two must agree.
Asking an agent to hand-maintain that invariant across 100+ cells is how
"unresolved with a score of 3" gets written -- a cell that reads
unresolved to the queue derivation while holding a real score, which
silently re-queues a capability that was just resolved.

Usage:
    complandscape_reintegration.py plan [--prior DIR] [--write]
    complandscape_reintegration.py validate RUN_DIR
    complandscape_reintegration.py apply-dispositions RUN_DIR [--write]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ROOT = ".ziya/complandscape"
SCHEMA_VERSION = "2.0"

#: Stage A verdicts.  A verdict field carrying anything else -- prose, a
#: qualifier like "PARTIAL (reconstructed)", an empty string -- is refused,
#: because the qualifier is exactly where the first run hid the fact that
#: no Stage A audit had happened.
VERDICTS = ("FOUND", "PARTIAL", "ABSENT")

#: Stage B dispositions.
DISPOSITIONS = ("LEDGER_CORRECTION", "BUILD_CANDIDATE", "DELIBERATE_NON_GOAL")

#: Effort classes for a build candidate.  ARCHITECTURAL is deliberately
#: distinct from LARGE: "a month of work" and "this cuts against a
#: load-bearing assumption" are different findings and must not collapse.
EFFORT_CLASSES = ("TRIVIAL", "SMALL", "MEDIUM", "LARGE", "ARCHITECTURAL")

#: Required Stage A fields.  Derived from what 110 of 112 first-run files
#: already carried, so this is a floor rather than a new demand.
STAGE_A_REQUIRED = (
    "capability_id", "verdict", "searched", "evidence", "nearest_subsystem",
)

#: Required Stage B fields.  All eight were universal across all 112
#: first-run dispositions.
DISPOSITION_REQUIRED = (
    "capability_id", "disposition", "stage_a_verdict", "ledger_correction",
    "residual_quality_gap", "documentation_defect", "stretch",
    "recommendation",
)

#: A bare verdict word is at most this long.  The quarantined records put a
#: 400-character narrative in this field; a length bound catches that even
#: when the narrative happens to begin with a valid word.
MAX_VERDICT_LEN = 24


# --------------------------------------------------------------------------
# io helpers
# --------------------------------------------------------------------------

def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def head_commit(repo: str = ".") -> Optional[str]:
    """Short HEAD sha, or None outside a repo.

    Recorded on every new audit so a later run can tell whether the code
    an audit described has moved.  Never raises: provenance is worth
    having but is not worth failing a run over.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo,
            capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return None


# --------------------------------------------------------------------------
# plan: what must be re-audited, and what can be carried forward
# --------------------------------------------------------------------------

def load_gap_queue(root: str) -> List[Dict[str, Any]]:
    """Gap-queue entries, in the queue's own priority order."""
    q = _read_json(os.path.join(root, "31-gap-queue.json"))
    if isinstance(q, list):
        return q
    # "items" added after the standalone Stage 4 rerun (2026-08-30) wrote the
    # v2 queues under that key; the earlier run used "gaps".  Accept both so
    # neither corpus generation strands the planner.
    for key in ("items", "gaps", "queue_entries", "entries"):
        if isinstance(q.get(key), list):
            return q[key]
    raise ValueError("31-gap-queue.json has no recognisable entry list")


#: Separators observed in real first-run ``evidence[].path`` values, which
#: are prose rather than paths: "app/routes/,app/api/",
#: "pdf_exporter.py + html_exporter.py", "token_master.py / base.py:283",
#: "context_management.py-and-memory_tools.py".
_PATH_SEPARATORS = (",", " + ", " / ", " and ", "-and-", ";", " & ")


def _split_citation(raw: str) -> List[str]:
    """Pull path-looking tokens out of a freely-authored citation string.

    The ``path`` field was specified as a path and used as prose, so a
    naive ``os.path.exists`` on the whole value reports every multi-path
    citation as a vanished file.  Measured on the first run: 34 of 112
    records would have been declared stale, of which none had actually
    moved -- they cited two or three real files in one string, or a
    placeholder like "repo-root".
    """
    text = raw
    # Drop parentheticals: "19-ziya-ledger.json (conversation-graph record)".
    depth = 0
    stripped = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            stripped.append(ch)
    text = "".join(stripped)
    parts = [text]
    for sep in _PATH_SEPARATORS:
        parts = [p for chunk in parts for p in chunk.split(sep)]
    out: List[str] = []
    for p in parts:
        tok = p.strip().strip("'\"`")
        # Trim a trailing ":123-456" / ":429,642" line reference.
        if ":" in tok:
            tok = tok.split(":", 1)[0].strip()
        if not tok:
            continue
        # A path candidate has a separator or a file extension.  Without
        # this, prose words ("repo-root") are treated as missing files.
        if "/" in tok or os.path.splitext(tok)[1]:
            out.append(tok)
    return out


def _evidence_paths(rec: Dict[str, Any]) -> List[str]:
    """Path candidates a Stage A record cited as evidence."""
    out: List[str] = []
    for ev in rec.get("evidence") or []:
        raw = ev.get("path") if isinstance(ev, dict) else ev
        if isinstance(raw, str) and raw.strip():
            out.extend(_split_citation(raw))
    return out


def stage_a_carry_state(
    rec: Dict[str, Any], capability_id: str, repo_root: str = ".",
) -> Tuple[bool, str]:
    """Is this prior Stage A record safe to carry forward?

    Returns (carry, reason).  Refuses on anything that makes the record an
    unreliable stand-in for a fresh audit:

      * capability_id mismatch -- the record is about something else.  This
        is not hypothetical: the first run wrote at least one disposition
        under the wrong capability id after a parallel-iteration binding
        leak handed Stage B another iteration's Stage A.
      * a verdict outside the vocabulary -- includes the "(reconstructed)"
        shape that marked an audit which never actually ran.
      * missing required fields -- a record too thin to re-derive from.
      * a cited evidence path that no longer exists -- the audit described
        code that has since moved or been deleted, so its conclusion can
        no longer be checked.  This is the only staleness signal available,
        because the first-run records carry no dates at all.
    """
    if not isinstance(rec, dict):
        return False, "prior record is not an object"
    if rec.get("capability_id") != capability_id:
        return False, (
            f"capability_id mismatch: record says "
            f"{rec.get('capability_id')!r}, expected {capability_id!r}"
        )
    verdict = rec.get("verdict")
    if not isinstance(verdict, str) or verdict.strip() not in VERDICTS:
        return False, f"verdict {verdict!r} is not one of {VERDICTS}"
    missing = [f for f in STAGE_A_REQUIRED if f not in rec]
    if missing:
        return False, f"missing required field(s): {', '.join(missing)}"
    cands = _evidence_paths(rec)
    if not cands:
        # Nothing path-shaped to check.  Absence of evidence is not evidence
        # of staleness, so this carries -- but it is surfaced, because a
        # record whose citations cannot be resolved is a weak record.
        return True, "carried, but no resolvable citation to verify against"
    live = [p for p in cands if os.path.exists(os.path.join(repo_root, p))]
    if not live:
        # EVERY candidate is gone.  One missing file out of five is a
        # refactor the finding survives; none of five resolving means the
        # audit described code that is no longer there.
        return False, (
            f"no cited evidence path still exists, audit is stale: "
            f"{', '.join(cands[:3])}"
        )
    return True, "prior Stage A verdict is intact and its evidence still exists"


def plan_reintegration(
    root: str = DEFAULT_ROOT, prior_dir: Optional[str] = None,
    repo_root: str = ".",
) -> Dict[str, Any]:
    """Classify every gap-queue entry as carry-forward or fresh.

    ``prior_dir`` defaults to the un-versioned first-run directory, which
    is where the only existing records live.  A carried entry still runs
    Stage B: the disposition depends on the competitor grid, and the grid
    is what changed.
    """
    if prior_dir is None:
        prior_dir = os.path.join(root, "40-reintegration")
    entries = load_gap_queue(root)
    items: List[Dict[str, Any]] = []
    for e in entries:
        # v2 queues (Stage 4 rerun) name entries "capability_id"; v1 used "id".
        cid = (
            (e.get("capability_id") or e.get("id"))
            if isinstance(e, dict) else e
        )
        if not cid:
            continue
        rec_path = os.path.join(prior_dir, f"{cid}-stageA.json")
        carry, reason = False, "no prior Stage A record"
        if os.path.exists(rec_path):
            try:
                carry, reason = stage_a_carry_state(
                    _read_json(rec_path), cid, repo_root)
            except Exception as exc:  # noqa: BLE001
                carry, reason = False, f"prior record unreadable: {exc}"
        items.append({
            "capability_id": cid,
            "stage_a": "carry_forward" if carry else "fresh",
            "stage_a_reason": reason,
            "prior_stage_a_path": rec_path if os.path.exists(rec_path) else None,
            # Always re-derived: a disposition weighs the gap against the
            # competitor grid, and the grid changed.
            "stage_b": "fresh",
        })
    n_carry = sum(1 for i in items if i["stage_a"] == "carry_forward")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated": date.today().isoformat(),
        "audited_at_commit": head_commit(repo_root),
        "prior_dir": prior_dir,
        "totals": {
            "queue_entries": len(items),
            "stage_a_carry_forward": n_carry,
            "stage_a_fresh": len(items) - n_carry,
            "stage_b_fresh": len(items),
        },
        "items": items,
    }


# --------------------------------------------------------------------------
# validate: the gate the first run did not have
# --------------------------------------------------------------------------

def _bad_verdict_word(value: Any, allowed: Tuple[str, ...]) -> Optional[str]:
    """Why ``value`` is not a bare vocabulary word, or None if it is fine."""
    if not isinstance(value, str):
        return f"not a string ({type(value).__name__})"
    raw = value.strip()
    if not raw:
        return "empty"
    if len(raw) > MAX_VERDICT_LEN:
        return (
            f"{len(raw)} chars of prose where a bare word is required "
            f"(begins {raw[:40]!r}) -- a qualified verdict is how the first "
            f"run recorded an audit that never ran"
        )
    if raw not in allowed:
        return f"{raw!r} is not one of {allowed}"
    return None


def validate_run(run_dir: str, root: str = DEFAULT_ROOT) -> Dict[str, Any]:
    """Validate one reintegration run directory.

    Errors block the apply step; warnings do not.  The pair checks are the
    load-bearing ones -- an orphan Stage B is the shape the first run
    quarantined by hand, and it is only visible by looking across the two
    files at once.
    """
    errors: List[str] = []
    warnings: List[str] = []
    stage_a: Dict[str, Dict[str, Any]] = {}
    dispositions: Dict[str, Dict[str, Any]] = {}

    if not os.path.isdir(run_dir):
        return {"ok": False, "errors": [f"run dir not found: {run_dir}"],
                "warnings": [], "stats": {}}

    for name in sorted(os.listdir(run_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(run_dir, name)
        try:
            rec = _read_json(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: unreadable JSON: {exc}")
            continue
        if not isinstance(rec, dict):
            errors.append(f"{name}: top level is not an object")
            continue
        if name.endswith("-stageA.json"):
            cid = name[: -len("-stageA.json")]
            stage_a[cid] = rec
            _check_stage_a(name, cid, rec, errors, warnings)
        elif name.endswith("-disposition.json"):
            cid = name[: -len("-disposition.json")]
            dispositions[cid] = rec
            _check_disposition(name, cid, rec, errors, warnings)

    # Pair integrity.  An orphan disposition is the quarantined shape: Stage
    # B ran without its Stage A and audited the capability itself.
    for cid in sorted(set(dispositions) - set(stage_a)):
        errors.append(
            f"{cid}: disposition with NO Stage A record -- Stage B cannot "
            f"dispose of an audit that did not run; this is the shape the "
            f"first run had to quarantine by hand"
        )
    for cid in sorted(set(stage_a) - set(dispositions)):
        errors.append(f"{cid}: Stage A record with no disposition (pair incomplete)")

    # Cross-file agreement.  Catches a disposition built on some other
    # capability's audit even when both files are individually well-formed.
    for cid in sorted(set(stage_a) & set(dispositions)):
        a = (stage_a[cid].get("verdict") or "").strip()
        b = (dispositions[cid].get("stage_a_verdict") or "").strip()
        if a and b and a in VERDICTS and b in VERDICTS and a != b:
            errors.append(
                f"{cid}: disposition records stage_a_verdict={b!r} but the "
                f"Stage A record says {a!r} -- the disposition was built on "
                f"a different audit"
            )
        disp = (dispositions[cid].get("disposition") or "").strip()
        if a == "FOUND" and disp == "BUILD_CANDIDATE":
            errors.append(
                f"{cid}: Stage A found the capability present, but the "
                f"disposition is BUILD_CANDIDATE -- there is nothing to build"
            )
        if a == "ABSENT" and disp == "LEDGER_CORRECTION":
            errors.append(
                f"{cid}: Stage A found nothing, but the disposition is "
                f"LEDGER_CORRECTION -- there is no score to correct"
            )

    # Coverage against the queue this run was supposed to cover.
    try:
        expected = {
            e.get("capability_id") or e.get("id")
            for e in load_gap_queue(root) if isinstance(e, dict)
        }
        expected.discard(None)
    except Exception:  # noqa: BLE001
        expected = set()
    if expected:
        missing = sorted(expected - set(stage_a))
        if missing:
            errors.append(
                f"COVERAGE: {len(missing)} queue entries have no Stage A "
                f"record in this run (e.g. {', '.join(missing[:5])})"
            )
        extra = sorted(set(stage_a) - expected)
        if extra:
            warnings.append(
                f"{len(extra)} Stage A records for capabilities not in the "
                f"queue (e.g. {', '.join(extra[:5])})"
            )

    stats = {
        "stage_a_files": len(stage_a),
        "disposition_files": len(dispositions),
        "pairs": len(set(stage_a) & set(dispositions)),
        "verdicts": dict(Counter(
            (r.get("verdict") or "?").strip() for r in stage_a.values())),
        "dispositions": dict(Counter(
            (r.get("disposition") or "?").strip() for r in dispositions.values())),
        "carried_forward": sum(
            1 for r in stage_a.values() if r.get("carried_forward") is True),
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "stats": stats}


def _check_stage_a(name: str, cid: str, rec: Dict[str, Any],
                   errors: List[str], warnings: List[str]) -> None:
    if rec.get("capability_id") != cid:
        errors.append(
            f"{name}: capability_id {rec.get('capability_id')!r} does not "
            f"match the filename ({cid!r})"
        )
    bad = _bad_verdict_word(rec.get("verdict"), VERDICTS)
    if bad:
        errors.append(f"{name}: verdict {bad}")
    for f in STAGE_A_REQUIRED:
        if f not in rec:
            errors.append(f"{name}: missing required field {f!r}")
    if not rec.get("as_of"):
        warnings.append(
            f"{name}: no as_of date -- a later run cannot judge staleness")
    verdict = (rec.get("verdict") or "").strip()
    if verdict == "FOUND":
        if not rec.get("ziya_internal_name"):
            errors.append(
                f"{name}: FOUND without ziya_internal_name -- the internal "
                f"name IS the terminology finding"
            )
        if rec.get("maturity_if_found") in (None, ""):
            errors.append(f"{name}: FOUND without maturity_if_found")
    if verdict == "PARTIAL" and not rec.get("missing_behaviors"):
        errors.append(
            f"{name}: PARTIAL without missing_behaviors -- 'partially "
            f"implemented' with no decomposition is a non-answer"
        )
    if verdict == "ABSENT" and not rec.get("searched"):
        errors.append(
            f"{name}: ABSENT without a searched list -- a negative result "
            f"is only a finding if it is auditable"
        )


def _check_disposition(name: str, cid: str, rec: Dict[str, Any],
                       errors: List[str], warnings: List[str]) -> None:
    if rec.get("capability_id") != cid:
        errors.append(
            f"{name}: capability_id {rec.get('capability_id')!r} does not "
            f"match the filename ({cid!r})"
        )
    for f in DISPOSITION_REQUIRED:
        if f not in rec:
            errors.append(f"{name}: missing required field {f!r}")
    bad = _bad_verdict_word(rec.get("disposition"), DISPOSITIONS)
    if bad:
        errors.append(f"{name}: disposition {bad}")
    bad = _bad_verdict_word(rec.get("stage_a_verdict"), VERDICTS)
    if bad:
        errors.append(f"{name}: stage_a_verdict {bad}")
    disp = (rec.get("disposition") or "").strip()
    if disp == "LEDGER_CORRECTION":
        lc = rec.get("ledger_correction")
        if not isinstance(lc, dict) or not lc:
            errors.append(
                f"{name}: LEDGER_CORRECTION with no ledger_correction object "
                f"-- the correction is the whole output"
            )
        elif lc.get("corrected_score") in (None, ""):
            errors.append(
                f"{name}: ledger_correction has no corrected_score")
    if disp == "BUILD_CANDIDATE":
        st = rec.get("stretch")
        if not isinstance(st, dict) or not st:
            errors.append(
                f"{name}: BUILD_CANDIDATE with no stretch estimate")
        else:
            ec = (st.get("effort_class") or "").strip()
            if ec not in EFFORT_CLASSES:
                errors.append(
                    f"{name}: effort_class {ec!r} is not one of "
                    f"{EFFORT_CLASSES}"
                )


# --------------------------------------------------------------------------
# apply-dispositions: write Ziya cells mechanically, status and score together
# --------------------------------------------------------------------------

def apply_dispositions(
    run_dir: str, root: str = DEFAULT_ROOT,
) -> Dict[str, Any]:
    """Fold LEDGER_CORRECTION / ABSENT outcomes into the matrix's Ziya cells.

    Mechanical on purpose.  A v2 Ziya cell carries ``status`` and ``score``
    and the two must agree; hand-editing 100+ cells across a JSON document
    is how "unresolved with a score of 3" gets written, which reads as
    unresolved to the queue derivation while holding a real score -- so the
    capability is re-queued for an audit that already answered it.

    Returns the updated matrix plus a report.  Writes nothing.
    """
    matrix_path = os.path.join(root, "30-matrix.json")
    matrix = _read_json(matrix_path)
    cells = matrix.get("cells") or []
    by_cap = {c["capability_id"]: c for c in cells if c.get("tool") == "ziya"}
    today = date.today().isoformat()
    commit = head_commit()

    applied: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for name in sorted(os.listdir(run_dir)):
        if not name.endswith("-disposition.json"):
            continue
        rec = _read_json(os.path.join(run_dir, name))
        cid = rec.get("capability_id")
        cell = by_cap.get(cid)
        if cell is None:
            skipped.append(f"{cid}: no Ziya cell in the matrix")
            continue
        disp = (rec.get("disposition") or "").strip()
        verdict = (rec.get("stage_a_verdict") or "").strip()
        before = {"status": cell.get("status"), "score": cell.get("score")}

        if disp == "LEDGER_CORRECTION":
            lc = rec.get("ledger_correction") or {}
            score = lc.get("corrected_score")
            if not isinstance(score, int) or isinstance(score, bool):
                skipped.append(f"{cid}: corrected_score {score!r} is not an int")
                continue
            # status and score written together, so they cannot disagree.
            cell["status"] = "present" if score >= 1 else "absent"
            cell["score"] = score
            cell["evidence_tier"] = lc.get("evidence_tier") or cell.get("evidence_tier") or "A"
            cell["citation"] = lc.get("citation") or cell.get("citation")
            cell["note"] = f"CL4 ({verdict}): {lc.get('note') or 'ledger correction'}"
            cell["corrected"] = True
        elif verdict == "ABSENT":
            # A settled absence is a FINDING and must carry a tier, or the
            # matrix merge cannot tell it from an unfilled placeholder.
            cell["status"] = "absent"
            cell["score"] = 0
            cell["evidence_tier"] = cell.get("evidence_tier") or "A"
            cell["note"] = (
                f"CL4 (ABSENT): confirmed absent by mechanism search "
                f"({rec.get('recommendation', '')[:120]})"
            )
            cell["corrected"] = False
        else:
            # PARTIAL/BUILD_CANDIDATE leaves the existing score alone: the
            # capability is genuinely incomplete and its score already says so.
            skipped.append(f"{cid}: {verdict}/{disp} leaves the cell unchanged")
            continue
        cell["as_of"] = today
        if commit:
            cell["audited_at_commit"] = commit
        applied.append({"capability_id": cid, "before": before,
                        "after": {"status": cell["status"], "score": cell["score"]},
                        "disposition": disp, "stage_a_verdict": verdict})

    matrix["cells"] = cells
    return {"matrix": matrix, "matrix_path": matrix_path,
            "applied": applied, "skipped": skipped}


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command",
                    choices=["plan", "validate", "apply-dispositions"])
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--prior", default=None,
                    help="prior run dir for carry-forward (default: 40-reintegration)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "plan":
        plan = plan_reintegration(args.root, args.prior)
        t = plan["totals"]
        print(f"queue entries          : {t['queue_entries']}")
        print(f"Stage A carry-forward  : {t['stage_a_carry_forward']}")
        print(f"Stage A fresh audit    : {t['stage_a_fresh']}")
        print(f"Stage B (always fresh) : {t['stage_b_fresh']}")
        print(f"audited_at_commit      : {plan['audited_at_commit']}")
        reasons = Counter(i["stage_a_reason"].split(":")[0]
                          for i in plan["items"] if i["stage_a"] == "fresh")
        for r, n in reasons.most_common():
            print(f"  fresh because: {r} ({n})")
        if args.write:
            out = os.path.join(args.root, "42-reintegration-plan.json")
            _atomic_write(out, plan)
            print(f"wrote {out}")
        return 0

    if args.command == "validate":
        if not args.run_dir:
            print("validate needs a RUN_DIR", file=sys.stderr)
            return 2
        res = validate_run(args.run_dir, args.root)
        print(f"ok: {res['ok']}")
        print(f"stats: {json.dumps(res['stats'])}")
        for e in res["errors"]:
            print(f"  ERROR   {e}")
        for w in res["warnings"]:
            print(f"  WARNING {w}")
        return 0 if res["ok"] else 1

    if args.command == "apply-dispositions":
        if not args.run_dir:
            print("apply-dispositions needs a RUN_DIR", file=sys.stderr)
            return 2
        res = validate_run(args.run_dir, args.root)
        if not res["ok"]:
            print("refusing to apply: run does not validate", file=sys.stderr)
            for e in res["errors"][:10]:
                print(f"  ERROR   {e}", file=sys.stderr)
            return 1
        out = apply_dispositions(args.run_dir, args.root)
        print(f"applied {len(out['applied'])} Ziya cell updates")
        for a in out["applied"][:8]:
            print(f"  {a['capability_id']}: {a['before']} -> {a['after']}"
                  f"  ({a['disposition']})")
        print(f"skipped {len(out['skipped'])}")
        if args.write:
            _atomic_write(out["matrix_path"], out["matrix"])
            print(f"wrote {out['matrix_path']}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
