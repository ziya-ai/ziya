"""Unit tests for the golden-set quality scorer's scoring MATH.

These tests exercise ONLY the pure functions ``compute_conversation_dims`` and
``aggregate_scorecard`` with synthetic judge verdicts.  They make NO LLM calls
and touch NO real store — the LLM judge and the sandboxed pipeline are the
*source* of the boolean verdicts, but the scoring arithmetic is deterministic
and independently checkable, which is what these tests pin down.
"""

import pytest

from scripts.memory_quality_score import (
    compute_conversation_dims,
    aggregate_scorecard,
    WEIGHTS,
    DIMS,
)


def _mem(content="x", layer="architecture"):
    return {"content": content, "layer": layer}


# ---------------------------------------------------------------------------
# compute_conversation_dims
# ---------------------------------------------------------------------------

def test_perfect_extraction_scores_one_on_every_dimension():
    """All golden facts covered + retrievable, and every produced memory
    useful/atomic/self-contained => 1.0 on all five dimensions, composite 1.0.
    """
    ideal = [_mem("fact a"), _mem("fact b")]
    produced = [_mem("mem a"), _mem("mem b")]
    judgment = {
        "covered": [True, True],
        "useful": [True, True],
        "atomic": [True, True],
        "self_contained": [True, True],
        "retrievable": [True, True],
    }
    dims = compute_conversation_dims(ideal, produced, judgment)
    assert dims == {
        "coverage": 1.0,
        "precision": 1.0,
        "granularity": 1.0,
        "self_containment": 1.0,
        "retrievability": 1.0,
    }
    agg = aggregate_scorecard([dims])
    assert agg["composite"] == pytest.approx(1.0)


def test_blob_bundling_three_facts_is_penalised_on_granularity_only():
    """A produced memory that bundles multiple facts (atomic=False) drops
    GRANULARITY below 1.0 while leaving the other dimensions perfect."""
    ideal = [_mem("fact a")]
    produced = [_mem("blob bundling 3 facts"), _mem("clean fact"), _mem("clean fact 2")]
    judgment = {
        "covered": [True],
        "useful": [True, True, True],
        "atomic": [False, True, True],       # the blob is not atomic
        "self_contained": [True, True, True],
        "retrievable": [True],
    }
    dims = compute_conversation_dims(ideal, produced, judgment)
    assert dims["granularity"] == pytest.approx(2 / 3)
    assert dims["granularity"] < 1.0
    # Other dimensions unaffected by the granularity failure.
    assert dims["precision"] == 1.0
    assert dims["self_containment"] == 1.0
    assert dims["coverage"] == 1.0


def test_unresolved_reference_is_penalised_on_self_containment_only():
    """A produced memory with a dangling reference (self_contained=False) drops
    SELF_CONTAINMENT while GRANULARITY/PRECISION stay perfect."""
    ideal = [_mem("fact a")]
    produced = [_mem("fix the bug in the function"), _mem("names ziya D3Renderer")]
    judgment = {
        "covered": [True],
        "useful": [True, True],
        "atomic": [True, True],
        "self_contained": [False, True],     # first leans on "the bug"/"the function"
        "retrievable": [True],
    }
    dims = compute_conversation_dims(ideal, produced, judgment)
    assert dims["self_containment"] == pytest.approx(0.5)
    assert dims["granularity"] == 1.0
    assert dims["precision"] == 1.0


def test_empty_extraction_against_empty_golden_scores_precision_one():
    """Correctly keeping nothing from a no-durable-knowledge conversation:
    PRECISION == 1.0, and every other dimension is undefined (None)."""
    dims = compute_conversation_dims([], [], {
        "covered": [], "useful": [], "atomic": [],
        "self_contained": [], "retrievable": [],
    })
    assert dims["precision"] == 1.0
    assert dims["coverage"] is None
    assert dims["granularity"] is None
    assert dims["self_containment"] is None
    assert dims["retrievability"] is None


def test_empty_extraction_against_nonempty_golden_excludes_precision():
    """|P|==0 & |G|>0: PRECISION excluded (nothing to judge), COVERAGE 0.0,
    RETRIEVABILITY 0.0 (the misses are punished by coverage/retrievability)."""
    ideal = [_mem("fact a"), _mem("fact b")]
    dims = compute_conversation_dims(ideal, [], {
        "covered": [False, False], "useful": [], "atomic": [],
        "self_contained": [], "retrievable": [False, False],
    })
    assert dims["precision"] is None
    assert dims["coverage"] == 0.0
    assert dims["retrievability"] == 0.0
    assert dims["granularity"] is None
    assert dims["self_containment"] is None


def test_partial_coverage_and_precision_fractions():
    ideal = [_mem("a"), _mem("b"), _mem("c"), _mem("d")]
    produced = [_mem("p0"), _mem("p1"), _mem("p2")]
    judgment = {
        "covered": [True, True, False, False],     # 2/4
        "useful": [True, False, True],             # 2/3
        "atomic": [True, True, True],
        "self_contained": [True, True, False],     # 2/3
        "retrievable": [True, False, False, False],  # 1/4
    }
    dims = compute_conversation_dims(ideal, produced, judgment)
    assert dims["coverage"] == pytest.approx(0.5)
    assert dims["precision"] == pytest.approx(2 / 3)
    assert dims["granularity"] == pytest.approx(1.0)
    assert dims["self_containment"] == pytest.approx(2 / 3)
    assert dims["retrievability"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# aggregate_scorecard
# ---------------------------------------------------------------------------

def test_aggregate_excludes_undefined_dims_from_their_means():
    """A conversation where a dim is None must not drag that dim's mean; it is
    simply excluded from that dimension's average."""
    conv_full = {
        "coverage": 1.0, "precision": 1.0, "granularity": 1.0,
        "self_containment": 1.0, "retrievability": 1.0,
    }
    conv_empty_golden = {   # the empty-golden PRECISION anchor
        "coverage": None, "precision": 1.0, "granularity": None,
        "self_containment": None, "retrievability": None,
    }
    agg = aggregate_scorecard([conv_full, conv_empty_golden])
    # coverage mean over the ONE conv where it's defined.
    assert agg["dims"]["coverage"] == pytest.approx(1.0)
    # precision mean over BOTH.
    assert agg["dims"]["precision"] == pytest.approx(1.0)
    assert agg["dims"]["granularity"] == pytest.approx(1.0)
    assert agg["composite"] == pytest.approx(1.0)


def test_aggregate_composite_uses_rubric_weights():
    """Composite is the weighted sum of the per-dimension means."""
    conv = {
        "coverage": 0.4, "precision": 0.6, "granularity": 0.9,
        "self_containment": 0.8, "retrievability": 0.5,
    }
    agg = aggregate_scorecard([conv])
    expected = (0.25 * 0.4 + 0.25 * 0.6 + 0.20 * 0.9
                + 0.15 * 0.8 + 0.15 * 0.5)
    assert agg["composite"] == pytest.approx(expected)
    for d in DIMS:
        assert agg["dims"][d] == pytest.approx(conv[d])


def test_aggregate_a_fully_undefined_dim_is_none_and_dropped_from_composite():
    """If NO conversation defines a dimension (e.g. only empty-golden convos),
    that dim's mean is None and contributes 0 to the composite."""
    conv_a = {"coverage": None, "precision": 1.0, "granularity": None,
              "self_containment": None, "retrievability": None}
    conv_b = {"coverage": None, "precision": 0.0, "granularity": None,
              "self_containment": None, "retrievability": None}
    agg = aggregate_scorecard([conv_a, conv_b])
    assert agg["dims"]["coverage"] is None
    assert agg["dims"]["retrievability"] is None
    assert agg["dims"]["precision"] == pytest.approx(0.5)
    # Only precision contributes: 0.25 * 0.5.
    assert agg["composite"] == pytest.approx(0.25 * 0.5)


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
