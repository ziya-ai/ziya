"""A model-staged task binding belongs at the END of the turn that built it.

The turn's human prompt is timestamped before the tool call and the
assistant reply after it, so "last message at or before created_at" put
the tile between the two — the start of the turn.  These pin the pure
rule and, through ``_inject_task_results``, the seam where server.py
actually uses it: a helper nobody calls fixes nothing.
"""

import uuid

from app.utils.binding_placement import unanchored_insert_index

# Fixtures of the existing injection tests, so the storage layout is the
# one the server reads rather than a guess at it.
from tests.test_task_result_injection import (  # noqa: F401
    _write_binding, _write_card, _write_chat, _write_run, env_with_chat,
)


def h(t):
    return {"type": "human", "content": "q", "_timestamp": t}


def ai(t=None):
    m = {"type": "ai", "content": "a"}
    if t is not None:
        m["_timestamp"] = t
    return m


class TestUnanchoredInsertIndex:
    def test_mid_turn_binding_lands_after_the_reply_not_the_prompt(self):
        # prompt@100, staged@150, reply persisted@200, next prompt@300
        hist = [h(100), ai(200), h(300), ai(400)]
        assert unanchored_insert_index(hist, 150) == 2

    def test_mid_turn_binding_of_the_last_turn_goes_to_the_end(self):
        assert unanchored_insert_index([h(100), ai(200)], 150) == 2

    def test_walks_past_every_non_human_message_of_the_turn(self):
        hist = [h(100), ai(200), {"type": "system", "_timestamp": 210}, ai(220), h(300)]
        assert unanchored_insert_index(hist, 150) == 4

    def test_between_turns_binding_stays_after_the_reply_it_followed(self):
        # Run clicked after reply@200, before prompt@300: unchanged rule.
        assert unanchored_insert_index([h(100), ai(200), h(300), ai(400)], 250) == 2

    def test_reply_without_timestamp_is_still_walked_through(self):
        assert unanchored_insert_index([h(100), ai(), h(300)], 150) == 2

    def test_prompt_with_no_reply_yet_inserts_after_the_prompt(self):
        # Streaming: the reply is not in the history.  Nowhere else to go.
        assert unanchored_insert_index([h(100), ai(200), h(300)], 350) == 3

    def test_older_than_every_message_goes_to_the_head(self):
        assert unanchored_insert_index([h(100), ai(200)], 50) == 0

    def test_empty_history(self):
        assert unanchored_insert_index([], 1) == 0

    def test_accepts_role_keyed_records(self):
        hist = [{"role": "human", "_timestamp": 100}, {"role": "assistant", "_timestamp": 200}]
        assert unanchored_insert_index(hist, 150) == 2


# ── seam: app.server._inject_task_results uses the rule ────────────────

def test_inject_places_a_mid_turn_result_after_the_reply(env_with_chat):
    env = env_with_chat
    chat_id = "c_" + uuid.uuid4().hex[:8]
    _write_chat(env, chat_id, [
        {"id": "m1", "role": "human", "content": "q", "timestamp": 100},
        {"id": "m2", "role": "assistant", "content": "a", "timestamp": 200},
        {"id": "m3", "role": "human", "content": "q2", "timestamp": 300},
        {"id": "m4", "role": "assistant", "content": "a2", "timestamp": 400},
    ])
    _write_card(env, "card_1", "T")
    _write_run(env, "run_1", "card_1", "done", summary="did it", decisions=[])
    # Staged by a tool call mid-turn: after the prompt@100, before the
    # reply@200 was persisted.
    _write_binding(env, chat_id, "b1", "card_1", "run_1",
                   anchor_message_id=None, created_at=150)

    history = [h(100), ai(200), h(300), ai(400)]
    from app.server import _inject_task_results
    _inject_task_results(history, chat_id)

    kinds = [m.get("type") or m.get("role") for m in history]
    # After the reply that staged it — NOT between prompt and reply.
    assert kinds == ["human", "ai", "system", "human", "ai"], kinds
    assert "did it" in history[2]["content"]
