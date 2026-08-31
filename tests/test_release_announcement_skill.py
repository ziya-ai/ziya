"""
Tests for the release-announcement skill and its wiring into the sweep task.

A skill is a prompt artifact, so its *content* cannot be asserted the way
code can.  What CAN be pinned, and what these tests pin, is everything that
made the previous announcements bad -- each of which was a structural
property, not a wording preference:

  1. The skill is discoverable at all.  A skill with a malformed frontmatter
     ``visibility`` silently degrades to ``user_selectable``, which means the
     model never sees it in its catalog and the sweep task's instruction to
     load it fails at run time with nothing in the diff to explain why.

  2. The sweep task actually routes through it.  The original Step 8
     specified the summary in a single sentence ("a concise quickly readable
     list of any major customer visible changes"), which is what produced
     leaf-instead-of-trunk summaries.  A regression here looks like a
     perfectly valid YAML file.

  3. Step ordering.  Slack ran BEFORE the GitHub Release, so the Slack post
     structurally could not link to the release and inlined a 67KB changelog
     instead.  Ordering is load-bearing, not cosmetic.

  4. The caps and the mrkdwn dialect are stated.  The observed posts
     overflowed Slack's message limit (splitting mid-code-fence) and emitted
     literal ``**folder**`` because standard markdown was assumed.
"""

import re
from pathlib import Path

import pytest
import yaml

SKILL_PATH = Path(".agents/skills/release-announcement/SKILL.md")
TASKS_PATH = Path(".ziya/tasks.yaml")

pytestmark = pytest.mark.skipif(
    not SKILL_PATH.exists() or not TASKS_PATH.exists(),
    reason="run from the repository root",
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_flat(skill_text: str) -> str:
    """Skill text with runs of whitespace collapsed to single spaces.

    The skill is hard-wrapped for readability, so a required phrase can span
    a line break.  Asserting against the raw text would make these tests
    fail on a reflow -- which is a formatting change, not a requirements
    change -- and would tempt loosening the phrase instead.
    """
    return re.sub(r"\s+", " ", skill_text)


@pytest.fixture(scope="module")
def sweep_prompt() -> str:
    data = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
    return data["sweep"]["prompt"]


# ---------------------------------------------------------------------------
# 1. Discoverability
# ---------------------------------------------------------------------------

def test_skill_is_discovered_by_the_project_scanner():
    """The scanner is the only thing that makes a skill reachable.

    A frontmatter typo does not raise -- the skill simply never appears, so
    the sweep task's ``get_skill_details`` call finds nothing.
    """
    from app.services.skill_discovery import discover_project_skills
    from app.services.token_service import TokenService

    found = discover_project_skills(
        str(Path.cwd()), TokenService(), load_body=False
    )
    assert "release-announcement" in {s.name for s in found}


def test_skill_is_model_discoverable():
    """Anything other than the exact string degrades to user_selectable.

    ``_normalize_visibility`` accepts only two literals and falls back
    silently, so a near-miss ("model-discoverable", "discoverable") hides the
    skill from the catalog without any error.
    """
    from app.services.skill_discovery import discover_project_skills
    from app.services.token_service import TokenService

    skill = next(
        s for s in discover_project_skills(
            str(Path.cwd()), TokenService(), load_body=False
        )
        if s.name == "release-announcement"
    )
    assert skill.visibility == "model_discoverable"


def test_skill_name_matches_its_directory():
    """The scanner validates name-vs-directory; a mismatch drops the skill."""
    fm = re.match(r"^---\n(.*?)\n---\n", SKILL_PATH.read_text(), re.S)
    assert fm, "frontmatter block is missing or malformed"
    assert yaml.safe_load(fm.group(1))["name"] == SKILL_PATH.parent.name


def test_skill_body_survives_progressive_disclosure(skill_text: str):
    """Stage-2 load must return a real body.

    The catalog lists frontmatter only; the body arrives on demand.  An empty
    body would let the model "activate" the skill and receive no rules.
    """
    body = re.sub(r"^---\n.*?\n---\n", "", skill_text, flags=re.S)
    assert len(body.strip()) > 2000


# ---------------------------------------------------------------------------
# 2. The sweep task routes through the skill
# ---------------------------------------------------------------------------

def test_sweep_loads_the_skill_by_name(sweep_prompt: str):
    assert "release-announcement" in sweep_prompt
    assert "get_skill_details" in sweep_prompt


def test_sweep_no_longer_carries_the_one_sentence_spec(sweep_prompt: str):
    """The original single-sentence brief is the root cause.

    Reintroducing it alongside the skill reference would silently win, since
    an inline instruction is read before any tool is called.
    """
    assert "concise quickly readable list" not in sweep_prompt


def test_sweep_does_not_ask_for_an_inline_changelog(sweep_prompt: str):
    """Inlining is what produced a 67KB snippet and a split code fence."""
    assert "full changelog section and commit summary" not in sweep_prompt


def test_sweep_forbids_inline_commit_hashes(sweep_prompt: str):
    lowered = sweep_prompt.lower()
    assert "do not paste commit hashes inline" in lowered


def test_sweep_reads_diffstat_for_new_modules(sweep_prompt: str):
    """New top-level modules are the trunk/leaf signal.

    Without this input the model can only rank by changelog order, which is
    exactly how a demo (chemistry) outranked its own engine (LaTeX).
    """
    assert "git diff --stat" in sweep_prompt


# ---------------------------------------------------------------------------
# 3. Step ordering
# ---------------------------------------------------------------------------

def test_github_release_precedes_slack(sweep_prompt: str):
    """Reversed, the Slack post cannot link to the release it describes."""
    steps = re.findall(r"^\s*## Step (\d+) — (.+)$", sweep_prompt, re.M)
    by_title = {title.strip(): int(num) for num, title in steps}
    assert by_title["GitHub Release"] < by_title["Notify Slack"]


def test_step_numbers_are_sequential_and_unique(sweep_prompt: str):
    """A duplicate or skipped number breaks Step 0's resume-point logic."""
    nums = [int(n) for n, _ in re.findall(r"^\s*## Step (\d+) — (.+)$",
                                          sweep_prompt, re.M)]
    assert nums == sorted(set(nums)) == list(range(min(nums), max(nums) + 1))


def test_best_effort_rules_name_the_current_step_numbers(sweep_prompt: str):
    """The trailing RULES block hardcodes step numbers.

    After the swap it must say GitHub=8 / Slack=9; the stale pairing would
    mark the wrong step best-effort and let a real failure pass silently.
    """
    assert "GitHub Release (step 8) and Slack (step 9)" in sweep_prompt
    assert "Slack (step 8) and GitHub Release (step 9)" not in sweep_prompt


# ---------------------------------------------------------------------------
# 4. Routing: highlights to channel, detail to thread, prose to interest
# ---------------------------------------------------------------------------

def test_highlights_go_to_the_channel_not_the_thread(sweep_prompt: str):
    """Previously the channel showed only "pushed to public", so the
    highlights required expanding a thread to see."""
    assert "CHANNEL, not the thread" in sweep_prompt


def test_both_slack_destinations_are_named(sweep_prompt: str):
    assert "#ziya-dev" in sweep_prompt
    assert "#ziya-interest" in sweep_prompt


def test_interest_channel_is_given_a_distinct_register(skill_flat: str):
    """That channel's voice is prose, not bullets.

    Without an explicit instruction the model reuses the dev bullets, which
    reads wrong for a non-engineering audience.
    """
    lowered = skill_flat.lower()
    assert "do not reuse the dev bullets" in lowered
    assert "no bullet lists" in lowered


def test_interest_channel_message_is_not_threaded(skill_text: str):
    assert "no thread" in skill_text.lower()


def test_skill_guards_against_claiming_unpublished_install_channels(
    skill_flat: str,
):
    """Tag-push does not imply toolbox/pip publication.

    Announcing availability that has not happened is a factual error, and it
    is the kind a summarizer makes readily because past posts said it.
    """
    assert "toolbox" in skill_flat.lower()
    # A tag-push job must be told publication has not happened yet.
    assert "separate later step" in skill_flat
    # And the publish job must be told to read a per-channel record rather
    # than trust an exit code — uploads warn non-fatally and still exit 0.
    assert "exits zero is not evidence" in skill_flat


# ---------------------------------------------------------------------------
# 5. Caps and dialect
# ---------------------------------------------------------------------------

def test_the_cap_is_on_length_not_on_item_count(skill_text: str):
    """The cap moved twice, and both moves matter.

    v1 capped bullet COUNT only and forbade trimming words, so a 251-char
    opening bullet met the cap.  v2 added a per-bullet length cap but KEPT a
    6-item ceiling, which is wrong for this repo in the other direction: a
    release routinely carries dozens of significant items, and a ceiling
    forces them to be dropped or merged away.  The cap is per line only.
    """
    assert "ONE TITLE PER ITEM" in skill_text
    assert "70 characters" in skill_text
    assert "NO CAP ON THE NUMBER OF ITEMS" in skill_text
    # Both superseded caps must be gone, not merely outvoted elsewhere.
    assert "1200" not in skill_text
    assert "6 bullets" not in skill_text
    assert "ONE CLAUSE PER BULLET" not in skill_text


def test_overflow_continues_and_never_drops_an_item(skill_flat: str):
    """A count ceiling and "cut the lowest-impact item" together mean a large
    release silently ships an incomplete announcement.  With no ceiling, the
    only legal response to a long list is another message."""
    flat = skill_flat.lower()
    assert "continuing into another message" in flat
    assert "never by lengthening" in flat
    assert "more in thread" in flat
    # Every superseded overflow rule must be absent.
    assert "pass 1 was not done" not in flat
    assert "do not solve an overflow by trimming words" not in flat
    assert "cut the lowest-impact item" not in flat


def test_a_title_must_be_rewritten_not_truncated(skill_flat: str):
    """Measured: the changelog's bolded leads run a median of 104 chars, and
    truncating each at its first comma still leaves 37 of 63 over the cap —
    so "use the lead sentence" is not an implementation of this rule.  The
    stump is also a correctness risk, not merely an ugly one."""
    flat = skill_flat.lower()
    assert "rewritten, not truncated" in flat
    assert "median of 104 characters" in flat
    assert "37 of 63" in flat
    assert "it is false" in flat


def test_skill_states_slack_mrkdwn_not_standard_markdown(skill_flat: str):
    """A generated post emitted literal ``**folder**``.

    Slack bold is single-asterisk, so the default markdown assumption is
    visibly wrong in the channel.
    """
    assert "*single asterisks*" in skill_flat
    assert "renders as literal asterisks" in skill_flat


# ---------------------------------------------------------------------------
# 6. The four passes, in order
# ---------------------------------------------------------------------------

def test_all_four_passes_are_present_and_ordered(skill_text: str):
    """Order is the mechanism: aggregating after ranking cannot fix a
    trunk/leaf inversion, because ranking already happened."""
    passes = re.findall(r"^## Pass (\d+) — (\w+)", skill_text, re.M)
    assert [(int(n), name) for n, name in passes] == [
        (1, "AGGREGATE"), (2, "AUDIENCE"), (3, "LABEL"), (4, "RANK"),
    ]


def test_label_is_a_prefix_and_never_a_sort_key(skill_flat: str):
    """Pass 3 used to be TIER — ordered buckets — which is itself a sort key
    and therefore silently overrode pass 4's "rank by impact".  A fix every
    user was being re-billed for landed at bullet four purely because its
    bucket was "Now works".  Bucketing must be forbidden, not merely
    de-emphasised, or the two passes keep contradicting each other."""
    flat = skill_flat.lower()
    assert "it does **not** order the message" in flat
    assert "do not group the message into label buckets" in flat
    assert "the same label may appear on non-adjacent bullets" in flat


def test_rank_is_by_impact_and_outranks_the_label(skill_flat: str):
    """"Rank by new capability surface" biased the head of the message toward
    New, so the top slot could not be won by a fix however severe."""
    flat = skill_flat.lower()
    assert "rank by impact" in flat
    assert "even when it\nis a bug fix" in flat or "even when it is a bug fix" in flat
    assert "not the label" in flat


def test_comma_test_is_present_and_flagged_as_a_detector(skill_flat: str):
    """The check that operationalises "only the first clause is valuable".
    It must also warn that it detects rather than auto-edits: truncating
    `19 defects across mermaid, Vega-Lite and PDF` at its comma yields a
    narrower claim that is false."""
    flat = skill_flat.lower()
    assert "comma test" in flat
    assert "delete everything from that mark onward" in flat
    assert "detects**; it does not auto-edit" in flat
    assert "19 rendering and export defects across mermaid`, which is now false" in flat


def test_worked_example_specimen_does_not_itself_trail_a_list(skill_text: str):
    """The ✅ specimen previously ended "— first renderers are chemistry
    notation, musical scores, and circuit diagrams", i.e. it demonstrated the
    trailing enumeration the caps forbid.  An example that contradicts the
    rule teaches the example."""
    m = re.search(r'^> ✅ "(.+?)"', skill_text, re.M | re.S)
    assert m, "worked example lost its ✅ specimen"
    specimen = m.group(1)
    assert len(specimen) <= 100, f"specimen is {len(specimen)} chars: {specimen}"
    for mark in (",", ";", "("):
        assert mark not in specimen, f"specimen trails a list at {mark!r}: {specimen}"


def test_aggregation_is_explicitly_first(skill_flat: str):
    assert "do not start writing bullets until pass 3" in skill_flat.lower()


def test_skill_defines_the_audience_inclusion_test(skill_flat: str):
    """"Customer visible" was undefined, so everything qualified."""
    assert "without naming an internal symbol" in skill_flat


def test_skill_distinguishes_user_facing_from_internal_identifiers(
    skill_text: str,
):
    """A blanket "no identifiers" rule would strip useful things like
    ``/join``; the carve-out is what keeps the gate usable."""
    assert "/join" in skill_text
    assert "scope.tools" in skill_text


def test_fixes_aggregate_to_one_title_without_comma_joining(skill_flat: str):
    """Aggregation survives; the comma-joined SPELLING of it does not.
    "One bullet total, comma-joined" satisfied the bullet cap by turning the
    bullet itself into a list — a 163-character line naming five fixes.  A
    line still collapses N entries, but carries the theme or a bare count
    and stops."""
    flat = skill_flat.lower()
    assert "produce **one** title" in flat
    assert "no comma-joined list of further" in flat
    assert "(n fixes)` count is the only parenthetical permitted" in flat
    assert "comma-joined. not one bullet per fix" not in flat


def test_aggregation_may_not_be_used_to_shorten_the_message(skill_flat: str):
    """With no item cap, merging distinct work buys nothing and costs the
    reader a change they can no longer see.  Aggregation removes duplication,
    not volume — otherwise a 200-item release is compressed into six themes
    and the announcement stops being an inventory."""
    flat = skill_flat.lower()
    assert "do not aggregate to shorten the message" in flat
    assert "facets of one change" in flat
    assert "200 distinct user-observable changes has 200 lines" in flat


def test_thread_never_carries_the_changelog_in_any_form(skill_flat: str):
    """The digest was the right fix for a 143KB raw attachment and the wrong
    surface once the channel list became complete: two accounts of one
    inventory at two verbosities is the padding this skill removes."""
    flat = skill_flat.lower()
    assert "not as a condensed digest" in flat
    assert "143,590 bytes" in flat
    # The superseded digest instruction must be gone from the skill.
    assert "lead lines only" not in flat


def test_skill_requires_a_trunk_vs_leaf_self_check(skill_flat: str):
    assert "single headline change" in skill_flat
    assert "trunk or a leaf" in skill_flat


def test_skill_carries_the_worked_failure_example(skill_flat: str):
    """An abstract rule did not prevent this; the concrete case is the
    part that makes the rule actionable."""
    lowered = skill_flat.lower()
    assert "chemfig" in lowered and "latex" in lowered
    assert "leaf reported as the trunk" in lowered


def test_skill_names_the_genre_distinction(skill_flat: str):
    """Summarizing a changelog yields a shorter changelog.  If the skill
    does not say so, the model treats this as a length problem."""
    assert "different genres, not different lengths" in skill_flat
