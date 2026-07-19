"""
Regression tests for ``NAME=value`` environment-prefix peeling in
app.mcp_servers.shell_server.ShellServer._peel_env_prefix.

Origin of these tests
----------------------
The validator rejected a benign multi-line changelog-comparison script with
the misleading message ``'1"' is not allowed (in pipeline segment: '1"')``.

Root cause: ``_peel_env_prefix`` located the leading ``NAME=value`` token
with ``shlex.split(remaining, posix=True)[0]`` and then sliced the *raw*
string by ``len(tok)``.  Posix shlex **strips the surrounding quotes**, so
for ``ALL="150 152 … 141"`` the unquoted token was two bytes shorter than the
raw span.  Slicing off only ``len(tok)`` bytes left the trailing ``1"`` as a
bogus residual "command", which was then validated and rejected.

Fix: a quote-aware ``_consume_raw_word`` scanner determines the *raw* byte
span (quotes preserved, escapes honored) used for slicing; shlex is applied
only to that raw word to compute the *unquoted* value stored in the env dict.

These tests lock in:
  * the raw-word scanner boundaries (quotes, escapes, whitespace),
  * peeling of single/double-quoted, escaped, empty, and chained
    assignments with no residual token,
  * ``$(...)``-valued assignments still go through the substitution path,
  * blocked dynamic-loader prefixes are rejected,
  * end-to-end ``is_command_allowed`` acceptance of the reproducer.
"""

import pytest

from app.mcp_servers.shell_server import (
    ShellServer,
    _consume_raw_word,
    _consume_assignment_with_subst,
)


@pytest.fixture
def server():
    return ShellServer()


class TestConsumeRawWord:
    """The quote-aware raw-word scanner underpinning the fix.

    It must return the *raw* leading span (quotes included) so the caller can
    slice the source string without the shlex quote-stripping mismatch.
    """

    def test_plain_word(self):
        assert _consume_raw_word("FOO=bar echo hi") == "FOO=bar"

    def test_double_quoted_value_span_includes_quotes(self):
        # The exact reproducer shape: whitespace inside double quotes must
        # NOT terminate the word, and both quote bytes are part of the span.
        raw = _consume_raw_word('ALL="150 152 141" echo x')
        assert raw == 'ALL="150 152 141"'

    def test_single_quoted_value_span_includes_quotes(self):
        raw = _consume_raw_word("MSG='a b c' next")
        assert raw == "MSG='a b c'"

    def test_escaped_space_is_part_of_word(self):
        # Outside single quotes a backslash escapes the next char, so the
        # escaped space stays inside the word.
        raw = _consume_raw_word(r"ESC=a\ b echo done")
        assert raw == r"ESC=a\ b"

    def test_backslash_inside_single_quotes_is_literal(self):
        # Inside single quotes a backslash is a literal char, not an escape,
        # and does not consume the following quote.
        raw = _consume_raw_word(r"P='a\'")
        assert raw == r"P='a\'"

    def test_unterminated_quote_consumes_to_end(self):
        # A dangling quote has no closing partner, so the whole remainder is
        # one raw word; downstream shlex will then raise and peeling stops.
        raw = _consume_raw_word('X="unterminated value')
        assert raw == 'X="unterminated value'

    def test_leading_word_only(self):
        # Only the first whitespace-delimited word is returned.
        assert _consume_raw_word("A=1 B=2 C=3") == "A=1"


class TestPeelSingleAssignment:
    """Peeling a single assignment yields no residual command token."""

    def test_double_quoted_value_no_residual(self):
        # The headline bug: this used to leave a stray ``1"``.
        cleaned, env, reason = ShellServer._peel_env_prefix(
            'ALL="150 152 158 169 153 141"'
        )
        assert reason == ""
        assert cleaned == ""            # <-- no bogus leftover command
        assert env == {"ALL": "150 152 158 169 153 141"}

    def test_single_quoted_value(self):
        cleaned, env, reason = ShellServer._peel_env_prefix("MSG='a b c' echo done")
        assert reason == ""
        assert cleaned == "echo done"
        assert env == {"MSG": "a b c"}

    def test_unquoted_value(self):
        cleaned, env, reason = ShellServer._peel_env_prefix("FOO=bar echo hi")
        assert reason == ""
        assert cleaned == "echo hi"
        assert env == {"FOO": "bar"}

    def test_empty_value(self):
        cleaned, env, reason = ShellServer._peel_env_prefix("FOO= echo hi")
        assert reason == ""
        assert cleaned == "echo hi"
        assert env == {"FOO": ""}

    def test_escaped_space_value(self):
        cleaned, env, reason = ShellServer._peel_env_prefix(r"ESC=a\ b echo done")
        assert reason == ""
        assert cleaned == "echo done"
        assert env == {"ESC": "a b"}     # shlex resolves the escape for the value


class TestPeelChainedAssignments:
    """Multiple leading assignments are all peeled, in order, cleanly."""

    def test_three_unquoted(self):
        cleaned, env, reason = ShellServer._peel_env_prefix("A=1 B=2 C=3 true")
        assert reason == ""
        assert cleaned == "true"
        assert env == {"A": "1", "B": "2", "C": "3"}

    def test_mixed_quoting(self):
        cleaned, env, reason = ShellServer._peel_env_prefix(
            "X='a b c' Y=\"d e\" Z=f echo go"
        )
        assert reason == ""
        assert cleaned == "echo go"
        assert env == {"X": "a b c", "Y": "d e", "Z": "f"}

    def test_quoted_then_command_with_own_quotes(self):
        # The peeled prefix must stop at the first non-assignment word even
        # when that command carries its own quoted args.
        cleaned, env, reason = ShellServer._peel_env_prefix(
            'LANG="en_US" grep -E "a|b" file'
        )
        assert reason == ""
        assert cleaned == 'grep -E "a|b" file'
        assert env == {"LANG": "en_US"}


class TestPeelNoAssignment:
    """A segment that does not start with an assignment is returned as-is."""

    def test_plain_command_untouched(self):
        cleaned, env, reason = ShellServer._peel_env_prefix("echo hello")
        assert reason == ""
        assert cleaned == "echo hello"
        assert env == {}

    def test_equals_in_argument_not_a_prefix(self):
        # ``--opt=val`` is an argument, not a leading NAME=value assignment
        # (the regex requires the name at position 0 and ``--`` is not a
        # valid name start).
        cleaned, env, reason = ShellServer._peel_env_prefix("grep --color=auto x")
        assert reason == ""
        assert cleaned == "grep --color=auto x"
        assert env == {}


class TestPeelSubstitutionValue:
    """Assignments whose value is a command substitution use the $() path."""

    def test_dollar_paren_value_consumed(self):
        cleaned, env, reason = ShellServer._peel_env_prefix("D=$(date) echo x")
        assert reason == ""
        assert cleaned == "echo x"
        assert env == {"D": "$(date)"}

    def test_consume_assignment_with_subst_balances_parens(self):
        # Sanity-check the helper the above path relies on.
        tok = _consume_assignment_with_subst("D=$(echo (nested))  rest")
        assert tok == "D=$(echo (nested))"


class TestPeelBlockedPrefixes:
    """Dynamic-loader hijack prefixes are rejected with a reason."""

    @pytest.mark.parametrize(
        "name",
        [
            "LD_PRELOAD",
            "LD_AUDIT",
            "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES",
            "DYLD_LIBRARY_PATH",
            "DYLD_FALLBACK_LIBRARY_PATH",
        ],
    )
    def test_blocked_env_prefix_rejected(self, name):
        cleaned, env, reason = ShellServer._peel_env_prefix(f"{name}=/tmp/evil.so ls")
        assert reason != ""
        assert name in reason
        assert env == {}


class TestEndToEndAcceptance:
    """The reproducer and its shapes validate through is_command_allowed."""

    def test_quoted_assignment_prefix_command_allowed(self, server):
        ok, reason = server.is_command_allowed(
            'ALL="150 152 158 169 153 141" echo "$ALL"'
        )
        assert ok, f"quoted env-prefix + echo should validate: {reason}"

    def test_no_stray_token_in_denial_path(self, server):
        # A bare quoted assignment (no command) must not manufacture a bogus
        # ``1"`` segment; it peels to an empty command, which is accepted as
        # a pure assignment.
        ok, reason = server.is_command_allowed('ALL="150 152 141"')
        assert ok, f"bare quoted assignment should not fabricate a token: {reason}"
        assert '1"' not in reason

    def test_blocked_prefix_command_rejected(self, server):
        ok, reason = server.is_command_allowed("LD_PRELOAD=/tmp/x.so ls")
        assert not ok
        assert "LD_PRELOAD" in reason

    def test_env_prefix_then_disallowed_command_rejected(self, server):
        # Peeling the prefix must still leave the real command to be
        # validated — a disallowed one is rejected.
        ok, _ = server.is_command_allowed("FOO=bar definitely_not_a_real_cmd")
        assert not ok


class TestCommAllowlisted:
    """`comm` was added to the allowlist alongside join/paste/cut."""

    def test_comm_allowed(self, server):
        ok, reason = server.is_command_allowed("comm -12 /tmp/a /tmp/b")
        assert ok, f"comm should be allowlisted: {reason}"

    def test_comm_in_pipeline_allowed(self, server):
        ok, reason = server.is_command_allowed(
            "comm -23 /tmp/a /tmp/b | tr '\\n' ' '"
        )
        assert ok, f"comm in a pipeline should validate: {reason}"
