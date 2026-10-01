#!/usr/bin/env python3
"""Arm source trees for the longitudinal replay.

An arm is a source tree plus optional constant overrides:

  * ``baseline`` -- ``git archive <commit> app scripts`` unpacked (by Python,
    read-only export) into ``.ziya/memory-replay/arms/baseline``.
  * ``current``  -- a frozen copy of the working tree's ``app/`` and
    ``scripts/memory_replay`` taken ONCE into ``.ziya/memory-replay/arms/current``
    so later edits cannot change an arm mid-run.
  * ablations    -- ``current`` + overrides (``--overrides JSON`` to the engine).

``tree_hash`` is a content hash of a directory (relative paths + file sha256,
``__pycache__`` ignored) recorded in state.json facts.arms and in every run's
meta, so a resumed run can prove it is still running the same code.

Usage:
    python3 scripts/memory_replay/arms.py export-baseline [--commit 5e0e1562]
    python3 scripts/memory_replay/arms.py freeze-current
    python3 scripts/memory_replay/arms.py hash <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from typing import Dict, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARMS_DIR = os.path.join(ROOT, ".ziya", "memory-replay", "arms")
BASELINE_COMMIT = "5e0e1562"
_IGNORE = ("__pycache__",)


def tree_hash(root: str) -> Tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in _IGNORE)
        for f in sorted(fn):
            if f.endswith(".pyc"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            with open(p, "rb") as fh:
                h.update(rel.encode() + b"\0" + hashlib.sha256(fh.read()).digest())
            n += 1
    return h.hexdigest(), n


def arm_root(name: str) -> str:
    base = "current" if name not in ("baseline",) else "baseline"
    return os.path.join(ARMS_DIR, base)


def export_baseline(commit: str = BASELINE_COMMIT) -> Dict[str, str]:
    dest = os.path.join(ARMS_DIR, "baseline")
    if os.path.isdir(dest) and os.listdir(dest):
        raise SystemExit(f"{dest} exists; arms are exported once")
    os.makedirs(dest, exist_ok=True)
    raw = subprocess.run(["git", "archive", "--format=tar", commit, "app", "scripts"],
                         cwd=ROOT, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        t.extractall(dest)
    full = subprocess.run(["git", "rev-parse", commit, f"{commit}^{{tree}}"], cwd=ROOT,
                          check=True, capture_output=True, text=True).stdout.split()
    return {"commit": full[0], "git_tree": full[1], "app_hash": tree_hash(os.path.join(dest, "app"))[0]}


def freeze_harness() -> Dict[str, str]:
    """Snapshot scripts/memory_replay into the current arm (provenance; the
    engine records the live harness hash per run and refuses to resume a run
    under a different harness)."""
    dest = os.path.join(ARMS_DIR, "current", "scripts", "memory_replay")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(os.path.join(ROOT, "scripts", "memory_replay"), dest,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return {"harness_hash": tree_hash(dest)[0]}


def freeze_current() -> Dict[str, str]:
    dest = os.path.join(ARMS_DIR, "current")
    if os.path.isdir(os.path.join(dest, "app")):
        raise SystemExit(f"{dest} exists; the current arm is frozen once")
    ig = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(os.path.join(ROOT, "app"), os.path.join(dest, "app"), ignore=ig)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    return {"from": "working tree", "head": head,
            "app_hash": tree_hash(os.path.join(dest, "app"))[0]}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export-baseline")
    e.add_argument("--commit", default=BASELINE_COMMIT)
    sub.add_parser("freeze-current")
    sub.add_parser("freeze-harness")
    hp = sub.add_parser("hash")
    hp.add_argument("dir")
    a = ap.parse_args()
    if a.cmd == "export-baseline":
        print(json.dumps(export_baseline(a.commit)))
    elif a.cmd == "freeze-current":
        print(json.dumps(freeze_current()))
    elif a.cmd == "freeze-harness":
        print(json.dumps(freeze_harness()))
    else:
        print(json.dumps(dict(zip(("hash", "files"), tree_hash(a.dir)))))


if __name__ == "__main__":
    sys.exit(main())
