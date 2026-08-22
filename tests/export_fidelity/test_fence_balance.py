"""MD-03 regression: per-message code-fence balancing on the markdown export.

An unterminated ``` fence inside ONE message must NOT swallow the following
messages or the export footer into a single runaway code block (the CommonMark
behaviour on Gist/GitHub/local viewers). The exporter balances each message's
fences at the message boundary — losslessly: the code block still renders in
full, it just ends where the message ends (as it does in the chat UI, which
renders each message in its own isolated container).

These tests guard both directions:
  * the runaway is fixed (fence_integrity + roundtrip go fail -> pass), AND
  * the balancer does NOT touch already-balanced / non-fence content
    (over-consumption guards — a widened structural fix must not eat content).
"""

from app.utils.conversation_exporter import (
    export_conversation_for_paste,
    _balance_code_fences,
)
from tests.export_fidelity import checks as C
from tests.export_fidelity import fixture

F = "`" * 3  # a literal triple-backtick fence, built without embedding one in source


def _export(msgs):
    return export_conversation_for_paste(msgs, format_type="markdown")["content"]


# --------------------------------------------------------------------------
# End-to-end: the reproduced defect is fixed.
# --------------------------------------------------------------------------

def test_unterminated_fence_does_not_swallow_following_message():
    # A long answer so the doc clears roundtrip's >20-nonblank-line runaway gate.
    answer = "ANSWER_AFTER_MARKER the real answer.\n" + "\n".join(
        f"answer line {i}" for i in range(1, 22)
    )
    msgs = [
        {"role": "human", "content": "Show me:\n" + F + "python\nprint('no closing fence')"},
        {"role": "assistant", "content": answer},
    ]
    md = _export(msgs)

    fi = C.check_md_fence_integrity(md)
    rt = C.check_md_roundtrip_legible(md)
    assert fi.measurements["unterminated_fence"] == 0, fi.failures
    assert rt.passed, rt.failures
    # Nothing lost: both the code and the whole answer survive.
    assert "print('no closing fence')" in md
    assert "ANSWER_AFTER_MARKER" in md
    assert "answer line 21" in md
    # The footer is not inside the code block.
    assert "Export Metadata" in md


def test_short_unterminated_fence_case_is_balanced():
    msgs = [
        {"role": "human", "content": F + "python\nunclosed"},
        {"role": "assistant", "content": "ANSWER_AFTER"},
    ]
    md = _export(msgs)
    assert C.check_md_fence_integrity(md).measurements["unterminated_fence"] == 0
    assert "ANSWER_AFTER" in md
    assert "unclosed" in md


def test_canonical_fixture_export_is_unchanged_and_balanced():
    """The balancer is a no-op on well-formed conversations (no drift)."""
    md = _export(fixture.make_fidelity_conversation())
    fi = C.check_md_fence_integrity(md)
    assert fi.passed, fi.failures
    assert fi.measurements["unterminated_fence"] == 0


# --------------------------------------------------------------------------
# Unit-level: _balance_code_fences behaviour + over-consumption guards.
# --------------------------------------------------------------------------

def test_balancer_closes_open_fence():
    out = _balance_code_fences(F + "python\nopen block")
    assert out.rstrip().endswith(F)
    assert out.startswith(F + "python")


def test_balancer_matches_opener_tick_length():
    # A 4-backtick opener must be closed with a 4-backtick fence, not 3.
    quad = "`" * 4
    out = _balance_code_fences(quad + "\nopen wide")
    assert out.split("\n")[-1] == quad


def test_balancer_noop_on_balanced_fence():
    src = F + "python\nx = 1\n" + F
    assert _balance_code_fences(src) == src


def test_balancer_noop_on_no_fences():
    src = "prose with `inline` code and no block fences"
    assert _balance_code_fences(src) == src


def test_balancer_noop_on_two_balanced_fences():
    src = F + "a\n" + F + "\nmiddle text\n" + F + "b\n" + F
    assert _balance_code_fences(src) == src


def test_balancer_treats_longer_inner_fence_as_content():
    # A longer (4+) tick line INSIDE a 3-tick block is content, not a closer,
    # so the block is genuinely still open and must be closed once.
    src = F + "\nnested " + ("`" * 4) + " stuff\n" + F
    # This particular src IS balanced (opens 3, closes 3), inner 4 is content.
    assert _balance_code_fences(src) == src


def test_balancer_does_not_double_close():
    # Already-closed content must be returned byte-identical.
    src = "text\n" + F + "sh\necho hi\n" + F + "\nmore text"
    assert _balance_code_fences(src) == src
