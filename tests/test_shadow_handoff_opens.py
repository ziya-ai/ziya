"""Shadow sessions: the open items carried over from the line-control
handoff (Docs/design/shadow-sessions.md §7, §8, §9).

  - per-session / shareable journal redaction sets (``--redact``)
  - ``search`` matching meta records (asks, comments, control verdicts)
  - the ``[i]`` instrument snippet delivered as a bracketed paste when the
    child's line editor has DECSET 2004 on
  - transcript cosmetics: no leading blank line, tab stops preserved
  - one source of truth for the top-level CLI subcommand set

Each test fails against the tree before the corresponding change.
"""
import json
import os
import re
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Redaction sets
# ---------------------------------------------------------------------------

def _write_set(name, patterns):
    from app.shadow.redaction import redact_sets_dir
    p = redact_sets_dir() / f"{name}.json"
    p.write_text(json.dumps({"patterns": patterns}))
    return p


def test_user_redact_set_is_applied_to_journal_output(home):
    """The lab banner credential: a shape only the user can name."""
    from app.shadow.redaction import Redactor, build_user_patterns
    from app.shadow.journal import JournalWriter, JournalReader
    _write_set("lab", [{"kind": "lab-cred", "regex": r"l2Cwv4W[A-Za-z0-9]{6,}"}])
    w = JournalWriter(str(home / "j.journal"),
                      Redactor(build_user_patterns(sets=["lab"])))
    w.cmd("ssh lab-42")
    w.output("Welcome. Temporary credential: l2Cwv4Wq9ZkT3pM7 (expires 1h)\n", 1)
    recs = JournalReader(str(home / "j.journal")).read(1, 10)["records"]
    out = [r for r in recs if r["t"] == "output"][0]["text"]
    assert "l2Cwv4W" not in out
    assert "[REDACTED:lab-cred]" in out and "(expires 1h)" in out


def test_inline_redact_regex_and_builtin_shapes_both_apply(home):
    from app.shadow.redaction import Redactor, build_user_patterns
    r = Redactor(build_user_patterns(regexes=[r"CORP-[0-9]{6}"]))
    s = r.redact("id CORP-123456 key AKIAABCDEFGHIJKLMNOP")
    assert "[REDACTED:custom-1]" in s and "[REDACTED:aws-access-key]" in s


def test_default_redactor_has_no_user_patterns(home):
    """Positive control: without --redact the lab string is journaled as-is."""
    from app.shadow.redaction import Redactor
    assert "l2Cwv4Wq9ZkT3pM7" in Redactor().redact("cred l2Cwv4Wq9ZkT3pM7")


@pytest.mark.parametrize("bad", [
    {"patterns": [{"kind": "x", "regex": "(a+)+$"}]},        # catastrophic shape
    {"patterns": [{"kind": "x", "regex": "a*"}]},            # matches empty string
    {"patterns": [{"kind": "x", "regex": "("}]},             # invalid
    {"patterns": [{"kind": "bad kind!", "regex": "abc"}]},
    {"patterns": "abc"},
    {"nope": []},
])
def test_unsafe_or_malformed_redact_set_is_refused(home, bad):
    """A user regex runs on every output byte inside the terminal wrapper;
    the same ReDoS guard as the model-supplied search pattern applies."""
    from app.shadow.redaction import build_user_patterns, redact_sets_dir
    (redact_sets_dir() / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        build_user_patterns(sets=["bad"])


def test_missing_redact_set_is_an_error_not_a_silent_noop(home):
    from app.shadow.redaction import build_user_patterns
    with pytest.raises(FileNotFoundError):
        build_user_patterns(sets=["does-not-exist"])
    with pytest.raises(ValueError):
        build_user_patterns(sets=["../escape"])


def test_cli_wires_redact_flags_through_to_the_core(home, monkeypatch):
    """Seam: ``ziya shadow --redact lab --redact-regex X`` reaches
    ShadowCore as compiled patterns (a flag parsed and dropped would pass
    every unit test above)."""
    from app import cli
    _write_set("lab", [{"kind": "lab-cred", "regex": "SECRET-[0-9]+"}])
    got = {}

    def fake_run_interactive(argv, **kw):
        got.update(kw)
        return 0
    import app.shadow.pty_host as ph
    monkeypatch.setattr(ph, "run_interactive", fake_run_interactive)
    monkeypatch.setattr(sys.stdout, "write", lambda s: None)
    parser = cli.create_parser()
    args = parser.parse_args(["shadow", "--redact", "lab", "--redact-regex", "X-[a-z]+", "--", "sh"])
    with pytest.raises(SystemExit) as ei:
        cli.cmd_shadow(args)
    assert ei.value.code == 0
    kinds = [k for k, _ in got["redact_patterns"]]
    assert kinds == ["lab-cred", "custom-1"]


def test_cli_refuses_to_start_with_a_bad_redact_set(home, monkeypatch, capsys):
    from app import cli
    parser = cli.create_parser()
    args = parser.parse_args(["shadow", "--redact", "missing", "--", "sh"])
    with pytest.raises(SystemExit) as ei:
        cli.cmd_shadow(args)
    assert ei.value.code == 2
    assert "--redact" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# search over meta records
# ---------------------------------------------------------------------------

def test_search_matches_ask_comment_and_control_meta(home):
    from app.shadow.journal import JournalWriter, JournalReader
    w = JournalWriter(str(home / "j.journal"))
    w.cmd("ls")
    w.output("a b c\n", 1)
    w.meta("ask", {"question": "why did the deploy fail on prod-42?"})
    w.meta("comment", {"text": "looks like a cert expiry", "provenance": {}})
    w.meta("control_denied", {"text": "rm -rf /tmp/x", "reason": "'rm' modifies files"})
    rd = JournalReader(str(home / "j.journal"))
    assert [r["event"] for r in rd.search("deploy fail")] == ["ask"]
    assert [r["event"] for r in rd.search("cert expiry")] == ["comment"]
    assert [r["event"] for r in rd.search("modifies files")] == ["control_denied"]
    # cmd/output still match, and a pattern hits across kinds in seq order
    assert [r["t"] for r in rd.search("a b c")] == ["output"]
    assert [r.get("t") for r in rd.search("rm|ls")] == ["cmd", "meta"]


def test_search_does_not_match_meta_keys_or_nested_provenance(home):
    """Only values are searchable: 'question' / 'conversation_id' as words
    must not make every ask/comment a hit."""
    from app.shadow.journal import JournalWriter, JournalReader
    w = JournalWriter(str(home / "j.journal"))
    w.meta("comment", {"text": "hello", "provenance": {"conversation_id": "abc123"}})
    rd = JournalReader(str(home / "j.journal"))
    assert rd.search("provenance") == [] and rd.search("abc123") == []
    assert len(rd.search("hello")) == 1


# ---------------------------------------------------------------------------
# instrument snippet under bracketed paste
# ---------------------------------------------------------------------------

def _core_with_frontend(home):
    from app.shadow.pty_host import ShadowCore, InteractiveFrontend
    core = ShadowCore(["sh", "-c", "read x"], label="bp", headless=True)
    out_r, out_w = os.pipe()
    fe = InteractiveFrontend(core, stdin_fd=out_r, stdout_fd=out_w)
    return core, fe, (out_r, out_w)


def test_bracketed_paste_state_tracks_decset_2004_from_output(home):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh"], label="bp", headless=True)
    assert core.bracketed_paste is False
    core.handle_output(b"prompt \x1b[?2004h")
    assert core.bracketed_paste is True
    core.handle_output(b"\x1b[?2004l running...")
    assert core.bracketed_paste is False
    core.handle_output(b"\x1b[?2004l\x1b[?2004h")      # last one wins
    assert core.bracketed_paste is True


def test_instrument_snippet_is_bracketed_when_child_has_paste_mode_on(home, monkeypatch):
    from app.shadow.pty_host import INSTRUMENT_SNIPPETS, _PASTE_BEGIN, _PASTE_END
    core, fe, fds = _core_with_frontend(home)
    written = []
    monkeypatch.setattr(core, "write_to_child", lambda data: written.append(data))
    monkeypatch.setattr(fe, "overlay", lambda text: None)
    try:
        core.handle_output(b"\x1b[?2004h")
        fe._compose_purpose = "instrument"
        fe._compose_done("z")
        assert len(written) == 1
        snippet = INSTRUMENT_SNIPPETS["zsh"].encode()
        assert written[0] == _PASTE_BEGIN + snippet + _PASTE_END + b"\r"
        # The journaled cmd is the snippet itself: paste markers are input
        # escapes the segmenter strips.
        recs = [r for r in _journal(core) if r["t"] == "cmd"]
        assert recs and recs[-1]["text"] == INSTRUMENT_SNIPPETS["zsh"]
        assert any(r.get("event") == "instrument" and r["data"]["bracketed_paste"] is True
                   for r in _journal(core))
    finally:
        for fd in fds:
            os.close(fd)


def test_instrument_snippet_is_plain_when_paste_mode_is_off(home, monkeypatch):
    """Positive control: a plain shell (no DECSET 2004) gets the bare
    snippet — bash's readline with bracketed-paste off would echo the
    markers as garbage."""
    from app.shadow.pty_host import INSTRUMENT_SNIPPETS
    core, fe, fds = _core_with_frontend(home)
    written = []
    monkeypatch.setattr(core, "write_to_child", lambda data: written.append(data))
    monkeypatch.setattr(fe, "overlay", lambda text: None)
    try:
        fe._compose_purpose = "instrument"
        fe._compose_done("b")
        assert written == [INSTRUMENT_SNIPPETS["bash"].encode() + b"\r"]
    finally:
        for fd in fds:
            os.close(fd)


def _journal(core):
    from app.shadow.journal import JournalReader
    return JournalReader(core.entry.journal).read(1, 1000)["records"]


# ---------------------------------------------------------------------------
# transcript cosmetics
# ---------------------------------------------------------------------------

def test_transcript_output_block_has_no_leading_blank_line():
    from app.shadow.client import format_records
    text = format_records([{"seq": 1, "t": "cmd", "text": "ls"},
                           {"seq": 2, "t": "output", "text": "\nfile-a\nfile-b\n"}])
    assert text.splitlines() == ["[1] $ ls", "[2] file-a", "     file-b"]


def test_transcript_expands_tabs_so_columns_survive_the_prefix():
    """``ls``/``ps`` align columns with tabs; a 5-column prefix shifts the
    tab stops so the columns collapsed into each other."""
    from app.shadow.client import format_records
    text = format_records([{"seq": 2, "t": "output",
                            "text": "a\tb\nlonger\tb\n"}])
    lines = text.splitlines()
    assert "\t" not in text
    # column two starts at the same offset on both lines
    assert lines[0].index("b") - len("[2] ") == lines[1].index("b") - len("     ")


# ---------------------------------------------------------------------------
# one subcommand set
# ---------------------------------------------------------------------------

def test_top_level_commands_are_defined_once_and_match_the_parser():
    from app.cli_commands import TOP_LEVEL_COMMANDS
    from app import cli
    import app.main as main_mod
    parser = cli.create_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert set(sub.choices) == set(TOP_LEVEL_COMMANDS)
    for mod in (main_mod, cli):
        src = open(mod.__file__).read()
        assert not re.search(r"\{\s*'chat',\s*'ask'", src), f"literal command set in {mod.__file__}"
        assert not re.search(r"\[\s*'chat',\s*'ask'", src), f"literal command list in {mod.__file__}"
    assert "shadow" in TOP_LEVEL_COMMANDS
