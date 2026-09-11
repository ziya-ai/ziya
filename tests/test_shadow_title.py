"""Title rewrite (design doc §8): the shadowed terminal's window title always
carries the shadow prefix; the child's own title survives as a parenthetical.
Display path only — the journal never sees the rewrite."""
import os
import sys
import threading
import time

import pytest

from app.shadow.title import TitleRewriter, MAX_CARRY

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")

BEL, ST = b"\x07", b"\x1b\\"


def _rw():
    return TitleRewriter("⏺ prod-42 (a3f21e)")


def test_child_title_is_rewritten_with_prefix_and_parenthetical():
    rw = _rw()
    out = rw.filter(b"hello\x1b]0;user@host:~\x07world")
    assert out == b"hello\x1b]0;\xe2\x8f\xba prod-42 (a3f21e) (user@host:~)\x07world"
    assert rw.child_title == "user@host:~"


def test_osc2_and_st_terminator_also_rewritten_osc1_and_others_pass_through():
    rw = _rw()
    out = rw.filter(b"\x1b]2;T" + ST + b"\x1b]1;icon\x07\x1b]133;A\x07\x1b[22;0t")
    assert out.startswith(b"\x1b]0;\xe2\x8f\xba prod-42 (a3f21e) (T)\x07")
    # icon-name-only, OSC 133, and the title stack are byte-identical
    assert out.endswith(b"\x1b]1;icon\x07\x1b]133;A\x07\x1b[22;0t")


def test_sequence_split_across_reads_is_held_then_rewritten():
    rw = _rw()
    parts = [b"abc\x1b]", b"0;user@", b"host", b"\x1b", b"\\def"]
    outs = [rw.filter(p) for p in parts]
    assert outs[0] == b"abc"           # opener held back
    assert outs[1] == b"" and outs[2] == b"" and outs[3] == b""
    assert outs[4] == b"\x1b]0;\xe2\x8f\xba prod-42 (a3f21e) (user@host)\x07def"


def test_never_terminated_osc_is_released_raw_past_the_cap():
    rw = _rw()
    blob = b"\x1b]0;" + b"A" * (MAX_CARRY + 10)
    out = rw.filter(blob)
    assert out == blob                  # not stalled, not rewritten
    assert rw._carry == b""


def test_child_title_control_bytes_stripped_and_length_capped():
    rw = _rw()
    rw.filter(b"\x1b]0;bad\x01\r\ntitle" + b"x" * 500 + b"\x07")
    assert rw.child_title.startswith("badtitle")
    assert len(rw.child_title) == 200


def test_esc_inside_osc_body_aborts_the_sequence_like_a_real_terminal():
    """An ESC that is not the start of ST terminates the OSC (xterm behaviour);
    the bytes are passed through raw rather than held or rewritten."""
    rw = _rw()
    raw = b"\x1b]0;bad\x1b[2Jrest\x07"
    assert rw.filter(raw) == raw
    assert rw.child_title is None and rw._carry == b""


def test_prefix_from_model_text_is_sanitized_and_emit_without_child_title():
    rw = TitleRewriter("pwn\x1b[6n\u202eed")
    assert rw.emit() == b"\x1b]0;pwn\xe2\x80\xae" .replace(b"\xe2\x80\xae", b"") + b"ed\x07"
    rw.filter(b"\x1b]0;child\x07")
    rw.set_prefix("⏺ relabelled")
    assert rw.emit() == b"\x1b]0;\xe2\x8f\xba relabelled (child)\x07"


def test_disabled_is_pure_passthrough():
    rw = _rw()
    rw.enabled = False
    raw = b"\x1b]0;x\x07\x1b]"
    assert rw.filter(raw) == raw and rw.emit() == b""


def test_journal_gets_original_bytes_display_gets_rewrite(tmp_path, monkeypatch):
    """End to end: the child sets a title; the journal records nothing of our
    prefix, while the human's screen receives the rewritten sequence."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.shadow.pty_host import ShadowCore, InteractiveFrontend
    core = ShadowCore(["sh", "-c", "printf '\\033]0;child-title\\007visible\\n'; exit 0"],
                      label="t")
    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    fe = InteractiveFrontend(core, stdin_fd=r_in, stdout_fd=w_out)
    seen = []
    core.journal.add_listener(seen.append)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": r_in}, daemon=True)
    t.start()
    t.join(timeout=5)
    os.close(w_in)
    os.close(w_out)
    shown = b""
    while True:
        chunk = os.read(r_out, 65536)
        if not chunk:
            break
        shown += chunk
    assert b"\x1b]0;\xe2\x8f\xba t (" in shown and b"(child-title)\x07" in shown
    assert b"\x1b]0;child-title\x07" not in shown
    journaled = "".join(r.get("text", "") for r in seen)
    assert "\u23fa" not in journaled and "visible" in journaled
