"""
Ranking and sort-mode tests for app/storage/chat_search.search_chats.

These guard the three specific defects the old ordering had:

  1. Relevance was a raw occurrence count, so a long conversation that
     mentions the term incidentally many times outranked the conversation
     whose *title* is the term.
  2. Repeated hits inside a single message each counted fully, letting one
     verbose message dominate.
  3. "Age" was lastAccessedAt, which moves when a chat is merely OPENED.
     Skimming a two-year-old thread made it the freshest search result.

Most tests assert on the ORDER search_chats returns -- the outermost surface
the sidebar renders -- rather than on the private score helper, so they stay
valid if the scoring internals are retuned.  The expected scores behind each
fixture are noted inline; see _relevance_score for the formula.
"""

import json
import time

import pytest

from app.storage.chat_search import search_chats, _relevance_score


PID = "proj-rank"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / ".ziya"
    (h / "projects" / PID / "chats").mkdir(parents=True)
    return h


def _write(home, chat_id, *, title="Untitled", messages,
           last_active=None, last_accessed=None, project_id=PID):
    """Write a chat record.  `messages` is a list of (role, content) pairs."""
    chats = home / "projects" / project_id / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    body = {
        "id": chat_id,
        "title": title,
        "messages": [
            {"id": f"m{i}", "role": r, "content": c}
            for i, (r, c) in enumerate(messages)
        ],
        "createdAt": 1000,
        "lastActiveAt": last_active if last_active is not None else 1000,
    }
    if last_accessed is not None:
        body["lastAccessedAt"] = last_accessed
    (chats / f"{chat_id}.json").write_text(json.dumps(body))


def _ids(results):
    return [r["conversationId"] for r in results]


def _score_of(results, chat_id):
    return next(r["relevanceScore"] for r in results
                if r["conversationId"] == chat_id)


# --------------------------------------------------------------------------
# Relevance
# --------------------------------------------------------------------------

def test_title_hit_outranks_incidental_body_volume(home):
    """A conversation TITLED for the term beats a long one that name-drops it.

    titled:  title hit only                       -> 8.00
    verbose: 12 hits at idx 20..31 of 60 messages -> 12 / sqrt(6) = 4.90

    Under the old raw-count score this failed: verbose scored 12 and titled
    scored 1, so the incidental thread came first.
    """
    _write(home, "titled", title="Kalman filter design",
           messages=[("human", "how do I tune this"),
                     ("assistant", "start with the process noise")])
    _write(home, "verbose", title="Weekly sync notes",
           messages=([("assistant", "unrelated chatter")] * 20
                     + [("assistant", f"aside {i}: kalman came up again")
                        for i in range(12)]
                     + [("assistant", "unrelated chatter")] * 28))

    results = search_chats(home, PID, "kalman")
    assert _ids(results)[0] == "titled", f"got {_ids(results)}"
    # Both must still be RETURNED -- the fix reorders, it does not filter.
    assert set(_ids(results)) == {"titled", "verbose"}
    assert _score_of(results, "titled") > _score_of(results, "verbose")


def test_repeated_hits_in_one_message_are_collapsed(home):
    """One message saying the term 10x must not beat 4 messages saying it once.

    shouty:    1 msg, 10 occurrences, idx 0 -> 1.964 * 2 = 3.93
    sustained: 4 msgs, 1 occurrence each    -> 2 + 2 + 1 + 1 = 6.00

    Old behaviour: 10 occurrences scored 10 and beat 4.
    """
    _write(home, "shouty", title="a",
           messages=[("human", " ".join(["widget"] * 10))])
    _write(home, "sustained", title="b",
           messages=[("human", "widget one"),
                     ("assistant", "widget two"),
                     ("human", "widget three"),
                     ("assistant", "widget four")])

    order = _ids(search_chats(home, PID, "widget"))
    assert order == ["sustained", "shouty"], (
        f"sustained discussion should outrank one repetitive message, got {order}")


def test_term_frequency_saturates(home):
    """Ten hits in a message must be worth well under ten single hits.

    Asserted on the score directly because it is the saturation curve itself
    that is under test, not the resulting order.  Asymptote is TF_K1 + 1.
    """
    one = _relevance_score([{"messageIndex": 5,
                             "highlightPositions": [{"start": 0, "length": 1}]}],
                           False, 1)
    ten = _relevance_score([{"messageIndex": 5,
                             "highlightPositions": [{"start": i, "length": 1}
                                                    for i in range(10)]}],
                           False, 1)
    assert one == pytest.approx(1.0)
    assert 1.5 < ten < 2.2, f"10 hits scored {ten}; expected saturation near 2"


def test_long_conversation_is_length_normalised(home):
    """Equal hit counts: the shorter, denser conversation is more on-topic.

    focused:   4 hits in 4 messages  -> 6.00 (no length penalty under 10 msgs)
    sprawling: same 4 hits in 80     -> 6 / sqrt(8) = 2.12
    """
    _write(home, "focused", title="a",
           messages=[("human", "gearbox note")] * 4)
    _write(home, "sprawling", title="b",
           messages=[("human", "gearbox note")] * 4
                    + [("assistant", "unrelated chatter")] * 76)

    order = _ids(search_chats(home, PID, "gearbox"))
    assert order == ["focused", "sprawling"], (
        f"shorter on-topic thread should win, got {order}")


def test_short_conversations_are_not_penalised(home):
    """At or under the free-length threshold the normaliser must be exactly 1.

    Without the max(1.0, ...) floor, sqrt(1/10) would *inflate* short
    conversations -- a different bug in the same expression.
    """
    match = [{"messageIndex": 5,
              "highlightPositions": [{"start": 0, "length": 1}]}]
    assert _relevance_score(match, False, 1) == pytest.approx(1.0)
    assert _relevance_score(match, False, 10) == pytest.approx(1.0)


def test_opening_messages_weigh_more_than_later_ones(home):
    """A hit in the first two messages counts double.

    Both conversations are six messages with one hit each; only the position
    differs, so nothing but the opening multiplier can separate them.
    early -> 2.00, late -> 1.00.
    """
    _write(home, "early", title="a",
           messages=[("human", "about telemetry")] + [("assistant", "x")] * 5)
    _write(home, "late", title="b",
           messages=[("assistant", "x")] * 5 + [("human", "about telemetry")])

    order = _ids(search_chats(home, PID, "telemetry"))
    assert order == ["early", "late"], f"got {order}"


def test_score_and_activity_are_exposed_on_every_result(home):
    """relevanceScore and lastActivityAt must be present on the wire.

    The frontend comparator reads both; if the server stopped emitting them
    ordering would silently degrade to totalMatches with no visible error.
    """
    _write(home, "c1", title="a", messages=[("human", "hello world")],
           last_active=4242)
    (result,) = search_chats(home, PID, "hello")
    assert result["relevanceScore"] > 0
    assert result["lastActivityAt"] == 4242
    # totalMatches keeps its old meaning -- it is displayed as "N matches".
    assert result["totalMatches"] == 1


# --------------------------------------------------------------------------
# Age / sort modes
# --------------------------------------------------------------------------

def test_age_uses_last_activity_not_last_access(home):
    """Opening an old chat must not make it the newest result.

    stale was last written in 2020 but has just been opened (recent
    lastAccessedAt).  fresh was written seconds ago but not opened since 2020.
    The old code preferred lastAccessedAt and ranked stale first.
    """
    now = int(time.time() * 1000)
    _write(home, "stale", title="a", messages=[("human", "sprocket")],
           last_active=1_600_000_000_000, last_accessed=now)
    _write(home, "fresh", title="b", messages=[("human", "sprocket")],
           last_active=now - 1000, last_accessed=1_600_000_000_000)

    order = _ids(search_chats(home, PID, "sprocket", sort="newest"))
    assert order == ["fresh", "stale"], (
        f"newest must follow last activity, not last access; got {order}")


def test_message_timestamp_backfills_missing_last_activity(home):
    """Records predating lastActiveAt fall back to the newest message stamp."""
    chats = home / "projects" / PID / "chats"
    (chats / "legacy.json").write_text(json.dumps({
        "id": "legacy", "title": "t", "createdAt": 0, "lastActiveAt": 0,
        "messages": [
            {"id": "m0", "role": "human", "content": "grommet", "_timestamp": 500},
            {"id": "m1", "role": "assistant", "content": "ok", "_timestamp": 900},
        ],
    }))
    (result,) = search_chats(home, PID, "grommet")
    assert result["lastActivityAt"] == 900


def test_newest_and_oldest_are_inverses(home):
    """Equal scores, distinct activity times, so ordering is purely by age."""
    for cid, ts in (("a", 300), ("b", 200), ("c", 100)):
        _write(home, cid, title="t", messages=[("human", "flange")],
               last_active=ts)

    assert _ids(search_chats(home, PID, "flange", sort="newest")) == ["a", "b", "c"]
    assert _ids(search_chats(home, PID, "flange", sort="oldest")) == ["c", "b", "a"]


def test_sort_mode_actually_changes_order(home):
    """Positive control: relevance and newest must disagree on this fixture.

    wordy:  title hit + 2 opening hits, oldest -> 12.00, activity 100
    recent: one late hit, newest              ->  1.00, activity 9000

    If the sort parameter were ignored, both calls would return the same
    order and this test fails -- which is what makes the other sort tests
    meaningful rather than vacuous.
    """
    _write(home, "wordy", title="hydraulics deep dive",
           messages=[("human", "hydraulics"), ("assistant", "hydraulics")],
           last_active=100)
    _write(home, "recent", title="misc",
           messages=[("assistant", "x")] * 5 + [("human", "hydraulics once")],
           last_active=9_000)

    by_rel = _ids(search_chats(home, PID, "hydraulics", sort="relevance"))
    by_new = _ids(search_chats(home, PID, "hydraulics", sort="newest"))
    assert by_rel == ["wordy", "recent"], f"relevance order: {by_rel}"
    assert by_new == ["recent", "wordy"], f"newest order: {by_new}"
    assert by_rel != by_new


def test_unknown_sort_falls_back_to_relevance(home):
    """A stale client sending an unknown mode gets relevance, not an error."""
    _write(home, "hi", title="pump curve", messages=[("human", "pump")],
           last_active=100)
    _write(home, "lo", title="misc", messages=[("assistant", "pump")],
           last_active=9_000)

    assert (_ids(search_chats(home, PID, "pump", sort="nonsense"))
            == _ids(search_chats(home, PID, "pump", sort="relevance"))
            == ["hi", "lo"])


def test_oldest_pushes_unknown_timestamps_last(home):
    """lastActivityAt == 0 means unknown; it must not pose as the oldest."""
    _write(home, "known", title="t", messages=[("human", "bearing")],
           last_active=500)
    chats = home / "projects" / PID / "chats"
    (chats / "unknown.json").write_text(json.dumps({
        "id": "unknown", "title": "t",
        "messages": [{"id": "m0", "role": "human", "content": "bearing"}],
        "createdAt": 0, "lastActiveAt": 0,
    }))

    order = _ids(search_chats(home, PID, "bearing", sort="oldest"))
    assert order == ["known", "unknown"], (
        f"unknown age should sort last, got {order}")


def test_default_sort_is_relevance(home):
    """Omitting sort must behave identically to sort='relevance'."""
    _write(home, "x", title="cavitation", messages=[("human", "cavitation")],
           last_active=100)
    _write(home, "y", title="misc",
           messages=[("assistant", "z")] * 4 + [("human", "cavitation")],
           last_active=9_000)
    assert (_ids(search_chats(home, PID, "cavitation"))
            == _ids(search_chats(home, PID, "cavitation", sort="relevance"))
            == ["x", "y"])
