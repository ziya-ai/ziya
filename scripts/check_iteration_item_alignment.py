"""Check that each Repeat iteration processed the item its index names.

A ``for_each`` fan-out binds ``{{item}}`` from the planned roster by
index.  If an iteration's recorded output names a DIFFERENT roster entry
than its index does, then either the binding or the artifact attribution
is wrong -- and in a 60-wide research fan-out that is silent corruption:
every downstream consumer reads the artifact as evidence about the
capability the index claims.

Usage:
  python3 scripts/check_iteration_item_alignment.py \
      --ids /tmp/ids.json --iter-dir /tmp --block b-2cf2a30f
"""

import argparse
import json
import os


def load_ids(path):
    with open(path) as fh:
        return json.load(fh)


def capability_of(artifact):
    """The capability id the artifact itself claims to be about."""
    for part in artifact.get("outputs") or []:
        if part.get("name") == "second-look":
            data = part.get("data")
            if isinstance(data, dict) and data.get("capability_id"):
                return data["capability_id"], "second-look"
    # Fall back to the Stage B part, which carries the same field.
    for part in artifact.get("outputs") or []:
        if part.get("name") == "disposition":
            data = part.get("data")
            if isinstance(data, dict) and data.get("capability_id"):
                return data["capability_id"], "disposition"
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--iter-dir", required=True)
    ap.add_argument("--prefix", default="it_")
    args = ap.parse_args()

    ids = load_ids(args.ids)
    rows = []
    for name in sorted(os.listdir(args.iter_dir)):
        if not name.startswith(args.prefix) or not name.endswith(".json"):
            continue
        stem = name[len(args.prefix):-len(".json")]
        if not stem.isdigit():
            continue
        idx = int(stem)
        with open(os.path.join(args.iter_dir, name)) as fh:
            art = json.load(fh)
        if "outputs" not in art:
            rows.append((idx, ids[idx] if idx < len(ids) else "?",
                         "<unreadable>", None))
            continue
        got, src = capability_of(art)
        expected = ids[idx] if idx < len(ids) else "?"
        rows.append((idx, expected, got, src))

    print("%-5s %-44s %-44s %s" % ("idx", "roster[idx]", "artifact claims",
                                   "verdict"))
    mism = 0
    for idx, exp, got, src in rows:
        ok = (got == exp)
        if got is None:
            verdict = "no capability_id"
        elif ok:
            verdict = "OK"
        else:
            verdict = "*** MISMATCH"
            mism += 1
        print("%-5d %-44s %-44s %s" % (idx, exp, got or "-", verdict))
    print()
    print("checked %d iteration(s); %d mismatch(es)" % (len(rows), mism))


if __name__ == "__main__":
    main()
