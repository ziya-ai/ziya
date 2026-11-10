"""open_fence_at_end: the fence open after already-streamed text (app/utils/fence_state.py)."""
from app.utils.fence_state import open_fence_at_end, closer_re


def test_reads_open_fence_and_its_close():
    c = {}
    assert open_fence_at_end("intro\n\n```mermaid\ngraph TD\n", c) == (0, '`', 3)
    assert open_fence_at_end("intro\n\n```mermaid\ngraph TD\n```\n", c) is None


def test_incremental_scan_only_reads_new_complete_lines():
    c = {}
    t = "```diff\n"
    assert open_fence_at_end(t, c) == (0, '`', 3)
    t += " ```bash\n"          # indented: content inside a column-0 diff
    assert open_fence_at_end(t, c) == (0, '`', 3)
    t += "``"                  # partial line: not scanned yet
    assert open_fence_at_end(t, c) == (0, '`', 3)
    t += "`\n"
    assert open_fence_at_end(t, c) is None
    assert c['_fence_state_len'] == len(t)


def test_truncated_text_resets_the_cache():
    c = {}
    open_fence_at_end("```python\nx\n", c)
    assert open_fence_at_end("done\n", c) is None


def test_code_span_at_line_start_is_not_an_opener():
    assert open_fence_at_end("```` ```diff ````\n", {}) is None
    assert open_fence_at_end("~~~\nsee ```diff\n", {}) == (0, '~', 3)


def test_closer_must_be_long_enough_and_same_char():
    f = (0, '`', 4)
    assert closer_re(f).match("````")
    assert closer_re(f).match("`````  ")
    assert not closer_re(f).match("```")
    assert not closer_re(f).match("~~~~")
    assert not closer_re((0, '`', 3)).match(" ```")
    assert closer_re((1, '`', 3)).match(" ```")
