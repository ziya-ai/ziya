#!/usr/bin/env python3
"""
One-off: relabel usage-ledger rows whose recorded service tier was the
REQUESTED tier rather than the tier actually billed.

Background: until the resolved-tier fix (app/utils/service_tier.py
``note_call`` / ``billed_tier``), the meter wrote the Task Card run's
requested tier (``flex``) at dispatch and never revisited it.  Bedrock
rejects Flex for Claude models and the provider retried at standard, so
every Claude row from a task run says ``flex`` while it was billed at
standard.  The pricer refuses to price a tier the catalog has no factor
for, so those rows show as unknown.

Usage:
    python3 scripts/usage_ledger_relabel_tier.py                # dry run
    python3 scripts/usage_ledger_relabel_tier.py --apply
    python3 scripts/usage_ledger_relabel_tier.py --pattern '%amazon.nova%' --apply

The pattern is a SQL LIKE on model_id; default covers Claude on Bedrock.
Only rows with tier='flex' and status='actual' are touched, and the
database is copied to <db>.bak-<timestamp> before any write.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="ledger path (default: ~/.ziya/usage.db, ZIYA_HOME honoured)")
    ap.add_argument("--pattern", default="%anthropic.claude%", help="SQL LIKE pattern on model_id")
    ap.add_argument("--from-tier", default="flex")
    ap.add_argument("--to-tier", default="standard")
    ap.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    args = ap.parse_args()

    if args.db:
        db = Path(args.db)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.storage.usage_ledger import ledger_path
        db = ledger_path()
    if not db.is_file():
        print(f"no ledger at {db}")
        return 1

    conn = sqlite3.connect(str(db))
    where = "tier = ? AND status = 'actual' AND provider = 'bedrock' AND model_id LIKE ?"
    params = (args.from_tier, args.pattern)
    rows = conn.execute(
        f"SELECT model_id, COUNT(*) FROM usage_records WHERE {where} GROUP BY model_id ORDER BY 2 DESC",
        params).fetchall()
    total = sum(n for _, n in rows)
    print(f"{db}: {total} row(s) tier={args.from_tier!r} matching {args.pattern!r}")
    for mid, n in rows:
        print(f"  {n:>7}  {mid}")
    if not total:
        return 0
    if not args.apply:
        print("dry run; pass --apply to relabel these to", repr(args.to_tier))
        return 0

    backup = db.with_name(f"{db.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(db, backup)
    print(f"backup: {backup}")
    cur = conn.execute(f"UPDATE usage_records SET tier = ? WHERE {where}", (args.to_tier, *params))
    conn.commit()
    print(f"relabelled {cur.rowcount} row(s) -> {args.to_tier!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
