"""The Repeat-with-``until`` iteration cap must mean one thing, not three.

The defect: three places describe the same concept and none of them
agree.

  * ``RepeatBlockEditor.tsx`` displays ``block.repeat_max ?? 3`` for
    until-mode, so an author who never touches the field reads "3".
  * ``_plan_iterations`` computes ``int(block.repeat_max or 1)``, so that
    same block plans exactly ONE iteration.
  * the sibling Until BLOCK uses ``int(block.until_max or 5)`` and its
    validator says so out loud ("defaults to 5 iterations").

So the editor promises three passes, the runtime performs one, and the
adjacent primitive would have done five.  The author is not told any of
this, and the mode is silently inert: a Repeat whose cap is 1 evaluates
its until-condition after the only iteration it will ever run, so the
condition cannot cause a second pass.  "Until" that cannot loop is
indistinguishable from ``count=1``.

The fix deliberately does NOT change the iteration count.  Raising the
runtime default would change spend on every existing until-mode Repeat
that never set a cap, silently, which is the same class of harm as the
scope-loss bug this file's sibling covers.  Instead the displayed number
is corrected to the truth and the degenerate default is surfaced at
launch, where the author can act on it.

Assertions here pin, in order: the runtime number (so a later change to
it is a conscious one), the launch-time warning, and the cross-language
agreement between the editor's displayed default and the runtime's real
one -- the seam where the three numbers diverged in the first place.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agents.block_executor import ExecutionContext, _plan_iterations
from app.models.task_card import Block
from app.utils.task_card_validation import validate_card_tree

#: The effective number of iterations an until-mode Repeat plans when no
#: cap is authored.  Asserted against the runtime below rather than
#: assumed, and re-used as the value the editor must display.
EFFECTIVE_UNTIL_DEFAULT = 1

EDITOR = Path("frontend/src/components/TaskCard/RepeatBlockEditor.tsx")


def _ctx() -> ExecutionContext:
    return ExecutionContext(run_id="r-until", project_id="p", project_root="/tmp")


def _until_repeat(cap: int | None) -> Block:
    return Block(
        block_type="repeat", id="b-untilrepeat", name="retry until clean",
        repeat_mode="until",
        repeat_max=cap,
        repeat_until="ALL CLEAN",
        body=[Block(block_type="task", id="b-body", name="attempt",
                    instructions="try the thing")],
    )


def _messages(result) -> list[str]:
    return [f.message for f in result.warnings] + [
        f.message for f in result.errors
    ]


def _cap_warnings(result) -> list[str]:
    """Warnings about the until-mode cap defaulting, not other findings.

    Anchored on the conjunction a cap-default warning satisfies: it names
    ``repeat_max`` and says something about the default or about running
    once.  The for_each scope-loss warning also names ``repeat_max`` but
    claims items are "never run", and is excluded by requiring the
    until-mode vocabulary instead.
    """
    out = []
    for msg in _messages(result):
        low = msg.lower()
        if "repeat_max" in low and ("default" in low or "once" in low):
            out.append(msg)
    return out


class TestTheRuntimeNumberIsPinned:
    """What actually happens, so a change to it must be deliberate."""

    def test_uncapped_until_plans_exactly_one_iteration(self):
        plan = _plan_iterations(_until_repeat(None), _ctx())
        assert len(plan) == EFFECTIVE_UNTIL_DEFAULT, (
            "the effective until-mode default changed; if that was "
            "intended, EFFECTIVE_UNTIL_DEFAULT and the editor's displayed "
            "default must both follow it"
        )

    def test_zero_cap_is_also_one_not_zero(self):
        """0 is not an 'uncapped' opt-out in until mode, unlike for_each.

        Without this the editor's ``|| 1`` coercion on 0 could be called a
        bug and 'fixed' into passing 0 through, which would plan no
        iterations at all and make the block silently do nothing.
        """
        assert len(_plan_iterations(_until_repeat(0), _ctx())) == 1

    def test_an_explicit_cap_is_honoured(self):
        """Negative control: the default is a default, not a ceiling."""
        assert len(_plan_iterations(_until_repeat(4), _ctx())) == 4


class TestLaunchWarnsThatUntilCannotLoop:
    """The degenerate default must be visible before anything is spent."""

    def test_uncapped_until_mode_warns(self):
        res = validate_card_tree(_until_repeat(None))
        assert _cap_warnings(res), (
            "an until-mode Repeat with no repeat_max runs once, so its "
            f"until-condition can never loop; got: {_messages(res)}"
        )

    def test_warning_names_the_real_number(self):
        found = _cap_warnings(validate_card_tree(_until_repeat(None)))
        assert any(str(EFFECTIVE_UNTIL_DEFAULT) in m for m in found), (
            "the warning must name the effective iteration count, "
            "otherwise it repeats the ambiguity it exists to resolve"
        )

    def test_warning_is_not_an_error(self):
        """A one-shot until-Repeat is legal; it must not block a launch."""
        res = validate_card_tree(_until_repeat(None))
        assert res.ok, f"launch was blocked: {res.summary()}"

    def test_explicit_cap_does_not_warn(self):
        """Negative control - without this, warning on every Repeat passes."""
        res = validate_card_tree(_until_repeat(3))
        assert not _cap_warnings(res), (
            f"an authored cap needs no warning; got: {_messages(res)}"
        )

    def test_count_mode_does_not_warn(self):
        """repeat_max is meaningless in count mode; nothing to say about it."""
        block = Block(
            block_type="repeat", id="b-count", name="three times",
            repeat_mode="count", repeat_count=3,
            body=[Block(block_type="task", id="b-b", name="x",
                        instructions="do it")],
        )
        assert not _cap_warnings(validate_card_tree(block))

    def test_for_each_mode_does_not_emit_the_until_warning(self):
        """The two cap warnings must not collide.

        for_each has its own scope-loss warning; emitting the until-mode
        "runs once" text there would be actively wrong, since an uncapped
        for_each runs its whole roster.
        """
        block = Block(
            block_type="repeat", id="b-fe", name="fan out",
            repeat_mode="for_each", repeat_for_each_source='["a", "b"]',
            body=[Block(block_type="task", id="b-b", name="x",
                        instructions="do {{item}}")],
        )
        assert not _cap_warnings(validate_card_tree(block))


class TestEditorAgreesWithRuntime:
    """The seam: the number shown and the number used are one number.

    A pure-frontend test can assert what the editor displays and a pure
    backend test can assert what the runtime plans, and both can pass
    while the two disagree -- which is exactly the state this file was
    written for.  Reading the displayed default out of the source and
    comparing it to the measured runtime default is the only assertion
    here that can fail when the halves drift apart.
    """

    def _until_branch(self) -> str:
        """The editor's until-mode JSX, located by identifier not line.

        Scoped to the region between the two mode guards so the for_each
        cap input -- which legitimately defaults to 0, meaning uncapped --
        cannot be mistaken for the until-mode control.
        """
        if not EDITOR.exists():
            pytest.skip(f"{EDITOR} not present in this checkout")
        src = EDITOR.read_text()
        start = src.find("mode === 'until'")
        assert start != -1, "until-mode branch not found in the editor"
        end = src.find("mode === 'for_each'", start)
        return src[start:end if end != -1 else len(src)]

    def test_displayed_default_matches_the_runtime_default(self):
        branch = self._until_branch()
        m = re.search(r"repeat_max\s*\?\?\s*(\d+)", branch)
        assert m, (
            "no `repeat_max ?? N` fallback in the until-mode branch - if "
            "the control was restructured, update this assertion to match"
        )
        shown = int(m.group(1))
        assert shown == EFFECTIVE_UNTIL_DEFAULT, (
            f"the editor shows {shown} iterations for an uncapped "
            f"until-mode Repeat, but the runtime plans "
            f"{EFFECTIVE_UNTIL_DEFAULT}. The author is being told a "
            f"number the run will not honour."
        )
