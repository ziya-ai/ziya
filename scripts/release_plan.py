#!/usr/bin/env python3
"""Index the ``## [Unreleased]`` changelog entries against the dirty tree.

This is the deterministic half of release planning.  It is release-agnostic:
nothing here names a version, a file, or an entry.  Everything is derived
from two inputs that already exist at sweep time:

  * the ``- `` entries under ``## [Unreleased]`` in CHANGELOG.md, which ARE
    the enumeration of the release's logical changes, and
  * ``git status --porcelain``, which is the set of paths that can ship.

Each entry gets a stable ``entry_id`` (a slug of its bolded lead sentence),
and every changed path is placed in exactly one bucket:

  claimed    named by exactly one entry   -> that entry stages the whole file
  contended  named by two or more entries -> hunks must be split between them
                                              (see release_hunks.py)
  unclaimed  named by no entry            -> needs triage (assign / new entry /
                                              exclude as in-progress work)

Paths an entry cites that are NOT changed are reported as
``dropped_citations``: a few are normal (the work already shipped), a large
count means the changelog is describing a different tree.

Usage:
  release_plan.py index [--root DIR] [--ledger PATH] [--json]

  --ledger PATH  merge the result into PATH under the key ``entry_index``
                 (the file is created if missing; other keys are preserved)
  --json         print the full index as JSON instead of the summary

Exit status is 0 whenever the index was built; the caller decides what an
unclaimed or contended path means for its stage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

# A cited path is a backticked token with a directory separator and a file
# extension and no whitespace.  Brace groups (``a/{b,c}.ts``) are expanded.
_CITE = re.compile(r"`([^`\s]+)`")
_EXT = re.compile(r"\.[A-Za-z0-9]+$")
_BRACE = re.compile(r"\{([^{}]*)\}")

SLUG_MAX = 60


def git(args, root):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=False).stdout


def repo_root(explicit=None):
    if explicit:
        return os.path.abspath(explicit)
    top = git(["rev-parse", "--show-toplevel"], os.getcwd()).strip()
    return top or os.getcwd()


# ---------------------------------------------------------------- changelog

def unreleased_section(text):
    """Return the body of ``## [Unreleased]`` up to the next ``## [`` header."""
    m = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", text,
                  re.S | re.M)
    return m.group(1) if m else ""


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > SLUG_MAX:
        s = s[:SLUG_MAX].rsplit("-", 1)[0] or s[:SLUG_MAX]
    return s or "entry"


def expand_braces(token):
    m = _BRACE.search(token)
    if not m:
        return [token]
    out = []
    for alt in m.group(1).split(","):
        out.extend(expand_braces(token[:m.start()] + alt.strip() + token[m.end():]))
    return out


def cited_paths(entry_text):
    paths = []
    for raw in _CITE.findall(entry_text):
        for tok in expand_braces(raw):
            tok = tok.strip().lstrip("./")
            if "/" in tok and _EXT.search(tok):
                paths.append(tok)
    # preserve order, drop duplicates
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def parse_entries(changelog_text):
    """One record per ``- `` item under [Unreleased].

    Indented continuation lines are folded into the preceding entry.  The
    ``### `` heading in force is recorded on each entry.
    """
    heading, items = None, []
    for line in unreleased_section(changelog_text).split("\n"):
        if line.startswith("### "):
            heading = line[4:].strip()
        elif line.startswith("- "):
            items.append([heading, line])
        elif items and line.startswith("  ") and line.strip():
            items[-1][1] += " " + line.strip()

    entries, used = [], defaultdict(int)
    for i, (head, text) in enumerate(items):
        b = re.match(r"- \*\*(.+?)\*\*", text)
        lead = b.group(1) if b else text[2:].strip()
        base = slugify(lead)
        used[base] += 1
        entry_id = base if used[base] == 1 else f"{base}-{used[base]}"
        entries.append({
            "i": i,
            "entry_id": entry_id,
            "heading": head,
            "title": lead[:120],
            "paths": cited_paths(text),
            "text": text,
        })
    return entries


# -------------------------------------------------------------------- tree

def dirty_paths(root):
    """All paths that could ship: tracked modifications/deletions/renames and
    untracked files (untracked directories expanded to their files).

    Returns ``(tracked, untracked)`` as sorted lists.  Gitignored paths never
    appear in porcelain output, so nothing here can name one.
    """
    tracked, untracked = set(), set()
    for line in git(["status", "--porcelain"], root).splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if code == "??":
            if path.endswith("/"):
                out = git(["ls-files", "--others", "--exclude-standard", "--", path], root)
                untracked.update(p for p in out.splitlines() if p.strip())
            else:
                untracked.add(path)
        else:
            tracked.add(path)
    return sorted(tracked), sorted(untracked)


# ------------------------------------------------------------------- index

def build_index(root):
    changelog = os.path.join(root, "CHANGELOG.md")
    with open(changelog, encoding="utf-8") as fh:
        entries = parse_entries(fh.read())
    tracked, untracked = dirty_paths(root)
    dirty = set(tracked) | set(untracked)

    claims = defaultdict(list)
    dropped = []
    for e in entries:
        live = []
        for p in e["paths"]:
            if p in dirty:
                live.append(p)
                claims[p].append(e["entry_id"])
            else:
                dropped.append({"entry_id": e["entry_id"], "path": p})
        e["live_paths"] = live

    claimed = {p: ids[0] for p, ids in claims.items() if len(ids) == 1}
    contended = {p: ids for p, ids in claims.items() if len(ids) > 1}
    unclaimed = sorted(p for p in dirty if p not in claims)

    # Entries whose cited files are all absent from the tree: the work shipped
    # already (a stale entry) or the citations are wrong.  Either way they
    # have nothing to stage and the commit loop must not expect a commit.
    no_live = [e["entry_id"] for e in entries if e["paths"] and not e["live_paths"]]
    no_paths = [e["entry_id"] for e in entries if not e["paths"]]

    return {
        "entries": [{k: v for k, v in e.items() if k != "text"} for e in entries],
        "claimed": claimed,
        "contended": contended,
        "unclaimed": unclaimed,
        "untracked": untracked,
        "dropped_citations": dropped,
        "entries_without_live_paths": no_live,
        "entries_without_cited_paths": no_paths,
        "counts": {
            "entries": len(entries),
            "dirty": len(dirty),
            "tracked": len(tracked),
            "untracked": len(untracked),
            "claimed": len(claimed),
            "contended": len(contended),
            "unclaimed": len(unclaimed),
            "dropped_citations": len(dropped),
        },
    }


def merge_into_ledger(ledger_path, index):
    data = {}
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as fh:
            try:
                data = json.load(fh) or {}
            except json.JSONDecodeError:
                data = {}
    data["entry_index"] = index
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def print_summary(index):
    c = index["counts"]
    print(f"entries:            {c['entries']}")
    print(f"dirty paths:        {c['dirty']}  (tracked {c['tracked']}, untracked {c['untracked']})")
    print(f"claimed (1 entry):  {c['claimed']}")
    print(f"contended (>1):     {c['contended']}")
    print(f"unclaimed (0):      {c['unclaimed']}")
    print(f"dropped citations:  {c['dropped_citations']}")
    print(f"entries w/o live paths (already shipped?): {len(index['entries_without_live_paths'])}")
    print(f"entries citing no paths:                  {len(index['entries_without_cited_paths'])}")
    if index["contended"]:
        print("\ncontended files:")
        for p, ids in sorted(index["contended"].items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(ids):2d}  {p}")
    if index["unclaimed"]:
        print("\nunclaimed paths:")
        for p in index["unclaimed"]:
            print(f"      {p}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ix = sub.add_parser("index", help="build the entry/path index")
    ix.add_argument("--root", help="repository root (default: git toplevel of cwd)")
    ix.add_argument("--ledger", help="merge the index into this JSON file under 'entry_index'")
    ix.add_argument("--json", action="store_true", help="print the index as JSON")
    args = ap.parse_args(argv)

    root = repo_root(args.root)
    index = build_index(root)
    if args.ledger:
        merge_into_ledger(args.ledger if os.path.isabs(args.ledger)
                          else os.path.join(root, args.ledger), index)
    if args.json:
        json.dump(index, sys.stdout, indent=1)
        print()
    else:
        print_summary(index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
