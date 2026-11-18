"""
The server and the client must agree on which diff a later diff replaces.

The UI fades an earlier diff for a file as "Superseded by a later revision
below" (findSupersededDiffParts in frontend/src/utils/diffUtils.ts).  The
diff-apply hold counts the diffs it waits for on the server
(_superseded_indices in app/utils/apply_ledger.py).  When the two disagree
the chip says "Waiting for 2 of 2 diffs" under a response that offers one,
and the turn never resumes.

Both suites read tests/fixtures/diff_supersede_cases.json.  The client half
is frontend/src/utils/__tests__/diffSupersedeParity.test.ts.
"""
import json
from pathlib import Path

import pytest

from app.utils import apply_ledger as al

FIXTURE = Path(__file__).parent / "fixtures" / "diff_supersede_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
_TICKS = "`" * 3


def _message(blocks):
    return "\n\nSome prose.\n\n".join(f"{_TICKS}diff\n{b}\n{_TICKS}" for b in blocks)


def _pairs(entries):
    """[fence, file-section] of every entry marked superseded."""
    out, seen = [], {}
    for e in entries:
        n = seen.get(e["fence_index"], 0)
        seen[e["fence_index"]] = n + 1
        if e.get("superseded"):
            out.append([e["fence_index"], n])
    return sorted(out)


def test_fixture_exercises_both_outcomes():
    assert len(CASES) >= 10
    assert any(c["superseded"] for c in CASES) and any(not c["superseded"] for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_ledger_drops_the_fences_the_client_fades(case):
    entries = al._diff_files(_message(case["blocks"]))
    # Every file section was read at all, or an empty "superseded" proves nothing.
    assert len(entries) == sum(b.count("diff --git ") for b in case["blocks"])
    assert _pairs(entries) == sorted(case["superseded"])
    # What the hold counts is what is left.
    total = sum(e["count"] for e in al.expected_from_text(_message(case["blocks"]))["expected"])
    assert total == len(entries) - len(case["superseded"])
