"""Unit tests for app/utils/card_self_improve.py — the patch whitelist,
id bounds, ledger behavior, and critic reply parsing."""

import json

import pytest

from app.models.task_card import Block
from app.utils.card_self_improve import (
    LessonLedger,
    PATCHABLE_FIELDS,
    apply_text_patch,
    collect_patchable_ids,
    parse_critic_reply,
    patch_hash,
)


def _tree() -> Block:
    return Block(
        block_type="group", id="g-1",
        body=[
            Block(block_type="state", id="s-1", state_context="assume prod"),
            Block(block_type="task", id="t-1", instructions="do the thing",
                  scope=None),
            Block(block_type="repeat", id="r-1", repeat_mode="count",
                  repeat_count=2, body=[
                      Block(block_type="task", id="t-2",
                            instructions="inner work"),
                  ]),
        ],
    )


class TestCollectIds:
    def test_walks_whole_subtree(self):
        ids = collect_patchable_ids(_tree())
        assert set(ids) == {"g-1", "s-1", "t-1", "r-1", "t-2"}
        assert ids["t-2"] == "task"

    def test_dict_form(self):
        ids = collect_patchable_ids(_tree().model_dump())
        assert set(ids) == {"g-1", "s-1", "t-1", "r-1", "t-2"}


class TestApplyTextPatch:
    def test_applies_whitelisted_fields(self):
        tree = _tree()
        errs = apply_text_patch(tree, {
            "t-1": {"instructions": "do the thing, but check X first"},
            "s-1": {"state_context": "assume prod; migration already ran"},
        })
        assert errs == []
        assert tree.body[1].instructions == "do the thing, but check X first"
        assert tree.body[0].state_context == "assume prod; migration already ran"

    def test_rejects_unknown_id(self):
        tree = _tree()
        errs = apply_text_patch(tree, {"nope": {"instructions": "x"}})
        assert len(errs) == 1 and "unknown block id" in errs[0]
        # all-or-nothing: nothing was written
        assert tree.body[1].instructions == "do the thing"

    def test_rejects_non_whitelisted_field(self):
        tree = _tree()
        for bad in ("scope", "repeat_count", "shell_commands", "id",
                    "block_type", "body"):
            errs = apply_text_patch(tree, {"t-1": {bad: "x"}})
            assert errs, f"field {bad!r} must be rejected"
        assert tree.body[1].instructions == "do the thing"

    def test_all_or_nothing_on_partial_failure(self):
        tree = _tree()
        errs = apply_text_patch(tree, {
            "t-1": {"instructions": "good change"},
            "t-2": {"repeat_count": "bad change"},
        })
        assert errs
        assert tree.body[1].instructions == "do the thing"

    def test_allowed_ids_bounds_the_subtree(self):
        tree = _tree()
        # simulate improving only the repeat subtree: t-1 is out of bounds
        allowed = set(collect_patchable_ids(tree.body[2]))
        errs = apply_text_patch(tree, {"t-1": {"instructions": "x"}}, allowed)
        assert len(errs) == 1 and "outside the improving subtree" in errs[0]
        errs = apply_text_patch(
            tree, {"t-2": {"instructions": "inner work v2"}}, allowed)
        assert errs == []
        assert tree.body[2].body[0].instructions == "inner work v2"

    def test_rejects_empty_and_non_string_values(self):
        tree = _tree()
        assert apply_text_patch(tree, {"t-1": {"instructions": ""}})
        assert apply_text_patch(tree, {"t-1": {"instructions": 42}})
        assert apply_text_patch(tree, {})
        assert apply_text_patch(tree, {"t-1": {}})

    def test_never_touches_ids_or_structure(self):
        tree = _tree()
        before = tree.model_dump()
        apply_text_patch(tree, {"t-2": {"instructions": "inner work v2"}})
        after = tree.model_dump()
        after["body"][2]["body"][0]["instructions"] = before["body"][2]["body"][0]["instructions"]
        assert after == before  # only the one whitelisted field differed


class TestPatchHash:
    def test_stable_and_order_insensitive(self):
        a = {"t-1": {"instructions": "x"}, "s-1": {"state_context": "y"}}
        b = {"s-1": {"state_context": "y"}, "t-1": {"instructions": "x"}}
        assert patch_hash(a) == patch_hash(b)
        assert patch_hash(a) != patch_hash({"t-1": {"instructions": "z"}})


class TestLessonLedger:
    def test_record_and_query(self, tmp_path):
        ledger = LessonLedger(tmp_path)
        ledger.record({"card_id": "c1", "block_id": "b1",
                       "verdict": "revise", "lesson": "first",
                       "patch_hash": "h1", "applied": True})
        ledger.record({"card_id": "c1", "block_id": "b2",
                       "verdict": "accept", "lesson": "second",
                       "applied": False})
        ledger.record({"card_id": "c2", "block_id": "b1",
                       "verdict": "revise", "lesson": "other card",
                       "applied": True})
        got = ledger.lessons_for("c1")
        assert [e["lesson"] for e in got] == ["first", "second"]
        got = ledger.lessons_for("c1", "b1")
        assert [e["lesson"] for e in got] == ["first"]

    def test_seen_patch_requires_applied_and_same_block(self, tmp_path):
        ledger = LessonLedger(tmp_path)
        ledger.record({"card_id": "c1", "block_id": "b1",
                       "verdict": "revise", "patch_hash": "h1",
                       "applied": True})
        ledger.record({"card_id": "c1", "block_id": "b2",
                       "verdict": "revise", "patch_hash": "h2",
                       "applied": False})
        assert ledger.seen_patch("c1", "b1", "h1")
        assert not ledger.seen_patch("c1", "b2", "h2")  # never applied
        assert not ledger.seen_patch("c1", "b1", "h2")
        assert not ledger.seen_patch("c9", "b1", "h1")

    def test_ledger_faults_never_raise(self, tmp_path):
        bad = tmp_path / "task_card_lessons.jsonl"
        bad.write_text("{not json}\n")
        ledger = LessonLedger(tmp_path)
        ledger.record({"card_id": "c1", "block_id": "b1", "verdict": "accept"})
        assert ledger.lessons_for("c1")


class TestParseCriticReply:
    def test_valid_revise(self):
        got = parse_critic_reply(json.dumps({
            "verdict": "revise", "lesson": "be specific",
            "patch": {"t-1": {"instructions": "x"}},
        }))
        assert got["verdict"] == "revise"
        assert got["patch"] == {"t-1": {"instructions": "x"}}

    def test_prose_wrapped_json(self):
        got = parse_critic_reply(
            'Sure! Here is my verdict:\n{"verdict": "accept", "lesson": "ok"}')
        assert got["verdict"] == "accept"

    def test_patch_stripped_unless_revise(self):
        got = parse_critic_reply(json.dumps({
            "verdict": "accept", "lesson": "ok",
            "patch": {"t-1": {"instructions": "sneaky"}},
        }))
        assert got["patch"] == {}

    def test_garbage_resolves_to_no_improvement(self):
        for text in (None, "", "yes", "{broken", '{"verdict": "maybe"}'):
            got = parse_critic_reply(text)
            assert got["verdict"] == "no_improvement"
            assert got["patch"] == {}

    def test_lesson_capped(self):
        got = parse_critic_reply(json.dumps(
            {"verdict": "accept", "lesson": "x" * 1000}))
        assert len(got["lesson"]) == 300
