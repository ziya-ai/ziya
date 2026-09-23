"""A newline INSIDE a quoted argument is data, not a command separator.

Regression for a false-positive WRITE BLOCK on a multi-line
``python3 -c "..."`` one-liner:

    python3 -c "
    for m in mems:
        if len(pp)>1: pairs[tuple(pp)]+=1
    "

    -> "Redirection to '1:' blocked."

Mechanism: ``ShellWriteChecker._check`` pre-split the command on every raw
``\\n`` (``str.split``) before handing each line to the quote-aware operator
splitter.  The opening ``"`` lived on line 1, so every later line arrived at
``_redirection`` as its own segment with NO quote context, and ``>1:`` in the
Python source read as a shell redirection to a file named ``1:``.

``_redirection`` itself is quote-aware, and so is the server's
``_split_by_shell_operators`` (which already treats an unquoted newline as
``;``).  The only piece that shredded quoted text was the line pre-split, so
the fix makes that pre-split honour quote state.  These tests pin both
directions: a newline inside quotes is data, and an unquoted newline still
separates commands so a write hidden on line 2 is still validated.

The same shredding was also a BYPASS, not just a false positive.
``_interpreter`` only inspects a segment whose first token is an allowed
interpreter.  Pre-fix, ``python3 -c "<newline>import os<newline>os.system('id')"``
became segments ``python3 -c "`` (nothing to inspect) and ``import os`` /
``os.system('id')`` (first token not an interpreter, so skipped), so neither
the process-spawn gate [PenPal #157] nor the write-indicator gate ever saw
the body -- and the allowlist gate had already passed the whole thing as
one ``python3`` segment.  ``TestQuoteNestingAndOtherInterpreters`` pins that
the body is now seen whole; those "still caught" tests FAIL against the
pre-fix code, which is the evidence for the bypass.
"""
import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker
from app.mcp_servers.shell_server import ShellServer


@pytest.fixture
def project_root(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / ".ziya").mkdir()
    return str(proj)


@pytest.fixture
def checker(project_root):
    pm = WritePolicyManager()
    pm.load_for_project("test-project", project_root)
    c = ShellWriteChecker(pm)
    c.set_project_root(project_root)
    yield c
    c.clear_project_root()


def _split(cmd):
    # The real splitter the shell server passes to check().
    return ShellServer._split_by_shell_operators(ShellServer.__new__(ShellServer), cmd)


# The shape of the command that was blocked in the field: a comparison
# operator followed by a digit and a colon, on a line after the opening quote.
MULTILINE_PY = (
    'python3 -c "\n'
    'import collections\n'
    'pairs = collections.Counter()\n'
    'for pp in [[1, 2], [3]]:\n'
    '    if len(pp)>1: pairs[tuple(pp)]+=1\n'
    'print(dict(pairs))\n'
    '"'
)


class TestNewlineInsideQuotesIsData:

    def test_multiline_python_c_with_gt_digit_colon_is_allowed(self, checker):
        ok, reason = checker.check(MULTILINE_PY, _split)
        assert ok, reason

    def test_reason_never_names_a_python_fragment_as_a_redirect_target(self, checker):
        ok, reason = checker.check(MULTILINE_PY, _split)
        assert "1:" not in reason

    def test_single_quoted_multiline_script_is_allowed(self, checker):
        cmd = (
            "python3 -c '\n"
            "x = 5\n"
            "if x>1: print(x)\n"
            "'"
        )
        ok, reason = checker.check(cmd, _split)
        assert ok, reason

    def test_escaped_quote_inside_quotes_does_not_flip_state(self, checker):
        # r\"...\" inside a double-quoted script (as in the field command).
        cmd = (
            'python3 -c "\n'
            'import re\n'
            'r = re.sub(r\\"//[^@/]+@\\", \\"//\\", \\"a\\")\n'
            'if len(r)>1: print(r)\n'
            '"'
        )
        ok, reason = checker.check(cmd, _split)
        assert ok, reason


class TestUnquotedNewlineStillSeparates:
    """The fix must not weaken the gate: a write on its own line is still
    a separate segment and is still judged."""

    def test_redirect_on_second_line_is_still_blocked(self, checker):
        cmd = 'echo hi\necho pwned > app/main.py'
        ok, reason = checker.check(cmd, _split)
        assert not ok
        assert "app/main.py" in reason

    def test_redirect_after_quoted_multiline_arg_is_still_blocked(self, checker):
        cmd = MULTILINE_PY + '\necho x > app/main.py'
        ok, reason = checker.check(cmd, _split)
        assert not ok
        assert "app/main.py" in reason

    def test_destructive_command_on_second_line_is_still_blocked(self, checker):
        cmd = 'ls\nrm app/main.py'
        ok, reason = checker.check(cmd, _split)
        assert not ok

    def test_quoted_redirect_text_inside_script_is_not_a_write(self, checker):
        # Same fragment on ONE line — must be allowed both before and after
        # the fix (proves the multi-line case is the only thing that changed).
        cmd = 'python3 -c "if 2>1: print(1)"'
        ok, reason = checker.check(cmd, _split)
        assert ok, reason

    def test_semicolon_after_quoted_multiline_arg_is_still_blocked(self, checker):
        # The quoted-newline handling must not swallow a later ``;`` operator.
        cmd = MULTILINE_PY + '; echo y > app/main.py'
        ok, reason = checker.check(cmd, _split)
        assert not ok
        assert "app/main.py" in reason

    def test_backslash_newline_continuation_still_reaches_redirect(self, checker):
        # ``\\<newline>`` is a line continuation, not quoted data: the
        # redirect on the physical second line belongs to the same command
        # and must still be judged.
        cmd = 'echo a \\\n  > app/main.py'
        ok, reason = checker.check(cmd, _split)
        assert not ok
        assert "app/main.py" in reason

    def test_heredoc_body_with_open_quote_does_not_hide_following_write(self, checker):
        # Heredoc bodies are stripped BEFORE the quote-aware line split, so an
        # unbalanced quote inside the body cannot swallow the command after
        # the terminator. If the order were reversed this would leak.
        cmd = 'cat <<EOF\n"unterminated body text\nEOF\necho x > app/main.py'
        ok, reason = checker.check(cmd, _split)
        assert not ok
        assert "app/main.py" in reason

    def test_redirect_inside_heredoc_body_is_data(self, checker):
        cmd = 'cat <<EOF\nif a>1:\n    pass\nEOF'
        ok, reason = checker.check(cmd, _split)
        assert ok, reason


class TestQuoteNestingAndOtherInterpreters:
    """Quote-state tracking must handle the forms scripts actually use."""

    @pytest.mark.parametrize("cmd", [
        # single quotes nested inside a double-quoted script
        'python3 -c "print(\'>1:\')\nif a>1: pass"',
        # double quotes nested inside a single-quoted script
        "python3 -c 'print(\">1:\")\nif a>1: pass'",
        # backslash is literal inside single quotes; must not escape the
        # closing quote and must not desync the walker
        "python3 -c 'print(\"\\\\\")\nif a>1: pass'",
        # a real (not shell) comparison operator on a later line
        "python3 -c '\nprint(1>2)\nprint(\"a>b\")\n'",
        # CRLF line endings inside the quoted body
        'python3 -c "\r\nif a>1: pass\r\n"',
        # node -e with a multi-line body containing ``>``
        'node -e "\nconst a = [1,2];\nif (a.length>1) console.log(1)\n"',
    ])
    def test_multiline_quoted_bodies_are_allowed(self, checker, cmd):
        ok, reason = checker.check(cmd, _split)
        assert ok, reason

    def test_list_form_subprocess_in_multiline_body_is_allowed(self, checker):
        # No shell=True, no destructive argv: neither the process-spawn nor
        # the write indicators apply. Pins that the field command (which
        # called ``git`` this way) passes once the newline bug is fixed.
        cmd = (
            'python3 -c "\n'
            'import subprocess\n'
            "r = subprocess.run(['git', 'status'], capture_output=True, text=True)\n"
            'if len(r.stdout)>1: print(r.stdout)\n'
            '"'
        )
        ok, reason = checker.check(cmd, _split)
        assert ok, reason

    def test_shell_true_in_multiline_body_is_still_caught(self, checker):
        # With the body no longer shredded, _interpreter sees the whole
        # script — the process-spawn gate must still fire on a later line.
        cmd = (
            'python3 -c "\n'
            'import subprocess\n'
            "subprocess.run('ls', shell=True)\n"
            '"'
        )
        ok, reason = checker.check(cmd, _split)
        assert not ok
        assert "spawn a process" in reason

    def test_write_indicator_on_later_line_is_still_caught(self, checker):
        cmd = (
            'python3 -c "\n'
            'import shutil\n'
            "shutil.rmtree('src')\n"
            '"'
        )
        ok, reason = checker.check(cmd, _split)
        assert not ok
        assert "write files" in reason


class TestUnterminatedQuoteIsRefusedUpstream:
    """An unterminated quote now makes the write checker treat the whole
    remainder as quoted data, so a redirect after the open quote is invisible
    to it. That is only safe because ``ShellServer.is_command_allowed`` —
    which ``handle_request`` runs BEFORE the write check — refuses any
    command whose quote state is still open. Pin that gate here: if it were
    ever relaxed, the write checker would silently stop covering this shape.
    """

    @pytest.mark.parametrize("cmd", [
        'python3 -c "\nx=1\n echo > app/main.py',
        "python3 -c '\nx=1\n echo > app/main.py",
    ])
    def test_allowlist_gate_refuses_unterminated_quote(self, cmd):
        server = ShellServer()
        ok, reason = server.is_command_allowed(cmd)
        assert not ok
        assert "unterminated quote" in reason

    def test_terminated_multiline_script_passes_allowlist_gate(self):
        # Positive control for the gate: the same shape, properly closed.
        server = ShellServer()
        ok, reason = server.is_command_allowed(MULTILINE_PY)
        assert ok, reason
