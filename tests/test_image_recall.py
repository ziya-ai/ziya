"""
Tests for image recall (app.utils.image_recall) and the per-call retention
floor (app.utils.image_pin_context).

Recall exists because compaction reclaims CONTEXT space, not storage — two
different resources.  Dropping an image from the conversation need not mean
forgetting it, so the bytes stay in RAM behind a handle.  These tests pin
the properties that make that safe: PER-SCOPE bounded memory (so one busy
conversation cannot evict another's renders), no cross-conversation reads,
and honest failure when a handle is stale.
"""

import time

import pytest

from app.utils import image_recall
from app.utils.image_pin_context import (
    RETAIN_LEVELS,
    request_image_retain,
    take_image_retain_floor,
)
from app.utils.image_result_compaction import (
    compact_prior_image_results,
    has_image_blocks,
)


def _image_block(data="aGVsbG8="):
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def _text_block(text):
    return {"type": "text", "text": text}


@pytest.fixture(autouse=True)
def _clean_store():
    image_recall.clear()
    take_image_retain_floor()  # drain any floor left by another test
    yield
    image_recall.clear()


class TestStashRetrieve:
    def test_round_trip(self):
        content = [_image_block("payload"), _text_block("desc")]
        handle = image_recall.stash(content, scope="conv-1")
        assert handle and handle.startswith("img-")
        assert image_recall.retrieve(handle, scope="conv-1") == content

    def test_unknown_handle_is_none(self):
        assert image_recall.retrieve("img-nope", scope="conv-1") is None

    def test_empty_handle_is_none(self):
        assert image_recall.retrieve("", scope="conv-1") is None

    def test_handles_are_unique(self):
        c = [_image_block("a")]
        assert image_recall.stash(c) != image_recall.stash(c)

    def test_nothing_to_stash_yields_no_handle(self):
        # No handle means the placeholder won't advertise a dead lookup.
        assert image_recall.stash([_text_block("text only")]) is None
        assert image_recall.stash([]) is None
        assert image_recall.stash("not a list") is None

    def test_entry_too_big_for_its_scope_refused(self, monkeypatch):
        monkeypatch.setattr(image_recall, "MAX_BYTES_PER_SCOPE", 4)
        assert image_recall.stash([_image_block("x" * 100)]) is None

    def test_label_is_retrievable(self):
        h = image_recall.stash([_image_block("a")], label="a mermaid graph")
        assert image_recall.describe(h) == "a mermaid graph"


class TestScopeIsolation:
    """A handle leaked into another conversation must not pull pixels
    across the boundary."""

    def test_other_scope_refused(self):
        h = image_recall.stash([_image_block("secret")], scope="conv-A")
        assert image_recall.retrieve(h, scope="conv-B") is None

    def test_own_scope_allowed(self):
        h = image_recall.stash([_image_block("ok")], scope="conv-A")
        assert image_recall.retrieve(h, scope="conv-A") is not None

    def test_unscoped_entry_readable_by_anyone(self):
        # Nothing to protect when the producer declared no scope.
        h = image_recall.stash([_image_block("ok")], scope=None)
        assert image_recall.retrieve(h, scope="conv-Z") is not None

    def test_scoped_entry_readable_when_caller_has_no_scope(self):
        # A caller with no conversation id (CLI one-shot) is not an
        # attacker; the guard fires only on a genuine MISMATCH.
        h = image_recall.stash([_image_block("ok")], scope="conv-A")
        assert image_recall.retrieve(h, scope=None) is not None


class TestPerScopeBounds:
    """Per-scope budgets exist for FAIRNESS, not just total memory: a long
    fuzz run must not evict the one diagram another conversation is still
    consulting."""

    def test_busy_scope_cannot_evict_a_quiet_one(self, monkeypatch):
        monkeypatch.setattr(image_recall, "MAX_ENTRIES_PER_SCOPE", 2)
        quiet = image_recall.stash([_image_block("q")], scope="quiet")
        for i in range(10):
            image_recall.stash([_image_block(f"b{i}")], scope="busy")
        assert image_recall.retrieve(quiet, scope="quiet") is not None, (
            "a quiet conversation's render must survive another "
            "conversation's render storm"
        )
        assert image_recall.stats(scope="busy")["entries"] <= 2

    def test_entry_count_capped_per_scope(self, monkeypatch):
        monkeypatch.setattr(image_recall, "MAX_ENTRIES_PER_SCOPE", 3)
        handles = [image_recall.stash([_image_block(f"i{i}")], scope="s")
                   for i in range(6)]
        assert image_recall.stats(scope="s")["entries"] <= 3
        assert image_recall.retrieve(handles[0], scope="s") is None
        assert image_recall.retrieve(handles[-1], scope="s") is not None

    def test_byte_ceiling_per_scope_evicts_oldest(self, monkeypatch):
        monkeypatch.setattr(image_recall, "MAX_BYTES_PER_SCOPE", 20)
        first = image_recall.stash([_image_block("x" * 10)], scope="s")
        image_recall.stash([_image_block("y" * 10)], scope="s")
        third = image_recall.stash([_image_block("z" * 10)], scope="s")
        assert image_recall.stats(scope="s")["bytes"] <= 20
        assert image_recall.retrieve(first, scope="s") is None
        assert image_recall.retrieve(third, scope="s") is not None

    def test_each_scope_gets_its_own_budget(self, monkeypatch):
        # Two scopes at their individual limits both keep their entries —
        # the budget is per-scope, not split between them.
        monkeypatch.setattr(image_recall, "MAX_ENTRIES_PER_SCOPE", 2)
        a = [image_recall.stash([_image_block(f"a{i}")], scope="A")
             for i in range(2)]
        b = [image_recall.stash([_image_block(f"b{i}")], scope="B")
             for i in range(2)]
        assert all(image_recall.retrieve(h, scope="A") for h in a)
        assert all(image_recall.retrieve(h, scope="B") for h in b)

    def test_unscoped_entries_share_one_bucket(self, monkeypatch):
        # Otherwise every scopeless stash would be its own unbounded scope.
        monkeypatch.setattr(image_recall, "MAX_ENTRIES_PER_SCOPE", 2)
        for i in range(5):
            image_recall.stash([_image_block(f"u{i}")])
        assert image_recall.stats()["entries"] <= 2


class TestGlobalBackstop:
    """Per-scope budgets are unbounded in aggregate, so a global ceiling
    still applies — and it charges the scope that created the pressure."""

    def test_global_ceiling_enforced(self, monkeypatch):
        monkeypatch.setattr(image_recall, "MAX_BYTES_PER_SCOPE", 100)
        monkeypatch.setattr(image_recall, "MAX_TOTAL_BYTES", 30)
        for scope in ("A", "B", "C", "D"):
            image_recall.stash([_image_block("x" * 10)], scope=scope)
        assert image_recall.stats()["bytes"] <= 30

    def test_heaviest_scope_pays(self, monkeypatch):
        monkeypatch.setattr(image_recall, "MAX_BYTES_PER_SCOPE", 100)
        monkeypatch.setattr(image_recall, "MAX_TOTAL_BYTES", 45)
        light = image_recall.stash([_image_block("l" * 5)], scope="light")
        for i in range(4):
            image_recall.stash([_image_block("h" * 10)], scope="heavy")
        assert image_recall.retrieve(light, scope="light") is not None, (
            "global pressure must be charged to the heaviest scope, not "
            "paid for by a quiet conversation's single render"
        )


class TestRecency:
    def test_retrieve_refreshes_recency(self, monkeypatch):
        # An image the model keeps consulting should outlive one it never
        # looks at, or recall degrades exactly when it is being used.
        monkeypatch.setattr(image_recall, "MAX_ENTRIES_PER_SCOPE", 2)
        a = image_recall.stash([_image_block("a")], scope="s")
        b = image_recall.stash([_image_block("b")], scope="s")
        assert image_recall.retrieve(a, scope="s") is not None   # touch a
        image_recall.stash([_image_block("c")], scope="s")       # evicts one
        assert image_recall.retrieve(a, scope="s") is not None, "touched survives"
        assert image_recall.retrieve(b, scope="s") is None, "untouched evicted"

    def test_expiry(self, monkeypatch):
        monkeypatch.setattr(image_recall, "MAX_AGE_SECONDS", 0)
        h = image_recall.stash([_image_block("a")], scope="s")
        time.sleep(0.01)
        assert image_recall.retrieve(h, scope="s") is None


class TestSweepIntegration:
    def _conv(self, n):
        return [
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": f"t{i}",
                "content": [_image_block(f"img{i}"),
                            _text_block(f"Rendered diagram {i}")],
            }]}
            for i in range(n)
        ]

    def test_compacted_image_is_recallable(self):
        conv = self._conv(2)
        assert compact_prior_image_results(
            conv, keep_recent=1, recall_scope="conv-1") == 1
        text = conv[0]["content"][0]["content"]
        assert "recall_image" in text, "handle must be offered to the model"
        handle = text.split('handle="')[1].split('"')[0]
        restored = image_recall.retrieve(handle, scope="conv-1")
        assert has_image_blocks(restored), "original pixels come back"
        assert restored[0]["source"]["data"] == "img0"

    def test_recall_is_scoped_to_the_producing_conversation(self):
        conv = self._conv(2)
        compact_prior_image_results(
            conv, keep_recent=1, recall_scope="conv-1")
        text = conv[0]["content"][0]["content"]
        handle = text.split('handle="')[1].split('"')[0]
        assert image_recall.retrieve(handle, scope="conv-OTHER") is None

    def test_retained_image_is_not_stashed(self):
        # Only what LEAVES context needs a recall entry.
        conv = self._conv(1)
        compact_prior_image_results(conv, keep_recent=1, recall_scope="c")
        assert image_recall.stats()["entries"] == 0

    def test_no_scope_means_destructive_compaction(self):
        conv = self._conv(1)
        assert compact_prior_image_results(conv, keep_recent=0) == 1
        assert image_recall.stats()["entries"] == 0
        assert "recall_image" not in conv[0]["content"][0]["content"]

    def test_failed_stash_advertises_no_handle(self, monkeypatch):
        # An unredeemable handle is worse than none — a failed recall reads
        # as the earlier observation having been unreliable.
        monkeypatch.setattr(image_recall, "MAX_BYTES_PER_SCOPE", 1)
        conv = self._conv(1)
        assert compact_prior_image_results(
            conv, keep_recent=0, recall_scope="c") == 1
        text = conv[0]["content"][0]["content"]
        assert "recall_image" not in text
        assert "DID see" in text, "epistemic wording still applies"


class TestRetainFloor:
    """The floor is the piece most likely to break silently: it must clear
    on read, or one retain='pin' pins every later render in the run."""

    def test_default_floor_is_zero(self):
        assert take_image_retain_floor() == 0

    def test_request_raises_floor(self):
        request_image_retain("turn")
        assert take_image_retain_floor() == RETAIN_LEVELS["turn"]

    def test_cleared_on_read(self):
        request_image_retain("pin")
        assert take_image_retain_floor() == RETAIN_LEVELS["pin"]
        assert take_image_retain_floor() == 0, (
            "a single retain request must not pin every later render"
        )

    def test_takes_max_of_repeated_requests(self):
        request_image_retain("turn")
        request_image_retain("pin")
        assert take_image_retain_floor() == RETAIN_LEVELS["pin"]

    def test_lower_request_cannot_narrow(self):
        request_image_retain("pin")
        request_image_retain("turn")
        assert take_image_retain_floor() == RETAIN_LEVELS["pin"]

    def test_auto_is_a_noop(self):
        request_image_retain("auto")
        assert take_image_retain_floor() == 0

    def test_unknown_level_is_a_noop(self):
        request_image_retain("forever")
        assert take_image_retain_floor() == 0

    def test_none_and_empty_tolerated(self):
        request_image_retain(None)
        request_image_retain("")
        assert take_image_retain_floor() == 0

    def test_case_and_whitespace_insensitive(self):
        request_image_retain("  PIN  ")
        assert take_image_retain_floor() == RETAIN_LEVELS["pin"]

    def test_pin_is_bounded_not_infinite(self):
        # An unbounded window is how a run ends up re-sending 40 MB of
        # base64 on iteration 30.
        assert 0 < RETAIN_LEVELS["pin"] < 100
        assert RETAIN_LEVELS["turn"] < RETAIN_LEVELS["pin"]
