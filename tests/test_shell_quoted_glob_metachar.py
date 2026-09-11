"""A quoted glob pattern must be passed through literally, never expanded.

Regression for the second half of the quote-blindness bug in the shell
server.  ``_expand_and_tokenize`` uses ``shlex.split``, which STRIPS quotes,
and then globbed any resulting token containing ``*``, ``?`` or ``[`` --
with no knowledge of whether those characters had been quoted.  All three
of these tokenized identically, which is wrong for two of them::

    find . -name '*.json'     ->  find . -name <expanded> <expanded> ...
    find . -name "*.json"     ->  find . -name <expanded> <expanded> ...
    find . -name *.json       ->  find . -name <expanded> <expanded> ...

Observed consequences:

  1. ``find . -name '*.json'`` failed outright with ``unknown primary or
     operator``, because ``find`` received two filenames where it expected
     one pattern.
  2. Silent wrong answers wherever a tool takes its own pattern: ``grep
     '*.log'``, ``rsync --exclude '*.tmp'``, ``tar --wildcards``.
  3. The destructive case: ``rm '*.txt'`` fans out over every match instead
     of targeting the single file literally named ``*.txt``.  Within an
     approved write path nothing would stop it, and the write-policy layer
     cannot help -- it sees the command before expansion, so the deletion
     it approves is not the deletion that happens.

The fix asks the RAW (pre-``shlex``) word whether a metacharacter was
unquoted, and globs only then.  These tests pin both directions: quoted
patterns stay literal, and unquoted ones still expand -- a fix that
disabled globbing outright would break ordinary commands.
"""
import os

import pytest

from app.mcp_servers.shell_server import (
    ShellServer,
    _has_unquoted_glob,
    _split_raw_words,
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A cwd holding real files, so a wrongly-globbed pattern is visible.

    The expansion bug only manifests when matches EXIST: with no matches the
    literal pattern is passed through and a quote-blind implementation looks
    correct.  Every file here is therefore load-bearing.
    """
    for name in ('a.json', 'b.json', 'x.txt', 'y.txt', 'a1c.log'):
        (tmp_path / name).write_text('.')
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def server():
    return ShellServer.__new__(ShellServer)


class TestHasUnquotedGlob:
    """The per-word verdict, in isolation."""

    @pytest.mark.parametrize('raw', [
        '*.json',
        'a?c',
        'a[bc]',
        'pre*post',
        '"a"*.json',          # metachar outside the quotes
        r'a\\*b',             # escaped BACKSLASH, so the star is bare
    ])
    def test_unquoted_metachar_detected(self, raw):
        assert _has_unquoted_glob(raw) is True

    @pytest.mark.parametrize('raw', [
        "'*.json'",
        '"*.json"',
        "'a?c'",
        "'a[bc]'",
        r'a\*b',              # backslash-escaped star
        'plain.json',
        '',
        "''",
        'a"*".json',          # metachar inside the quotes
    ])
    def test_quoted_or_absent_metachar_not_detected(self, raw):
        assert _has_unquoted_glob(raw) is False

    def test_quote_inside_other_quote_is_literal(self):
        # A double quote inside single quotes does not open a dq region, so
        # the star that follows is still quoted.
        assert _has_unquoted_glob("'a\"*'") is False

    def test_backslash_is_literal_inside_single_quotes(self):
        # Bash does not honour escapes inside '...', so the backslash here
        # cannot escape anything and the star stays quoted by the sq region.
        assert _has_unquoted_glob(r"'a\*b'") is False


class TestQuotedPatternsStayLiteral:
    """The reported bug, both quoting styles."""

    def test_single_quoted_find_pattern(self, sandbox, server):
        assert server._expand_and_tokenize("find . -name '*.json'") == [
            'find', '.', '-name', '*.json',
        ]

    def test_double_quoted_find_pattern(self, sandbox, server):
        assert server._expand_and_tokenize('find . -name "*.json"') == [
            'find', '.', '-name', '*.json',
        ]

    def test_quoted_question_mark(self, sandbox, server):
        # a1c.log exists, so a quote-blind implementation would expand this.
        assert server._expand_and_tokenize("grep -rn 'a?c' .") == [
            'grep', '-rn', 'a?c', '.',
        ]

    def test_quoted_bracket_class(self, sandbox, server):
        assert server._expand_and_tokenize("ls 'a[bc].json'") == [
            'ls', 'a[bc].json',
        ]

    def test_backslash_escaped_metachar(self, sandbox, server):
        assert server._expand_and_tokenize(r'ls \*.json') == ['ls', '*.json']

    def test_quoted_rm_does_not_fan_out(self, sandbox, server):
        """The destructive case.

        x.txt and y.txt both exist.  Expanding here would turn a request to
        delete one literally-named file into a request to delete two real
        ones -- and the write-policy layer, which inspects the command
        BEFORE expansion, would have approved only the former.
        """
        argv = server._expand_and_tokenize("rm '*.txt'")
        assert argv == ['rm', '*.txt']
        assert 'x.txt' not in argv and 'y.txt' not in argv


class TestUnquotedGlobsStillExpand:
    """A fix that just stopped globbing would pass every test above."""

    def test_unquoted_star_expands(self, sandbox, server):
        assert server._expand_and_tokenize('ls *.json') == [
            'ls', 'a.json', 'b.json',
        ]

    def test_unquoted_expansion_is_sorted(self, sandbox, server):
        argv = server._expand_and_tokenize('ls *.txt')
        assert argv == ['ls', 'x.txt', 'y.txt']

    def test_unquoted_question_mark_expands(self, sandbox, server):
        assert server._expand_and_tokenize('ls a?c.log') == ['ls', 'a1c.log']

    def test_unquoted_rm_still_expands(self, sandbox, server):
        # Unchanged behaviour: this one really is a multi-file request.
        assert server._expand_and_tokenize('rm *.txt') == [
            'rm', 'x.txt', 'y.txt',
        ]

    def test_unmatched_unquoted_pattern_passes_through(self, sandbox, server):
        # Bash parity: a pattern matching nothing is passed literally.
        assert server._expand_and_tokenize('ls *.nomatch') == [
            'ls', '*.nomatch',
        ]

    def test_command_word_is_never_globbed(self, sandbox, server):
        # argv[0] is excluded from expansion, before and after the fix.
        assert server._expand_and_tokenize('ls')[0] == 'ls'


class TestVariableExpansionParity:
    """Quoting must be judged AFTER substitution, as bash does.

    The scanner and the tokenizer must expand identically.  If they did not,
    a pattern arriving via a variable would be a glob to one and a literal
    to the other.
    """

    def test_unquoted_var_holding_pattern_expands(self, sandbox, server, monkeypatch):
        monkeypatch.setenv('PAT', '*.json')
        assert server._expand_and_tokenize('ls $PAT') == [
            'ls', 'a.json', 'b.json',
        ]

    def test_quoted_var_holding_pattern_stays_literal(self, sandbox, server, monkeypatch):
        monkeypatch.setenv('PAT', '*.json')
        assert server._expand_and_tokenize('ls "$PAT"') == ['ls', '*.json']

    def test_extra_env_unquoted_expands(self, sandbox, server):
        assert server._expand_and_tokenize('ls $P2', {'P2': '*.json'}) == [
            'ls', 'a.json', 'b.json',
        ]

    def test_extra_env_quoted_stays_literal(self, sandbox, server):
        assert server._expand_and_tokenize('ls "$P2"', {'P2': '*.json'}) == [
            'ls', '*.json',
        ]


class TestMalformedQuotesFallBack:
    """An unparseable segment must degrade, not raise or silently mis-glob."""

    def test_unterminated_quote_does_not_raise(self, sandbox, server):
        argv = server._expand_and_tokenize('ls "unterminated')
        assert argv[0] == 'ls'

    def test_fallback_path_still_globs_unquoted(self, sandbox, server):
        """The count-mismatch fallback keeps the OLDER quote-blind behaviour.

        Deliberate: a parse we cannot trust must not start breaking commands
        that work today.  It degrades to over-globbing, never to a crash.
        """
        argv = server._expand_and_tokenize('ls *.json "unterminated')
        assert 'a.json' in argv and 'b.json' in argv


class TestTokenizerActuallyConsultsTheRawText:
    """Pin the wiring, not just the helpers.

    Every test above would still pass if the call site were reverted to the
    quote-blind loop while the helper stayed exported and correct.  These
    read the source to assert the tokenizer really asks.
    """

    @staticmethod
    def _source():
        import inspect
        return inspect.getsource(ShellServer._expand_and_tokenize)

    @staticmethod
    def _code_only(src):
        """Strip whole-line and trailing ``#`` comments.

        Without this, a revert that left the explanatory comment in place
        would satisfy a naive substring check -- the comment names the very
        symbols being looked for.
        """
        out = []
        for line in src.splitlines():
            stripped = line.split('#', 1)[0]
            if stripped.strip():
                out.append(stripped)
        return '\n'.join(out)

    def test_derives_verdict_from_raw_words(self):
        code = self._code_only(self._source())
        assert '_split_raw_words(' in code
        assert '_has_unquoted_glob(' in code

    def test_no_unconditional_metachar_scan_of_tokens(self):
        """The old predicate must be gone from the glob decision.

        It survives only as the documented fallback, guarded by
        ``globbable is None``; an unguarded occurrence means the fix is
        bypassed for every word.
        """
        code = self._code_only(self._source())
        assert 'for arg in tokens[1:]:' not in code
        assert 'globbable is None' in code

    def test_scanner_and_tokenizer_share_one_expansion(self):
        """Both must read the same expanded text.

        If the scanner re-expanded independently, ``ls "$PAT"`` could be
        quoted to one and bare to the other.
        """
        code = self._code_only(self._source())
        assert '_split_raw_words(expanded)' in code
