"""
Warn-and-redirect for explicit ``git <sub>`` shell grants
(design/git-tool.md step 6; app/utils/git_grant_hazards.py).

Three layers:
  * the catalogue itself (what each grant admits, which get a tool redirect);
  * TestClaimsAreTrue — the warnings are only worth printing if they are
    accurate, so each headline claim is checked against the pattern the shell
    server really builds for that grant;
  * the surfaces that must emit the warning (shell startup, CLI, API).
"""

import re

import pytest

from app.utils import git_grant_hazards as gh


# ── catalogue ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sub", [
    "push", "pull", "fetch", "checkout", "switch", "restore", "reset", "clean",
    "stash", "rebase", "merge", "rm", "mv", "branch", "tag", "worktree", "gc",
    "filter-branch", "config", "commit", "add", "clone",
])
def test_every_mutating_subcommand_has_a_hazard(sub):
    h = gh.assess(f"git {sub}")
    assert h is not None and h.hazards and h.reviewed


@pytest.mark.parametrize("sub, op", [
    ("add", "op=add"), ("commit", "op=commit"), ("push", "op=push"),
    ("stash", "op=stash_push"), ("stash", "op=stash_pop"), ("fetch", "op=fetch"),
    ("clone", "op=clone"), ("switch", "op=switch"), ("checkout", "op=checkout"),
    ("restore", "op=unstage"), ("restore", "op=checkout"), ("reset", "op=unstage"),
])
def test_tool_covered_subcommands_redirect_to_the_right_op(sub, op):
    assert op in (gh.assess(f"git {sub}").alternative or "")


@pytest.mark.parametrize("sub", ["rebase", "clean", "pull", "gc"])
def test_uncovered_subcommands_do_not_pretend_the_tool_covers_them(sub):
    assert gh.assess(f"git {sub}").alternative is None


def test_push_warning_names_force_and_delete():
    text = " ".join(gh.assess("git push").hazards)
    assert "--force" in text and "--delete" in text


@pytest.mark.parametrize("entry", ["git status", "git grep", "git ls-files", "git rev-parse"])
def test_pure_read_only_grants_are_not_flagged(entry):
    assert gh.assess(entry) is None


@pytest.mark.parametrize("entry", ["git log", "git diff", "git show", "git blame"])
def test_explicit_read_grant_is_flagged_for_output_bypass(entry):
    h = gh.assess(entry)
    assert h and "--output" in h.hazards[0]


def test_unknown_subcommand_is_flagged_as_unreviewed():
    h = gh.assess("git frobnicate")
    assert h and not h.reviewed and "every flag" in h.hazards[0]


def test_bare_git_is_flagged():
    h = gh.assess("git")
    assert h and h.subcommand is None and "reset --hard" in h.hazards[0]


@pytest.mark.parametrize("entry", ["ls", "gitk", "grep git", "", "  "])
def test_non_git_entries_are_ignored(entry):
    assert gh.assess(entry) is None


def test_entry_with_args_uses_the_subcommand():
    h = gh.assess("git commit -m")
    assert h and h.subcommand == "commit" and h.grant == "git commit -m"


def test_assess_all_dedups_and_keeps_order():
    got = [h.grant for h in gh.assess_all(["ls", "git push", "git status", "git push", "git"])]
    assert got == ["git push", "git"]


def test_format_lines_carries_hazards_and_redirect():
    lines = gh.format_lines(gh.assess("git push"))
    assert lines[0].startswith("'git push' admits every flag")
    assert any("--force" in ln for ln in lines) and lines[-1].startswith("  Prefer: git tool op=push")


# ── the claims are true ───────────────────────────────────────────────────

@pytest.fixture
def patterns_for(monkeypatch):
    """Patterns the real shell server builds for a given allowlist."""
    monkeypatch.setenv("GIT_OPERATIONS_ENABLED", "true")
    from app.mcp_servers.shell_server import ShellServer
    server = ShellServer()

    # After __init__, allowed_commands also holds display-only "git <op>"
    # entries for the safe tier; rebuilding from that list would turn them
    # into explicit (unguarded) grants.  Model the allowlist AS CONFIGURED.
    configured = [c for c in server.allowed_commands if not c.startswith("git")]

    def build(*grants):
        server.allowed_commands = configured + list(grants)
        return server._build_safe_command_patterns()
    return build


def _admits(patterns, cmd):
    return any(re.match(p, cmd, re.IGNORECASE | re.DOTALL) for p in patterns.values())


class TestClaimsAreTrue:
    @pytest.mark.parametrize("grant, cmd", [
        ("git push", "git push --force origin main"),
        ("git push", "git push origin :main"),
        ("git checkout", "git checkout -- ."),
        ("git commit", "git commit --amend --no-verify -m x"),
        ("git stash", "git stash clear"),
        ("git add", "git add -f -A"),
        ("git log", "git log -p --output=untracked.txt"),
    ])
    def test_grant_admits_the_destructive_form(self, patterns_for, grant, cmd):
        assert _admits(patterns_for(grant), cmd)

    def test_without_the_grant_the_form_is_not_admitted(self, patterns_for):
        """Positive control: the admission comes from the explicit grant."""
        base = patterns_for()
        assert not _admits(base, "git push --force origin main")
        assert not _admits(base, "git log -p --output=untracked.txt")


# ── surfaces that must say it ─────────────────────────────────────────────

class TestShellStartupWarns:
    def test_explicit_grant_warns_at_build(self, patterns_for, capsys):
        patterns_for("git push")
        err = capsys.readouterr().err
        assert "'git push' admits every flag" in err and "--force" in err
        assert "Prefer: git tool op=push" in err

    def test_no_git_grant_no_warning(self, patterns_for, capsys):
        capsys.readouterr()
        patterns_for()
        assert "admits every flag" not in capsys.readouterr().err

    def test_safe_tier_display_entries_are_not_warned(self, monkeypatch, capsys):
        """A default server must not cry wolf about its own read-only tier."""
        monkeypatch.setenv("GIT_OPERATIONS_ENABLED", "true")
        from app.mcp_servers.shell_server import ShellServer
        ShellServer()
        assert "admits every flag" not in capsys.readouterr().err


def _cli():
    from app.cli import CLI
    cli = CLI.__new__(CLI)
    cli._session_shell_commands = ["ls"]

    async def _no_restart():
        return None
    cli._restart_shell_server = _no_restart
    return cli


class TestCliWarnsAndRedirects:
    @pytest.mark.asyncio
    async def test_shell_git_push_warns_and_suggests_the_tool(self, capsys):
        cli = _cli()
        await cli._handle_shell_command("git push")
        out = capsys.readouterr().out
        assert "git push" in cli._session_shell_commands, "grant still works (not removed)"
        assert "--force" in out and "git tool op=push" in out

    @pytest.mark.asyncio
    async def test_shell_add_git_checkout_warns(self, capsys):
        cli = _cli()
        await cli._handle_shell_command("add git checkout")
        out = capsys.readouterr().out
        assert "git checkout" in cli._session_shell_commands
        assert "discards uncommitted changes" in out

    @pytest.mark.asyncio
    async def test_shell_add_non_git_is_quiet(self, capsys):
        cli = _cli()
        await cli._handle_shell_command("add jq")
        assert "admits every flag" not in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_help_points_at_the_tool(self, capsys):
        await _cli()._handle_shell_command("git")
        out = capsys.readouterr().out
        assert "git tool" in out


class TestApi:
    @pytest.mark.asyncio
    async def test_hazards_endpoint(self):
        from app.routes import mcp_routes
        out = mcp_routes.git_grant_hazards(
            mcp_routes.GitGrantHazardsRequest(commands=["ls", "git push", "git status", "git"]))
        grants = [h["grant"] for h in out["hazards"]]
        assert grants == ["git push", "git"]
        assert "op=push" in out["hazards"][0]["alternative"]
