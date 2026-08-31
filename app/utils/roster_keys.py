"""Stable item identity for ``for_each`` rosters.

The roster completeness assertion (``Block.repeat_require_complete``,
design/task-card-roster-assertion.md) diffs the set of roster members a
loop was asked to cover against the members whose iterations passed.
That diff is only meaningful if every member has a stable string
identity — an iteration's ordinal position names nothing once the loop
has returned.

Shared between the executor (``block_executor._plan_iterations``) and
the pre-launch validator (``task_card_validation._check_repeat``) so the
two cannot drift on what a key IS.  Two suites each passing against
their own reading of one field is the seam-failure shape this module
exists to prevent.

Deliberately import-light: no models, no executor, so it stays
importable from the API layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def derive_item_key(item: Any, key_path: Optional[str] = None) -> Optional[str]:
    """Stable string identity for one roster item, or None.

    * ``key_path`` declared — a dotted path into a dict item
      (``"id"``, ``"meta.slug"``).  Returns None when the path does not
      resolve, or resolves to a non-scalar: a key must name exactly one
      member, so a container value is never stringified into one.
    * scalar item, no path — ``str(item)``.  Every observed roster is
      scalar (slice ids, capability ids, tool slugs).
    * dict/list item, no path — None.  Refusing is the caller's job;
      guessing is nobody's.
    """
    if key_path:
        cur = item
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        if cur is None or isinstance(cur, (dict, list)):
            return None
        return str(cur)
    if item is None or isinstance(item, (dict, list)):
        return None
    return str(item)


def roster_key_problems(
    items: List[Any], key_path: Optional[str] = None,
) -> List[str]:
    """Why this roster cannot be asserted over.  Empty list = clean.

    Two hazards, both refusals rather than guesses:

    * an item with no derivable key — a dict/list with no declared
      ``repeat_item_key``, or a declared path that does not resolve to a
      scalar — could never be NAMED in a shortfall, and
    * duplicate keys — an ambiguous roster cannot be diffed against
      the produced set.
    """
    keys = [derive_item_key(it, key_path) for it in items]
    problems: List[str] = []

    unkeyed = [i for i, k in enumerate(keys) if k is None]
    if unkeyed:
        shown = ", ".join(str(i) for i in unkeyed[:5])
        more = f" (+{len(unkeyed) - 5} more)" if len(unkeyed) > 5 else ""
        if key_path:
            problems.append(
                f"repeat_item_key {key_path!r} does not resolve to a "
                f"scalar for roster item(s) at index {shown}{more}"
            )
        else:
            problems.append(
                f"roster item(s) at index {shown}{more} are not scalars "
                f"and no repeat_item_key names their identity - declare "
                f"a dotted path into the item (e.g. 'id')"
            )

    counts: Dict[str, int] = {}
    for k in keys:
        if k is not None:
            counts[k] = counts.get(k, 0) + 1
    dupes = sorted(k for k, n in counts.items() if n > 1)
    if dupes:
        shown = ", ".join(repr(d) for d in dupes[:5])
        more = f" (+{len(dupes) - 5} more)" if len(dupes) > 5 else ""
        problems.append(
            f"duplicate roster key(s) {shown}{more} - an ambiguous "
            f"roster cannot be asserted over; make the items unique or "
            f"point repeat_item_key at a distinguishing field"
        )
    return problems
