"""The inline reasoning-tag scanner stands down once native reasoning has
been seen in the iteration.

Every native path (Claude ``thinking_delta``, DeepSeek ``reasoning_content``,
GLM) sets ``TextDeltaState.native_thinking_seen`` in the executor.  A
provider that delivers reasoning on its own channel does not also emit it
as literal tags in text, so from then on any tag in the text stream is
content -- most often a diff or a code sample discussing the tags -- and
must reach the answer untouched.

Tags are constructed from TAG_NAMES rather than written literally.
"""
import ast
import pathlib

from app.text_delta_processor import process_text_delta
from app.utils.inline_thinking import TAG_NAMES

from tests.test_text_delta_processor import _make_executor, _make_state

OPEN = '<' + TAG_NAMES[-1] + '>'
CLOSE = '</' + TAG_NAMES[-1] + '>'


def _text(events):
    return ''.join(e.get('content', '') for e in events if e.get('type') == 'text')


def _reasoning(events):
    return ''.join(e.get('content', '') for e in events
                   if e.get('type') == 'thinking' and not e.get('done'))


def test_state_has_flag_defaulting_false():
    assert _make_state().native_thinking_seen is False


def test_scanner_active_without_native_thinking():
    """Positive control: the same input IS scanned when no native reasoning
    has arrived, so the gate below is doing the work."""
    ex, st = _make_executor(), _make_state()
    events = process_text_delta(ex, OPEN + 'r' + CLOSE + 'answer\n', st)
    assert _reasoning(events) == 'r'
    assert OPEN not in st.assistant_text


def test_scanner_skipped_once_native_thinking_seen():
    ex, st = _make_executor(), _make_state()
    st.native_thinking_seen = True
    events = process_text_delta(ex, 'The tag ' + OPEN + ' is content\n', st)
    assert _reasoning(events) == ''
    assert OPEN in st.assistant_text
    assert st.inline_thinking.open is False


def test_diff_quoting_a_tag_survives_when_native_thinking_seen():
    ex, st = _make_executor(), _make_state()
    st.native_thinking_seen = True
    body = 'Here:\n\n' + '`' * 3 + 'diff\n+' + OPEN + '\n+' + CLOSE + '\n' + '`' * 3 + '\n'
    process_text_delta(ex, body, st)
    assert OPEN in st.assistant_text and CLOSE in st.assistant_text
    assert st.inline_thinking.open is False


def test_executor_sets_flag_where_native_content_arrives():
    """Seam: the executor must actually set the flag, at the one branch all
    native reasoning paths converge on.  Asserted structurally so a refactor
    that drops the assignment fails here rather than in production."""
    src = pathlib.Path('app/streaming_tool_executor.py').read_text()
    tree = ast.parse(src)
    hits = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == 'native_thinking_seen'
                for t in n.targets)
        and isinstance(n.value, ast.Constant) and n.value.value is True
    ]
    assert hits, "executor never sets _td_state.native_thinking_seen = True"
