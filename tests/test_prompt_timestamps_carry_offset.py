"""Every timestamp the model sees carries its UTC offset, in one shared form.

Before: <CurrentDateTime>, <ConversationStartTime> and <MessageTime> were
naive local times ("2026-10-23 23:51:04"), while the project-change note in
the same transcript was labelled UTC ("2026-10-24 06:51:04 UTC") and tool
output (date, git, mtimes) carried whatever zone it had.  Nothing told the
model which clock the tags were on, so it spent turns reconciling them.

Now all four go through ``format_prompt_timestamp`` and read
"2026-10-23 23:51:04-07:00".
"""
import datetime
import re
from unittest import mock

import pytest

from app.utils.session_context_prompt import (
    build_session_context_section,
    format_prompt_timestamp,
)
from app.utils.project_change_note import build_project_change_message

TZ = datetime.timezone(datetime.timedelta(hours=-7))
AWARE = datetime.datetime(2026, 10, 23, 23, 51, 4, tzinfo=TZ)
AWARE_MS = int(AWARE.timestamp() * 1000)
# "YYYY-MM-DD HH:MM:SS" followed by a mandatory ±HH:MM offset.
OFFSET_RE = r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}'


class TestFormatter:
    def test_aware_datetime_keeps_its_offset(self):
        assert format_prompt_timestamp(AWARE) == "2026-10-23 23:51:04-07:00"
        # Not converted to the machine's zone: a fixed +05:30 stays +05:30
        # regardless of where the test runs or the time of year.
        ist = datetime.datetime(2026, 1, 2, 3, 4, 5,
                                tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        assert format_prompt_timestamp(ist) == "2026-01-02 03:04:05+05:30"

    def test_naive_datetime_is_local_wall_clock(self):
        # datetime.now() is naive-local; the formatter must not reinterpret
        # it as UTC.  Compare against the same instant made aware locally.
        naive = datetime.datetime(2026, 1, 2, 3, 4, 5)
        assert format_prompt_timestamp(naive) == naive.astimezone().isoformat(sep=" ", timespec="seconds")
        assert format_prompt_timestamp(naive).startswith("2026-01-02 03:04:05")

    def test_epoch_ms_is_the_same_instant(self):
        got = format_prompt_timestamp(AWARE_MS)
        assert datetime.datetime.fromisoformat(got) == AWARE
        assert re.fullmatch(OFFSET_RE, got)

    def test_every_output_has_an_offset(self):
        for v in (AWARE, AWARE_MS, datetime.datetime(2026, 1, 2, 3, 4, 5)):
            assert re.fullmatch(OFFSET_RE, format_prompt_timestamp(v)), v


class TestTags:
    def test_current_datetime_tag(self):
        out = build_session_context_section(project_root="/p", now=AWARE)
        assert '<CurrentDateTime value="2026-10-23 23:51:04-07:00" />' in out

    def test_default_now_carries_offset(self):
        out = build_session_context_section(project_root="/p")
        m = re.search(r'<CurrentDateTime value="([^"]+)" />', out)
        assert m and re.fullmatch(OFFSET_RE, m.group(1)), out

    def test_project_change_note_uses_the_same_form_not_utc(self):
        msg = build_project_change_message(
            "moved", {"id": "a", "name": "A", "path": "/a"},
            {"id": "b", "name": "B", "path": "/b"}, now_ms=AWARE_MS)
        assert " UTC" not in msg["content"]
        m = re.search(r"at (" + OFFSET_RE + r")\.", msg["content"])
        assert m, msg["content"]
        assert datetime.datetime.fromisoformat(m.group(1)) == AWARE


def _build(chat_history, conv_start_ts=None):
    from app.utils.precision_prompt_system import PrecisionPromptSystem
    with mock.patch("app.shadow.context.attached_sessions_tag", return_value=""):
        return PrecisionPromptSystem().build_messages(
            request_path="/ziya",
            model_info={"model_name": "sonnet4.0", "model_family": "claude",
                        "endpoint": "bedrock", "model_id": "x"},
            files=[], question="q", chat_history=chat_history,
            conversation_id="test-conv", conv_start_ts=conv_start_ts,
        )


class TestEndToEnd:
    """The outermost surface: the messages actually sent to the model."""

    def test_message_time_and_conversation_start_carry_offsets(self):
        msgs = _build([{"role": "user", "content": "hello", "_timestamp": AWARE_MS}],
                      conv_start_ts=AWARE_MS)
        user_tags = re.findall(r'<MessageTime value="([^"]+)" />', msgs[1]["content"])
        assert user_tags and datetime.datetime.fromisoformat(user_tags[0]) == AWARE
        assert re.fullmatch(OFFSET_RE, user_tags[0])
        last = msgs[-1]["content"]
        for tag in ("CurrentDateTime", "ConversationStartTime"):
            m = re.search(rf'<{tag} value="([^"]+)" />', last)
            assert m, (tag, last)
            assert re.fullmatch(OFFSET_RE, m.group(1)), (tag, m.group(1))
        start = re.search(r'<ConversationStartTime value="([^"]+)" />', last).group(1)
        assert datetime.datetime.fromisoformat(start) == AWARE

    def test_message_timing_paragraph_describes_the_offset(self):
        system = _build([])[0]["content"]
        assert 'HH:MM:SS+HH:MM' in system
        assert "UTC offset" in system
