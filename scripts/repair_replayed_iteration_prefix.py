#!/usr/bin/env python3
"""Reinstall the replayed-iteration prefix a resumed run should already hold.

Why this exists
---------------
``seed_replayed_iterations`` installs, at launch, one ``IterationSummary``
per iteration a resumed run inherited rather than executed.  It bails when
the resume point has no ``block_states`` entry yet — and a resume point
inside a CALLED card never does, because launch-time seeding walks only the
caller's tree while callee blocks are seeded later, when the Call executes.

The artifacts are copied regardless (that loop runs after, and is not
state-dependent), so the files are on disk and openable.  Only the
*summaries* are missing.  That is not cosmetic: the next resume selects
which iterations to bank by reading these summaries
(``parallel_replay_indices``), so their absence makes it re-run work whose
results it is holding.

Observed: run 9099930d inherited 50 banked iterations and recorded 0 of
them, leaving a resume that would re-run 52 of 60 iterations to redo 2.

What it does
------------
Reconstructs the missing summaries from two sources that are both still
present and authoritative:

  * ``run.resume_iteration_artifacts`` — the indices this run inherited;
  * the PARENT run's ``iteration_summaries`` — their real status, signature
    and timings, so a preserved iteration keeps its own record instead of
    being described by the attempt that merely carried it.

Each reinstalled record is marked ``replayed=True``, which is what keeps it
out of every progress aggregate (see IterationSummary.replayed).  Indices
the run executed itself are never touched.

Dry-run by default.  Pass --apply to write.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--block-id", required=True,
                    help="The loop block whose prefix is missing.")
    ap.add_argument("--apply", action="store_true",
                    help="Write the repair. Omit for a dry run.")
    args = ap.parse_args()

    from app.models.task_run import IterationSummary
    from app.storage.task_runs import TaskRunStorage
    from app.utils.paths import get_project_dir

    storage = TaskRunStorage(get_project_dir(args.project_id))
    run = storage.get(args.run_id)
    if run is None:
        print(f"run {args.run_id} not found", file=sys.stderr)
        return 1

    state = (run.block_states or {}).get(args.block_id)
    if state is None:
        print(f"block {args.block_id} has no state in this run",
              file=sys.stderr)
        return 1

    inherited = {int(k) for k in (run.resume_iteration_artifacts or {})}
    recorded = {s.index for s in state.iteration_summaries}
    missing = sorted(inherited - recorded)

    print(f"run          {run.id}  (attempt {run.attempt}, {run.status})")
    print(f"parent       {run.parent_run_id}")
    print(f"block        {args.block_id}")
    print(f"inherited    {len(inherited)}")
    print(f"recorded     {len(recorded)}  {sorted(recorded)}")
    print(f"missing      {len(missing)}  {missing}")

    if not missing:
        print("nothing to repair")
        return 0

    # Real status/timings from the attempt that executed them.  Absent a
    # readable parent we still record the index — an inherited artifact is
    # by construction a retained pass, since only passes are banked — but
    # say so rather than inventing timings.
    parent_by_index = {}
    if run.parent_run_id:
        parent = storage.get(run.parent_run_id)
        p_state = (parent.block_states or {}).get(args.block_id) if parent else None
        if p_state:
            parent_by_index = {s.index: s for s in p_state.iteration_summaries}
    print(f"parent recs  {len(parent_by_index)}")

    added = []
    for idx in missing:
        src = parent_by_index.get(idx)
        added.append(IterationSummary(
            index=idx,
            status=(src.status if src else "passed"),
            signature=(src.signature if src else None),
            duration_ms=(src.duration_ms if src else 0),
            tokens=(src.tokens if src else 0),
            has_artifact=True,
            replayed=True,
        ))

    unsourced = [s.index for s in added if s.index not in parent_by_index]
    if unsourced:
        print(f"WARNING: no parent record for {unsourced} — "
              f"recorded as passed with zero timings")

    if not args.apply:
        print(f"\nDRY RUN — would add {len(added)} replayed summaries. "
              f"Re-run with --apply.")
        return 0

    storage.seed_replayed_iterations(run.id, args.block_id, added)
    after = (storage.get(run.id).block_states or {}).get(args.block_id)
    n = len(after.iteration_summaries) if after else 0
    print(f"\napplied — block now holds {n} summaries "
          f"({len(added)} replayed, {len(recorded)} executed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
