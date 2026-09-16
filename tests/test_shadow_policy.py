"""Remote command policy for shadow line control (design doc §6.3).

The policy is the control against the *model* — it bounds what a lease
may ask the remote shell to run.  A wrong verdict is the quietest failure
in the whole feature (an allow that should deny types into a remote host),
so these are deliberately adversarial: substitution smuggling, redirection
hidden in quotes, destructive commands mid-pipeline, mode-table coverage.
"""
import json
import os

import pytest

from app.shadow import policy as P


# --- detectors ----------------------------------------------------------------

@pytest.mark.parametrize("cmd, expected", [
    ("echo hi > out.txt", True),
    ("echo hi >> out.txt", True),
    ("foo 2>/dev/null", True),
    ("foo 2>&1", True),
    ("cat a > /etc/passwd", True),
    ("sed -i 's/a/b/' f", True),
    ("perl -i -pe 's/x/y/' f", True),
    ("ls -l", False),
    ("grep '>' file", False),            # quoted, not a redirection
    ("echo 'a > b'", False),             # quoted
    ("df -h | grep sd", False),          # pipe is not a file redirection
])
def test_has_redirection(cmd, expected):
    assert P.has_redirection(cmd) is expected


@pytest.mark.parametrize("cmd, expected", [
    ("cat $(whoami)", True),
    ("echo `id`", True),
    ("ls $(dirname /a/b)", True),
    ("ls -l", False),
    ("echo 'literal $(x)'", False),      # quoted single → not executed
    ("echo price is 5", False),
])
def test_has_substitution(cmd, expected):
    assert P.has_substitution(cmd) is expected


# --- none policy --------------------------------------------------------------

def test_none_policy_allows_everything_but_mode_still_governs():
    strict = P.resolve_policy("none", "strict")
    # 'none' means the allowlist admits everything, so classify=ALLOWED and it RUNS
    # even in strict — 'none' is only meaningful with unrestricted, but the table
    # is honest: allowed always runs.
    assert strict.classify("rm -rf /")[0] == P.ALLOWED
    assert strict.decide("rm -rf /")[0] == P.RUN


# --- named policy: read-only allowlist ---------------------------------------

@pytest.fixture
def readonly_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".ziya/shadow/policies"
    d.mkdir(parents=True)
    (d / "ro.json").write_text(json.dumps({
        "allowedCommands": ["ls", "cat", "grep", "df", "echo", "systemctl", "rm"]
    }))
    return d


def test_named_readonly_allows_plain_command(readonly_policy):
    pol = P.resolve_policy("named:ro", "gated")
    assert pol.classify("ls -l /var")[0] == P.ALLOWED
    assert pol.decide("ls -l /var") == (P.RUN, "")


def test_named_redirection_is_not_allowed_even_if_base_is(readonly_policy):
    # echo is allowlisted, but the redirection target can't be vetted remotely.
    pol_strict = P.resolve_policy("named:ro", "strict")
    v, why = pol_strict.classify("echo hi > /tmp/x")
    assert v == P.NOT_ALLOWED and "redirection" in why
    assert pol_strict.decide("echo hi > /tmp/x")[0] == P.DENY


def test_named_destructive_not_allowed_even_when_in_allowlist(readonly_policy):
    # 'rm' is in the allowlist, but a destructive command's target is opaque
    # remotely, so it must fall to the mode table rather than run free.
    pol = P.resolve_policy("named:ro", "gated")
    v, why = pol.classify("rm -rf /tmp/data")
    assert v == P.NOT_ALLOWED and "modifies files" in why


def test_named_substitution_not_allowed(readonly_policy):
    pol = P.resolve_policy("named:ro", "strict")
    v, why = pol.classify("cat $(ls /etc)")
    assert v == P.NOT_ALLOWED and "substitution" in why


def test_named_offlist_command_not_allowed(readonly_policy):
    pol = P.resolve_policy("named:ro", "gated")
    assert pol.classify("nmap localhost")[0] == P.NOT_ALLOWED


# --- mode table (verdict × mode) ---------------------------------------------

@pytest.mark.parametrize("mode, expected", [
    ("strict", P.DENY),
    ("gated", P.CONFIRM),
    ("unrestricted", P.RUN),
])
def test_mode_table_for_not_allowed(readonly_policy, mode, expected):
    pol = P.resolve_policy("named:ro", mode)
    # a destructive command is 'not allowed' under the remote profile
    assert pol.decide("rm -rf /tmp/x")[0] == expected


def test_allowed_always_runs_regardless_of_mode(readonly_policy):
    for mode in ("strict", "gated", "unrestricted"):
        pol = P.resolve_policy("named:ro", mode)
        assert pol.decide("ls /")[0] == P.RUN


# --- pipeline: destructive in a later segment is caught ----------------------

def test_destructive_mid_pipeline_not_allowed(readonly_policy):
    pol = P.resolve_policy("named:ro", "strict")
    # ls is fine, but the second segment is destructive → whole line not allowed
    assert pol.classify("ls /tmp && rm -rf /tmp/x")[0] == P.NOT_ALLOWED


# --- resolution errors --------------------------------------------------------

def test_inherit_is_deferred_with_clear_error():
    with pytest.raises(NotImplementedError):
        P.resolve_policy("inherit", "gated")


def test_unknown_policy_set_raises():
    with pytest.raises(ValueError):
        P.resolve_policy("banana", "gated")


def test_missing_named_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        P.resolve_policy("named:does-not-exist", "gated")


def test_named_policy_name_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError):
        P.resolve_policy("named:../../etc/passwd", "gated")


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        P.RemotePolicy("none", "wideopen")


def test_builtin_policy_is_the_readonly_remote_profile():
    """'builtin' = shipped default allowlist under the remote profile: read-only
    diagnostics run; writers (incl. allowlisted tee/touch), destructive commands,
    redirection, substitution and off-allowlist commands are not allowed."""
    from app.shadow.policy import resolve_policy, RUN, DENY, CONFIRM
    strict = resolve_policy("builtin", "strict")
    for ok in ("ls -la", "cat /etc/hostname", "df -h", "tail -n 50 /var/log/syslog", "grep -r foo ."):
        assert strict.decide(ok) == (RUN, ""), ok
    for bad in ("echo x > /tmp/f", "tee /tmp/f", "touch /tmp/f", "rm -rf x", "sudo ls",
                "systemctl status foo", "cat $(whoami)"):
        assert strict.decide(bad)[0] == DENY, bad
    gated = resolve_policy("builtin", "gated")
    assert gated.decide("tee /tmp/f")[0] == CONFIRM
    assert resolve_policy("builtin", "unrestricted").decide("tee /tmp/f")[0] == RUN


def test_engine_never_writes_to_stderr(capfd):
    """Inside a shadow host stderr is the human's terminal.  ShellServer
    narrates every verdict to stderr; a verdict must be silent, or it sprays
    the screen and — with nobody draining the PTY — blocks send_line."""
    from app.shadow.policy import resolve_policy
    pol = resolve_policy("builtin", "gated")
    capfd.readouterr()
    for cmd in ("ls -la", "tee /tmp/x", "sudo ls", "cat $(whoami)"):
        pol.decide(cmd)
    out, err = capfd.readouterr()
    assert err == "" and out == "", (out, err)
