"""
scripts/memory_replay/timeline.py: one extraction event per point where
production would have run extraction, with production's history.

Production calls run_post_conversation_extraction once per assistant reply
with the request messages -- the history BEFORE that reply.  Forks repeat
their parent's prefix with the parent's timestamps, so counting it would
double every forked turn (671 of 1,816 live chats are forks).
"""
from scripts.memory_replay.timeline import (
    Event, build_timeline, events_for_chat, history_for, report)


T0 = 1_790_000_000_000   # realistic ms epoch; small values read as seconds


def _m(role, content, t, **kw):
    return {"role": role, "content": content, "timestamp": T0 + t, **kw}


CHAT = {"id": "c1", "projectId": "p1", "messages": [
    _m("human", "q1", 1_000), _m("assistant", "a1", 2_000),
    _m("human", "q2", 3_000), _m("assistant", "a2", 4_000)]}


def test_one_event_per_reply_with_prior_history():
    evs = list(events_for_chat(CHAT, "/w/p1"))
    assert [(e.msg_index, e.t, e.history_len) for e in evs] == [(1, T0 + 2_000, 1), (3, T0 + 4_000, 3)]
    assert history_for(CHAT["messages"], 3) == [
        {"role": "human", "content": "q1"}, {"role": "assistant", "content": "a1"},
        {"role": "human", "content": "q2"}]            # the reply itself is not included


def test_fork_prefix_is_not_replayed():
    fork = {**CHAT, "id": "c2", "branchedFrom": "c1", "branchedAtMessageIndex": 2,
            "messages": CHAT["messages"] + [_m("human", "q3", 9_000),
                                            _m("assistant", "a3", 9_500)]}
    evs = list(events_for_chat(fork, None))
    assert [e.msg_index for e in evs] == [3, 5]        # not 1: that turn is c1's
    assert evs[-1].history_len == 5                     # but history keeps the prefix


def test_filters_match_the_request_path():
    chat = {"id": "c", "messages": [
        _m("human", "q", 1), _m("system", "model changed", 2),
        _m("human", "muted aside", 3, muted=True), _m("assistant", "", 4),
        _m("assistant", "real answer", 5)]}
    evs = list(events_for_chat(chat, None))
    assert [e.msg_index for e in evs] == [4]
    assert history_for(chat["messages"], 4) == [{"role": "human", "content": "q"}]


def test_delegates_and_shells_are_skipped():
    assert list(events_for_chat({**CHAT, "delegateMeta": {"role": "delegate"}}, None)) == []
    assert len(list(events_for_chat({**CHAT, "delegateMeta": {"role": "delegate"}}, None,
                                    include_delegates=True))) == 2
    assert list(events_for_chat({**CHAT, "_isShell": True}, None)) == []


def test_timeline_interleaves_chats_by_time():
    other = {"id": "k", "projectId": "p2", "messages": [
        _m("human", "kq", 2_500), _m("assistant", "ka", 3_500)]}
    evs = build_timeline([CHAT, other], {"p1": "/w/ziya", "p2": "/w/shop"})
    assert [(e.chat_id, e.t) for e in evs] == [("c1", T0 + 2_000), ("k", T0 + 3_500), ("c1", T0 + 4_000)]
    r = report(evs)
    assert r["events"] == 3 and r["project_switches"] == 2
    assert r["by_project"] == {"ziya": 2, "shop": 1}


def test_seconds_timestamps_are_normalised():
    chat = {"id": "s", "messages": [
        {"role": "human", "content": "q", "timestamp": 1_700_000_000},
        {"role": "assistant", "content": "a", "timestamp": 1_700_000_005}]}
    assert [e.t for e in events_for_chat(chat, None)] == [1_700_000_005_000]
