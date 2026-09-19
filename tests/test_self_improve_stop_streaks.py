"""
Self-improvement fix 4: repeated ``stop`` verdicts surface as ONE finding.

Motivating evidence: the GFX Stage 2 card's block b-5cc1081c stopped six
runs in a row (Sep 1–14) for the same root cause — the headless render
server does not pick up a rebuilt bundle — worded six different ways.
The panel showed six orange rows; nothing said "this block has been
blocked by its environment for two weeks".

Grouping is positional (consecutive stops on one block, oldest→newest,
with judge errors skipped and any accept/revise resetting), never
textual: no string compare groups those six rationales, and clustering
ledger rows with a model call is not worth the cost.
"""
import asyncio

import pytest

from app.utils import self_improve as si


def _run(coro):
    return asyncio.run(coro)


def _rec(verdict, block="b", ts=0.0, run="r", **kw):
    d = {"card_id": "c1", "block_id": block, "verdict": verdict,
         "ts": ts, "run_id": run, "rationale": f"{verdict}@{ts}"}
    d.update(kw)
    return d


class TestStopStreaks:
    def test_single_stop_is_not_a_streak(self):
        assert si.stop_streaks([_rec("stop")]) == {}

    def test_consecutive_stops_group(self):
        recs = [_rec("stop", ts=1, run="r1"), _rec("stop", ts=2, run="r2"),
                _rec("stop", ts=3, run="r3")]
        got = si.stop_streaks(recs)
        assert set(got) == {"b"}
        s = got["b"]
        assert s["count"] == 3
        assert (s["first_ts"], s["last_ts"]) == (1, 3)
        assert s["run_ids"] == ["r1", "r2", "r3"]
        # newest first, so the most recent wording leads
        assert s["rationales"] == ["stop@3", "stop@2", "stop@1"]

    def test_accept_resets_the_streak(self):
        recs = [_rec("stop", ts=1), _rec("stop", ts=2),
                _rec("accept", ts=3), _rec("stop", ts=4)]
        # trailing run is length 1 → not a streak; the earlier pair is
        # history, not a live finding
        assert si.stop_streaks(recs) == {}

    def test_revise_resets_the_streak(self):
        recs = [_rec("stop", ts=1), _rec("stop", ts=2), _rec("revise", ts=3)]
        assert si.stop_streaks(recs) == {}

    def test_judge_error_is_transparent(self):
        """An error record says the JUDGE failed, not that the run
        passed — it must neither extend nor break the streak."""
        recs = [_rec("stop", ts=1), _rec(si.JUDGE_ERROR_VERDICT, ts=2),
                _rec("stop", ts=3)]
        got = si.stop_streaks(recs)
        assert got["b"]["count"] == 2
        assert got["b"]["run_ids"] == ["r", "r"]

    def test_streaks_are_per_block(self):
        recs = [_rec("stop", block="x", ts=1), _rec("stop", block="y", ts=2),
                _rec("stop", block="x", ts=3), _rec("accept", block="y", ts=4)]
        got = si.stop_streaks(recs)
        assert set(got) == {"x"}
        assert got["x"]["count"] == 2

    def test_rationales_capped_at_five(self):
        recs = [_rec("stop", ts=i) for i in range(8)]
        got = si.stop_streaks(recs)
        assert got["b"]["count"] == 8
        assert len(got["b"]["rationales"]) == 5
        assert got["b"]["rationales"][0] == "stop@7"

    def test_lesson_preferred_over_rationale(self):
        recs = [_rec("stop", ts=1, lesson="L1"), _rec("stop", ts=2, lesson="L2")]
        assert si.stop_streaks(recs)["b"]["rationales"] == ["L2", "L1"]

    def test_gfx2_shape(self):
        """The real ledger sequence for GFX Stage 2 b-5cc1081c: stop,
        accept, error, accept, then six stops.  The finding is the six,
        not seven, and the earlier stop is history."""
        seq = ["stop", "accept", si.JUDGE_ERROR_VERDICT, "accept"] + ["stop"] * 6
        recs = [_rec(v, block="b-5cc1081c", ts=i, run=f"r{i}")
                for i, v in enumerate(seq)]
        got = si.stop_streaks(recs)
        assert got["b-5cc1081c"]["count"] == 6
        assert got["b-5cc1081c"]["run_ids"] == [f"r{i}" for i in range(4, 10)]


class TestDeckAggregate:
    def test_summary_carries_max_trailing_streak(self, tmp_path):
        ledger = si.LessonLedger(tmp_path)
        for v in ("stop", "stop", "stop"):
            ledger.record(_rec(v, block="x"))
        ledger.record(_rec("stop", block="y"))
        ledger.record({"card_id": "c2", "block_id": "z", "verdict": "accept"})
        s = ledger.summary_by_card()
        assert s["c1"]["stop_streak"] == 3
        assert s["c2"]["stop_streak"] == 0

    def test_summary_streak_resets_on_pass(self, tmp_path):
        ledger = si.LessonLedger(tmp_path)
        for v in ("stop", "stop", "accept"):
            ledger.record(_rec(v))
        assert ledger.summary_by_card()["c1"]["stop_streak"] == 0


class TestApiSeam:
    def test_card_lessons_endpoint_reports_stop_streaks(self, tmp_path, monkeypatch):
        """The aggregate must reach the HTTP payload the panel reads,
        keyed by block id, with the records themselves untouched."""
        pytest.importorskip("fastapi")
        from app.api import task_cards as api

        class _Store:
            def get(self, cid):
                return object()

        monkeypatch.setattr(api, "_get_storage", lambda pid: _Store())
        monkeypatch.setattr(api, "get_project_dir", lambda pid: tmp_path)
        ledger = si.LessonLedger(tmp_path)
        for i in range(3):
            ledger.record(_rec("stop", block="b-5cc1081c", ts=i, run=f"r{i}"))
        body = _run(api.get_card_lessons("p1", "c1", limit=200))
        assert body["count"] == 3
        assert body["stop_streaks"]["b-5cc1081c"]["count"] == 3
        assert body["stop_streaks"]["b-5cc1081c"]["run_ids"] == ["r0", "r1", "r2"]
        # rows are still all there — the streak is an overlay, not a
        # replacement, so nothing a user could revert or read is hidden
        assert len(body["lessons"]) == 3

    def test_no_streak_key_is_empty_dict(self, tmp_path, monkeypatch):
        pytest.importorskip("fastapi")
        from app.api import task_cards as api

        class _Store:
            def get(self, cid):
                return object()

        monkeypatch.setattr(api, "_get_storage", lambda pid: _Store())
        monkeypatch.setattr(api, "get_project_dir", lambda pid: tmp_path)
        si.LessonLedger(tmp_path).record(_rec("accept"))
        body = _run(api.get_card_lessons("p1", "c1", limit=200))
        assert body["stop_streaks"] == {}
