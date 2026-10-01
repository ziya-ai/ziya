#!/usr/bin/env python3
"""Remove outage entries from the replay cache and quarantine tainted runs.

Background (2026-09-27): the cache used to store infrastructure failures
(expired credentials, network drops, embedding calls that returned None) as
if they were model answers.  Every run that replayed such an entry took the
network-down code path silently.  llmcache now never stores them and drops
old ones on read, but a run that already CONSUMED one is tainted from that
event on, and nothing records which event that was -- so the run restarts.

  python3 scripts/memory_replay/reset_contaminated.py                 # report only
  python3 scripts/memory_replay/reset_contaminated.py --purge-cache
  python3 scripts/memory_replay/reset_contaminated.py --reset-run sep-baseline --reset-run sep-current

A reset moves the run directory to runs/_quarantine/<run>-<unix time>/ and
recreates it holding only spec.json, so the next engine start is a fresh
run with the same arm/window/overrides.  A run whose engine.lock is held by
a live process is refused: stop that engine first.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REPLAY = ROOT / ".ziya" / "memory-replay"
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(ROOT))


def _locked(run_dir: Path) -> bool:
    p = run_dir / "engine.lock"
    if not p.exists():
        return False
    fd = os.open(str(p), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def scan_cache(purge: bool) -> dict:
    from app.utils.encryption import get_encryptor
    from llmcache import is_infra_entry
    enc = get_encryptor()
    report = {}
    for ns in sorted(p for p in (REPLAY / "cache").iterdir() if p.is_dir()):
        n = bad = 0
        for f in ns.rglob("*.enc"):
            n += 1
            try:
                val = json.loads(enc.decrypt(f.read_bytes()))
            except Exception:
                continue
            if is_infra_entry(val):
                bad += 1
                if purge:
                    f.unlink()
        report[ns.name] = {"entries": n, "outage_entries": bad, "removed": bad if purge else 0}
    return report


def reset_run(name: str) -> str:
    d = REPLAY / "runs" / name
    if not d.is_dir():
        return f"{name}: no such run"
    if _locked(d):
        return f"{name}: REFUSED - an engine holds {d / 'engine.lock'}; stop it first"
    spec = d / "spec.json"
    spec_bytes = spec.read_bytes() if spec.exists() else None
    q = REPLAY / "runs" / "_quarantine"
    q.mkdir(parents=True, exist_ok=True)
    dest = q / f"{name}-{int(time.time())}"
    shutil.move(str(d), str(dest))
    d.mkdir()
    if spec_bytes is not None:
        (d / "spec.json").write_bytes(spec_bytes)
    return f"{name}: quarantined to {dest.relative_to(ROOT)}; fresh run keeps spec.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--purge-cache", action="store_true")
    ap.add_argument("--reset-run", action="append", default=[])
    a = ap.parse_args()
    os.environ.setdefault("ZIYA_ENABLE_MEMORY", "true")
    from app.plugins import initialize
    initialize()
    print(json.dumps(scan_cache(a.purge_cache), indent=1))
    for r in a.reset_run:
        print(reset_run(r))


if __name__ == "__main__":
    main()
