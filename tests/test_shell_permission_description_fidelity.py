"""The shell server's self-described permissions must never understate what it enforces.

WHY THIS EXISTS
---------------
``get_allowed_commands_description()`` is not cosmetic.  Its output is
interpolated directly into the ``run_shell_command`` tool description
(shell_server.py ~line 1792) and into denial messages (~line 1866), so it is
the *only* channel through which an agent learns what it is allowed to do.
An agent auditing its own privileges — "do I have write access to git?" —
reads this string and nothing else.

The defect this pins: the description was derived from pattern-key *name
prefixes* rather than from what the patterns actually permit.  Every key
beginning ``git_`` collapsed to the literal string ``git (safe operations)``.
But a bare ``git`` entry in the allowlist builds

    patterns['git_all'] = r'^git(\\s+.*)?$'

which matches ``git reset --hard``, ``git checkout .``, ``git clean -fdx``,
``git stash`` and ``git push --force``.  So the single most dangerous grant in
the system rendered as an affirmative claim of read-only safety.  An explicit
``git push`` grant mislabelled identically.

That is worse than an absent label.  A missing capability statement makes an
agent cautious; a false one makes it confident.  For an unattended task run
editing a tree with uncommitted work, "I have safe git" is the belief that
turns an unused grant into data loss.

The generalisable invariant these tests assert: **a privilege description may
overstate restriction, never overstate safety.**  Whenever enforcement permits
a destructive operation, the description must disclose it.

Structural meta-patterns (``piped_commands``, ``find_exec``) are a second,
milder instance of the same root cause: internal pattern labels leaking into a
list the agent reads as command names.  ``find_exec`` is not an invokable
command.
"""
from __future__ import annotations

import re

import pytest

from app.mcp_servers.shell_server import ShellServer


# Commands that must never be reachable without disclosure.
DESTRUCTIVE_GIT = [
    "git reset --hard",
    "git checkout .",
    "git clean -fdx",
    "git stash",
    "git push --force",
    "git commit -am wip",
]

# The exact legacy rendering.  Asserted as a literal because this specific
# string is the lie: it appeared verbatim while full git was in force.
LEGACY_SAFE_LABEL = "git (safe operations)"

# At least one of these must appear once anything destructive is reachable.
# Kept as substrings rather than an exact phrase so the wording can be
# improved without the test having to be rewritten to follow it.
DISCLOSURE_TOKENS = ("destructive", "write", "all subcommands")


def _server_with(commands: list[str]) -> ShellServer:
    """A server whose allowlist is exactly ``commands``, plus the floor's git ops.

    This bypasses the env/signature path deliberately.  An *unsigned*
    environment cannot introduce ``git`` at all — the scope gate strips it and
    falls back to the floor, which is why this defect is invisible in casual
    testing.  ``git_all`` arises only from a *signed* task-card scope, i.e.
    exactly the path a user has approved and therefore trusts most.

    The floor's safe git patterns are layered back on after the rebuild
    because ``__init__`` adds them *after* calling
    ``_build_safe_command_patterns()``; rebuilding alone would silently drop
    them and misrepresent the real runtime state.
    """
    server = ShellServer()
    floor_git = {
        key: pattern
        for key, pattern in server.safe_command_patterns.items()
        if key.startswith("git_")
    }
    server.allowed_commands = list(commands)
    server.safe_command_patterns = server._build_safe_command_patterns()
    for key, pattern in floor_git.items():
        server.safe_command_patterns.setdefault(key, pattern)
    return server


def _permits(server: ShellServer, command: str) -> bool:
    """True when any pattern admits ``command``."""
    for pattern in server.safe_command_patterns.values():
        try:
            if re.match(pattern, command):
                return True
        except re.error:
            continue
    return False


def _reachable_destruction(server: ShellServer) -> list[str]:
    return [c for c in DESTRUCTIVE_GIT if _permits(server, c)]


class TestBareGitGrantIsDescribedHonestly:
    """A bare ``git`` grant means ALL subcommands.  The description must admit it."""

    def test_bare_git_actually_permits_destructive_operations(self):
        """Positive control: prove the grant really is broad.

        Paired deliberately with the negative assertions below — without this,
        a description test could pass merely because the grant never existed.
        """
        server = _server_with(["ls", "git"])
        assert "git_all" in server.safe_command_patterns
        for command in DESTRUCTIVE_GIT:
            assert _permits(server, command), f"expected {command!r} to be permitted"

    def test_legacy_safe_label_is_gone(self):
        """FAILS before the fix: rendered exactly 'git (safe operations)'."""
        server = _server_with(["ls", "git"])
        description = server.get_allowed_commands_description()
        assert LEGACY_SAFE_LABEL not in description, (
            f"description claims safety while permitting "
            f"{DESTRUCTIVE_GIT[0]!r}: {description}"
        )

    def test_description_discloses_the_destructive_capability(self):
        """Silence is not enough — the breadth has to be legible."""
        server = _server_with(["ls", "git"])
        description = server.get_allowed_commands_description().lower()
        assert "git" in description
        assert any(t in description for t in DISCLOSURE_TOKENS), (
            f"description does not disclose full git access: {description}"
        )


class TestExplicitGitWriteGrantIsVisible:
    """``git push`` granted explicitly must appear as a write grant."""

    def test_explicit_write_subcommand_appears_in_description(self):
        """FAILS before the fix: collapsed to 'git (safe operations)'."""
        server = _server_with(["ls", "git push"])
        description = server.get_allowed_commands_description().lower()
        assert "push" in description, (
            f"explicit 'git push' grant invisible in description: {description}"
        )

    def test_explicit_write_grant_is_disclosed_as_a_write(self):
        server = _server_with(["ls", "git push"])
        description = server.get_allowed_commands_description().lower()
        assert any(t in description for t in DISCLOSURE_TOKENS), (
            f"explicit write grant not disclosed as such: {description}"
        )


class TestReadOnlyGitStillReadsAsReadOnly:
    """No regression: the genuinely-safe case must keep its reassuring label.

    Without this, "always disclose" could be satisfied by labelling everything
    dangerous, which would destroy the signal.
    """

    def test_safe_ops_only_are_described_as_read_only(self):
        server = _server_with(["ls", "cat"])
        git_keys = [k for k in server.safe_command_patterns if k.startswith("git_")]
        assert git_keys, "precondition: floor supplies safe git operations"
        assert "git_all" not in server.safe_command_patterns
        assert not _reachable_destruction(server), (
            "read-only configuration unexpectedly permits destruction"
        )
        description = server.get_allowed_commands_description().lower()
        assert "git" in description
        assert "destructive" not in description, (
            f"harmless git configuration described as destructive: {description}"
        )


class TestMetaPatternsAreNotSurfacedAsCommands:
    """Internal structural pattern labels are not invokable commands."""

    def test_find_exec_absent_from_description(self):
        """FAILS before the fix: 'find_exec' appeared in the command list."""
        server = _server_with(["ls", "find", "cat"])
        assert "find_exec" in server.safe_command_patterns, "precondition"
        description = server.get_allowed_commands_description()
        assert "find_exec" not in description, (
            f"structural pattern label leaked as a command name: {description}"
        )

    def test_piped_commands_absent_from_description(self):
        server = _server_with(["ls", "cat"])
        assert "piped_commands" in server.safe_command_patterns, "precondition"
        assert "piped_commands" not in server.get_allowed_commands_description()


class TestScopedSubcommandGrantsAreNarrow:
    """The recommended authoring pattern, pinned so it keeps working.

    The base floor already models this with ``npx jest`` / ``npx craco``
    ("bare npx is unsafe — only specific tools allowed").  A card wanting
    ``npm run build`` should grant ``npm run``, not ``npm``.
    """

    def test_scoped_grant_permits_only_its_subcommand(self):
        server = _server_with(["ls", "npm run"])
        assert _permits(server, "npm run build")
        assert not _permits(server, "npm publish")
        assert not _permits(server, "npm install something-hostile")

    def test_bare_grant_is_broader_than_scoped_grant(self):
        """Documents precisely what breadth a bare grant buys."""
        scoped = _server_with(["ls", "npm run"])
        bare = _server_with(["ls", "npm"])
        assert not _permits(scoped, "npm publish")
        assert _permits(bare, "npm publish")


class TestDescriptionFidelityInvariant:
    """The general rule, independent of any particular command family."""

    @pytest.mark.parametrize("grant", ["git", "git push", "git commit", "git reset"])
    def test_reachable_destruction_is_always_disclosed(self, grant):
        """Whenever a destructive op is reachable, the description says so.

        Parameterised over grant *shapes* rather than asserting one string, so
        a future grant form that reintroduces the divergence is caught.
        """
        server = _server_with(["ls", grant])
        reachable = _reachable_destruction(server)
        if not reachable:
            pytest.skip(f"grant {grant!r} permits nothing destructive")
        description = server.get_allowed_commands_description()
        assert LEGACY_SAFE_LABEL not in description, (
            f"grant {grant!r} permits {reachable} but description "
            f"still carries the legacy safe label: {description}"
        )
        assert any(t in description.lower() for t in DISCLOSURE_TOKENS), (
            f"grant {grant!r} permits {reachable} without disclosure: {description}"
        )
