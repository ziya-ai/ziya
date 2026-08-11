"""
Contract tests pinning the DELIBERATE divergences between the quote-state
walkers in app.mcp_servers.shell_server, plus regression coverage for the
ANSI-C (``$'...'``) quoting bypass on the compound route.

Why this file exists
--------------------
``shell_server`` contains six independent quote-state walkers:

  W1 ``_split_by_shell_operators``        segment separators
  W2 comment-strip loop in ``is_command_allowed``
  W3 ``_split_unquoted_lines``            logical-line split
  W4 ``_consume_raw_word``                raw ``NAME=value`` span
  W5 ``_find_substitution_spans``         ``$(...)`` / backtick spans
  W6 ``_consume_assignment_with_subst``   ``VAR=$(...)`` span

An earlier review proposed consolidating all six behind one shared
primitive.  Investigation showed that is the WRONG move: the six share
exactly one invariant (the quote/escape state machine) and differ on four
independent axes (backtick-as-quote, stop condition, paren-depth
tracking, raw-offset preservation).  Two of the divergences are not
duplication at all -- they are semantically REQUIRED:

  * W5 is double-quote TRANSPARENT.  bash expands ``$( )`` inside double
    quotes but not inside single quotes::

        echo "today is $(echo S)"   ->  today is S
        echo 'today is $(echo S)'   ->  today is $(echo S)

    So W5's predicate is "outside SINGLE quotes", not "outside all
    quotes".  A shared primitive answering the latter CANNOT serve W5 --
    it silently finds zero substitutions inside double quotes, which
    would drop them from validation entirely.

  * W1 treats a BACKTICK as a quote context that suppresses operator
    recognition, because a backtick substitution body is not a place to
    split segments.

Without tests, a future "unify the walkers" refactor would erase those
distinctions and only be caught indirectly, if at all.  These tests make
that failure loud.

The ANSI-C bypass
-----------------
Inside ``$'...'`` bash treats backslash as an ESCAPE, inverting the
normal single-quote rule (where backslash is literal).  So ``$'\\''`` is
the complete one-character string ``'``.  All six walkers apply the
normal rule, so they read the string as still open and swallow every
following ``;`` as quoted data.

On the compound route that was a live allowlist bypass::

    for i in 1; do echo $'\\'' ; /tmp/evil.sh; done
      W1 segments : ['for i in 1', "do echo $'\\'' ; /tmp/evil.sh; done"]
      verdict     : allowed          <- hidden command never validated
      execution   : sh -c, where bash DOES honour the ';'

Confirmed live: a non-allowlisted script (invoked by absolute path, so
the allowlist rejects it) executed its sentinel.  Control payloads on the
same route without the ANSI-C trick were correctly denied, so the route
itself works -- it was specifically the quoting divergence that defeated
it.  The heredoc route already had an unterminated-quote guard and denied
its variant; the compound and general paths did not.

Fix: hoist the unterminated-quote guard so it covers every route.  It is
fail-closed and costs almost no legitimate usage -- common ANSI-C forms
(``$'\\t'``, ``$'\\n'``) do not read as unterminated; only the genuinely
ambiguous escaped-quote-inside-``$'...'`` form does.
"""

import subprocess

import pytest

from app.mcp_servers.shell_server import (
    ShellServer,
    _consume_assignment_with_subst,
    _consume_raw_word,
    _find_substitution_spans,
)


@pytest.fixture
def server():
    return ShellServer()


def _bash_parses(command: str) -> bool:
    """True if bash can PARSE *command* (syntax check only, no execution)."""
    try:
        r = subprocess.run(['bash', '-n', '-c', command],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:  # noqa: BLE001 - bash absent / unusable
        pytest.skip('bash unavailable for ground-truth check')


# ---------------------------------------------------------------------------
# Deliberate divergence 1: W5 is double-quote transparent
# ---------------------------------------------------------------------------

class TestSubstitutionFinderIsDoubleQuoteTransparent:
    """``_find_substitution_spans`` must look INSIDE double quotes.

    This is the divergence a naive "one shared quote primitive" refactor
    would destroy.  If these fail, substitutions inside double quotes are
    no longer being validated -- an enforcement gap, not a cosmetic one.
    """

    def test_finds_substitution_inside_double_quotes(self):
        bodies = [b for _s, _e, b, _k in
                  _find_substitution_spans('echo "x $(echo S)"')]
        assert bodies == ['echo S'], (
            'substitutions inside double quotes MUST be found: bash expands '
            'them, so they must be validated'
        )

    def test_ignores_substitution_inside_single_quotes(self):
        bodies = [b for _s, _e, b, _k in
                  _find_substitution_spans("echo 'x $(echo S)'")]
        assert bodies == [], (
            'single quotes suppress expansion in bash, so a $() there is '
            'literal text and must NOT be validated as a command'
        )

    def test_bash_agrees_dq_expands_sq_does_not(self):
        """Ground truth, so the contract is pinned to bash, not to belief."""
        def out(cmd):
            r = subprocess.run(['bash', '-c', cmd], capture_output=True,
                               text=True, timeout=5)
            return r.stdout.strip()

        try:
            dq = out('echo "v=$(echo S)"')
            sq = out("echo 'v=$(echo S)'")
        except Exception:  # noqa: BLE001
            pytest.skip('bash unavailable')
        assert dq == 'v=S', 'bash expands $() inside double quotes'
        assert sq == 'v=$(echo S)', 'bash does NOT expand inside single quotes'

    def test_nested_substitution_in_double_quotes_still_found(self):
        bodies = [b for _s, _e, b, _k in
                  _find_substitution_spans('echo "$(grep "$(date)" f)"')]
        assert len(bodies) == 1, 'outermost span only'
        assert 'date' in bodies[0], 'nested body preserved for re-validation'

    def test_a_shared_outside_all_quotes_predicate_would_break_this(self):
        """Documents WHY consolidation was rejected.

        A primitive whose contract is "which offsets are outside ALL
        quotes" reports the inside of a double-quoted string as quoted,
        and therefore finds nothing here.  This test asserts the real
        finder does not behave that way.
        """
        cmd = 'echo "$(id)"'
        assert _find_substitution_spans(cmd), (
            'if this returns empty, W5 has been re-pointed at an '
            '"outside all quotes" predicate and $() inside double quotes '
            'is no longer validated'
        )


# ---------------------------------------------------------------------------
# Deliberate divergence 2: W1 treats backticks as a quote context
# ---------------------------------------------------------------------------

class TestOperatorSplitterTreatsBacktickAsQuote:
    """W1 suppresses operator recognition inside a backtick substitution."""

    def test_semicolon_inside_backticks_is_not_a_separator(self, server):
        segs = [seg for _op, seg in
                server._split_by_shell_operators('echo `date; id` tail')]
        assert len(segs) == 1, (
            'a ; inside a backtick substitution body is not a segment '
            'separator for W1 -- the body is validated separately by the '
            'substitution path'
        )

    def test_pipe_inside_backticks_is_not_a_separator(self, server):
        segs = [seg for _op, seg in
                server._split_by_shell_operators('echo `ls | head` tail')]
        assert len(segs) == 1

    def test_backtick_body_is_still_validated_by_substitution_path(self, server):
        """Suppressing the split must NOT mean skipping validation."""
        ok, reason = server.is_command_allowed('echo `sudo reboot`')
        assert not ok, 'a disallowed command inside backticks must be denied'
        assert 'sudo' in reason or 'substitution' in reason, reason

    def test_walkers_that_ignore_backticks_are_unaffected(self):
        """W4 has no backtick handling; that is intentional, not a bug.

        W4 consumes a raw ``NAME=value`` word; a backtick there is just a
        character in the value.
        """
        assert _consume_raw_word('V=`date` next') == 'V=`date`'


# ---------------------------------------------------------------------------
# Deliberate divergence 3: paren-depth tracking is walker-specific
# ---------------------------------------------------------------------------

class TestParenDepthTrackingIsLocalToWalkersThatNeedIt:
    def test_assignment_walker_tracks_paren_depth(self):
        assert _consume_assignment_with_subst('V=$(echo a b c) rest') == \
            'V=$(echo a b c)'

    def test_assignment_walker_handles_nested_parens(self):
        got = _consume_assignment_with_subst('V=$(echo $(date)) rest')
        assert got == 'V=$(echo $(date))', got

    def test_raw_word_walker_does_not_track_parens(self):
        """W4 stops at unquoted whitespace regardless of paren depth.

        This is why W4 and W6 cannot be the same function: given
        ``V=$(a b)`` W4 stops at the space, W6 spans to the closing paren.
        """
        assert _consume_raw_word('V=$(a b) rest') == 'V=$(a'


# ---------------------------------------------------------------------------
# ANSI-C quoting: the bypass and its guard
# ---------------------------------------------------------------------------

# Payloads whose hidden command is invoked by ABSOLUTE PATH, so the
# allowlist rejects it on its own merits.  If the validator allows the
# payload, the only possible reason is that the segment was hidden.
_ANSIC_PAYLOADS = {
    'compound_for': "for i in 1; do echo $'\\'' ; /tmp/zz_evil.sh; done",
    'compound_while': "while true; do echo $'\\'' ; /tmp/zz_evil.sh; done",
    'compound_if': "if true; then echo $'\\'' ; /tmp/zz_evil.sh; fi",
    'plain': "echo $'\\'' ; /tmp/zz_evil.sh",
    'heredoc': "cat <<'EOF'\nbody\nEOF\necho $'\\'' ; /tmp/zz_evil.sh",
}


class TestAnsiCQuotingCannotHideACommand:
    """The ANSI-C escaped-quote form must not smuggle a command past the
    allowlist on ANY route (compound, heredoc, or plain orchestrator)."""

    @pytest.mark.parametrize('payload', list(_ANSIC_PAYLOADS.values()),
                             ids=list(_ANSIC_PAYLOADS))
    def test_hidden_command_is_denied(self, server, payload):
        ok, reason = server.is_command_allowed(payload)
        assert not ok, (
            'ANSI-C quoting hid a non-allowlisted command from validation; '
            'bash honours the ; and would run it on the sh -c routes'
        )
        assert reason, 'denial must carry a reason'

    def test_walker_reports_open_quote_state_for_the_ambiguous_form(self, server):
        """The signal the guard relies on.

        The walkers apply the normal single-quote rule, under which
        ``$'\\''`` leaves quote state open.  That mismatch with bash is
        exactly what made the bypass possible, so the guard keys on it.
        """
        _lines, unterm = server._split_unquoted_lines("echo $'\\'' ; ls")
        assert unterm is True

    def test_guard_is_uniform_across_routes(self, server):
        """Previously only the heredoc route consulted the flag.

        Every payload above must be denied, not just the heredoc one --
        that asymmetry was the vulnerability.
        """
        verdicts = {name: server.is_command_allowed(cmd)[0]
                    for name, cmd in _ANSIC_PAYLOADS.items()}
        assert not any(verdicts.values()), verdicts


class TestCommonAnsiCFormsStillWork:
    """The guard must not over-reject ordinary ANSI-C usage.

    These are the realistic forms (tab/newline literals).  None of them
    leaves quote state open, so the fail-closed guard costs nothing here.
    """

    @pytest.mark.parametrize('cmd', [
        r"grep $'\t' file",
        r"echo $'line1\nline2'",
        r"sed $'s/\t/ /g' file",
        r"awk -F$'\t' '{print $1}' file",
        r"printf $'%s\n' x",
    ])
    def test_ansi_c_literal_escapes_still_validate(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f'common ANSI-C form must not be refused: {reason}'

    @pytest.mark.parametrize('cmd', [
        'grep -E "\\|" file',
        'echo hi | grep h',
        'cd frontend && ls',
        'find . -name x -exec rm {} \\;',
        "echo A \\' ; ls",
        "for i in 1 2; do echo $i; done",
        "cat <<'EOF'\nbody\nEOF\nls",
    ])
    def test_benign_commands_unaffected(self, server, cmd):
        ok, reason = server.is_command_allowed(cmd)
        assert ok, f'guard must not disturb benign command: {reason}'


class TestUnterminatedQuoteIsRefusedNotGuessed:
    """A genuinely unterminated quote is refused on every route.

    bash cannot parse these either, so refusing is strictly correct --
    but we refuse explicitly rather than relying on the downstream shell
    to fail, because on the ``shell=False`` orchestrator route there is
    no shell to do the rejecting.
    """

    @pytest.mark.parametrize('cmd', [
        "echo 'unclosed ; sudo reboot",
        'echo "unclosed ; sudo reboot',
        "echo A \\\\' ; sudo reboot",
    ])
    def test_unterminated_is_denied(self, server, cmd):
        ok, _reason = server.is_command_allowed(cmd)
        assert not ok

    @pytest.mark.parametrize('cmd', [
        "echo 'unclosed ; sudo reboot",
        'echo "unclosed ; sudo reboot',
        "echo A \\\\' ; sudo reboot",
    ])
    def test_bash_also_refuses_to_parse(self, cmd):
        """Ground truth: our refusal matches bash's own parse failure."""
        assert _bash_parses(cmd) is False, (
            'if bash CAN parse this, refusing it is over-rejection and the '
            'guard needs narrowing'
        )
