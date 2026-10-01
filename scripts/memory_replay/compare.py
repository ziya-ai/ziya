#!/usr/bin/env python3
"""Compare the final sandbox stores of replay runs (plaintext-canonical).

    python3 scripts/memory_replay/compare.py <run> <run> [...]

Prints per-file sha256 of the decrypted contents (npz by arrays) and whether
all runs are identical; also compares transitions.jsonl byte-for-byte.
Exit 0 when identical, 1 otherwise.  Hashes and names only -- no text.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine  # noqa: E402

engine.setup_sys_path("current")


def main(names):
    engine.bootstrap()
    fps = {}
    for n in names:
        d = engine.REPLAY_DIR / "runs" / n
        fp = engine.fingerprint(d / "home")
        tr = d / "transitions.jsonl"
        fp["<transitions.jsonl>"] = hashlib.sha256(tr.read_bytes()).hexdigest() if tr.exists() else None
        fps[n] = fp
    keys = sorted(set().union(*fps.values()))
    same = all(fps[n] == fps[names[0]] for n in names)
    rows = {k: {n: (fps[n].get(k) or "-")[:12] for n in names} for k in keys}
    print(json.dumps({"identical": same, "combined": {n: engine.combined(fps[n]) for n in names},
                      "files": rows}, indent=1))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
