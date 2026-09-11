"""A quoted ``">text"`` argument must never be treated as a redirection.

Regression for a leak that created files named after grep patterns, e.g.
``frontend/src/[A-Za-z][A-Za-z ]{4,}<``.

Mechanism: ``_expand_and_tokenize`` uses ``shlex.split``, which STRIPS
quotes.  ``_extract_redirections`` then matched ``^([12]?)(>>|>)(.*)$``
against the resulting tokens, so a token that came from a quoted
``">pattern"`` argument was indistinguishable from a real ``>pattern``
operator.  Two things went wrong at once:

  1. The argument was silently removed from argv, so ``grep`` lost its
     pattern and read the next argument as one -- a wrong answer with no
     error.
  2. A file was created that the write-policy layer never approved.
     ``write_policy._redirection`` IS quote-aware and correctly saw no
     redirection, so it never checked a target against the allowed paths.
     The executor invented the write afterwards.  That second point is the
     serious one: it is an unvalidated arbitrary-path write.

The fix derives the operator set from the RAW segment text, where quoting
is still visible, and honours a token as an operator only if it appears
there.  These tests pin both directions: quoted text stays data, and every
genuine redirection form still works -- a fix that suppressed real
redirections would silently stop them being validated at all.
"""
import shlex
import subprocess

import pytest

from app.mcp_servers.shell_server import (
    ShellServer,
    _split_raw_words,
    _unquoted_redirect_words,
)

extract = ShellServer._extract_redirections


def _tokenize_and_extract(command: str):
    """Mirror what the executor does: shlex tokens + raw-text operator set."""
    return extract(shlex.split(command), _unquoted_redirect_words(command))


# The two commands recovered from the audit log that created junk files.
AUDIT_CMD_1 = (
    'grep -rn ">[A-Z][a-zA-Z ]{3,}<\\|(label\\|title\\|placeholder\\|'
    'aria-label\\|tooltip)=\\"[A-Z]" frontend/src'
)
AUDIT_CMD_2 = 'grep -rn ">[A-Za-z][A-Za-z ]{4,}<" frontend/src'


class TestSplitRawWords:
    """Word breaks must match shlex's, but the source text is preserved."""

    def test_preserves_double_quotes(self):
        assert _split_raw_words('grep ">abc" file') == ['grep', '">abc"', 'file']

    def test_preserves_single_quotes(self):
        assert _split_raw_words("grep '>x' f") == ['grep', "'>x'", 'f']

    def test_quoted_space_does_not_split(self):
        assert _split_raw_words('grep "a b c" f') == ['grep', '"a b c"', 'f']

    def test_word_count_agrees_with_shlex(self):
        for cmd in [
            'echo hello world',
            'grep ">a b" f',
            "grep '>x' f",
            'a   b\t\tc',
            'echo hi >out.txt',
        ]:
            assert len(_split_raw_words(cmd)) == len(shlex.split(cmd)), cmd


class TestQuotedTextIsNotAnOperator:

    @pytest.mark.parametrize("command", [
        AUDIT_CMD_1,
        AUDIT_CMD_2,
        'grep ">x" f',
        "grep '>x' f",
        'grep ">>x" f',
        'grep "2>&1" f',
        'grep "1>x" f',
    ])
    def test_no_operator_detected(self, command):
        assert _unquoted_redirect_words(command) == [], (
            f"a quoted argument in {command!r} was read as a redirection "
            f"operator; it will be dropped from argv AND create a file the "
            f"write policy never approved"
        )

    @pytest.mark.parametrize("command", [AUDIT_CMD_1, AUDIT_CMD_2])
    def test_audit_command_invents_no_redirection(self, command):
        _, redir = _tokenize_and_extract(command)
        assert redir == {}, (
            "this is the exact leak that created files named after grep "
            "patterns in frontend/src"
        )

    def test_pattern_survives_in_argv(self):
        args, _ = _tokenize_and_extract(AUDIT_CMD_2)
        assert args == ['grep', '-rn', '>[A-Za-z][A-Za-z ]{4,}<', 'frontend/src'], (
            "grep must still receive its pattern; dropping it makes grep read "
            "the next argument as the pattern and return a wrong answer"
        )


class TestRealRedirectionsStillWork:
    """Positive controls.

    Over-suppressing is not the safe direction: a redirection the executor
    stops recognising is also one the write-policy layer stops validating.
    """

    @pytest.mark.parametrize("command,expect_op", [
        ('echo hi >out.txt', ['>out.txt']),
        ('echo hi > out.txt', ['>']),
        ('echo hi >>out.txt', ['>>out.txt']),
        ('cmd 2>&1', ['2>&1']),
        ('cmd 2>/dev/null', ['2>/dev/null']),
        ('cmd 1>file', ['1>file']),
    ])
    def test_operator_detected(self, command, expect_op):
        assert _unquoted_redirect_words(command) == expect_op

    def test_fused_stdout_target(self):
        args, redir = _tokenize_and_extract('echo hi >out.txt')
        assert args == ['echo', 'hi']
        assert redir == {'stdout': ('file', 'out.txt', 'w')}

    def test_split_stdout_target(self):
        args, redir = _tokenize_and_extract('echo hi > out.txt')
        assert args == ['echo', 'hi']
        assert redir == {'stdout': ('file', 'out.txt', 'w')}

    def test_append_mode(self):
        _, redir = _tokenize_and_extract('echo hi >>out.txt')
        assert redir == {'stdout': ('file', 'out.txt', 'a')}

    def test_stderr_to_stdout(self):
        args, redir = _tokenize_and_extract('cmd 2>&1')
        assert args == ['cmd']
        assert redir == {'stderr': subprocess.STDOUT}

    def test_stderr_to_devnull(self):
        args, redir = _tokenize_and_extract('cmd 2>/dev/null')
        assert args == ['cmd']
        assert redir == {'stderr': subprocess.DEVNULL}

    def test_quoted_data_and_real_operator_together(self):
        """The discriminating case: both in one segment."""
        args, redir = _tokenize_and_extract('grep ">x" f >real.txt')
        assert args == ['grep', '>x', 'f']
        assert redir == {'stdout': ('file', 'real.txt', 'w')}


class TestBackwardCompatibility:
    """``operator_words=None`` must keep the pre-fix behaviour exactly.

    Other callers and older tests rely on the quote-blind path, so the new
    argument is additive rather than a change of contract.
    """

    @pytest.mark.parametrize("command", [
        'echo hi >out.txt',
        'echo hi > out.txt',
        'echo hi >>out.txt',
        'cmd 2>&1',
        'cmd 2>/dev/null',
        'echo plain words only',
    ])
    def test_none_matches_explicit_list_for_real_redirections(self, command):
        toks = shlex.split(command)
        assert extract(list(toks)) == extract(
            list(toks), _unquoted_redirect_words(command)
        )

    def test_legacy_path_still_exhibits_the_bug(self):
        """Guards against the fix being silently bypassed.

        If this ever starts failing, the quote-blind path has changed and
        the ``operator_words`` plumbing may no longer be what protects the
        executor -- worth knowing rather than passing quietly.
        """
        toks = shlex.split(AUDIT_CMD_2)
        args, redir = extract(list(toks))
        assert '>[A-Za-z][A-Za-z ]{4,}<' not in args
        assert redir != {}


class TestExecutorActuallyPassesTheOperatorSet:
    """Pin the CALL SITE, not just the helpers.

    Every test above supplies ``operator_words`` itself, so they would all
    stay green if ``_execute_pipeline`` were reverted to calling
    ``_extract_redirections(args)`` with one argument -- and the leak would
    be fully reopened while the suite reported success.  The helpers being
    correct is worthless if the executor does not use them.
    """

    @staticmethod
    def _pipeline_source() -> str:
        import inspect
        from app.mcp_servers import shell_server
        src = inspect.getsource(shell_server.ShellServer._execute_pipeline)
        # Drop comment-only lines: this file DOCUMENTS the old one-argument
        # call, and a naive substring scan would match the prose.
        return "\n".join(
            ln for ln in src.splitlines()
            if not ln.lstrip().startswith('#')
        )

    def test_extract_is_called_with_an_operator_set(self):
        src = self._pipeline_source()
        assert '_unquoted_redirect_words(' in src, (
            "_execute_pipeline must derive the operator set from the raw "
            "segment; without it _extract_redirections is quote-blind again "
            "and a quoted \">pattern\" becomes an unvalidated file write"
        )

    def test_operator_set_is_built_from_the_same_expansion(self):
        """``_expand_vars`` must feed BOTH the tokenizer and the scanner.

        If they expanded differently, ``cmd >$OUT`` would be an operator to
        one and data to the other, reintroducing the disagreement in a
        subtler form.
        """
        src = self._pipeline_source()
        assert '_expand_vars(' in src

    def test_no_bare_single_argument_call_remains(self):
        """No ``_extract_redirections(args)`` call without the operator set."""
        import re
        src = self._pipeline_source()
        bare = re.findall(r'_extract_redirections\(\s*args\s*\)', src)
        assert bare == [], (
            f"found {len(bare)} quote-blind call(s) to _extract_redirections; "
            f"each one reopens the leak"
        )
