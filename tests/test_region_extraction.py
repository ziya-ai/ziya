"""
Canonical direct-unit suite for app.hallucination.region_extraction.

WHY THIS FILE EXISTS
--------------------
The fence-parsing surface historically grew three separate authorities, each
with its own test file exercising it *indirectly* through a higher-level
consumer:

  * A  = _FENCE_RE / open_fence_at / extract_scannable_regions
         (CommonMark-faithful; ``` AND ~~~, indentation-aware, a close line
         may carry trailing content). Tested indirectly via the sanitizer,
         parroting, and continuation-gate suites.
  * B  = StreamingToolExecutor._update_code_block_tracker (incremental line
         state machine). Tested in test_code_block_tracker.py.
  * C  = the fake-shell detector's fence walk. Historically a private regex;
         now delegated to extract_fenced_regions() in this module. Tested
         indirectly via test_hallucination_detection.py.

When C's private regex was collapsed onto the shared extract_fenced_regions()
primitive, that primitive (and the FencedRegion NamedTuple it returns) had
ZERO direct unit coverage -- it was only ever exercised through detector
assertions. This file closes that gap: it tests the primitives DIRECTLY, and
pins the deliberate grammar divergence between A and C as an explicit
contract so a future "unify onto one CommonMark dialect" change fails loudly
here instead of silently altering detector behavior.

Map of the fence/hallucination regression surface (see also
Docs/fence-awareness-step0-findings.md):

  test_region_extraction.py        <- THIS FILE: A & C primitives, A/C contract
  test_continuation_fence_state.py <- open_fence_at (A) + continuation gate
  test_code_block_tracker.py       <- tracker (B) + A/B contract
  test_hallucination_detection.py  <- detect_fake_shell_session (C), shingles
  test_fake_shell_structure_gate.py<- C inner-fence structural skip
  test_sanitize_assistant_text.py  <- sanitizer (consumes A)
"""

from __future__ import annotations

import pytest

from app.hallucination.region_extraction import (
    FencedRegion,
    extract_fenced_regions,
    extract_scannable_regions,
    open_fence_at,
    scannable_line_indices,
    scannable_text,
)

BT3 = "`" * 3
BT4 = "`" * 4
TIL = "~" * 3


# ---------------------------------------------------------------------------
# extract_fenced_regions (C-grammar primitive) -- previously ZERO direct tests
# ---------------------------------------------------------------------------
class TestExtractFencedRegions:
    """Direct coverage of the detector-compat fence walk."""

    def test_simple_closed_fence(self):
        text = f"pre\n{BT3}bash\n$ ls\nout\n{BT3}\npost"
        regions = extract_fenced_regions(text)
        assert len(regions) == 1
        r = regions[0]
        assert r.opening_line == f"{BT3}bash"
        assert r.marker == BT3
        assert r.closed is True
        # Body runs from the line after the opener up to (exclusive) the
        # close line, and INCLUDES the trailing newline before the close.
        assert r.body == "$ ls\nout\n"

    def test_unclosed_trailing_fence_is_emitted(self):
        # The streaming case: an open fence with no close yet must still be
        # returned (closed=False) with a body running to end of text. This is
        # load-bearing -- the detector fires on in-progress fabrication.
        text = f"pre\n{BT3}bash\n$ ls\nout"
        regions = extract_fenced_regions(text)
        assert len(regions) == 1
        assert regions[0].closed is False
        assert regions[0].body == "$ ls\nout"

    def test_opener_with_no_newline_is_not_emitted(self):
        # No body has arrived yet -- nothing to scan, so no region.
        assert extract_fenced_regions(f"pre\n{BT3}bash") == []

    def test_tilde_fence_ignored_by_c_grammar(self):
        # C-grammar is backticks-only by design (see module NOTE). A tilde
        # fence is invisible here. This asymmetry vs A is pinned in
        # TestACgrammarContract below.
        assert extract_fenced_regions(f"{TIL}bash\n$ ls\n{TIL}") == []

    def test_indented_fence_ignored_by_c_grammar(self):
        # C-grammar requires the fence at column 0 (no leading indent).
        assert extract_fenced_regions(f"    {BT3}bash\n$ ls\n    {BT3}") == []

    def test_wider_fence_wraps_narrower_as_content(self):
        # A 4-backtick fence is closed only by 4+ backticks; the inner 3-tick
        # lines are inert body content (CommonMark width discipline).
        text = f"{BT4}\n{BT3}\ninner\n{BT3}\n{BT4}"
        regions = extract_fenced_regions(text)
        assert len(regions) == 1
        assert regions[0].marker == BT4
        assert regions[0].closed is True
        assert regions[0].body == f"{BT3}\ninner\n{BT3}\n"

    def test_two_separate_blocks(self):
        text = f"{BT3}\na\n{BT3}\nmid\n{BT3}\nb\n{BT3}"
        regions = extract_fenced_regions(text)
        assert len(regions) == 2
        assert regions[0].body == "a\n"
        assert regions[1].body == "b\n"
        # 'mid' sits between the two fences and belongs to neither region.
        assert all("mid" not in r.body for r in regions)

    def test_close_line_with_trailing_content_is_body_not_close(self):
        # C requires a close line to be backticks-then-whitespace only. A
        # second ```bash line therefore does NOT close the block under C --
        # it stays as body content. (A would treat it as a close; see the
        # contract test.) This is the exact grammar point that makes the
        # detector fire on the inner shell fabrication.
        text = f"{BT3}bash\n$ ls\n{BT3}bash\nmore\n{BT3}"
        regions = extract_fenced_regions(text)
        assert len(regions) == 1
        assert regions[0].body == f"$ ls\n{BT3}bash\nmore\n"

    def test_empty_text(self):
        assert extract_fenced_regions("") == []

    def test_no_fences(self):
        assert extract_fenced_regions("just prose\nwith no fences\n") == []


class TestFencedRegionShape:
    """The FencedRegion NamedTuple contract (previously ZERO tests)."""

    def test_is_namedtuple_with_named_fields(self):
        (r,) = extract_fenced_regions(f"{BT3}\nx\n{BT3}\n")
        assert isinstance(r, FencedRegion)
        # positional / named access agree
        opening_line, marker, body, closed = r
        assert r.opening_line == opening_line
        assert r.marker == marker
        assert r.body == body
        assert r.closed == closed

    def test_fields_are_exactly_the_documented_four(self):
        assert FencedRegion._fields == ("opening_line", "marker", "body", "closed")


# ---------------------------------------------------------------------------
# A <-> C grammar contract.
#
# A (open_fence_at, CommonMark) and C (extract_fenced_regions, detector-compat)
# now AGREE on close-line semantics and deliberately disagree on TWO points
# (tilde fences, indentation). This class pins each so that any future attempt
# to unify the module onto a single dialect must consciously update this
# contract rather than silently change detector behavior.
#
# History: close-line semantics used to be the load-bearing divergence -- A
# allowed a close line to carry trailing content (so a second ```lang line
# CLOSED the current fence), whereas C required backticks-then-whitespace
# only. The A-unification (#1) tightened A (via _is_fence_close) to match C,
# so both now treat a ```lang line as OPEN-only. The detector's ability to
# catch a fabricated shell block nested after real content depends on that
# (now shared) stricter close rule.
# ---------------------------------------------------------------------------
class TestACgrammarContract:
    """Pin the A/C fence-grammar relationship: shared close rule, and the
    two intentional divergences (tilde, indent)."""

    def test_infostring_close_C_keeps_one_region(self):
        # C: ```bash is not a valid close -> the whole thing is ONE closed
        # python region whose body contains the fabricated shell session.
        text = f"{BT3}python\ndef foo(): return 42\n{BT3}bash\n$ evil\n{BT3}"
        regions = extract_fenced_regions(text)
        assert len(regions) == 1
        assert regions[0].closed is True
        assert regions[0].opening_line == f"{BT3}python"
        assert f"{BT3}bash\n$ evil" in regions[0].body

    def test_infostring_close_now_unified_with_C(self):
        # Post-#1: A no longer treats ```bash as a close (a close line may
        # carry no info string), so the python fence stays open through
        # "$ evil" and the final bare ``` closes it -> nothing open at end
        # of text -> None. This now MATCHES C's one-closed-region reading
        # above; the info-string-close divergence has been eliminated.
        text = f"{BT3}python\ndef foo(): return 42\n{BT3}bash\n$ evil\n{BT3}"
        assert open_fence_at(text, len(text)) is None

    def test_tilde_divergence(self):
        # C ignores tilde fences entirely; A treats ~~~ as a real fence.
        text = f"{TIL}\nin\n{TIL}"
        assert extract_fenced_regions(text) == []           # C: nothing
        # A: position just after the opener line is inside the tilde fence.
        assert open_fence_at(text, text.find("in")) == TIL   # A: fenced

    def test_indent_divergence(self):
        # C requires column-0 fences; A's scannable extractor treats an
        # indented block as non-scannable (code) too, but via a different
        # rule. Here we pin only the C side: indented backtick fence -> [].
        assert extract_fenced_regions(f"    {BT3}\nin\n    {BT3}") == []


# ---------------------------------------------------------------------------
# Light sanity net for the scannable-region API co-located here so the whole
# region_extraction module has a single canonical direct-test home. Deeper
# scannable behavior lives in test_hallucination_detection.TestRegionExtraction
# and the sanitizer suite; these guard the module's public entry points.
# ---------------------------------------------------------------------------
class TestScannableApiSanity:
    def test_scannable_line_indices_skips_fenced_lines(self):
        text = f"line0\n{BT3}\ncode1\n{BT3}\nline4"
        assert scannable_line_indices(text) == [(0, "line0"), (4, "line4")]

    def test_scannable_text_joins_regions_with_newlines(self):
        text = f"line0\n{BT3}\ncode1\n{BT3}\nline4"
        assert scannable_text(text) == "line0\n\nline4"

    def test_extract_scannable_regions_excludes_fence_body(self):
        text = f"prose\n{BT3}\nsecret\n{BT3}\nmore"
        joined = "".join(extract_scannable_regions(text))
        assert "secret" not in joined
        assert "prose" in joined and "more" in joined


# ---------------------------------------------------------------------------
# CHARACTERIZATION LOCK for the scannable consumers on the fence-grammar
# boundary that the planned A-unification (#1) will move.
#
# extract_scannable_regions (sanitizer) and scannable_line_indices
# (truncation) both consume A's shared _FENCE_RE close-branch, which today
# is PERMISSIVE: a close line may carry trailing content, so a second
# ```lang line CLOSES the current fence. #1 tightens that branch to
# CommonMark (a close line is backticks-then-whitespace only), which moves
# content sitting after a ```lang info-string line from "scannable" to
# "inside the fence". Neither consumer had a test on this boundary, so the
# change would otherwise be SILENT. These tests pin current behavior; when
# #1 lands they must flip and be updated in the same change (documenting the
# delta), never edited away silently.
# ---------------------------------------------------------------------------
class TestScannableInfoStringCloseCharacterization:
    """Post-#1 behavior of the scannable consumers on info-string close
    lines, tilde fences, and indented fences. Load-bearing regression lock.

    The A-unification (#1) tightened A's close-branch (via _is_fence_close)
    so a close line may carry NO info string. A ```lang line can therefore
    only OPEN, never close, so content after it stays INSIDE the fence and
    is NON-scannable. These two ``_CURRENT``-named tests flipped when #1
    landed and were updated here to assert the new semantics; the control
    tests below were unaffected, as designed."""

    def test_infostring_close_reopens_scannable_region_CURRENT(self):
        # ```python ... ```bash $ ls out ```  -- post-#1 A treats ```bash as
        # an OPEN-only info-string line, NOT a close, so the python fence
        # stays open and "$ ls\nout" is INSIDE it -> NON-scannable. (Pre-#1
        # this content was scannable because ```bash closed the fence.)
        text = f"{BT3}python\nx=1\n{BT3}bash\n$ ls\nout\n{BT3}\n"
        joined = "".join(extract_scannable_regions(text))
        assert "$ ls" not in joined and "out" not in joined, (
            "post-#1: a ```lang line cannot close a fence, so trailing "
            "content stays inside and is NON-scannable. If this flips back "
            "to scannable, the close-branch permissiveness regressed."
        )

    def test_infostring_close_line_indices_CURRENT(self):
        # Same input via the truncation consumer: post-#1 the two output
        # lines are INSIDE the still-open python fence, so they are NOT
        # reported as scannable line indices.
        text = f"{BT3}python\nx=1\n{BT3}bash\n$ ls\nout\n{BT3}\n"
        idx = scannable_line_indices(text)
        scannable_lines = [ln for _, ln in idx]
        assert "$ ls" not in scannable_lines and "out" not in scannable_lines, (
            "post-#1 lock: info-string close no longer exposes trailing "
            "lines to truncation scanning; they stay inside the fence."
        )

    def test_bare_close_unaffected_by_planned_change(self):
        # Control: a NORMAL bare ``` close is unaffected by #1. This must
        # stay green before AND after -- if it flips, #1 broke something it
        # should not have.
        text = f"{BT3}\nsecret\n{BT3}\nafter\n"
        joined = "".join(extract_scannable_regions(text))
        assert "secret" not in joined
        assert "after" in joined

    def test_tilde_fence_scannable_CURRENT(self):
        # extract_scannable_regions ALREADY understands ~~~ (unlike the
        # C-grammar detector primitive). #1 does not change the scannable
        # side here; this pins that the tilde body is excluded today.
        text = f"{TIL}\n$ ls\nfile\n{TIL}\nafter\n"
        joined = "".join(extract_scannable_regions(text))
        assert "$ ls" not in joined
        assert "after" in joined

    def test_indented_fence_body_already_excluded_via_indent_rule(self):
        # The scannable extractor excludes indented lines via _INDENT_BLOCK_RE
        # regardless of fences, so an indented fake session is already
        # non-scannable. #1's C-side indent widening does not regress this.
        text = f"    {BT3}\n    $ ls\n    file\n"
        joined = "".join(extract_scannable_regions(text))
        assert "$ ls" not in joined

    def test_width_wider_close_current(self):
        # 4-tick fence closed only by 4-tick line; inner 3-tick line is body.
        # Width discipline is unchanged by #1 -- pin it as a control.
        text = f"{BT4}\nbody\n{BT3}\nstill\n{BT4}\nafter\n"
        joined = "".join(extract_scannable_regions(text))
        assert "body" not in joined and "still" not in joined
        assert "after" in joined


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
