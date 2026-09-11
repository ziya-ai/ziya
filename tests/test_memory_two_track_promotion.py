"""
Tests for the two-track promotion redesign (§2 of Docs/design/memory-redesign.md).

Two layers of coverage:

  * Graded-quality unit tests over ``extractor.quality_gate`` — the model's
    self-grades are clamped by structural evidence and attached as ``quality``
    / ``quality_components`` on each candidate.

  * Two-track lifecycle tests.  The promotion/archival tests are SEAM tests:
    they run ``run_lifecycle_pass`` against tmp_path-backed sandbox stores and
    assert on the resulting sandbox ``MemoryStorage`` (a memory actually
    appears / does not), not merely that ``_evaluate_promotion`` returns a
    reason.  This is what the fail-on-backup / pass-on-change contract checks.

These tests FAIL on the stage-3 backup copies (backup lifecycle.py lacks
FAST_TRACK_* and quality-based promotion; backup extractor.py's quality_gate
does not attach a quality score) and PASS on the change.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.memory.extractor import quality_gate
from app.memory.lifecycle import (
    _evaluate_promotion,
    _evaluate_archival,
    _is_fast_track_eligible,
    run_lifecycle_pass,
    ARCHIVAL_AGE_THRESHOLD,
    FAST_TRACK_LAYERS,
    FAST_TRACK_QUALITY_THRESHOLD,
    FAST_TRACK_MIN_AGE,
)
from app.models.memory import MemoryProposal


# ── Graded-quality unit tests (extractor.quality_gate) ──────────────────

class TestQualityGrading:

    def test_high_grades_produce_high_quality(self):
        c = {
            "content": "Component A forwards packets; Component B owns routing "
                       "policy in the FooBar control plane.",
            "layer": "architecture", "tags": ["arch"],
            "atomicity": 0.9, "self_containment": 0.9, "durability": 0.95,
        }
        (out,) = quality_gate([c])
        # 0.40*0.95 + 0.35*0.9 + 0.25*0.9 = 0.92
        assert out["quality"] == pytest.approx(0.92)
        assert out["quality_components"] == {
            "atomicity": 0.9, "self_containment": 0.9, "durability": 0.95,
        }

    def test_missing_grades_default_to_zero_never_high(self):
        """A candidate the model failed to grade must score 0.0, never a
        default-high value that could fast-track junk."""
        c = {"content": "A durable fact about the ThingamaBob constant value 42.",
             "layer": "decision", "tags": ["x"]}
        (out,) = quality_gate([c])
        assert out["quality"] == 0.0
        assert out["quality_components"] == {
            "atomicity": 0.0, "self_containment": 0.0, "durability": 0.0}

    def test_out_of_range_grade_treated_as_zero(self):
        c = {"content": "A durable architecture fact about the WidgetPlane bus.",
             "layer": "architecture", "tags": ["x"],
             "atomicity": 1.7, "self_containment": -0.2, "durability": "high"}
        (out,) = quality_gate([c])
        assert out["quality_components"] == {
            "atomicity": 0.0, "self_containment": 0.0, "durability": 0.0}
        assert out["quality"] == 0.0

    def test_dangling_ref_clamps_self_containment(self):
        # Exactly one dangling ref ("the system") — warn-only, not a reject —
        # clamps self_containment to <= 0.6 even though the model claimed 0.95.
        c = {"content": "This API returns QDEPTH counters for the ScatterGather queue.",
             "layer": "architecture", "tags": ["x"],
             "atomicity": 0.95, "self_containment": 0.95, "durability": 0.95}
        (out,) = quality_gate([c])
        # "This API" is a single dangling reference (warn-only, not a reject).
        assert out["quality_components"]["self_containment"] <= 0.6

    def test_long_content_clamps_atomicity(self):
        long_content = ("The WidgetPlane data bus carries frames between the "
                        "ingress and egress stages. " * 5)[:400]
        assert len(long_content) > 300
        c = {"content": long_content, "layer": "architecture", "tags": ["x"],
             "atomicity": 0.95, "self_containment": 0.9, "durability": 0.9}
        (out,) = quality_gate([c])
        assert out["quality_components"]["atomicity"] <= 0.7

    def test_code_artifact_clamps_durability(self):
        # One backtick identifier (1-2 range) clamps durability to <= 0.7.
        c = {"content": "The retry policy for ServiceX uses `backoffMs` tuning.",
             "layer": "architecture", "tags": ["x"],
             "atomicity": 0.9, "self_containment": 0.9, "durability": 0.95}
        (out,) = quality_gate([c])
        assert out["quality_components"]["durability"] <= 0.7


# ── Fast-track eligibility (pure) ───────────────────────────────────────

def _proposal(layer, quality, activity_count_at_proposal=0,
              corroborations=0, signals=None, conversation_id="conv-1"):
    d = {
        "id": "prop_abc", "content": "some durable fact",
        "layer": layer, "corroborations": corroborations,
        "activity_count_at_proposal": activity_count_at_proposal,
        "signals": signals or [], "conversation_id": conversation_id,
    }
    if quality is not None:
        d["quality"] = quality
    return d


class TestFastTrackEligibility:

    def test_fast_track_layers_are_the_three(self):
        assert FAST_TRACK_LAYERS == {
            "architecture", "decision", "negative_constraint"}

    def test_eligible_above_threshold(self):
        assert _is_fast_track_eligible(
            _proposal("architecture", FAST_TRACK_QUALITY_THRESHOLD)) is True

    def test_ineligible_below_threshold(self):
        assert _is_fast_track_eligible(
            _proposal("architecture", FAST_TRACK_QUALITY_THRESHOLD - 0.01)) is False

    def test_ineligible_wrong_layer(self):
        assert _is_fast_track_eligible(_proposal("preference", 0.99)) is False

    def test_ineligible_when_quality_missing(self):
        assert _is_fast_track_eligible(_proposal("architecture", None)) is False

    def test_ineligible_when_contradicted(self):
        p = _proposal("architecture", 0.99, signals=[{"name": "contradicted"}])
        assert _is_fast_track_eligible(p) is False


class TestTwoTrackPromotionPure:

    def test_fast_track_promotes_at_min_age_no_corroboration(self):
        p = _proposal("architecture", 0.8, activity_count_at_proposal=0)
        assert _evaluate_promotion(p, current_counter=FAST_TRACK_MIN_AGE) \
            == "quality_fast_track"

    def test_fast_track_not_before_min_age(self):
        p = _proposal("architecture", 0.8, activity_count_at_proposal=0)
        assert _evaluate_promotion(p, current_counter=FAST_TRACK_MIN_AGE - 1) is None

    def test_corroboration_track_unchanged_for_preference(self):
        # A preference proposal never fast-tracks regardless of quality/age.
        p = _proposal("preference", 0.99, activity_count_at_proposal=0)
        assert _evaluate_promotion(p, current_counter=99) is None

    def test_legacy_proposal_never_fast_tracks(self):
        p = _proposal("architecture", None, activity_count_at_proposal=0)
        assert _evaluate_promotion(p, current_counter=99) is None


class TestTwoTrackArchival:

    def test_preference_corr0_still_decays_at_threshold(self):
        """The corroboration-track decay is bit-identical to before for
        non-fast-track layers."""
        p = _proposal("preference", None, activity_count_at_proposal=0)
        assert _evaluate_archival(p, current_counter=ARCHIVAL_AGE_THRESHOLD,
                                  active_embedding_lookup=lambda _: 0.0) == "decayed"

    def test_fast_track_proposal_not_decayed_for_lack_of_corroboration(self):
        p = _proposal("architecture", 0.8, activity_count_at_proposal=0)
        # At the decay age, an eligible fast-track proposal is guarded.
        assert _evaluate_archival(p, current_counter=ARCHIVAL_AGE_THRESHOLD,
                                  active_embedding_lookup=lambda _: 0.0) is None

    def test_legacy_arch_still_decays(self):
        p = _proposal("architecture", None, activity_count_at_proposal=0)
        assert _evaluate_archival(p, current_counter=ARCHIVAL_AGE_THRESHOLD,
                                  active_embedding_lookup=lambda _: 0.0) == "decayed"


# ── SEAM tests: run_lifecycle_pass against sandbox stores ───────────────

def _sandbox(tmp_path):
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    store = MemoryStorage(memory_dir=tmp_path / "memory")
    proposals = ProposalsStore(memory_dir=tmp_path / "memory")
    return store, proposals


def _lifecycle_patches(store, proposals, counter):
    """Patch set the scorer/sim use: sandbox stores + a fixed activity
    counter + a no-op embedding cache.  ``current_activity_count`` (not
    _next_activity_count) is what the sweep reads for age."""
    return [
        patch("app.storage.proposals.get_proposals_store", return_value=proposals),
        patch("app.storage.memory.get_memory_storage", return_value=store),
        patch("app.memory.lifecycle.current_activity_count", return_value=counter),
        patch("app.services.embedding_service.get_embedding_cache",
              return_value=MagicMock(get=MagicMock(return_value=None),
                                     retain_only=MagicMock(return_value=0),
                                     flush=MagicMock())),
        patch("app.services.embedding_service.embed_and_cache"),
    ]


@pytest.mark.asyncio
async def test_seam_fast_track_promotes_to_active_store(tmp_path):
    """An architecture proposal, quality >= threshold, corroborations == 0,
    at FAST_TRACK_MIN_AGE, appears in the sandbox active store."""
    store, proposals = _sandbox(tmp_path)
    p = MemoryProposal(
        content="The FooBar control plane separates packet forwarding "
                "(plane A) from routing policy (plane B).",
        layer="architecture", tags=["arch"], conversation_id="conv-1",
        quality=0.85,
        quality_components={"atomicity": 0.9, "self_containment": 0.85,
                            "durability": 0.85},
    )
    with patch("app.services.embedding_service.embed_and_cache"):
        pid = proposals.add(p, activity_count=0)

    patches = _lifecycle_patches(store, proposals, counter=FAST_TRACK_MIN_AGE)
    for pt in patches:
        pt.start()
    try:
        result = await run_lifecycle_pass()
    finally:
        for pt in patches:
            pt.stop()

    assert result["promoted"] == 1
    memories = store.list_memories()
    assert len(memories) == 1
    assert memories[0].content.startswith("The FooBar control plane")
    assert memories[0].learned_from == "promoted_from_proposal"
    # corroborations == 0 → default confidence.
    assert memories[0].importance == pytest.approx(0.5)
    assert proposals.get(pid)["status"] == "promoted"
    assert len(proposals.list_open()) == 0


@pytest.mark.asyncio
async def test_seam_below_threshold_does_not_promote(tmp_path):
    """The same architecture proposal below the quality threshold does NOT
    promote (and, being under decay age, stays probationary)."""
    store, proposals = _sandbox(tmp_path)
    p = MemoryProposal(
        content="The FooBar control plane separates packet forwarding "
                "(plane A) from routing policy (plane B).",
        layer="architecture", tags=["arch"], conversation_id="conv-1",
        quality=FAST_TRACK_QUALITY_THRESHOLD - 0.05,
    )
    with patch("app.services.embedding_service.embed_and_cache"):
        proposals.add(p, activity_count=0)

    patches = _lifecycle_patches(store, proposals, counter=FAST_TRACK_MIN_AGE)
    for pt in patches:
        pt.start()
    try:
        result = await run_lifecycle_pass()
    finally:
        for pt in patches:
            pt.stop()

    assert result["promoted"] == 0
    assert store.list_memories() == []
    assert len(proposals.list_open()) == 1


@pytest.mark.asyncio
async def test_seam_preference_corr0_decays_as_before(tmp_path):
    """A preference proposal with corroborations == 0 still archives at
    ARCHIVAL_AGE_THRESHOLD — corroboration track unchanged."""
    store, proposals = _sandbox(tmp_path)
    p = MemoryProposal(
        content="User prefers concise, no-preamble answers.",
        layer="preference", tags=["style"], conversation_id="conv-1",
        quality=0.99,  # high quality must NOT save a non-fast-track layer
    )
    with patch("app.services.embedding_service.embed_and_cache"):
        proposals.add(p, activity_count=0)

    patches = _lifecycle_patches(store, proposals, counter=ARCHIVAL_AGE_THRESHOLD)
    for pt in patches:
        pt.start()
    try:
        result = await run_lifecycle_pass()
    finally:
        for pt in patches:
            pt.stop()

    assert result["archived"] == 1
    assert result["promoted"] == 0
    assert store.list_memories() == []
    assert len(proposals.list_open()) == 0


@pytest.mark.asyncio
async def test_seam_legacy_proposal_without_quality_never_fast_tracks(tmp_path):
    """A legacy architecture proposal with no quality field is never
    fast-tracked; with corr==0 and no use it decays exactly as before."""
    store, proposals = _sandbox(tmp_path)
    p = MemoryProposal(
        content="Legacy architecture fact recorded before graded quality.",
        layer="architecture", tags=["arch"], conversation_id="conv-1",
    )
    # Simulate an old row: strip the quality fields entirely.
    with patch("app.services.embedding_service.embed_and_cache"):
        pid = proposals.add(p, activity_count=0)
    assert proposals.get(pid).get("quality") is None

    patches = _lifecycle_patches(store, proposals, counter=ARCHIVAL_AGE_THRESHOLD)
    for pt in patches:
        pt.start()
    try:
        result = await run_lifecycle_pass()
    finally:
        for pt in patches:
            pt.stop()

    # No fast-track promotion; decays on the corroboration track.
    assert result["promoted"] == 0
    assert result["archived"] == 1
    assert store.list_memories() == []


@pytest.mark.asyncio
async def test_seam_corroboration_raises_confidence_on_fast_track(tmp_path):
    """Corroboration is a confidence bonus: a fast-track promotion with one
    corroboration lands with higher importance than the uncorroborated case."""
    store, proposals = _sandbox(tmp_path)
    p = MemoryProposal(
        content="The Versal Gen2 NIC exposes VNP4 match-action tables to DPDK.",
        layer="architecture", tags=["arch"], conversation_id="conv-1",
        quality=0.85,
    )
    with patch("app.services.embedding_service.embed_and_cache"):
        pid = proposals.add(p, activity_count=0)
    # One corroboration from a DIFFERENT conversation → corroborations == 1.
    # (corr==1 without a use signal does not satisfy the corroboration-track
    # rules, so promotion here is the fast track; importance reflects the +1.)
    proposals._append({
        "kind": "corroborate", "id": pid, "ts": 1000,
        "conversation_id": "conv-2",
    })
    assert proposals.get(pid)["corroborations"] == 1

    patches = _lifecycle_patches(store, proposals, counter=FAST_TRACK_MIN_AGE)
    for pt in patches:
        pt.start()
    try:
        result = await run_lifecycle_pass()
    finally:
        for pt in patches:
            pt.stop()

    assert result["promoted"] == 1
    (mem,) = store.list_memories()
    assert mem.corroborations == 1
    # 0.5 baseline + 0.1 per corroboration.
    assert mem.importance == pytest.approx(0.6)
    assert mem.importance > 0.5
