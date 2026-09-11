"""
Tests for context-debug request identity, payload capture and overflow
classification (``app.utils.context_debug``).

These pin three defects observed in real snapshot data
(``~/.ziya/debug/context_snapshots``, 66 conversations / 3493 entries):

1. The ledger had no request identity, so concurrent streams sharing one
   ``conversation_id`` interleaved into a single flat deque whose
   ``iteration`` column read ``2,3,4,3,3,3,2,4,5,...`` — 101
   non-increasing transitions in 20 minutes, unattributable.
2. The 200-entry ring buffer was per CONVERSATION, so a busy conversation
   silently ate the front of its own history (one file began at iteration
   59) with no marker that anything had been dropped.
3. ``is_failure`` was 0 across every recorded entry: overflow detection
   existed as three separate narrow substring lists, and the Bedrock
   client wrapper swallowed context-limit rejections to retry with
   extended-context headers before the executor could see them.

Every test isolates ``ZIYA_HOME`` to a temp dir so no real snapshot or
payload data is read or written.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _fresh_module(payload_enabled: bool = False, detailed: bool = True):
    """Reload context_debug so it re-reads the isolated ZIYA_HOME/flags."""
    os.environ["ZIYA_CONTEXT_DEBUG"] = "1" if detailed else "0"
    os.environ["ZIYA_CONTEXT_PAYLOAD_DEBUG"] = "1" if payload_enabled else "0"
    import app.utils.context_debug as cd
    importlib.reload(cd)
    cd.reset_for_tests()
    return cd


class _IsolatedHome(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="ziya_ctxdbg_"))
        self._prev_home = os.environ.get("ZIYA_HOME")
        os.environ["ZIYA_HOME"] = str(self.home)
        import app.utils.paths as paths
        importlib.reload(paths)
        self.cd = _fresh_module()
        # Guard: a leaked real home would make these tests destructive.
        self.assertTrue(
            str(paths.get_ziya_home()).startswith(str(self.home)),
            "ZIYA_HOME isolation failed; refusing to touch the real store",
        )

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        if self._prev_home is None:
            os.environ.pop("ZIYA_HOME", None)
        else:
            os.environ["ZIYA_HOME"] = self._prev_home
        os.environ.pop("ZIYA_CONTEXT_DEBUG", None)
        os.environ.pop("ZIYA_CONTEXT_PAYLOAD_DEBUG", None)


# --------------------------------------------------------------------------- #
# 1. Request identity
# --------------------------------------------------------------------------- #

class RequestIdentityTests(_IsolatedHome):
    def test_concurrent_streams_do_not_interleave_into_one_group(self):
        """The reported symptom: 0,1,2,3,0,1,2,0,1,... in one flat column."""
        cd = self.cd
        ra, rb = cd.new_request_id(), cd.new_request_id()
        for i in range(5):
            cd.record_iteration("c1", i, request_id=ra, total_input_tokens=100 + i)
            cd.record_iteration("c1", i, request_id=rb, total_input_tokens=900 + i)

        groups = {g["request_id"]: g for g in cd.get_requests("c1")}
        self.assertEqual(set(groups), {ra, rb})
        # Each round's own iteration column is clean and monotonic.
        for rid in (ra, rb):
            cols = [e["iteration"] for e in groups[rid]["iterations"]]
            self.assertEqual(cols, [0, 1, 2, 3, 4])
        # And the two rounds did not contaminate each other's token values.
        self.assertTrue(all(e["total_input_tokens"] < 900
                            for e in groups[ra]["iterations"]))
        self.assertTrue(all(e["total_input_tokens"] >= 900
                            for e in groups[rb]["iterations"]))

    def test_seq_is_monotonic_across_interleaved_requests(self):
        """seq must reconstruct true wall-clock order despite interleaving."""
        cd = self.cd
        ra, rb = cd.new_request_id(), cd.new_request_id()
        for i in range(4):
            cd.record_iteration("c1", i, request_id=ra)
            cd.record_iteration("c1", i, request_id=rb)
        seqs = [e["seq"] for e in cd.get_history("c1")]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(seqs, list(range(8)))

    def test_every_entry_carries_request_id(self):
        cd = self.cd
        rid = cd.new_request_id()
        cd.record_iteration("c1", 0, request_id=rid)
        entries = cd.get_history("c1")
        self.assertTrue(entries)
        self.assertTrue(all(e.get("request_id") == rid for e in entries))

    def test_callers_without_request_id_still_group(self):
        """Legacy call sites must not regress to interleaving with live ones."""
        cd = self.cd
        cd.record_iteration("c1", 0)
        cd.record_iteration("c1", 1)
        groups = cd.get_requests("c1")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["request_id"], "legacy")

    def test_buffers_are_per_request_not_per_conversation(self):
        """A busy conversation must not cannibalise its own rounds.

        Two rounds each shorter than the cap must BOTH survive in full;
        under the old per-conversation deque the first was evicted.
        """
        cd = self.cd
        cap = cd._MAX_ITERATIONS_PER_REQUEST
        ra, rb = cd.new_request_id(), cd.new_request_id()
        for i in range(cap - 10):
            cd.record_iteration("c1", i, request_id=ra)
        for i in range(cap - 10):
            cd.record_iteration("c1", i, request_id=rb)
        groups = {g["request_id"]: g for g in cd.get_requests("c1")}
        self.assertEqual(len(groups[ra]["iterations"]), cap - 10)
        self.assertEqual(len(groups[rb]["iterations"]), cap - 10)
        self.assertEqual(groups[ra]["evicted"], 0)
        self.assertEqual(groups[ra]["iterations"][0]["iteration"], 0)


# --------------------------------------------------------------------------- #
# 2. Eviction is visible
# --------------------------------------------------------------------------- #

class EvictionVisibilityTests(_IsolatedHome):
    def test_overflowing_a_request_buffer_records_an_evicted_count(self):
        """Silent front-loss is what made a turn appear to start at iter 59."""
        cd = self.cd
        cap = cd._MAX_ITERATIONS_PER_REQUEST
        rid = cd.new_request_id()
        for i in range(cap + 25):
            cd.record_iteration("c1", i, request_id=rid)
        group = cd.get_requests("c1")[0]
        self.assertEqual(group["evicted"], 25)
        self.assertEqual(len(group["iterations"]), cap)
        # The front really is gone — the marker is the only way to know.
        self.assertEqual(group["iterations"][0]["iteration"], 25)

    def test_no_eviction_reported_when_under_cap(self):
        """Pair for the assertion above: the counter must not false-positive."""
        cd = self.cd
        rid = cd.new_request_id()
        for i in range(5):
            cd.record_iteration("c1", i, request_id=rid)
        self.assertEqual(cd.get_requests("c1")[0]["evicted"], 0)


# --------------------------------------------------------------------------- #
# 3. Overflow classification
# --------------------------------------------------------------------------- #

class OverflowClassifierTests(_IsolatedHome):
    def test_recognises_every_provider_phrasing_in_the_codebase(self):
        cd = self.cd
        # Each string is a phrasing that appears in this repo's provider
        # adapters or was observed from a provider.
        for msg in (
            "ValidationException: prompt is too long: 250000 tokens > 200000",
            "ValidationException: Input is too long for requested model.",
            "input length and `max_tokens` exceed context limit: 179563 + 64000 > 204698",
            "Message exceeds the maximum number of tokens allowed",
            "This model's maximum context length is 128000 tokens",
            "error code: context_length_exceeded",
            "Please reduce the length of the messages",
            "The input token count exceeds the maximum",
        ):
            self.assertTrue(cd.is_context_overflow_error(msg), msg)

    def test_does_not_misclassify_throttling_or_transport_errors(self):
        """Throttling says 'Too many tokens' — misreading it as overflow
        would trigger extended-context retries against a rate limit."""
        cd = self.cd
        for msg in (
            "ThrottlingException: Too many tokens, please wait before trying again",
            "ThrottlingException: Too many requests",
            "Read timeout on endpoint URL",
            "Connection reset by peer",
            "AccessDeniedException: not authorized",
        ):
            self.assertFalse(cd.is_context_overflow_error(msg), msg)

    def test_accepts_an_exception_object(self):
        cd = self.cd
        self.assertTrue(cd.is_context_overflow_error(
            ValueError("prompt is too long: 900000 > 200000")))

    def test_classifier_is_the_one_used_by_the_bedrock_client(self):
        """Seam check: the wrapper must import the shared classifier, not
        keep a fourth private copy of the substring list."""
        import app.utils.custom_bedrock as cb
        importlib.reload(cb)
        self.assertIs(cb.is_context_overflow_error, self.cd.is_context_overflow_error)

    def test_bedrock_client_exposes_request_scoped_globals(self):
        """Seam check: the executor sets these so a swallowed overflow can
        be attributed to its round; absence means attribution is lost."""
        import app.utils.custom_bedrock as cb
        importlib.reload(cb)
        self.assertTrue(hasattr(cb, "_current_request_id"))
        self.assertTrue(hasattr(cb, "_current_iteration"))


# --------------------------------------------------------------------------- #
# 4. Failure recording
# --------------------------------------------------------------------------- #

class FailureRecordingTests(_IsolatedHome):
    def test_record_failure_marks_estimate_and_failure_and_origin(self):
        cd = self.cd
        rid = cd.new_request_id()
        cd.record_failure(
            "c1", 12,
            error_message="prompt is too long: 900000 tokens > 200000",
            request_id=rid,
            system_content=[{"type": "text", "text": "s" * 400}],
            conversation=[{"role": "user", "content": "u" * 800}],
            effective_limit=200000,
            origin="custom_bedrock",
        )
        fails = [e for e in cd.get_history("c1") if e["is_failure"]]
        self.assertEqual(len(fails), 1)
        f = fails[0]
        self.assertTrue(f["is_estimated"], "must never read as a provider figure")
        self.assertEqual(f["origin"], "custom_bedrock")
        self.assertEqual(f["iteration"], 12)
        self.assertGreater(f["estimated_tokens"], 0)
        self.assertIn("custom_bedrock", f["note"])

    def test_failure_persists_to_disk_even_with_detailed_capture_off(self):
        """A failed round is the event most worth surviving a restart."""
        cd = _fresh_module(detailed=False)
        self.assertFalse(cd.is_enabled())
        rid = cd.new_request_id()
        cd.record_failure("c9", 3, error_message="prompt is too long",
                          request_id=rid, origin="executor")
        snapshot = self.home / "debug" / "context_snapshots" / "c9.json"
        self.assertTrue(snapshot.is_file(), "failure was not persisted")
        data = json.loads(snapshot.read_text())
        self.assertTrue(any(e["is_failure"] for e in data["iterations"]))

    def test_ordinary_iterations_are_not_marked_as_failures(self):
        """Positive pair: the flags must actually discriminate."""
        cd = self.cd
        rid = cd.new_request_id()
        cd.record_iteration("c1", 0, request_id=rid, total_input_tokens=1234)
        e = cd.get_history("c1")[0]
        self.assertFalse(e["is_failure"])
        self.assertFalse(e["is_estimated"])


# --------------------------------------------------------------------------- #
# 5. Payload capture + rolling retention
# --------------------------------------------------------------------------- #

SYS = [{"type": "text", "text": "SYSTEM " + "x" * 300}]


def _conv(n: int):
    return [{"role": "user", "content": f"m{i} " + "y" * 100} for i in range(n)]


class PayloadCaptureTests(_IsolatedHome):
    def setUp(self):
        super().setUp()
        self.cd = _fresh_module(payload_enabled=True)

    def test_frame_round_trips_the_submitted_payload_verbatim(self):
        """'100% of it exactly as sent' — structures must compare equal."""
        cd = self.cd
        rid = cd.new_request_id()
        cd.capture_payload("c1", 0, request_id=rid, system_content=SYS,
                           conversation=_conv(4), model_id="claude-sonnet-4",
                           tools=[{"name": "file_read"}, {"name": "pdf_search"}])
        frame = cd.get_payload_frame("c1", rid, 0)
        self.assertIsNotNone(frame)
        self.assertEqual(frame["system_content"], SYS)
        self.assertEqual(frame["conversation"], _conv(4))
        self.assertEqual(frame["model_id"], "claude-sonnet-4")
        self.assertEqual(frame["tools"], ["file_read", "pdf_search"])

    def test_nothing_is_captured_when_payload_capture_is_off(self):
        """Pair for the test above: proves the gate is real, not vestigial."""
        cd = _fresh_module(payload_enabled=False)
        rid = cd.new_request_id()
        cd.capture_payload("c1", 0, request_id=rid, system_content=SYS,
                           conversation=_conv(2))
        self.assertIsNone(cd.get_payload_frame("c1", rid, 0))
        self.assertEqual(cd.list_payload_frames("c1"), [])

    def test_retention_window_keeps_only_the_most_recent_rounds(self):
        cd = self.cd
        keep = cd.PAYLOAD_REQUESTS_RETAINED
        rids = []
        for _ in range(keep + 4):
            rid = cd.new_request_id()
            rids.append(rid)
            for it in range(2):
                cd.capture_payload("c1", it, request_id=rid,
                                   system_content=SYS, conversation=_conv(3))
        retained = {g["request_id"] for g in cd.list_payload_frames("c1")}
        self.assertEqual(len(retained), keep)
        self.assertIn(rids[-1], retained, "newest round must be kept")
        for old in rids[:4]:
            self.assertNotIn(old, retained, "old round should have aged out")
            self.assertIsNone(cd.get_payload_frame("c1", old, 0))

    def test_failed_round_survives_outside_the_retention_window(self):
        """The one round you most need the bytes for must not age out."""
        cd = self.cd
        rid_fail = cd.new_request_id()
        cd.record_failure("c1", 0, error_message="prompt is too long",
                          request_id=rid_fail, system_content=SYS,
                          conversation=_conv(3), origin="executor")
        for _ in range(cd.PAYLOAD_REQUESTS_RETAINED + 3):
            cd.capture_payload("c1", 0, request_id=cd.new_request_id(),
                               system_content=SYS, conversation=_conv(2))
        retained = {g["request_id"] for g in cd.list_payload_frames("c1")}
        self.assertIn(rid_fail, retained)
        self.assertIsNotNone(cd.get_payload_frame("c1", rid_fail, 0))

    def test_failure_frame_is_captured_even_with_capture_disabled(self):
        cd = _fresh_module(payload_enabled=False)
        rid = cd.new_request_id()
        cd.record_failure("c1", 5, error_message="prompt is too long",
                          request_id=rid, system_content=SYS,
                          conversation=_conv(2), origin="custom_bedrock")
        frame = cd.get_payload_frame("c1", rid, 5)
        self.assertIsNotNone(frame, "a failed round must always keep its bytes")
        self.assertEqual(frame["conversation"], _conv(2))

    def test_index_reports_sizes_without_loading_contents(self):
        cd = self.cd
        rid = cd.new_request_id()
        for it in range(3):
            cd.capture_payload("c1", it, request_id=rid,
                               system_content=SYS, conversation=_conv(5))
        idx = cd.list_payload_frames("c1")
        self.assertEqual(len(idx), 1)
        group = idx[0]
        self.assertEqual([f["iteration"] for f in group["frames"]], [0, 1, 2])
        self.assertGreater(group["total_bytes"], 0)
        self.assertEqual(group["total_bytes"],
                         sum(f["bytes"] for f in group["frames"]))

    def test_unserialisable_objects_do_not_lose_the_frame(self):
        """default=repr: an exotic object must degrade, not drop the frame."""
        cd = self.cd

        class Weird:
            def __repr__(self):
                return "<Weird sentinel>"

        rid = cd.new_request_id()
        cd.capture_payload("c1", 0, request_id=rid, system_content=SYS,
                           conversation=[{"role": "user", "content": Weird()}])
        frame = cd.get_payload_frame("c1", rid, 0)
        self.assertIsNotNone(frame)
        self.assertIn("Weird sentinel", json.dumps(frame["conversation"]))

    def test_frames_are_written_with_restrictive_permissions(self):
        """Frames hold the entire submitted context, including source."""
        cd = self.cd
        rid = cd.new_request_id()
        cd.capture_payload("c1", 0, request_id=rid, system_content=SYS,
                           conversation=_conv(2))
        path = (self.home / "debug" / "context_payloads"
                / "c1" / rid / "iter_0000.json")
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_clear_history_removes_payload_frames_too(self):
        cd = self.cd
        rid = cd.new_request_id()
        cd.record_iteration("c1", 0, request_id=rid)
        cd.capture_payload("c1", 0, request_id=rid, system_content=SYS,
                           conversation=_conv(2))
        self.assertTrue(cd.list_payload_frames("c1"))
        cd.clear_history("c1")
        self.assertEqual(cd.list_payload_frames("c1"), [])
        self.assertEqual(cd.get_history("c1"), [])


# --------------------------------------------------------------------------- #
# 6. Backwards compatibility of the read surface
# --------------------------------------------------------------------------- #

class ReadSurfaceCompatTests(_IsolatedHome):
    def test_get_history_still_returns_a_flat_list(self):
        """The existing panel consumes `iterations` as a flat array."""
        cd = self.cd
        rid = cd.new_request_id()
        cd.record_iteration("c1", 0, request_id=rid)
        hist = cd.get_history("c1")
        self.assertIsInstance(hist, list)
        self.assertIsInstance(hist[0], dict)
        for key in ("iteration", "total_input_tokens", "is_failure", "is_estimated"):
            self.assertIn(key, hist[0])

    def test_pre_identity_disk_snapshots_are_still_readable(self):
        """A snapshot written before request identity existed must not read
        as 'no history'."""
        cd = self.cd
        snap_dir = self.home / "debug" / "context_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "old.json").write_text(json.dumps({
            "conversation_id": "old",
            "iterations": [
                {"iteration": 0, "timestamp": 1.0, "total_input_tokens": 10,
                 "is_failure": False, "is_estimated": False},
                {"iteration": 1, "timestamp": 2.0, "total_input_tokens": 20,
                 "is_failure": False, "is_estimated": False},
            ],
        }))
        self.assertEqual(len(cd.get_history("old")), 2)
        groups = cd.get_requests("old")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["request_id"], "legacy")
        self.assertEqual(len(groups[0]["iterations"]), 2)


if __name__ == "__main__":
    unittest.main()
