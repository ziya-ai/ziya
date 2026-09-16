"""``ShellWriteChecker._inplace_edit`` must judge exactly the files being edited.

Two defects in how the in-place editor's target list was derived:

1. Redirection tokens were not stripped, so ``sed -i s/a/b/ tests/x 2>/dev/null``
   was refused for "targeting '2>/dev/null'" (``_destructive`` already used
   ``_strip_redirections``; ``_inplace_edit`` did not).

2. The first non-flag token was unconditionally treated as the script.  When
   the script is supplied through an option -- ``--expression=S``, glued
   ``-eS``, or separate ``-e S`` / ``-f FILE`` -- that heuristic drops the
   first *file* from the target list instead.  ``sed -i --expression=s/a/b/
   app/main.py tests/x`` therefore edited ``app/main.py`` unchecked whenever
   a later argument was approved.

The policy here approves ``tests/`` (plus the defaults); ``app/`` is not
approved, so any verdict that lets ``app/main.py`` through is a hole.
"""
from __future__ import annotations

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers import write_policy as wp
from app.mcp_servers.write_policy import ShellWriteChecker
from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "app").mkdir()
    return str(root)


@pytest.fixture
def checker(project_root):
    pm = WritePolicyManager()
    pm.load_for_project("inplace-test", project_root)
    pm._policy["safe_write_paths"] = list(pm._policy["safe_write_paths"]) + ["tests/"]
    c = ShellWriteChecker(pm)
    c.set_project_root(project_root)
    yield c
    c.clear_project_root()


@pytest.fixture
def split():
    return ShellServer.__new__(ShellServer)._split_by_shell_operators


class TestRedirectionTokens:
    @pytest.mark.parametrize("cmd", [
        "sed -i s/a/b/ tests/x 2>/dev/null",
        "sed -i s/a/b/ tests/x > /tmp/log",
        "sed -i s/a/b/ tests/x >/tmp/log 2>&1",
        "sed -i s/a/b/ tests/x 2>&1",
    ])
    def test_redirection_is_not_a_target(self, checker, split, cmd):
        ok, reason = checker.check(cmd, split)
        assert ok, reason

    def test_redirection_target_itself_is_still_policed(self, checker, split):
        ok, reason = checker.check("sed -i s/a/b/ tests/x > app/log", split)
        assert not ok
        assert "Redirection" in reason

    def test_unapproved_file_still_denied_with_redirection(self, checker, split):
        ok, reason = checker.check("sed -i s/a/b/ app/main.py 2>/dev/null", split)
        assert not ok
        assert "app/main.py" in reason
        assert "2>/dev/null" not in reason


class TestScriptOptionForms:
    """Every way of supplying the script must leave every file a target."""

    @pytest.mark.parametrize("cmd", [
        "sed -i --expression=s/a/b/ app/main.py tests/x",
        "sed -i -es/a/b/ app/main.py tests/x",
        "sed -i -e s/a/b/ app/main.py tests/x",
        "sed -i --expression s/a/b/ app/main.py tests/x",
        "sed -i -f /tmp/s.sed app/main.py tests/x",
        "sed -i --file=/tmp/s.sed app/main.py tests/x",
        "sed -ne s/a/b/p -i app/main.py tests/x",
        "perl -i -pe s/a/b/ app/main.py tests/x",
        "perl -pi -e s/a/b/ app/main.py tests/x",
        "perl -i -pE s/a/b/ app/main.py tests/x",
        "awk -i inplace -f /tmp/p.awk app/main.py tests/x",
    ])
    def test_first_file_is_not_mistaken_for_the_script(self, checker, split, cmd):
        ok, reason = checker.check(cmd, split)
        assert not ok, f"{cmd!r} edited app/main.py unchecked"
        assert "app/main.py" in reason

    @pytest.mark.parametrize("cmd", [
        "sed -i --expression=s/a/b/ tests/x",
        "sed -i -es/a/b/ tests/x tests/y",
        "sed -i -e s/a/b/ tests/x",
        "sed -i -f /tmp/s.sed tests/x",
        "perl -i -pe s/a/b/ tests/x",
        "sed -i s/a/b/ tests/x",
        "sed --in-place s/a/b/ tests/x",
    ])
    def test_approved_files_allowed_under_every_form(self, checker, split, cmd):
        ok, reason = checker.check(cmd, split)
        assert ok, reason

    def test_script_value_is_not_treated_as_a_file(self, checker, split):
        # ``-e s/a/b/``: the separate value is the script, not a path; it
        # must not be resolved against the project root and refused.
        ok, reason = checker.check("sed -i -e s/a/b/ tests/x", split)
        assert ok, reason

    def test_script_via_option_with_no_files_is_refused(self, checker, split):
        ok, _ = checker.check("sed -i --expression=s/a/b/", split)
        assert not ok

    def test_positional_script_with_no_files_is_refused(self, checker, split):
        ok, _ = checker.check("sed -i s/a/b/", split)
        assert not ok


class TestTargetExtractor:
    """Direct checks on the helper so the rules are documented by test."""

    def test_positional_script(self):
        assert wp._inplace_targets("sed", ["sed", "-i", "s/a/b/", "f1", "f2"]) == ["f1", "f2"]

    def test_glued_short_script(self):
        assert wp._inplace_targets("sed", ["sed", "-i", "-es/a/b/", "f1"]) == ["f1"]

    def test_separate_short_script_consumes_value(self):
        assert wp._inplace_targets("sed", ["sed", "-i", "-e", "s/a/b/", "f1"]) == ["f1"]

    def test_long_with_equals(self):
        assert wp._inplace_targets("sed", ["sed", "-i", "--expression=s/a/b/", "f1"]) == ["f1"]

    def test_long_separate_consumes_value(self):
        assert wp._inplace_targets("sed", ["sed", "-i", "--file", "s.sed", "f1"]) == ["f1"]

    def test_perl_cluster_ending_in_e(self):
        assert wp._inplace_targets("perl", ["perl", "-i", "-pe", "s/a/b/", "f1"]) == ["f1"]

    def test_double_dash_ends_options(self):
        assert wp._inplace_targets("sed", ["sed", "-i", "-e", "s/a/b/", "--", "-weird", "f1"]) == ["-weird", "f1"]

    def test_no_files(self):
        assert wp._inplace_targets("sed", ["sed", "-i", "s/a/b/"]) == []
        assert wp._inplace_targets("sed", ["sed", "-i", "-e", "s/a/b/"]) == []
