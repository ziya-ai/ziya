"""
Direct-unit coverage for the two hallucination-detection modules that were
previously exercised only indirectly:

  * app.hallucination.fake_tool_result_detector.detect_fake_tool_result
      -- was referenced only from test_text_delta_processor.py (integration).
  * app.hallucination.shingle_index internals (_compute_shingles,
      _compute_line_hashes, _hash_token, _match_score) and the confidence
      tiers of ShingleIndex.check -- helpers had ZERO direct tests.

This file is the canonical home for direct assertions against those
primitives. Higher-level / integration behavior continues to live in
test_hallucination_detection.py and test_text_delta_processor.py; the point
here is to pin the primitives on their own terms so a refactor of either
module fails locally instead of only through a distant integration test.

Every assertion in this file was grounded against the real functions before
being written (see the fence-awareness step-0 findings doc).
"""
from __future__ import annotations

import pytest

from app.hallucination.fake_tool_result_detector import (
    FakeToolResultMatch,
    detect_fake_tool_result,
)
from app.hallucination import shingle_index as si


# ===========================================================================
# detect_fake_tool_result
# ===========================================================================
class TestDetectFakeToolResult_ShouldDetect:
    def test_python_dict_success_leading_two_keys_is_high(self):
        m = detect_fake_tool_result("python", "{'success': True, 'path': '/tmp/x'}")
        assert m is not None
        assert m.confidence == "high"
        assert m.matched_keys == ("success", "path")

    def test_json_multiline_split_dict_detected(self):
        body = "{'success': True,\n'stdout': 'x',\n'returncode': 0}"
        m = detect_fake_tool_result("json", body)
        assert m is not None
        assert m.matched_keys == ("success", "stdout", "returncode")
        assert m.confidence == "high"

    def test_four_or_more_keys_is_high_even_untagged(self):
        body = "{'success': True, 'stdout': 'x', 'stderr': '', 'returncode': 0}"
        m = detect_fake_tool_result("", body)
        assert m is not None
        assert m.confidence == "high"
        assert len(m.matched_keys) >= 4

    def test_two_non_success_keys_tagged_is_medium(self):
        m = detect_fake_tool_result("python", "{'stdout': 'x', 'stderr': 'y'}")
        assert m is not None
        assert m.confidence == "medium"
        assert m.matched_keys == ("stdout", "stderr")

    def test_returns_match_dataclass_with_snippet_and_reason(self):
        m = detect_fake_tool_result("python", "{'success': True, 'path': '/x'}")
        assert isinstance(m, FakeToolResultMatch)
        assert m.fence_lang == "python"
        assert m.snippet  # non-empty logging snippet
        assert "canonical" in m.reason


class TestDetectFakeToolResult_ShouldNotDetect:
    @pytest.mark.parametrize("lang", ["diff", "patch", "tool", "DIFF", "Patch"])
    def test_skip_fence_langs_never_fire(self, lang):
        body = "{'success': True, 'path': '/tmp/x'}"
        assert detect_fake_tool_result(lang, body) is None

    def test_single_canonical_key_insufficient(self):
        assert detect_fake_tool_result("json", "{'success': True}") is None

    @pytest.mark.parametrize("body", ["", "   \n  \t "])
    def test_empty_or_whitespace_body(self, body):
        assert detect_fake_tool_result("python", body) is None

    def test_prose_first_line_not_a_dict(self):
        assert detect_fake_tool_result("python", "hello world\nmore text here") is None

    def test_untagged_two_keys_downgrades_below_high(self):
        # success+path (2 keys) would be 'high' when tagged python/json, but an
        # untagged fence is more ambiguous, so it must NOT reach 'high'.
        m = detect_fake_tool_result("", "{'success': True, 'path': '/x'}")
        assert m is not None
        assert m.confidence != "high"

    def test_real_tool_results_seen_suppresses_matching_shape(self):
        body = "{'stdout': 'x', 'returncode': 0}"
        # Without suppression the shape is detected...
        assert detect_fake_tool_result("python", body) is not None
        # ...but if run_shell_command genuinely ran this turn, it is narration.
        assert detect_fake_tool_result(
            "python", body, real_tool_results_seen=["run_shell_command"]
        ) is None

    def test_suppression_is_tool_specific_not_blanket(self):
        # file_write suppression keys (path/bytes_written) must NOT suppress a
        # run_shell_command-shaped dict.
        body = "{'stdout': 'x', 'returncode': 0}"
        assert detect_fake_tool_result(
            "python", body, real_tool_results_seen=["file_write"]
        ) is not None


# ===========================================================================
# shingle_index helpers (previously ZERO direct coverage)
# ===========================================================================
class TestShingleHelpers:
    def test_hash_token_is_deterministic(self):
        assert si._hash_token("abc") == si._hash_token("abc")
        assert si._hash_token("abc") != si._hash_token("abd")

    def test_shingles_empty_when_fewer_tokens_than_size(self):
        assert si._compute_shingles("one two three", 5, 200) == frozenset()

    def test_shingles_count_is_ngram_window(self):
        # 8 tokens, size-5 window -> 8-5+1 = 4 shingles.
        sh = si._compute_shingles("the quick brown fox jumps over lazy dog", 5, 200)
        assert len(sh) == 4

    def test_shingles_bounded_by_max_count(self):
        text = " ".join(f"tok{i}" for i in range(100))
        sh = si._compute_shingles(text, 5, max_count=10)
        assert len(sh) <= 10

    def test_shingles_case_insensitive(self):
        a = si._compute_shingles("The Quick Brown Fox Jumps", 5, 200)
        b = si._compute_shingles("the quick brown fox jumps", 5, 200)
        assert a == b

    def test_line_hashes_skip_short_lines(self):
        assert si._compute_line_hashes("short", 20) == frozenset()

    def test_line_hashes_hash_significant_lines(self):
        lh = si._compute_line_hashes("this is a sufficiently long line here", 20)
        assert len(lh) == 1

    def test_line_hashes_whitespace_normalized(self):
        a = si._compute_line_hashes("this   is    a  long  line  here now", 20)
        b = si._compute_line_hashes("this is a long line here now", 20)
        assert a == b


class TestMatchScoreOrdering:
    def _match(self, confidence, line_matches, overlap):
        return si.ShingleMatch(
            matched_tool_use_id="t",
            matched_tool_name="grep",
            shingle_overlap=overlap,
            line_matches=line_matches,
            confidence=confidence,
            registered_at=0.0,
        )

    def test_high_tier_beats_low_tier(self):
        hi = self._match("high", 1, 1)
        lo = self._match("low", 99, 99)
        assert si._match_score(hi) > si._match_score(lo)

    def test_within_tier_line_matches_dominate_overlap(self):
        more_lines = self._match("low", 5, 1)
        more_overlap = self._match("low", 4, 50)
        assert si._match_score(more_lines) > si._match_score(more_overlap)


# ===========================================================================
# ShingleIndex.check confidence tiers (direct, not via detector integration)
# ===========================================================================
class TestShingleIndexCheckTiers:
    def _rich(self):
        return "\n".join(
            f"line number {i} with plenty of distinct words here to hash"
            for i in range(10)
        )

    def test_verbatim_reproduction_is_high_confidence(self):
        idx = si.ShingleIndex()
        rich = self._rich()
        assert idx.register("c", "t1", "grep", rich) is True
        m = idx.check("c", rich)
        assert m is not None
        assert m.confidence == "high"
        assert m.line_matches >= si.LINE_MATCH_HIGH_CONFIDENCE

    def test_unrelated_text_returns_none(self):
        idx = si.ShingleIndex()
        idx.register("c", "t1", "grep", self._rich())
        assert idx.check("c", "totally different content nothing alike zzz qqq") is None

    def test_shingle_overlap_without_line_matches_stays_low(self):
        # Shared vocabulary but no verbatim lines -> low confidence, not high.
        idx = si.ShingleIndex()
        idx.register("c", "t1", "grep", self._rich())
        # Reuse the same words but broken across different line boundaries so
        # no whole line hashes identically.
        probe = "line number with plenty of distinct words here to hash reshuffled"
        m = idx.check("c", probe)
        if m is not None:
            assert m.confidence == "low"

    def test_session_size_tracks_registrations(self):
        idx = si.ShingleIndex()
        assert idx.session_size("c") == 0
        idx.register("c", "t1", "grep", self._rich())
        assert idx.session_size("c") == 1
