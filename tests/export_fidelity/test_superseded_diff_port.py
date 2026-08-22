"""Golden-case tests for the Python port of diffUtils.ts supersession logic.

Defect MD-01: the markdown export retained superseded diffs (the chat UI merely
fades them to opacity 0.45; markdown has no opacity, so a stale diff is
indistinguishable from the live one and is actively misleading).

The fix ports the frontend supersession algorithm
(frontend/src/utils/diffUtils.ts: findSupersededDiffParts and its helpers) to
Python in app/utils/conversation_exporter.py and applies it in
_process_content_for_export. These tests are the SHARED GOLDEN CASES that guard
against the two implementations drifting apart: each case encodes a behaviour
the TS algorithm is specified to produce, so if either side changes the
classification, a golden case here breaks.
"""

from app.utils import conversation_exporter as CE
from app.utils.conversation_exporter import export_conversation_for_paste
from tests.export_fidelity import fixture
from tests.export_fidelity import checks as C


# A minimal single-file diff builder. ``adds`` is the count of added lines so we
# can steer isSequentialPair (earlierAdds<=1) and rangesOverlap.
def _diff(path, start, count, add_markers, removes=0):
    lines = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -{start},{count} +{start},{count + len(add_markers) - removes} @@",
        " context_top",
    ]
    for _ in range(removes):
        lines.append("-removed_line")
    for mk in add_markers:
        lines.append(f"+{mk}")
    lines.append(" context_bottom")
    return "\n".join(lines)


def _fence(body):
    return "```diff\n" + body + "\n```"


# ---------------------------------------------------------------------------
# Port-level golden cases: _find_superseded_diff_parts / indices.
# ---------------------------------------------------------------------------

def test_single_diff_is_never_superseded():
    body = _diff("s.py", 1, 3, ["ONLY"])
    assert CE._find_superseded_diff_parts([body]) == {}


def test_same_file_overlapping_hunk_supersedes_earlier():
    # Earlier adds 2 lines (earlierAdds>1 => NOT a sequential pair); same file,
    # same hunk range => earlier (index 0) is superseded.
    earlier = _diff("app.py", 1, 4, ["STALE", "STALE2"])
    later = _diff("app.py", 1, 4, ["LIVE"])
    m = CE._find_superseded_diff_parts([earlier, later])
    assert m == {0: {0}}


def test_different_files_are_independent():
    a = _diff("x.py", 1, 3, ["ADD_X"])
    b = _diff("y.py", 1, 3, ["ADD_Y"])
    assert CE._find_superseded_diff_parts([a, b]) == {}


def test_non_overlapping_hunks_same_file_both_kept():
    a = _diff("z.py", 1, 3, ["FIRST_HUNK"])
    b = _diff("z.py", 60, 3, ["SECOND_HUNK"])
    assert CE._find_superseded_diff_parts([a, b]) == {}


def test_sequential_pair_not_treated_as_supersession():
    # Earlier is predominantly subtractive (earlierRemoves>0, earlierAdds<=1)
    # and later adds => complementary sequential edit; neither superseded.
    earlier = _diff("seq.py", 1, 5, ["PREP"], removes=3)  # 1 add, 3 removes
    later = _diff("seq.py", 1, 5, ["NEW1", "NEW2"])
    assert CE._find_superseded_diff_parts([earlier, later]) == {}


def test_multi_file_fence_supersedes_only_matching_section():
    # One fence with two files; a later fence supersedes only file m.py.
    block1 = (
        _diff("m.py", 1, 4, ["STALE_M", "STALE_M2"]) + "\n" +
        _diff("keep.py", 1, 3, ["KEEP_ME"])
    )
    block2 = _diff("m.py", 1, 4, ["LIVE_M"])
    m = CE._find_superseded_diff_parts([block1, block2])
    # In block 0, only fileIndex 0 (m.py) is superseded; keep.py (index 1) stays.
    assert m == {0: {0}}


def test_ziya_nopos_shared_locator_supersedes():
    earlier = "\n".join([
        "diff --git a/n.py b/n.py",
        "--- a/n.py",
        "+++ b/n.py",
        "@@ -1,3 +1,4 @@ ZIYA_NOPOS my_func",
        " ctx",
        "+STALE_NOPOS",
        "+STALE_NOPOS2",
    ])
    later = "\n".join([
        "diff --git a/n.py b/n.py",
        "--- a/n.py",
        "+++ b/n.py",
        "@@ -1,3 +1,4 @@ ZIYA_NOPOS my_func",
        " ctx",
        "+LIVE_NOPOS",
    ])
    assert CE._find_superseded_diff_parts([earlier, later]) == {0: {0}}


def test_ziya_nopos_different_locator_independent():
    earlier = "\n".join([
        "diff --git a/n.py b/n.py",
        "+++ b/n.py",
        "@@ -1,3 +1,4 @@ ZIYA_NOPOS func_a",
        "+ADD_A",
    ])
    later = "\n".join([
        "diff --git a/n.py b/n.py",
        "+++ b/n.py",
        "@@ -1,3 +1,4 @@ ZIYA_NOPOS func_b",
        "+ADD_B",
    ])
    assert CE._find_superseded_diff_parts([earlier, later]) == {}


# ---------------------------------------------------------------------------
# strip-level golden cases: _strip_superseded_diffs preserves content.
# ---------------------------------------------------------------------------

def test_strip_removes_stale_keeps_live_and_prose():
    earlier = _diff("app.py", 1, 4, ["STALE_ADD", "STALE_ADD2"])
    later = _diff("app.py", 1, 4, ["LIVE_ADD"])
    content = f"Intro prose.\n\n{_fence(earlier)}\n\nWait, corrected:\n\n{_fence(later)}\n\nClosing prose."
    out = CE._strip_superseded_diffs(content)
    assert "STALE_ADD" not in out
    assert "LIVE_ADD" in out
    assert "Intro prose." in out
    assert "Closing prose." in out
    assert "Wait, corrected:" in out
    assert out.count("diff --git") == 1


def test_strip_multi_file_fence_keeps_surviving_section():
    block1 = (
        _diff("m.py", 1, 4, ["STALE_M", "STALE_M2"]) + "\n" +
        _diff("keep.py", 1, 3, ["KEEP_ME"])
    )
    block2 = _diff("m.py", 1, 4, ["LIVE_M"])
    content = f"{_fence(block1)}\n\n{_fence(block2)}"
    out = CE._strip_superseded_diffs(content)
    assert "STALE_M" not in out
    assert "KEEP_ME" in out   # unrelated file in same fence survives
    assert "LIVE_M" in out


def test_strip_is_noop_without_supersession():
    a = _diff("x.py", 1, 3, ["ADD_X"])
    b = _diff("y.py", 1, 3, ["ADD_Y"])
    content = f"{_fence(a)}\n\n{_fence(b)}"
    out = CE._strip_superseded_diffs(content)
    assert "ADD_X" in out and "ADD_Y" in out


# ---------------------------------------------------------------------------
# End-to-end: the shared hygiene fixture exports only the live diff.
# ---------------------------------------------------------------------------

def test_superseded_fixture_export_passes_hygiene_check():
    msgs = fixture.make_superseded_diff_conversation()
    md = export_conversation_for_paste(msgs, format_type="markdown")["content"]
    res = C.check_no_superseded_diffs(md)
    assert res.passed, res.failures
    assert res.measurements["superseded_add_count"] == 0
    assert res.measurements["final_add_count"] == 1
    assert res.measurements["diff_git_sections"] == 1
    # Surrounding prose must not be collateral-damaged.
    m = fixture.SUPERSESSION_MARKERS
    assert m["intro"] in md
    assert m["closing"] in md


def test_superseded_strip_does_not_touch_canonical_fixture():
    # The canonical fixture has a single (non-superseded) diff; it must survive.
    for name, msgs in fixture.all_variants().items():
        md = export_conversation_for_paste(msgs, format_type="markdown")["content"]
        r = C.check_no_superseded_diffs(md)
        # No stale marker exists in canonical, and its single diff is retained.
        assert r.measurements["superseded_add_count"] == 0
        assert r.measurements["diff_git_sections"] >= 1
