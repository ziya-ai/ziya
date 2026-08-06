"""
Structural tests for the ``release`` CLI task in ``.ziya/tasks.yaml``.

``release`` is a prompt, so what is testable is the set of invariants that
encode decisions the investigation actually established — each of which is a
real trap that a plausible-looking rewrite would fall into:

  * ``dev.sh publish`` does NOT build the public wheel (only the ``public``
    mode calls ``build_public``).  A release task that runs ``publish`` and
    then twine-uploads ships a STALE wheel, silently, because ``dist/``
    already holds a correctly-named wheel from the previous release.
  * There is no subtask mechanism (``task_runner`` exposes no run-a-task
    function), so delegating to ``sweep`` means invoking the CLI, which
    means ``ziya`` must be a granted command.
  * The Toolbox upload inside ``dev.sh`` is explicitly non-fatal — it warns
    and the script still exits 0 — so an exit code is not evidence a
    channel published.
  * Announcing availability that did not happen is a factual error, and the
    most likely one here, because prior hand-written posts said "is on
    toolbox".

These are source-contract tests over the task definition, matching the
convention used by ``test_release_announcement_skill.py``.  They deliberately
do NOT execute any build or upload.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = REPO_ROOT / ".ziya" / "tasks.yaml"

pytestmark = pytest.mark.skipif(
    not TASKS_FILE.is_file(),
    reason=".ziya/tasks.yaml is local task config and may be absent",
)


@pytest.fixture(scope="module")
def tasks() -> dict:
    return yaml.safe_load(TASKS_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def release(tasks: dict) -> dict:
    assert "release" in tasks, "the release task is missing"
    return tasks["release"]


@pytest.fixture(scope="module")
def prompt(release: dict) -> str:
    return release.get("prompt", "")


@pytest.fixture(scope="module")
def flat(prompt: str) -> str:
    """Whitespace-flattened prompt.

    The prompt is hard-wrapped, so a multi-word phrase spans a newline and a
    naive substring check reports a missing requirement that is present.
    """
    return re.sub(r"\s+", " ", prompt)


# --------------------------------------------------------------------------
# Definition and discoverability
# --------------------------------------------------------------------------

def test_release_task_exists_and_is_described(release: dict):
    assert release.get("description"), "release needs a description for --list"


def test_release_task_yaml_is_valid(tasks: dict):
    assert isinstance(tasks, dict) and tasks


def test_sweep_still_exists(tasks: dict):
    """release delegates to sweep; sweep disappearing breaks Step 1."""
    assert "sweep" in tasks


# --------------------------------------------------------------------------
# Permissions — every command the prompt tells it to run must be granted
# --------------------------------------------------------------------------

def test_grants_every_command_the_prompt_requires(release: dict):
    """A prompt instruction with no matching grant fails at the floor.

    ``ziya`` is required specifically because there is no subtask mechanism:
    delegating to sweep is a CLI invocation.
    """
    granted = set((release.get("allow") or {}).get("commands") or [])
    for required in ("git", "ziya", "twine", "sh"):
        assert required in granted, f"{required!r} is used but not granted"


def test_does_not_grant_write_patterns(release: dict):
    """release must not be able to edit tracked files.

    Committing, bumping and changelog editing belong to sweep.  Granting
    write_patterns here would make an overreaching rewrite *work*, which is
    exactly how the separation would erode.
    """
    allow = release.get("allow") or {}
    assert not allow.get("write_patterns"), (
        "release should not write tracked files — that is sweep's job"
    )


def test_does_not_grant_mutating_git_operations(release: dict):
    """Read-only git only.  commit/push/add would let it re-do sweep."""
    ops = set((release.get("allow") or {}).get("git_operations") or [])
    for forbidden in ("commit", "push", "add", "rm"):
        assert forbidden not in ops, f"git {forbidden} must not be granted"


# --------------------------------------------------------------------------
# Step 1 — sweep verification and delegation
# --------------------------------------------------------------------------

def test_verifies_sweep_ran_before_publishing(flat: str):
    """Publishing an un-cut release ships untagged code."""
    assert "tag --points-at HEAD" in flat
    assert "release_progress.md" in flat


def test_verification_tolerates_untracked_files(flat: str):
    """sweep deliberately excludes in-progress work, so untracked entries
    are the NORMAL post-sweep state.  Treating them as 'sweep did not run'
    would make release re-invoke sweep on every single run."""
    assert "untracked" in flat.lower()


def test_delegates_to_sweep_by_cli_invocation(flat: str):
    """No subtask mechanism exists; the prompt must say how to delegate."""
    assert "ziya task sweep" in flat


def test_states_that_no_subtask_mechanism_exists(flat: str):
    """Without this the model looks for a subtask tool and stalls."""
    assert "no subtask mechanism" in flat.lower()


def test_sweep_is_invoked_at_most_once(flat: str):
    """A retry loop around a full agent run is unbounded work."""
    assert "never run sweep more than once" in flat.lower()


def test_rereads_target_after_sweep(flat: str):
    """sweep may bump the version, so a TARGET captured before it is stale."""
    assert "sweep may have bumped" in flat.lower()


# --------------------------------------------------------------------------
# Step 2/3 — the build ordering trap
# --------------------------------------------------------------------------

def test_uses_dev_sh_publish_for_internal(flat: str):
    assert "./dev.sh publish" in flat


def test_builds_public_separately_from_publish(flat: str):
    """THE load-bearing test.

    ``dev.sh publish`` = ``build_formatters; build_internal;
    TOOLBOX_PUBLISH=1`` — it never calls ``build_public``.  Without an
    explicit ``./dev.sh public``, twine uploads whatever is already in
    ``dist/``: a previous release's wheel, correctly named, with no error.
    """
    assert "./dev.sh public" in flat
    assert "does NOT build the public wheel" in flat


def test_explains_why_it_rebuilds_rather_than_reusing(flat: str):
    """A reviewer who does not know this deletes the rebuild as redundant."""
    assert "code at the tag" in flat


def test_verifies_wheel_version_matches_target(flat: str):
    """A stale wheel is indistinguishable from a fresh one by filename
    alone unless the version is checked against TARGET."""
    assert flat.count("TARGET") >= 5
    assert "amzn_ziya-<TARGET>" in flat
    assert "ziya-<TARGET>-py3-none-any.whl" in flat


def test_forbids_glob_upload(flat: str):
    """``dist/`` accumulates wheels; a glob re-uploads old releases."""
    assert "glob" in flat.lower()
    assert "Never upload with a glob" in flat
    assert "twine upload dist/ziya-<TARGET>-py3-none-any.whl" in flat


def test_treats_already_present_on_pypi_as_success(flat: str):
    """Re-running after a partial failure must not hard-fail on a file that
    is already uploaded — otherwise recovery is impossible."""
    assert "already exists on PyPI" in flat or "already published" in flat.lower()


# --------------------------------------------------------------------------
# Step 4/5 — availability claims must come from evidence
# --------------------------------------------------------------------------

def test_exit_code_is_not_treated_as_publish_evidence(flat: str):
    """dev.sh prints 'Toolbox publish failed (non-fatal)' and exits 0."""
    assert "Toolbox publish failed" in flat
    assert "non-fatal" in flat.lower()


def test_requires_a_verified_outcome_table_before_announcing(flat: str):
    assert "table" in flat.lower()
    assert "ONLY thing Step 5 may make availability claims from" in flat


def test_announces_to_interest_channel_only(flat: str):
    """release owns #ziya-interest; sweep owns #ziya-dev.

    #ziya-dev may be *mentioned* in prose (explaining that sweep owns it),
    so a bare substring ban is wrong.  What must not appear is an
    instruction to POST there.
    """
    assert "#ziya-interest" in flat
    assert "Post ONE message to #ziya-interest" in flat
    posting_verbs = re.findall(
        r"(?:post|Post|POST)[^.]{0,60}#ziya-dev", flat
    )
    assert not posting_verbs, f"release appears to post to #ziya-dev: {posting_verbs}"


def test_interest_post_is_a_single_message_with_no_thread(flat: str):
    assert "no thread" in flat.lower()


def test_loads_the_announcement_skill(flat: str):
    """Format decisions live in the skill; improvising re-creates the bug."""
    assert "release-announcement" in flat
    assert "get_skill_details" in flat


def test_forbids_claiming_an_unverified_channel(flat: str):
    assert "Never claim an install channel" in flat


# --------------------------------------------------------------------------
# Separation of concerns from sweep
# --------------------------------------------------------------------------

def test_explicitly_forbids_redoing_sweeps_work(flat: str):
    assert "Do NOT re-do sweep's work" in flat
    assert "never commits, tags, bumps" in flat


def test_sweep_no_longer_posts_to_interest(tasks: dict):
    """Ownership moved to release.  If sweep still posted there, every
    release would double-post, and sweep's post would claim availability
    before anything was actually installable."""
    sweep_flat = re.sub(r"\s+", " ", tasks["sweep"].get("prompt", ""))
    assert "Do NOT post to `#ziya-interest` here" in sweep_flat


def test_sweep_still_owns_the_dev_channel(tasks: dict):
    sweep_flat = re.sub(r"\s+", " ", tasks["sweep"].get("prompt", ""))
    assert "#ziya-dev" in sweep_flat


# --------------------------------------------------------------------------
# Path handling
# --------------------------------------------------------------------------

def test_does_not_hardcode_absolute_paths(prompt: str):
    """The public/ symlink resolves through a differently-named parent
    (``workplace`` vs ``workspace``), so a hardcoded path is wrong on any
    other machine and misleading on this one."""
    hardcoded = re.findall(r"/Users/[A-Za-z0-9_]+/\S+", prompt)
    assert not hardcoded, f"hardcoded absolute paths: {hardcoded}"


def test_warns_that_realpath_may_differ(flat: str):
    """Otherwise a resolved path that looks 'wrong' reads as a bug to fix."""
    assert "realpath" in flat.lower()


def test_step_numbers_are_sequential_and_unique(prompt: str):
    nums = [int(m) for m in re.findall(r"^\s*## Step (\d+)", prompt, re.M)]
    assert nums, "no step headers found"
    assert nums == sorted(nums), f"steps out of order: {nums}"
    assert len(nums) == len(set(nums)), f"duplicate step numbers: {nums}"
