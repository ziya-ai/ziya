"""--split chronological for scripts/run_memory_lifecycle_simulation.py.

Seed = oldest N by ChatRecord.created_at, later = the next M; undated chats
(created_at == 0) are dropped rather than placed by guess.  Also checks the
seam into the real loader: iter_random_conversations must populate
created_at (fails until app/memory/eval.py carries the field).
"""
import importlib.util
import sys
from dataclasses import fields
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_memory_lifecycle_simulation.py"


@pytest.fixture(scope="module")
def sim():
    spec = importlib.util.spec_from_file_location("lifecycle_sim", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lifecycle_sim"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Chat:
    def __init__(self, chat_id, created_at):
        self.chat_id, self.created_at = chat_id, created_at


def test_chronological_split_orders_by_created_at_and_drops_undated(sim):
    chats = [_Chat("d", 4000), _Chat("undated", 0), _Chat("a", 1000),
             _Chat("c", 3000), _Chat("b", 2000), _Chat("e", 5000)]
    seed, later = sim._split_chronological(chats, seed_count=2, later_count=2)
    assert [c.chat_id for c in seed] == ["a", "b"]
    assert [c.chat_id for c in later] == ["c", "d"]
    assert all(c.chat_id != "undated" for c in seed + later)


def test_chronological_split_shrinks_when_corpus_is_small(sim):
    chats = [_Chat(str(i), (i + 1) * 100) for i in range(5)]
    seed, later = sim._split_chronological(chats, seed_count=10, later_count=10)
    assert len(seed) == 2 and len(later) == 3
    assert seed[-1].created_at < later[0].created_at


def test_chat_record_carries_created_at():
    from app.memory.eval import ChatRecord
    assert "created_at" in {f.name for f in fields(ChatRecord)}, \
        "iter_random_conversations must populate created_at for the chronological split"
