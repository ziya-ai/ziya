"""
The server and the client must agree on which diff fences are one diff.

The UI merges a headerless continuation fence into the headed diff before it
and applies both with one button (chainHeaderlessContinuationDiffs in
frontend/src/components/MarkdownRenderer.tsx).  The apply ledger keys diffs
by content, so it has to merge the same fences (_chains_to_previous in
app/utils/apply_ledger.py); when the two rules drift, applies stop matching
their key and quietly fall back to matching by file.

Both suites read tests/fixtures/diff_continuation_cases.json.  The client
half is frontend/src/components/__tests__/diffContinuationParity.test.ts.
"""
import json
from pathlib import Path

import pytest

from app.utils import apply_ledger as al
from app.utils.diff_utils.parsing.diff_parser import split_combined_diff

FIXTURE = Path(__file__).parent / "fixtures" / "diff_continuation_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
_TICKS = "`" * 3


def _message(blocks):
    """The blocks as the fences of one response, with prose between them."""
    return "\n\nSome prose.\n\n".join(f"{_TICKS}diff\n{b}\n{_TICKS}" for b in blocks)


def test_fixture_is_well_formed():
    assert len(CASES) >= 10
    for case in CASES:
        blocks, into = case["blocks"], case["into"]
        assert len(blocks) == len(into), case["name"]
        for i, target in enumerate(into):
            if isinstance(target, int):
                assert target < i and into[target] == "head", case["name"]
            else:
                assert target in ("head", None), case["name"]
    # Both outcomes are exercised, or the parity tests prove nothing.
    flat = [t for c in CASES for t in c["into"]]
    assert any(isinstance(t, int) for t in flat) and None in flat


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_ledger_keys_fences_the_way_the_client_merges_them(case):
    blocks, into = case["blocks"], case["into"]
    entries = al._diff_files(_message(blocks))
    for i, block in enumerate(blocks):
        got = [e["key"] for e in entries if e["fence_index"] == i]
        if into[i] != "head":
            # Merged into an earlier diff, or headerless and left alone:
            # either way it is not a diff of its own.
            assert got == [], f"block {i} was keyed on its own"
            continue
        merged = [blocks[j] for j, target in enumerate(into)
                  if isinstance(target, int) and target == i]
        parts = split_combined_diff(block) or [block]
        want = [al.diff_key(p) for p in parts[:-1]]
        want.append(al.diff_key("\n".join([parts[-1]] + merged)))
        assert got == want, f"block {i}: merged {len(merged)} continuation(s) expected"
        if merged:
            assert got[-1] != al.diff_key(parts[-1])   # the merge changed the key
