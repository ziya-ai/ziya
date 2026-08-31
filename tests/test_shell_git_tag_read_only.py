"""The read-only ``git tag`` grant must not admit tag creation.

Context.  ``safeGitOperations`` is described to the model, and to the user in
the Shell Configuration modal, as the *read-only* git set, and
``get_allowed_commands_description()`` renders it as ``git (read-only
operations)``.  The pattern installed for ``tag`` guarded ``-d``/``--delete``
and nothing else, so every creating form was admitted at the unescalated
floor::

    git tag -a v1.0 -m "v1.0"     # annotated tag
    git tag v1.0                  # lightweight tag -- no flag at all

The second form is the reason a flag-guard alone cannot fix this: there is no
flag to refuse.  A bare operand IS the mutation, so the pattern has to reason
about operands, not just flags.

This is the same defect class as the shell-permission-description-fidelity
fix already in the tree: enforcement admitting a write that the description
calls read-only.  A missing capability makes an agent cautious; a falsely
*reassuring* one makes it confident, which is how an unused grant becomes a
stray tag on someone's release history during an unattended task run.

What these tests pin is the INVARIANT ("the read-only tag grant refuses every
mutating form"), parameterised over forms, rather than the regex itself -- a
future rewrite of the pattern has to keep the property, not the spelling.
Every refusal case is paired with the read-only forms that must keep working,
because a pattern of ``(?!)`` would satisfy all the refusals perfectly.
"""

import os
import re

import pytest

from app.config.scope_canonical import ESCALATION_ENV_KEYS
from app.config.shell_config import DEFAULT_SHELL_CONFIG


# Forms that MUTATE the repository.  Creation (annotated, signed, lightweight,
# forced), deletion, and the editor/message-bearing variants.
MUTATING = [
    # Lightweight creation -- carries no flag, which is what makes a
    # flag-only guard insufficient.
    "git tag v1.0",
    "git tag v0.8.7.0",
    "git tag v1.0 HEAD",
    "git tag v1.0 abc1234",
    # Annotated / signed creation.
    "git tag -a v1.0 -m 'v1.0'",
    "git tag -a v1.0",
    "git tag --annotate v1.0",
    "git tag -s v1.0",
    "git tag --sign v1.0",
    "git tag -u keyid v1.0",
    "git tag --local-user keyid v1.0",
    # Message / file / editor forms.
    "git tag -m 'x' v1.0",
    "git tag --message 'x' v1.0",
    "git tag -F notes.txt v1.0",
    "git tag --file notes.txt v1.0",
    "git tag -e",
    "git tag --edit v1.0",
    # Bundled short options -- the shape a per-flag alternation misses.
    "git tag -am 'x' v1.0",
    "git tag -sm 'x' v1.0",
    # Force (moves an existing tag -- silently rewrites release history).
    "git tag -f v1.0",
    "git tag --force v1.0",
    "git tag -a -f v1.0 -m 'x'",
    # Deletion.  These were already refused; kept so a rewrite cannot
    # regress them while fixing creation.
    "git tag -d v1.0",
    "git tag -D v1.0",
    "git tag --delete v1.0",
    # Reflog creation is a write to .git.
    "git tag --create-reflog v1.0",
]


# Forms that only READ.  These are what the grant exists for, and several are
# used by the release tooling in this repo.
READ_ONLY = [
    "git tag",
    "git tag -l",
    "git tag --list",
    "git tag -l 'v0.8*'",
    "git tag -l v0.8.6.1",
    "git tag --list v0.8.6.1",
    "git tag --sort=-v:refname",
    "git tag -l --sort=-v:refname",
    "git tag -n",
    "git tag -n5",
    "git tag --contains HEAD",
    "git tag --no-contains HEAD",
    "git tag --points-at HEAD",
    "git tag --merged main",
    "git tag --no-merged main",
    "git tag -v v0.8.6.1",
    "git tag --verify v0.8.6.1",
    "git tag --format='%(refname:short)'",
    "git tag --list --format='%(refname) %(objectname)'",
    "git tag -i -l 'v*'",
    "git tag --column",
]


def _floor_server():
    """A ``ShellServer`` carrying exactly the unescalated floor.

    Two things are load-bearing here.

    First, the ambient escalation keys are stripped: pytest runs as a child of
    the live shell MCP server and inherits its real environment, so a machine
    that has ever run ``/shell git tag`` would leak that grant in and make
    these assertions pass against a broken floor.

    Second, ``SAFE_GIT_OPERATIONS`` is then set back to the canonical floor
    value from ``DEFAULT_SHELL_CONFIG``.  Stripping alone is NOT the floor:
    absent that variable the server falls back to an inline default list which
    omits ``tag`` (along with ls-tree/rev-parse/describe and others), so
    ``git_tag`` is never installed and the pattern under test is never
    consulted -- every refusal assertion would pass for the wrong reason.
    That inline list and ``DEFAULT_SHELL_CONFIG["safeGitOperations"]`` are two
    hand-maintained copies which already disagree; the real floor is the
    config, because ``scope_canonical`` derives the floor from it and
    ``mcp_routes`` always transports it.
    """
    from unittest.mock import patch

    from app.mcp_servers.shell_server import ShellServer

    env = {k: v for k, v in os.environ.items() if k not in ESCALATION_ENV_KEYS}
    leaked = [k for k in ESCALATION_ENV_KEYS if k in env]
    assert leaked == [], f"escalation keys survived the strip: {leaked}"
    env["SAFE_GIT_OPERATIONS"] = ",".join(
        DEFAULT_SHELL_CONFIG["safeGitOperations"]
    )

    with patch.dict(os.environ, env, clear=True):
        return ShellServer()


@pytest.fixture(scope="module")
def tag_pattern():
    """The read-only tag pattern the floor server installs."""
    server = _floor_server()
    installed = server.safe_command_patterns.get("git_tag")
    assert installed, (
        "no read-only pattern is installed for git tag at the floor, so "
        "nothing below is measuring enforcement"
    )
    return installed


def _matches(pattern: str, command: str) -> bool:
    """Match exactly as ``_validate_command`` does, flags included.

    The IGNORECASE flag is load-bearing rather than incidental: it is what
    makes ``-D`` refused by the same character class that refuses ``-d``.
    Reimplementing the match without it would certify a pattern that lets
    ``git tag -D v1.0`` through.
    """
    return bool(re.match(pattern, command, re.IGNORECASE | re.DOTALL))


# ── the invariant ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", MUTATING)
def test_read_only_tag_refuses_every_mutating_form(tag_pattern, command):
    assert not _matches(tag_pattern, command), (
        f"the read-only git tag grant admits {command!r}, which mutates the "
        f"repository -- the floor is described to the model as read-only"
    )


@pytest.mark.parametrize("command", READ_ONLY)
def test_read_only_tag_still_admits_listing_and_verification(tag_pattern, command):
    """Positive control.

    Without these, a pattern that refuses everything would pass every
    assertion above while removing the capability the grant exists for.
    """
    assert _matches(tag_pattern, command), (
        f"the read-only git tag grant refuses {command!r}, which only reads"
    )


def test_the_creating_forms_were_genuinely_reachable_before(tag_pattern):
    """Attribution: name what this change actually closed.

    Pins the PRIOR pattern's behaviour so the record shows which forms were
    admitted, and so a reviewer can tell a real fix from a test written
    around already-correct code.  If this ever fails, the premise of the fix
    was wrong and the rest of this file needs re-reading.
    """
    previous = r'^git\s+tag(\s+(?!-[dD]|--delete).*)?$'
    admitted = [c for c in MUTATING if _matches(previous, c)]
    refused = [c for c in MUTATING if not _matches(previous, c)]

    assert "git tag v1.0" in admitted, (
        "the lightweight-creation hole is the premise of this fix"
    )
    assert "git tag -a v1.0 -m 'v1.0'" in admitted, (
        "annotated creation is the form the release tooling uses"
    )
    # Deletion was correctly guarded; only the creating class was open.
    assert set(refused) == {
        "git tag -d v1.0", "git tag -D v1.0", "git tag --delete v1.0",
    }, f"unexpected prior-refusal set: {sorted(refused)}"
    assert len(admitted) == len(MUTATING) - 3


# ── the seam: a pattern nobody installs enforces nothing ───────────────────

def test_tag_is_still_in_the_read_only_set(tag_pattern):
    """A pattern is only installed for ops named in ``safeGitOperations``.

    Tightening the pattern while dropping ``tag`` from the configured set
    would make every assertion above vacuous -- ``git tag`` would be refused
    outright by the allowlist and the pattern would never be consulted.  That
    is also the exact condition that made ``cat-file`` and ``check-ignore``
    silently inert before they were added to the default set.
    """
    assert "tag" in DEFAULT_SHELL_CONFIG["safeGitOperations"], (
        "tag left the read-only set, so listing is no longer available at "
        "the floor and the pattern tests above prove nothing"
    )


def test_the_validator_consults_the_tightened_pattern(tag_pattern):
    """The fix has to land in the dict the validator reads from.

    ``git_patterns`` is a staging table; only entries copied into
    ``safe_command_patterns`` are ever matched against a command.  A fix
    applied to the former alone would leave every unit assertion green
    against a live hole, so this walks the pattern set the validator really
    uses and requires that NOTHING in it admits tag creation -- which also
    catches the case where some other pattern (a broad ``git_all``, say)
    admits it from the side.
    """
    server = _floor_server()
    assert server.safe_command_patterns.get("git_tag") == tag_pattern, (
        "git_tag is registered with a pattern other than the one under test"
    )
    for command in ("git tag -a v1.0 -m 'x'", "git tag v1.0", "git tag -d v1.0"):
        matched = [
            name for name, pattern in server.safe_command_patterns.items()
            if _matches(pattern, command)
        ]
        assert matched == [], (
            f"{command!r} mutates the repository but is admitted at the "
            f"floor by {matched}"
        )


# ── the description must not promise what enforcement now refuses ─────────

def test_the_read_only_label_stays_a_single_folded_claim():
    """Guard on the label, which does not change with this fix but can drift.

    ``get_allowed_commands_description()`` renders the configured safe set as
    ``git (read-only operations)``, and that string reaches the model through
    the tool description and every denial message -- it is the only channel an
    agent learns its own privileges through.  This does not discriminate
    pre/post fix (the text is identical either way); it fails if tag later
    reappears as a standalone or WRITE-labelled grant, which is how the
    reassuring claim would silently stop being true again.
    """
    server = _floor_server()
    description = server.get_allowed_commands_description()

    assert "read-only" in description, (
        "the floor no longer describes its git access as read-only, so this "
        "test's premise is stale"
    )
    assert "git tag" not in description, (
        "tag is being described as a standalone grant rather than folded into "
        "the read-only git set, which reads as a broader capability than it is"
    )
