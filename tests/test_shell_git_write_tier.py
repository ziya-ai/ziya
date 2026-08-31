"""The git WRITE tier: pattern safety, default-off, and the escalation seam.

Three distinct properties, and the third is the one that actually matters:

1. Each write pattern admits the ordinary form and refuses the destructive
   flags of that same subcommand (``git push`` yes, ``git push --force`` no).
2. No write op is active unless WRITE_GIT_OPERATIONS names it.
3. WRITE_GIT_OPERATIONS is registered as privilege-bearing in
   scope_canonical, with an EMPTY floor -- so any entry is an escalation
   delta requiring a signature, and an unsigned value is clamped away.

(3) is a seam test rather than a unit test on purpose. Properties (1) and (2)
would both pass while the tier was a wide-open unsigned back door: the
patterns would still be strict, the default would still be empty, and
anybody able to edit .ziya/mcp_config.json could still grant `git push`
without approval. The pattern tests certify nothing about authorization.
"""

import os
import re
from unittest.mock import patch

import pytest

from app.config.scope_canonical import (
    _LIST_FIELDS,
    ESCALATION_ENV_KEYS,
    _floor,
    compute_delta,
    parse_env_scope,
    strip_escalations,
)
from app.config.shell_config import DEFAULT_SHELL_CONFIG
from app.mcp_servers.shell_server import ShellServer

ENV_KEY = "WRITE_GIT_OPERATIONS"


def _clean_env() -> dict:
    """Ambient env with every privilege-bearing key removed.

    Load-bearing, not hygiene. The test process is a CHILD of the live shell
    MCP server, so it inherits that server's real ALLOW_COMMANDS /
    SAFE_GIT_OPERATIONS. A developer machine that has ever granted
    ``git push`` via ``/shell git push`` leaks that grant in as
    ``git_explicit_push`` -- an unguarded pattern that admits
    ``git push --force`` and makes the flag-guard assertions below fail
    against correct code. Stripping via ESCALATION_ENV_KEYS rather than a
    hand-listed set so this cannot drift from the canonical definition.
    """
    env = {k: v for k, v in os.environ.items() if k not in ESCALATION_ENV_KEYS}
    return env


def _server(write_ops: str = "", extra: dict | None = None):
    """A ShellServer whose env grants exactly ``write_ops`` and nothing else.

    The signature gate is stubbed to authorize, because these cases are about
    what the tier DOES once authorized. The gate's own behaviour is asserted
    separately in TestEscalationSeam, unstubbed.
    """
    env = _clean_env()
    env[ENV_KEY] = write_ops
    env.update(extra or {})
    with patch.dict(os.environ, env, clear=True), \
         patch("app.mcp_servers.shell_server.is_env_scope_authorized", return_value=True):
        return ShellServer()


class TestFixtureIsHermetic:
    """Without this, every pattern assertion below could be measuring the
    developer's ambient shell grants rather than the write tier."""

    def test_ambient_privilege_keys_are_stripped(self):
        for key in ESCALATION_ENV_KEYS:
            assert key not in _clean_env(), f"{key} leaked into the test env"

    def test_ambient_git_grant_does_not_reach_the_server(self):
        """Pins the specific leak that produced a false failure while writing
        these tests: an inherited ALLOW_COMMANDS entry of 'git push'."""
        srv = _server("")
        assert not any(
            c.startswith("git ") and c != "git" and c[4:] not in srv.git_patterns
            for c in srv.allowed_commands
        ), f"ambient git write grant present: {srv.allowed_commands}"


# ── 1. Pattern safety ────────────────────────────────────────────────────────

ALLOWED_FORMS = [
    ("add", "git add app/utils/foo.py"),
    ("add", "git add -A"),
    ("add", "git add ."),
    ("add", "git add -- app/"),
    ("commit", 'git commit -m "msg"'),
    ("commit", 'git commit -am "msg"'),
    ("stash push", "git stash push -m wip"),
    ("stash push", "git stash save wip"),
    ("push", "git push"),
    ("push", "git push origin mainline"),
    ("push", "git push -u origin feature"),
    ("push", "git push --force-with-lease origin feature"),
    ("push", "git push --dry-run"),
]

REFUSED_FORMS = [
    # interactive/editor forms hang a non-interactive shell
    ("add", "git add -p"),
    ("add", "git add -i"),
    ("add", "git add -e file"),
    ("add", "git add --patch"),
    ("add", "git add --interactive"),
    # history rewrite / hook bypass
    ("commit", "git commit --amend"),
    ("commit", "git commit --amend -m x"),
    ("commit", "git commit --no-verify -m x"),
    # discards stashed work
    ("stash push", "git stash pop"),
    ("stash push", "git stash drop"),
    ("stash push", "git stash clear"),
    # irreversible remote effects
    ("push", "git push --force origin main"),
    ("push", "git push -f origin main"),
    ("push", "git push --delete origin feature"),
    ("push", "git push -d origin feature"),
    ("push", "git push --mirror"),
    ("push", "git push --prune origin"),
]


class TestWritePatterns:
    @pytest.mark.parametrize("op,cmd", ALLOWED_FORMS)
    def test_ordinary_form_allowed(self, op, cmd):
        ok, reason = _server(op).is_command_allowed(cmd)
        assert ok, f"{cmd!r} should be allowed when 'git {op}' is granted: {reason}"

    @pytest.mark.parametrize("op,cmd", REFUSED_FORMS)
    def test_destructive_flag_refused(self, op, cmd):
        ok, _ = _server(op).is_command_allowed(cmd)
        assert not ok, f"{cmd!r} must be refused even with 'git {op}' granted"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git addendum",
            "git committer",
            "git pushall",
        ],
    )
    def test_no_prefix_collision(self, cmd):
        """A grant must not admit a longer subcommand sharing its prefix."""
        srv = _server("add,commit,stash push,push")
        ok, _ = srv.is_command_allowed(cmd)
        assert not ok, f"{cmd!r} shares a prefix with a granted op but is a different command"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git -c core.pager=sh add .",
            "git -c alias.x=!sh commit -m x",
            "git --exec-path=/tmp push",
        ],
    )
    def test_global_option_injection_refused(self, cmd):
        """Global git options before the subcommand can smuggle execution."""
        srv = _server("add,commit,stash push,push")
        ok, _ = srv.is_command_allowed(cmd)
        assert not ok, f"{cmd!r} places options before the subcommand and must not match"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git reset --hard HEAD~3",
            "git checkout -- .",
            "git restore .",
            "git clean -fdx",
            "git rebase -i HEAD~5",
            "git merge --no-ff other",
            "git cherry-pick abc123",
            "git rm -rf app/",
            "git mv a b",
            "git filter-branch --all",
        ],
    )
    def test_omitted_subcommands_never_admitted(self, cmd):
        """Ops left out of the table must not be admitted by another's pattern.

        The tier grants every op it knows about, so a pattern that is loose at
        its tail (e.g. anchoring on 'git' rather than 'git push') would show up
        here rather than passing quietly.
        """
        srv = _server("add,commit,stash push,push")
        ok, _ = srv.is_command_allowed(cmd)
        assert not ok, f"{cmd!r} is deliberately outside the write tier"


# ── 2. Default-off ───────────────────────────────────────────────────────────

class TestDefaultOff:
    def test_config_default_is_empty(self):
        """A non-empty default would silently widen the (hardcoded) floor."""
        assert DEFAULT_SHELL_CONFIG["writeGitOperations"] == []

    @pytest.mark.parametrize(
        "cmd",
        ["git add .", 'git commit -m x', "git push", "git stash push"],
    )
    def test_write_op_refused_when_not_granted(self, cmd):
        ok, _ = _server("").is_command_allowed(cmd)
        assert not ok, f"{cmd!r} must be refused with no WRITE_GIT_OPERATIONS grant"

    def test_grant_is_per_op_not_wholesale(self):
        """Granting 'add' must not drag in 'push'."""
        srv = _server("add")
        assert srv.is_command_allowed("git add .")[0]
        assert not srv.is_command_allowed("git push")[0]
        assert not srv.is_command_allowed("git commit -m x")[0]

    def test_read_only_ops_unaffected(self):
        """Positive control: the read-only tier still works alongside."""
        srv = _server("add")
        assert srv.is_command_allowed("git status")[0]
        assert srv.is_command_allowed("git log --oneline")[0]

    def test_unknown_op_is_ignored_not_permissive(self):
        """An op with no pattern must grant nothing, not everything."""
        srv = _server("reset,checkout,clean")
        for cmd in ("git reset --hard", "git checkout -- .", "git clean -fdx"):
            assert not srv.is_command_allowed(cmd)[0]


# ── 3. Escalation seam (the one that certifies authorization) ─────────────────

class TestEscalationSeam:
    def test_key_is_registered_privilege_bearing(self):
        """Absent from _LIST_FIELDS => never gated, never forwarded."""
        assert ENV_KEY in _LIST_FIELDS

    def test_key_rides_the_forwarding_path(self):
        """The manager forwards exactly ESCALATION_ENV_KEYS to the subprocess."""
        assert ENV_KEY in ESCALATION_ENV_KEYS

    def test_floor_is_empty(self):
        """A non-empty floor would make some write op grantable unsigned."""
        assert _floor()[ENV_KEY] == set()

    @pytest.mark.parametrize("op", ["add", "commit", "stash push", "push"])
    def test_any_grant_is_an_escalation_delta(self, op):
        delta = compute_delta(parse_env_scope({ENV_KEY: op}))
        assert delta.get(ENV_KEY) == [op], (
            f"granting 'git {op}' must register as an escalation requiring a signature"
        )

    def test_unsigned_grant_is_clamped_away(self):
        stripped = strip_escalations({ENV_KEY: "push,commit"})
        assert stripped[ENV_KEY] == "", (
            "an unsigned WRITE_GIT_OPERATIONS value must be clamped to empty, "
            "not passed through"
        )

    def test_clamped_env_grants_nothing(self):
        """End-to-end: unsigned env reaching a real server grants no write op.

        The signature gate is deliberately NOT stubbed here -- this is the only
        case that exercises it for real.
        """
        env = _clean_env()
        env[ENV_KEY] = "push,commit,add"
        with patch.dict(os.environ, env, clear=True):
            srv = ShellServer()
        for cmd in ("git push", 'git commit -m x', "git add ."):
            ok, _ = srv.is_command_allowed(cmd)
            assert not ok, f"{cmd!r} granted without a signature"

    def test_signed_grant_does_take_effect(self):
        """Positive control for the clamp test above.

        Without this, test_clamped_env_grants_nothing would pass just as well
        if WRITE_GIT_OPERATIONS were ignored entirely and the tier never
        worked at all.
        """
        srv = _server("push")
        assert srv.is_command_allowed("git push origin mainline")[0]


class TestAllowCommandsPrecedence:
    """An explicit ``git <sub>`` entry in ALLOW_COMMANDS OUTRANKS the tier.

    Documented as current behaviour, not endorsed as correct. Validation
    returns True on the FIRST matching pattern, and an ALLOW_COMMANDS entry
    builds an unguarded ``git_explicit_<sub>`` pattern in
    _build_safe_command_patterns. So a user who runs ``/shell git push``
    gets ``git push --force`` too, and the write tier's flag guards are
    bypassed rather than intersected.

    This predates the tier and is itself signature-gated (ALLOW_COMMANDS is
    in _LIST_FIELDS), so it is an escape hatch rather than a hole. Pinned
    here so that if the precedence is ever changed to intersect, the change
    is deliberate and this assertion is updated to follow it.
    """

    def test_explicit_grant_bypasses_tier_flag_guards(self):
        srv = _server("push", extra={"ALLOW_COMMANDS": "ls,git push"})
        ok, _ = srv.is_command_allowed("git push --force origin main")
        assert ok, (
            "behaviour change: an explicit ALLOW_COMMANDS 'git push' no longer "
            "outranks the write tier. If intersecting was intended, update this "
            "assertion; if not, the escape hatch has regressed."
        )

    def test_tier_alone_still_guards_the_same_command(self):
        """Positive control: the bypass above is caused by ALLOW_COMMANDS."""
        srv = _server("push")
        assert not srv.is_command_allowed("git push --force origin main")[0]

    def test_read_only_floor_still_needs_no_signature(self):
        """Positive control: the read-only tier is NOT dragged into the gate.

        Without this, the previous assertions would also pass if the whole git
        configuration had been made signature-required, which would break
        everyday use rather than secure it.
        """
        scope = parse_env_scope(
            {"SAFE_GIT_OPERATIONS": ",".join(DEFAULT_SHELL_CONFIG["safeGitOperations"])}
        )
        assert compute_delta(scope) == {}
