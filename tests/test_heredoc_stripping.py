"""
Regression tests for app.mcp_servers.write_policy._strip_heredoc_bodies.

The function strips heredoc bodies (stdin *data*) before the shell-command
allowlist validator scans a command, so body content isn't mistaken for
command segments.  A bug iterated finditer(command) offsets against a
shrinking result, so only the FIRST heredoc body was stripped; the 2nd+
body and its closing delimiter survived into the per-line validator and
were rejected as bogus commands (e.g. a bare ``EOF`` -> "not allowed").
"""
from app.mcp_servers.write_policy import _strip_heredoc_bodies
from app.mcp_servers.shell_server import _has_heredoc


def test_single_heredoc_body_stripped():
    out = _strip_heredoc_bodies('cat <<EOF\nhello\nEOF')
    assert 'hello' not in out


def test_trailing_command_preserved():
    out = _strip_heredoc_bodies('cat <<EOF\nhello\nEOF\nrm -rf /tmp/x')
    assert 'hello' not in out
    assert 'rm -rf /tmp/x' in out


def test_two_different_delimiter_heredocs_both_stripped():
    # Regression: the old loop stripped only the first body.
    out = _strip_heredoc_bodies('cat <<A\nBODY1\nA\ncat <<B\nBODY2\nB\necho done')
    assert 'BODY1' not in out
    assert 'BODY2' not in out          # the bug left this in
    assert 'echo done' in out


def test_two_same_delimiter_heredocs_both_stripped():
    out = _strip_heredoc_bodies(
        'cat <<EOF\nBODY1\nEOF\necho keep\ncat <<EOF\nBODY2\nEOF'
    )
    assert 'BODY1' not in out
    assert 'BODY2' not in out
    assert 'echo keep' in out


def test_command_between_heredocs_preserved():
    # The sequenced command must never be dropped from the validated string.
    out = _strip_heredoc_bodies('cat <<A\nBODY1\nA\nrm /important\ncat <<B\nBODY2\nB')
    assert 'BODY1' not in out
    assert 'BODY2' not in out
    assert 'rm /important' in out


def test_three_heredocs_all_stripped():
    out = _strip_heredoc_bodies(
        'cat <<A\nB1\nA\ncat <<B\nB2\nB\ncat <<C\nB3\nC\necho end'
    )
    for tok in ('B1', 'B2', 'B3'):
        assert tok not in out
    assert 'echo end' in out


def test_second_closing_delimiter_not_left_as_command_line():
    # The validator splits on '\n' and checks each line; a bare leftover
    # ``EOF`` would be validated as a command and spuriously rejected.
    out = _strip_heredoc_bodies('cat <<A\na\nA\ncat <<EOF\nb\nEOF')
    lines = [ln.strip() for ln in out.split('\n')]
    assert 'EOF' not in lines
    assert 'b' not in out


def test_unterminated_heredoc_leaves_remainder_no_crash():
    # No closing delimiter: must not crash or loop; remainder left intact.
    out = _strip_heredoc_bodies('cat <<EOF\nstill open with no close')
    assert 'still open with no close' in out


# -- Opener-line trailing content (pipe / redirect / arg after delimiter) -----
# _HEREDOC_RE and _strip_heredoc_bodies are kept in lockstep.  Both required
# the delimiter to be immediately followed by '\n', so any trailing content on
# the opener line defeated heredoc recognition: validation rejected the body
# lines as commands, and (if the body was all-allowlisted) execution passed
# "<<EOF" as literal argv.  The patterns now tolerate "[^\n]*" before '\n'.

def test_detect_pipe_after_delimiter():
    assert _has_heredoc('cat <<EOF | grep h\nhi\nEOF')


def test_detect_redirect_after_delimiter():
    assert _has_heredoc('cat <<EOF > /tmp/out\nhi\nEOF')


def test_detect_arg_after_delimiter():
    assert _has_heredoc('cat <<EOF somearg\nhi\nEOF')


def test_detect_dash_variant_with_pipe():
    assert _has_heredoc('cat <<-EOF | sort\nhi\nEOF')


def test_detect_quoted_delim_with_pipe():
    assert _has_heredoc("cat <<'EOF' | wc -l\nhi\nEOF")


def test_non_heredoc_without_newline_not_detected():
    # ``<<`` with no newline-terminated opener is not a heredoc.
    assert not _has_heredoc('echo a << b')


def test_strip_pipe_after_delimiter():
    out = _strip_heredoc_bodies('cat <<EOF | grep h\nBODY\nEOF')
    assert 'BODY' not in out
    assert 'cat <<EOF | grep h' in out


def test_strip_multi_heredoc_with_trailing_pipe():
    # Second heredoc's opener carries a pipe; both bodies must still strip.
    out = _strip_heredoc_bodies('cat <<A\nB1\nA\ncat <<B | sort\nB2\nB\necho done')
    assert 'B1' not in out
    assert 'B2' not in out
    assert 'echo done' in out
