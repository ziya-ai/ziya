#!/usr/bin/env python3
"""
Backfill file-path artifacts into their run's own artifacts/ directory.

Context
-------
``build_part``'s ``file_path`` branch (app/utils/task_artifacts.py) used to
record ``file_uri`` as the artifact's *original* on-disk path (wherever the
emitting script happened to write it — a spike's ``.ziya/`` scratch dir, an
e2e test's output dir, etc.) without ever copying the bytes into the run's
own ``task_runs/{run_id}/artifacts/`` directory. The blob-serving route and
the frontend only ever look up artifacts by basename inside that directory,
so any such artifact 404s forever, even though the underlying file usually
still exists on disk right where it was emitted.

The live-path fix (same function) now copies into ``artifacts/`` at emit
time. This script is the one-time backfill for runs recorded *before* that
fix: it walks every project's task runs, finds "file" artifact parts whose
``file_uri`` resolves outside the run's ``artifacts/`` dir, and — if the
original file still exists — copies it in via the same ``save_artifact_blob``
helper (same encryption-at-rest and collision-suffixing behavior) and
rewrites ``file_uri`` to point at the copy.

Scans three places artifacts live:
  - run.artifact              (top-level artifact of the root block)
  - run.block_states[*].artifact   (per-block artifacts)
  - task_runs/{run_id}/iterations/*.json   (per-iteration artifacts)

Usage:
    python scripts/backfill_artifact_files.py            # dry run, reports only
    python scripts/backfill_artifact_files.py --apply     # actually writes
    python scripts/backfill_artifact_files.py --apply --project <project_id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Make the app package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.task_card import Artifact  # noqa: E402
from app.storage.projects import ProjectStorage  # noqa: E402
from app.storage.task_runs import TaskRunStorage  # noqa: E402
from app.utils.paths import get_ziya_home  # noqa: E402
from app.utils.task_artifacts import (  # noqa: E402
    finish_artifact_collection, save_artifact_blob, start_artifact_collection,
)


def _init_encryption() -> None:
    """Load the plugin system so encrypted project/run files can be
    read/written, same as the running server does at startup
    (app/main.py). Without this, any project using at-rest encryption
    (ALE) fails to decrypt with "key material is unavailable"."""
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()


class Stats:
    def __init__(self):
        self.scanned_parts = 0
        self.already_ok = 0
        self.backfilled = 0
        self.source_missing = 0
        self.copy_failed = 0
        self.runs_touched = 0


def _is_inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _backfill_artifact(
    artifact: Optional[Artifact], run_id: str, artifacts_dir: Path,
    stats: Stats, apply: bool, label: str,
) -> bool:
    """Mutate ``artifact.outputs`` in place. Returns True if changed."""
    if artifact is None or not artifact.outputs:
        return False
    changed = False
    for part in artifact.outputs:
        if part.part_type != "file" or not part.file_uri:
            continue
        stats.scanned_parts += 1
        src = Path(part.file_uri)
        if _is_inside(src, artifacts_dir):
            stats.already_ok += 1
            continue
        if not src.exists() or not src.is_file():
            print(f"  [MISSING] {label}: {part.file_uri!r} no longer exists on disk")
            stats.source_missing += 1
            continue
        if not apply:
            print(f"  [WOULD BACKFILL] {label}: {part.file_uri} -> {artifacts_dir}/")
            stats.backfilled += 1
            changed = True
            continue
        token = start_artifact_collection(
            block_id="backfill", artifacts_dir=str(artifacts_dir), run_id=run_id,
        )
        try:
            new_uri, err = save_artifact_blob(src.name, src.read_bytes())
        except OSError as e:
            new_uri, err = None, str(e)
        finally:
            finish_artifact_collection(token)
        if err or not new_uri:
            print(f"  [FAILED] {label}: could not copy {part.file_uri!r}: {err}")
            stats.copy_failed += 1
            continue
        print(f"  [BACKFILLED] {label}: {part.file_uri} -> {new_uri}")
        part.file_uri = new_uri
        stats.backfilled += 1
        changed = True
    return changed


def process_project(project_id: str, project_dir: Path, apply: bool, stats: Stats) -> None:
    storage = TaskRunStorage(project_dir)
    for run in storage.list():
        artifacts_dir = storage.runs_dir / run.id / "artifacts"
        run_changed = False

        if _backfill_artifact(
            run.artifact, run.id, artifacts_dir, stats, apply,
            label=f"{project_id}/{run.id} (top-level)",
        ):
            run_changed = True

        for block_id, state in run.block_states.items():
            if _backfill_artifact(
                state.artifact, run.id, artifacts_dir, stats, apply,
                label=f"{project_id}/{run.id} block={block_id}",
            ):
                run_changed = True

        if run_changed and apply:
            storage.set_artifact(run.id, run.artifact) if run.artifact else None
            for block_id, state in run.block_states.items():
                if state.artifact is not None:
                    storage.update_block_status(
                        run.id, block_id, state.status, artifact=state.artifact,
                    )

        # Iteration artifacts live in separate files, keyed by block_id/index
        # embedded in the filename — parsed rather than re-derived, since
        # TaskRunStorage exposes no "list iteration files" helper.
        iter_dir = storage._iteration_dir(run.id)
        if iter_dir.exists():
            for f in sorted(iter_dir.glob("*.json")):
                stem = f.stem  # "{block_id}_{index}"
                try:
                    block_id, index_s = stem.rsplit("_", 1)
                    index = int(index_s)
                except ValueError:
                    print(f"  [SKIP] unparseable iteration filename: {f}")
                    continue
                artifact = storage.read_iteration_artifact(run.id, block_id, index)
                if artifact is None:
                    continue
                if _backfill_artifact(
                    artifact, run.id, artifacts_dir, stats, apply,
                    label=f"{project_id}/{run.id} block={block_id} iter={index}",
                ):
                    run_changed = True
                    if apply:
                        storage.write_iteration_artifact(run.id, block_id, index, artifact)

        if run_changed:
            stats.runs_touched += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="Actually copy files and rewrite records "
                              "(default: dry run / report only)")
    parser.add_argument("--project", default=None,
                         help="Limit to a single project id (default: all projects)")
    args = parser.parse_args()

    _init_encryption()
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    projects = project_storage.list()
    if args.project:
        projects = [p for p in projects if p.id == args.project]
        if not projects:
            print(f"No project found with id {args.project!r}")
            sys.exit(1)

    stats = Stats()
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== Artifact file-path backfill ({mode}) — {len(projects)} project(s) ===\n")
    for project in projects:
        project_dir = ziya_home / "projects" / project.id
        print(f"-- project {project.id} ({project.path}) --")
        process_project(project.id, project_dir, args.apply, stats)

    print("\n=== Summary ===")
    print(f"file-parts scanned:     {stats.scanned_parts}")
    print(f"already correct:        {stats.already_ok}")
    print(f"backfilled{'' if args.apply else ' (would be)'}:            {stats.backfilled}")
    print(f"source file missing:    {stats.source_missing}")
    print(f"copy failed:            {stats.copy_failed}")
    print(f"runs touched:           {stats.runs_touched}")
    if not args.apply and stats.backfilled:
        print("\nRe-run with --apply to actually perform the backfill.")


if __name__ == "__main__":
    main()
