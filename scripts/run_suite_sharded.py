#!/usr/bin/env python3
"""Run the pytest suite as N detached shards and aggregate the results.

Usage:
    python scripts/run_suite_sharded.py start [--shards N] [--out DIR]
    python scripts/run_suite_sharded.py status [--out DIR]
    python scripts/run_suite_sharded.py report [--out DIR]

`start` collects every tests/**/test_*.py, sorts by size, deals them
round-robin into N buckets (so each shard gets a similar byte-weight), and
launches one detached `pytest` per bucket writing `shard_N.log` and
`shard_N.xml` into the output directory.  It returns immediately.

`status` prints per-shard liveness and the last summary line seen.
`report` parses the junit XML and lists every failure/error grouped by file.

Shards are independent processes, so a test that leaks state into another
file within the same shard is possible; the report attributes by file so
cross-shard reruns of a single file can confirm.
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = "/tmp/ziya_suite"
SUMMARY_RE = re.compile(r"=+ .*(passed|failed|error|no tests ran).* =+")


def _collect_files():
    files = glob.glob(os.path.join(ROOT, "tests", "**", "test_*.py"), recursive=True)
    files = [f for f in files if "__pycache__" not in f and "/temp/" not in f]
    return sorted(files, key=lambda f: -os.path.getsize(f))


def cmd_start(args):
    os.makedirs(args.out, exist_ok=True)
    for old in glob.glob(os.path.join(args.out, "shard_*")):
        os.remove(old)
    files = _collect_files()
    buckets = [[] for _ in range(args.shards)]
    for i, f in enumerate(files):
        buckets[i % args.shards].append(os.path.relpath(f, ROOT))
    for n, bucket in enumerate(buckets):
        listing = os.path.join(args.out, f"shard_{n}.files")
        with open(listing, "w") as fh:
            fh.write("\n".join(bucket) + "\n")
        log = open(os.path.join(args.out, f"shard_{n}.log"), "w")
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
               "--tb=short", "-rfE", "-o", "addopts=",
               "-m", "not integration",
               f"--junitxml={os.path.join(args.out, f'shard_{n}.xml')}",
               *bucket]
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        with open(os.path.join(args.out, f"shard_{n}.pid"), "w") as fh:
            fh.write(str(p.pid))
        print(f"shard {n}: pid {p.pid}, {len(bucket)} files")
    print(f"launched {args.shards} shards over {len(files)} files -> {args.out}")


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_status(args):
    pids = sorted(glob.glob(os.path.join(args.out, "shard_*.pid")))
    if not pids:
        print("no shards found"); return
    running = 0
    for pf in pids:
        n = re.search(r"shard_(\d+)", pf).group(1)
        pid = int(open(pf).read())
        alive = _alive(pid)
        running += alive
        log = os.path.join(args.out, f"shard_{n}.log")
        tail, summary = "", ""
        if os.path.exists(log):
            lines = open(log, errors="replace").read().splitlines()
            for ln in reversed(lines):
                if SUMMARY_RE.search(ln):
                    summary = ln.strip("= "); break
            prog = [l for l in lines if re.match(r"^[.FsExX%\[\] 0-9]+$", l.strip()) and l.strip()]
            tail = prog[-1][-40:] if prog else ""
        state = "RUN " if alive else "DONE"
        print(f"shard {n:>2} {state} {summary or tail}")
    print(f"{running}/{len(pids)} shards still running")


def cmd_report(args):
    xmls = sorted(glob.glob(os.path.join(args.out, "shard_*.xml")))
    totals = dict(tests=0, failures=0, errors=0, skipped=0)
    by_file = {}
    for x in xmls:
        try:
            root = ET.parse(x).getroot()
        except ET.ParseError:
            print(f"!! unparseable {x} (shard still running or crashed)"); continue
        for suite in root.iter("testsuite"):
            for k in totals:
                totals[k] += int(suite.get(k, 0))
        for tc in root.iter("testcase"):
            bad = tc.find("failure")
            kind = "FAIL"
            if bad is None:
                bad = tc.find("error"); kind = "ERROR"
            if bad is None:
                continue
            f = tc.get("file") or tc.get("classname", "").replace(".", "/") + ".py"
            msg = (bad.get("message") or "").splitlines()[0][:140]
            by_file.setdefault(f, []).append((kind, tc.get("name"), msg))
    print(f"TOTAL tests={totals['tests']} failures={totals['failures']} "
          f"errors={totals['errors']} skipped={totals['skipped']}  "
          f"files_with_problems={len(by_file)}")
    for f in sorted(by_file):
        print(f"\n{f}  ({len(by_file[f])})")
        for kind, name, msg in by_file[f]:
            print(f"  {kind:5} {name}\n        {msg}")
    missing = [n for n in range(len(glob.glob(os.path.join(args.out, 'shard_*.pid'))))
               if not os.path.exists(os.path.join(args.out, f"shard_{n}.xml"))]
    if missing:
        print(f"\n!! shards with no XML (crashed/killed before finishing): {missing}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "status", "report"])
    ap.add_argument("--shards", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    {"start": cmd_start, "status": cmd_status, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
