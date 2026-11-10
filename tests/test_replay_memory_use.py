"""Pure parts of scripts/replay_memory_use.py.

The replay's honesty rests on two filters: a memory is available to a chat
only if its SOURCE chat predates it (and is never the chat itself), and a
memory's use score is the max cosine over reply windows.  Pins both, plus
the summary's handling of a memory loaded by both core and search.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "replay_memory_use.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("replay_memory_use", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["replay_memory_use"] = m
    spec.loader.exec_module(m)
    return m


def _mem(mid, src):
    return SimpleNamespace(id=mid, learned_from_conversation=src, layer="architecture")


def test_available_memories_is_strictly_chronological_and_never_self(mod):
    created = {"c_old": 100, "c_same": 500, "c_new": 900, "c_target": 500}
    mems = [_mem("m_old", "c_old"), _mem("m_same_time", "c_same"),
            _mem("m_new", "c_new"), _mem("m_self", "c_target"),
            _mem("m_orphan", None), _mem("m_unknown_src", "c_missing")]
    out = mod.available_memories(mems, "c_target", 500, created)
    assert [m.id for m in out] == ["m_old"]


def test_score_loaded_is_max_cosine_over_windows_and_skips_unembedded(mod):
    cache = {"m_a": np.array([1.0, 0.0]), "m_b": np.array([0.0, 1.0])}
    windows = [np.array([0.6, 0.8]), np.array([1.0, 0.0])]
    out = mod.score_loaded(["m_a", "m_b", "m_none"],
                           SimpleNamespace(get=cache.get), windows,
                           lambda a, b: float(np.dot(a, b)))
    assert out == {"m_a": pytest.approx(1.0), "m_b": pytest.approx(0.8)}


def test_summary_counts_core_and_search_separately(mod):
    rows = [
        {"chat_id": "c1", "memory_id": "m1", "layer": "decision", "kinds": ["core", "search"], "best_cos": 0.9},
        {"chat_id": "c1", "memory_id": "m2", "layer": "decision", "kinds": ["core"], "best_cos": 0.3},
        {"chat_id": "c2", "memory_id": "m3", "layer": "architecture", "kinds": ["search"], "best_cos": 0.56},
    ]
    card = mod.summarize(rows)
    assert "| core | 2 | 2 |" in card
    assert "| search | 2 | 2 |" in card
    assert "| decision | 2 | 1 | 50.0% |" in card
    assert "Chats replayed: 2" in card and "ever used@0.55: 2 of 3" in card
