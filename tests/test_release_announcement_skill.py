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

def test_skill_states_hard_caps(skill_text: str):
    assert "6 bullets" in skill_text
    assert "1200" in skill_text


def test_overflow_is_routed_back_to_aggregation(skill_flat: str):
    """The wrong repair for an overflow is trimming words from every bullet;
    the right one is collapsing themes.  The skill must say which."""
    assert "pass 1 was not done" in skill_flat.lower()


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
        (1, "AGGREGATE"), (2, "AUDIENCE"), (3, "TIER"), (4, "RANK"),
    ]


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


def test_notable_fixes_collapse_to_a_single_bullet(skill_flat: str):
    """One bullet per fix is what buried the highlights."""
    assert "One bullet total" in skill_flat


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
