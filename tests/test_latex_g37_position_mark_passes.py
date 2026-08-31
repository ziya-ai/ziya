"""G-37 / D-043: position-mark bodies must get a mandatory second LaTeX pass.

chemfig ``\\chemmove`` electron-pushing arrows (and TikZ ``remember picture``
overlays) resolve coordinates recorded in the ``.aux`` on the PREVIOUS run, so
they need two compilation passes.  The renderer previously only re-ran the
engine when the log contained LaTeX's "Rerun to get cross-references right"
message -- but pgf records mark positions with its own aux plumbing and never
emits that string, so the loop stopped after one pass and the arrow was drawn
from a default position or not at all (measured: zero red pixels).

The fix adds a ``min_passes`` floor derived from ``requires_position_marks`` and
threads it through ``_compile`` via the pure ``_needs_another_pass`` policy
helper.  These tests exercise that helper directly (no TeX install required);
the end-to-end re-render is verified at the shared build+render stage.
"""

from app.services.latex_profiles import requires_position_marks
from app.services.latex_renderer import LatexRenderer


class TestNeedsAnotherPass:
    """Pure unit tests for the pass-count policy (D-043)."""

    # ---- the fix: a min_passes floor forces the documented second pass ----

    def test_floor_forces_second_pass_without_rerun_signal(self):
        # A position-mark body's log has NO "Rerun to get" line, yet the fix
        # must still request a second pass.  This is the assertion that FAILS
        # against the old signal-only loop (which had no floor at all).
        assert LatexRenderer._needs_another_pass(
            "This is page 1.\nNo rerun request here.\n",
            passes_done=1, min_passes=2) is True

    def test_floor_satisfied_stops_after_second_pass(self):
        assert LatexRenderer._needs_another_pass(
            "No rerun request here.\n", passes_done=2, min_passes=2) is False

    # ---- direction check: min_passes=1 reproduces the OLD behaviour ----
    # A test that would ALSO pass against unpatched code certifies the bug, not
    # the fix.  With min_passes=1 (every non-position-mark body) the identical
    # log must NOT trigger a rerun -- proving the floor, not some incidental
    # change, is what makes the position-mark case rerun.

    def test_no_floor_no_rerun_for_ordinary_body(self):
        assert LatexRenderer._needs_another_pass(
            "This is page 1.\nNo rerun request here.\n",
            passes_done=1, min_passes=1) is False

    def test_latex_rerun_signal_still_honoured(self):
        # The pre-existing trigger (\label/\ref, tikz-cd) must keep working
        # regardless of the floor.
        log = "LaTeX Warning: Label(s) may have changed. Rerun to get " \
              "cross-references right.\n"
        assert LatexRenderer._needs_another_pass(
            log, passes_done=1, min_passes=1) is True

    def test_floor_never_below_signal(self):
        # Once the floor is met, a genuine rerun request still wins.
        log = "Rerun to get cross-references right.\n"
        assert LatexRenderer._needs_another_pass(
            log, passes_done=2, min_passes=2) is True


class TestPositionMarkFloorWiring:
    """The floor is derived from ``requires_position_marks`` (D-043)."""

    def test_chemmove_body_flagged(self):
        body = r"\chemmove{\draw[->](a)..controls +(1,1)..(b);}"
        assert requires_position_marks(body) is True
        # render() maps this to min_passes = 2.
        assert (2 if requires_position_marks(body) else 1) == 2

    def test_remember_picture_body_flagged(self):
        body = r"\begin{tikzpicture}[remember picture, overlay] \end{tikzpicture}"
        assert requires_position_marks(body) is True

    def test_ordinary_body_stays_single_pass(self):
        body = r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"
        assert requires_position_marks(body) is False
        assert (2 if requires_position_marks(body) else 1) == 1
