"""
Tests for the ``sweep`` task's commit-grouping discipline (Step 2 / Step 3).

Why this file exists
--------------------
Every other step of the ~250-line sweep prompt is a decision procedure --
Step 5's version resolution is a four-branch tree with explicit rollover
handling -- while commit grouping was four sentences of exhortation:

    Do not un-naturally group unrelated changes together to minimize number
    of commits. ... the number of target commits may be hundreds long.

That states the goal and supplies no way to tell whether it was met, so the
failure is silent and only visible in ``git log`` weeks later.  Measured on
the v0.8.6.1 release:

    release     changelog entries   commits   entries/commit
    v0.8.3.0                   89       118              0.8
    v0.8.4.0                   48        48              1.0
    v0.8.5.0                   93        36              2.6
    v0.8.6.1                   63        20              3.1

63 logical changes landed as 20 commits whose own subject lines named 65
distinct changes between them -- ``feat(task-cards): concurrency limits,
pre-launch validation, self-improve, output gathering, hold surfacing,
run-status indicators, resume-banking fixes`` is seven commits wearing one
subject, and it touched 120 files.  The announced "20 commits since
v0.8.5.1" was an accurate report of a real under-grouping, not a miscount.

What is pinned here
-------------------
The prompt is a text artifact, so obedience cannot be asserted -- only that
the mechanism is present and that the superseded, unmeasurable phrasing is
gone rather than merely outvoted by new text elsewhere in the file:

  1. Grouping is keyed on the ``## [Unreleased]`` changelog entries, which
     already exist at Step 2 time, rather than on subsystem or directory.
     Keying on subsystem is what produces one omnibus commit per subsystem.
  2. A subject-line test exists, so "names several changes" is detectable
     rather than a matter of taste.
  3. A count check against the entry count exists, so a coarse grouping is
     caught before committing instead of after tagging.
  4. Step 3's combine escape hatch requires hunk-level impossibility, not
     the mere fact that two changes share a file.
"""

import re
from pathlib import Path

import pytest
import yaml

TASKS_PATH = Path(".ziya/tasks.yaml")

pytestmark = pytest.mark.skipif(
    not TASKS_PATH.exists(),
    reason="run from the repository root",
)


@pytest.fixture(scope="module")
def sweep_prompt() -> str:
    data = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
    return data["sweep"]["prompt"]


@pytest.fixture(scope="module")
def step2(sweep_prompt: str) -> str:
    """Step 2 only.  Scoped so a rule living in some other step cannot
    satisfy an assertion about the grouping step."""
    m = re.search(r"## Step 2 —.*?(?=## Step 3 —)", sweep_prompt, re.S)
    assert m, "Step 2 not found -- the prompt's step headings changed"
    return m.group(0)


@pytest.fixture(scope="module")
def step3(sweep_prompt: str) -> str:
    m = re.search(r"## Step 3 —.*?(?=## Step 4 —)", sweep_prompt, re.S)
    assert m, "Step 3 not found -- the prompt's step headings changed"
    return m.group(0)


# ---------------------------------------------------------------------------
# 1. The grouping key
# ---------------------------------------------------------------------------

def test_grouping_is_keyed_on_changelog_entries(step2: str):
    """The Unreleased section is accumulated during development, so at Step 2
    it is already the enumeration of logical changes in the release.  Using
    it as the key is what makes the commit count fall out of the work rather
    than out of how many subsystems the diff happens to span."""
    flat = " ".join(step2.split()).lower()
    assert "one commit per changelog entry" in flat
    assert "[unreleased]" in flat
    assert "grouping key" in flat


def test_grouping_is_not_keyed_on_subsystem(step2: str):
    """Grouping by subsystem is the mechanism of the observed failure: it
    yields exactly one commit per touched area regardless of how many
    distinct changes landed there."""
    flat = " ".join(step2.split()).lower()
    assert "do not derive the groups from directory or subsystem" in flat
    assert "omnibus commit per subsystem" in flat


def test_combining_requires_an_indivisible_edit(step2: str):
    """"Related" is not a licence to combine.  Without this the model can
    justify any bundle after the fact, which is what "logical groups" alone
    permitted."""
    flat = " ".join(step2.split()).lower()
    assert "only when one indivisible edit satisfies both" in flat
    assert "sharing a subsystem, a file, or a theme is not" in flat


def test_a_large_commit_count_is_stated_to_be_the_expected_outcome(step2: str):
    """The instinct being corrected is that many commits look untidy, so the
    prompt has to say plainly that a hundred-commit release is normal --
    otherwise minimizing count reads as good practice."""
    flat = " ".join(step2.split()).lower()
    assert "never combine entries to reduce the commit count" in flat
    assert "hundred-commit release is a normal outcome" in flat


# ---------------------------------------------------------------------------
# 2. The detectors
# ---------------------------------------------------------------------------

def test_entry_list_is_a_floor_not_the_whole_roster(step2: str):
    """Keying grouping on changelog entries introduces a gap the previous
    subsystem-shaped grouping did not have: Step 4 is what authors missing
    entries, and it runs AFTER committing, so a change present in the diff
    but not yet in the Unreleased section has no key.  Read literally, "one
    commit per changelog entry" orphans it -- either uncommitted or folded
    into a neighbour.  The floor rule closes that, and it is the seam most
    likely to be lost in a later edit because both halves read correctly on
    their own."""
    flat = " ".join(step2.split()).lower()
    assert "floor, not the whole roster" in flat
    assert "with no unreleased entry yet still gets its own group" in flat
    assert "step 4 is what authors the missing entry" in flat


def test_subject_test_makes_bundling_detectable(step2: str):
    """A subject naming N distinct changes IS N commits.  The carve-out
    matters as much as the rule: a list of FILES one change touched is not a
    list of changes, and without that distinction the rule would forbid a
    legitimate ``docs:`` commit spanning several files."""
    flat = " ".join(step2.split()).lower()
    assert "subject test" in flat
    assert "stop at its first comma" in flat
    # the carve-out
    assert "listing the files or targets that one change touched is fine" in flat
    # the worked negative, taken verbatim from the release that motivated this
    assert "five commits wearing one subject" in flat


def test_count_check_runs_before_committing(step2: str):
    """Detection after tagging is useless -- the history is already written.
    The check has to gate the commit loop, and it has to be reported so a
    coarse grouping is visible in the run output."""
    flat = " ".join(step2.split()).lower()
    assert "count check before you start committing" in flat
    assert "materially fewer means the grouping is too coarse" in flat
    assert "report the ratio" in flat


def test_count_check_carries_measured_reference_ratios(step2: str):
    """A threshold with no evidence is a guess the model can rationalize
    past.  These ratios are measured from this repo's own history, which is
    what makes "materially fewer" answerable."""
    assert "89 entries / 118 commits" in step2
    assert "48 / 48" in step2
    assert "63 entries into 20 commits" in step2


# ---------------------------------------------------------------------------
# 3. The escape hatch
# ---------------------------------------------------------------------------

def test_same_file_is_not_grounds_for_combining(step3: str):
    """Separate hunks in one file stage independently via ``git add -p``, so
    "they're in the same file" is not impossibility -- it is the ordinary
    case.  Left unqualified it is the cheapest available justification for
    any omnibus commit."""
    flat = " ".join(step3.split()).lower()
    assert "sharing a file is not impossibility" in flat
    assert "same hunk carries both changes" in flat


def test_superseded_unconditional_escape_hatch_is_gone(step3: str):
    """"If clean separation is genuinely impossible, combine them" set no
    evidentiary bar: impossibility was asserted, never shown.  It must be
    absent, not merely contradicted by a stricter rule sitting next to it --
    a prompt that says both leaves the model free to follow either."""
    flat = " ".join(step3.split()).lower()
    assert "genuinely impossible" not in flat


def test_superseded_unmeasurable_grouping_prose_is_gone(step2: str):
    """The replaced wording ("do not un-naturally group", "make commit
    groupings that make sense") is unfalsifiable: any grouping can be called
    natural and sensible after the fact.  Keeping it alongside the mechanism
    would re-license the outcome the mechanism exists to prevent."""
    flat = " ".join(step2.split()).lower()
    assert "un-naturally group" not in flat
    assert "make commit groupings that make sense" not in flat
    assert "squashing a bunch of stuff together" not in flat


# ---------------------------------------------------------------------------
# 4. Ordering — the key must be readable when the grouping happens
# ---------------------------------------------------------------------------

def test_changelog_is_readable_at_grouping_time(sweep_prompt: str):
    """Step 2 keys on the Unreleased section while Step 4 is what verifies
    and rewrites it, so the dependency runs backwards through the prompt.
    That is sound only because the section is accumulated during development
    rather than authored by this task -- Step 4 says so, and if that ever
    changes the Step 2 key evaporates with nothing else to catch it.

    NOT a regression test, and deliberately vacuous against the pre-change
    prompt: the precondition it guards predates the grouping fix and passed
    before it.  It is here because Step 2's grouping key silently depends on
    that precondition, and a later edit to Step 4 could remove it without
    touching Step 2 -- exactly the two-correct-halves failure that leaves
    both steps individually defensible and the key non-functional.
    """
    assert "have been accumulated" in sweep_prompt
    step4 = re.search(r"## Step 4 —.*?(?=## Step 5 —)", sweep_prompt, re.S)
    assert step4, "Step 4 not found"
    assert "[Unreleased]" in step4.group(0)
