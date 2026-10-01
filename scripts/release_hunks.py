#!/usr/bin/env python3
"""Split one contended file's working-tree diff into per-entry patches.

A *contended* file is one that several ``## [Unreleased]`` changelog entries
name (see ``release_plan.py index``).  Its hunks have to be divided between
those entries so each lands in its own commit.  Deciding which hunk belongs
to which entry is a judgement; everything around that judgement is mechanical
and lives here, so the judgement is recorded as a tiny JSON mapping instead
of a hand-written diff.

Modes:
  dump  <file>...
      Print a compact, numbered listing of the hunks in each file's
      ``git diff`` so the hunks can be attributed by index.

  split --file <path> --attrib <attrib.json> --out <patch_dir> [--no-check]
      ``attrib.json`` maps hunk index -> entry_id for ONE file:
          {"0": "fix-foo", "1": "feat-bar", "2": "fix-foo"}
      Writes ``<patch_dir>/<entry_id>/<flattened path>.patch`` holding the
      file's diff header plus only that entry's hunks, then validates every
      patch with ``git apply --cached --check`` (validate-only: it never
      touches the index).  Every hunk must be attributed; a missing index, an
      unknown index, or a patch that fails --check is an error (exit 1) and is
      reported, so a bad attribution cannot half-stage later.

  merge --file <path> --entries a,b,c --reason "<obstacle>" --out <patch_dir>
      Record that this file's hunks cannot be split between its claiming
      entries.  Writes ``<patch_dir>/_merges/<flattened path>.json``.  The
      reason must name the specific obstacle; an empty or generic reason is
      refused, because a merge without a stated obstacle is indistinguishable
      from giving up.

Hunk headers are left exactly as git produced them.  ``git apply`` locates a
hunk by its context and tolerates the line offsets that dropping neighbouring
hunks introduces, so nothing is renumbered.

Only tracked modifications have hunks.  An untracked (new) file has no diff
to split and belongs whole to one entry; ``dump`` says so.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

GENERIC_REASONS = {"", "hard to tell", "unclear", "unknown", "interleaved",
                   "complex", "too hard", "cannot split", "n/a", "none"}


def git(args, root, check=False):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=check)


def repo_root(explicit=None):
    if explicit:
        return os.path.abspath(explicit)
    top = git(["rev-parse", "--show-toplevel"], os.getcwd()).stdout.strip()
    return top or os.getcwd()


def flattened(path):
    return path.replace("/", "__")


def raw_diff(root, path):
    return git(["diff", "-U3", "--", path], root).stdout


def split_hunks(diff):
    """Return ``(header_lines, [hunk_text, ...])`` for one file's diff."""
    lines = diff.split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("@@ ")), len(lines))
    header = [l for l in lines[:start] if l]
    hunks, cur = [], []
    for l in lines[start:]:
        if l.startswith("@@ "):
            if cur:
                hunks.append("\n".join(cur))
            cur = [l]
        elif cur:
            cur.append(l)
    if cur:
        hunks.append("\n".join(cur))
    # Trailing blank produced by the final newline of the diff text.
    hunks = [h.rstrip("\n") for h in hunks]
    return header, hunks


def is_tracked(root, path):
    return git(["ls-files", "--error-unmatch", "--", path], root).returncode == 0


# --------------------------------------------------------------------- dump

def do_dump(root, paths, maxlines=4):
    for p in paths:
        if not is_tracked(root, p):
            print(f"\n##### {p}  (untracked: no hunks; belongs whole to one entry)")
            continue
        header, hunks = split_hunks(raw_diff(root, p))
        print(f"\n##### {p}  ({len(hunks)} hunks)")
        for i, h in enumerate(hunks):
            hl = h.split("\n")
            print(f"  [{i}] {hl[0][:150]}")
            shown = 0
            for l in hl[1:]:
                if l[:1] in "+-" and l[1:].strip():
                    print(f"      {l[:150]}")
                    shown += 1
                    if shown >= maxlines:
                        break
    return 0


# -------------------------------------------------------------------- split

def write_patches(root, path, attrib, out_dir):
    """Build the per-entry patch files.  Returns ``(written, problems)`` where
    ``written`` maps entry_id -> patch path."""
    problems = []
    if not is_tracked(root, path):
        return {}, [f"{path}: untracked file has no hunks to split; stage it whole"]
    header, hunks = split_hunks(raw_diff(root, path))
    if not hunks:
        return {}, [f"{path}: no hunks in working diff"]

    keys = set(attrib)
    want = {str(i) for i in range(len(hunks))}
    missing = sorted(want - keys, key=int)
    unknown = sorted(keys - want)
    if missing:
        problems.append(f"{path}: hunks unattributed {missing}")
    if unknown:
        problems.append(f"{path}: attribution names hunks that do not exist {unknown}")
    if problems:
        return {}, problems

    by_entry = {}
    for i, h in enumerate(hunks):
        eid = str(attrib[str(i)]).strip()
        if not eid:
            problems.append(f"{path}: hunk {i} mapped to an empty entry_id")
            continue
        by_entry.setdefault(eid, []).append(h)
    if problems:
        return {}, problems

    written = {}
    for eid, hs in by_entry.items():
        d = os.path.join(out_dir, eid)
        os.makedirs(d, exist_ok=True)
        target = os.path.join(d, flattened(path) + ".patch")
        text = "\n".join(header + hs) + "\n"
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        written[eid] = target
    return written, []


def check_patches(root, written):
    problems = []
    for eid, target in written.items():
        r = git(["apply", "--cached", "--check", target], root)
        if r.returncode != 0:
            problems.append(f"{eid}: git apply --cached --check failed for {target}: "
                            f"{(r.stderr or r.stdout).strip()}")
    return problems


def do_split(root, path, attrib_path, out_dir, check=True):
    with open(attrib_path, encoding="utf-8") as fh:
        attrib = json.load(fh)
    # Accept the multi-file shape {file: {idx: eid}} when it names this file.
    if path in attrib and isinstance(attrib[path], dict):
        attrib = attrib[path]
    if not isinstance(attrib, dict):
        print(f"PROBLEM: {attrib_path}: expected an object mapping hunk index -> entry_id")
        return 1
    out_dir = out_dir if os.path.isabs(out_dir) else os.path.join(root, out_dir)
    written, problems = write_patches(root, path, attrib, out_dir)
    if not problems and check:
        problems = check_patches(root, written)
    counts = {}
    for i, eid in attrib.items():
        counts[eid] = counts.get(eid, 0) + 1
    for eid, target in sorted(written.items()):
        print(f"wrote {target}  ({counts.get(eid, 0)} hunks)")
    for pr in problems:
        print("PROBLEM:", pr)
    return 1 if problems else 0


# -------------------------------------------------------------------- merge

def do_merge(root, path, entries, reason, out_dir):
    ids = [e.strip() for e in entries.split(",") if e.strip()]
    if len(ids) < 2:
        print("PROBLEM: a merge needs at least two entry ids")
        return 1
    r = (reason or "").strip()
    if r.lower().rstrip(".") in GENERIC_REASONS or len(r) < 20:
        print("PROBLEM: the reason must name the specific obstacle to splitting "
              "(what in the hunks carries both entries), not a generic phrase")
        return 1
    out_dir = out_dir if os.path.isabs(out_dir) else os.path.join(root, out_dir)
    d = os.path.join(out_dir, "_merges")
    os.makedirs(d, exist_ok=True)
    target = os.path.join(d, flattened(path) + ".json")
    with open(target, "w", encoding="utf-8") as fh:
        json.dump({"file": path, "entry_ids": ids, "reason": r}, fh, indent=1)
    print(f"wrote {target}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", help="repository root (default: git toplevel of cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump", help="numbered hunk listing for attribution")
    d.add_argument("paths", nargs="+")

    s = sub.add_parser("split", help="write per-entry patches from an attribution")
    s.add_argument("--file", required=True)
    s.add_argument("--attrib", required=True, help="JSON: {hunk index: entry_id}")
    s.add_argument("--out", required=True, help="patch directory")
    s.add_argument("--no-check", action="store_true",
                   help="skip git apply --cached --check validation")

    m = sub.add_parser("merge", help="record that a file cannot be split")
    m.add_argument("--file", required=True)
    m.add_argument("--entries", required=True, help="comma-separated entry ids")
    m.add_argument("--reason", required=True)
    m.add_argument("--out", required=True, help="patch directory")

    args = ap.parse_args(argv)
    root = repo_root(args.root)
    if args.cmd == "dump":
        return do_dump(root, args.paths)
    if args.cmd == "split":
        return do_split(root, args.file, args.attrib, args.out, check=not args.no_check)
    if args.cmd == "merge":
        return do_merge(root, args.file, args.entries, args.reason, args.out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
