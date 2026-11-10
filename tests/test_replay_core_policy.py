"""Pure parts of scripts/replay_core_policy.py: the three selection policies
share one budget/cost model, B ranks within the priority set, and C ranks by
cosine to the query and skips unembedded or personal memories."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "replay_core_policy.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("replay_core_policy", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["replay_core_policy"] = m
    spec.loader.exec_module(m)
    return m


def _m(mid, layer, imp=0.5, used=0, content="x" * 80):
    return SimpleNamespace(id=mid, layer=layer, importance=imp, retrieval_used_count=used, content=content)


def test_fill_respects_budget_and_order(mod):
    mems = [_m("a", "architecture"), _m("b", "architecture"), _m("c", "architecture")]
    assert [m.id for m in mod._fill(mems, 100 * 2 + 10)] == ["a", "b"]


def test_policy_b_ranks_priority_by_importance_then_use(mod):
    mems = [_m("p1", "negative_constraint", 0.5, 0), _m("p2", "negative_constraint", 0.9, 0),
            _m("p3", "negative_constraint", 0.5, 4), _m("r1", "architecture", 0.99),
            _m("x", "personal", 1.0)]
    out = mod.policy_b(mems, 10_000, {"negative_constraint"}, {"personal"})
    assert [m.id for m in out] == ["p2", "p3", "p1", "r1"]


def test_policy_c_ranks_by_cosine_and_skips_unembedded_and_personal(mod):
    vecs = {"hi": np.array([1.0, 0.0]), "mid": np.array([0.7, 0.7]), "lo": np.array([0.0, 1.0])}
    mems = [_m("lo", "architecture"), _m("hi", "decision"), _m("mid", "process"),
            _m("none", "architecture"), _m("pers", "personal")]
    vecs["pers"] = np.array([1.0, 0.0])
    out = mod.policy_c(mems, [np.array([1.0, 0.0])], vecs.get, 10_000, {"personal"})
    assert [m.id for m in out] == ["hi", "mid", "lo"]
    assert mod.policy_c(mems, [], vecs.get, 10_000, {"personal"}) == []
