#!/usr/bin/env python3
"""Snapshot ~/.ziya/memory into a durable, verifiable backup.

Every backup taken during the 2026-09-25..27 memory work went to /tmp and was
gone after the weekend purge.  This writes to <project>/.ziya/memory-backups/
<UTC stamp>/ instead (gitignored, survives reboots), copies the store files
byte-for-byte (they are ALE-encrypted at rest, so no plaintext lands here),
and records a manifest of sha256 + size per file so a restore can be checked
against what was actually captured.

    python3 scripts/backup_memory_store.py                 # snapshot, keep last 12
    python3 scripts/backup_memory_store.py --label pre-partc
    python3 scripts/backup_memory_store.py --verify .ziya/memory-backups/<stamp>
    python3 scripts/backup_memory_store.py --list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = ROOT / ".ziya" / "memory-backups"
# Directories under memory/ that are scratch, not store state.
SKIP_DIRS = {"priming-sandbox"}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_files(src: Path) -> List[Path]:
    out = []
    for p in sorted(src.rglob("*")):
        if p.is_file() and not (set(p.relative_to(src).parts[:-1]) & SKIP_DIRS):
            out.append(p)
    return out


def snapshot(src: Path, dest_root: Path, label: Optional[str] = None,
             keep: int = 12) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    dest = dest_root / (f"{stamp}-{label}" if label else stamp)
    dest.mkdir(parents=True, exist_ok=False)
    manifest: Dict[str, Dict[str, object]] = {}
    for p in store_files(src):
        rel = p.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        manifest[str(rel)] = {"sha256": _sha(target), "bytes": target.stat().st_size}
        if manifest[str(rel)]["sha256"] != _sha(p):
            raise RuntimeError(f"source changed while copying: {rel}")
    (dest / "manifest.json").write_text(json.dumps(
        {"source": str(src), "taken_utc": stamp, "label": label, "files": manifest},
        indent=1))
    prune(dest_root, keep)
    return dest


def verify(backup: Path) -> List[str]:
    """Names of files whose bytes no longer match the manifest (empty == ok)."""
    m = json.loads((backup / "manifest.json").read_text())
    bad = []
    for rel, info in m["files"].items():
        p = backup / rel
        if not p.exists() or _sha(p) != info["sha256"]:
            bad.append(rel)
    return bad


def prune(dest_root: Path, keep: int) -> List[Path]:
    """Drop the oldest unlabelled snapshots beyond ``keep``; labelled ones stay."""
    snaps = sorted(p for p in dest_root.iterdir()
                   if p.is_dir() and (p / "manifest.json").exists())
    unlabelled = [p for p in snaps if json.loads((p / "manifest.json").read_text()).get("label") is None]
    removed = unlabelled[:max(0, len(unlabelled) - keep)]
    for p in removed:
        shutil.rmtree(p)
    return removed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", default=str(Path.home() / ".ziya" / "memory"))
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--label", help="kept out of pruning; e.g. pre-partc")
    ap.add_argument("--keep", type=int, default=12)
    ap.add_argument("--verify", metavar="BACKUP_DIR")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    dest_root = Path(a.dest)
    if a.verify:
        bad = verify(Path(a.verify))
        print("ok" if not bad else f"MISMATCH: {bad}")
        sys.exit(1 if bad else 0)
    if a.list:
        for p in sorted(dest_root.iterdir()) if dest_root.exists() else []:
            if (p / "manifest.json").exists():
                m = json.loads((p / "manifest.json").read_text())
                print(p.name, len(m["files"]), "files",
                      sum(f["bytes"] for f in m["files"].values()) // 1024, "KiB")
        return
    dest = snapshot(Path(a.src), dest_root, a.label, a.keep)
    m = json.loads((dest / "manifest.json").read_text())
    print(f"backup: {dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest}  "
          f"({len(m['files'])} files, {sum(f['bytes'] for f in m['files'].values()) // 1024} KiB)")


if __name__ == "__main__":
    main()
