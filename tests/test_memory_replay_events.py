"""scripts/memory_replay/events.py (audit tool-call events) and metrics.py
(relearn / fairness / structure) -- the pieces the engine tests exercise only
indirectly."""
import json
import os
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent / "scripts" / "memory_replay"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import events as E  # noqa: E402
import metrics  # noqa: E402
from tests.test_memory_replay_engine import FakeEnc  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    import app.utils.encryption as enc
    monkeypatch.setattr(enc, "_encryptor", FakeEnc())
    h = tmp_path / "zh"
    (h / "audit").mkdir(parents=True)
    (h / "memory").mkdir()
    (h / "projects" / "p1" / "chats").mkdir(parents=True)
    (h / "projects" / "p1" / "chats" / "abcdef123456-7890.json").write_text("{}")
    full = "X" * 480 + " the full tail that the audit log cut off at five hundred characters."
    rec = {"kind": "record", "id": "prop_1", "data": {"content": full, "conversation_id": "abcdef123456-7890"}}
    (h / "memory" / "probationary.jsonl").write_bytes(FakeEnc().encrypt((json.dumps(rec) + "\n").encode()))
    rows = [
        {"eventTime": "2026-09-20T10:00:00+00:00", "eventName": "memory_propose", "status": "ok",
         "args": {"content": full[:500], "layer": "lexicon", "tags": "['a', 'b']",
                  "conversation_id": "abcdef123456-7890"}, "conv": "abcdef123456"},
        {"eventTime": "2026-09-20T10:05:00+00:00", "eventName": "memory_save", "status": "ok",
         "args": {"content": "short saved fact", "layer": "decision", "tags": "x, y"}, "conv": "abcdef123456"},
        {"eventTime": "2026-09-20T10:06:00+00:00", "eventName": "memory_propose", "status": "error",
         "args": {"content": "failed call"}, "conv": "abcdef123456"},
        {"eventTime": "2026-09-20T10:07:00+00:00", "eventName": "memory_propose", "status": "ok",
         "args": {"content": "unknown conversation"}, "conv": "zzzzzzzzzzzz"},
        {"eventTime": "2026-09-20T10:08:00+00:00", "eventName": "run_shell_command", "status": "ok", "args": {}},
    ]
    (h / "audit" / "tool_audit_2026-09-20.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return h, full


def test_audit_events_resolve_conversation_restore_truncation_and_skip_failures(home):
    h, full = home
    st = E.AuditStats()
    evs = E.audit_events(str(h), E.chat_index(str(h)), {"p1": ("/w/ProjB-a", "Project B")}, stats=st)
    assert [e.kind for e in evs] == ["propose", "save"]
    assert evs[0].payload["content"] == full                   # restored from the real store
    assert evs[0].payload["tags"] == ["a", "b"]
    assert evs[1].chat_id == "abcdef123456-7890"               # resolved from the 12-char prefix
    assert evs[1].project_path == "/w/ProjB-a"
    assert evs[1].payload["tags"] == ["x", "y"]
    assert st.as_dict() == {"seen": 4, "used": 2, "skipped_status": 1, "unresolved_conversation": 1,
                            "truncated": 1, "restored": 1, "unrestored": 0}
    assert "X" * 20 not in json.dumps(evs[0].ref())             # refs carry no text


def test_merge_orders_tool_calls_before_the_reply_at_the_same_ms():
    a = E.ReplayEvent(t=5, kind="extract", chat_id="c", project_id=None, project_path=None, msg_index=3)
    b = E.ReplayEvent(t=5, kind="propose", chat_id="c", project_id=None, project_path=None, msg_index=9)
    c = E.ReplayEvent(t=4, kind="extract", chat_id="d", project_id=None, project_path=None, msg_index=1)
    assert [e.kind + e.chat_id for e in E.merge([a], [b, c])] == ["extractd", "proposec", "extractc"]


def test_groups_are_reporting_only_buckets(monkeypatch):
    monkeypatch.setenv("ZIYA_REPLAY_GROUPS", "projb=ProjB,tools")
    assert metrics.group_of("ProjB-ground") == "projb"
    assert metrics.group_of("tools") == "projb"
    assert metrics.group_of("ziya-0.4.0.1") == "ziya"
    assert metrics.group_of("ZiyaInternal") == "ziya"
    assert metrics.group_of("dotfiles") == "other"
    assert metrics.group_of(None) == "other"


def test_structure_snapshot_counts(monkeypatch):
    monkeypatch.setenv("ZIYA_REPLAY_GROUPS", "projb=ProjB,tools")
    rows = [{"id": f"m{i}", "status": "active", "layer": "lexicon",
             "scope": {"project_paths": ["/w/ziya-x" if i < 3 else "/w/tools"]}} for i in range(5)]
    rows.append({"id": "m9", "status": "archived", "layer": "lexicon", "scope": {}})
    nodes = {"r": {"id": "r", "parent": None, "tags": ["a", "b", "c"], "memory_refs": ["m0", "m1", "m3"]},
             "c": {"id": "c", "parent": "r", "tags": [], "memory_refs": ["m2"]}}
    s = metrics.structure_snapshot(rows, nodes)
    assert (s["active"], s["archived"], s["nodes"], s["roots"], s["max_depth"]) == (5, 1, 2, 1, 2)
    assert s["largest_node_share"] == 3 / 5 and s["max_root_tags"] == 3
    assert s["orphan_rate"] == 1 / 5
    assert s["active_by_group"] == {"ziya": 3, "projb": 2}
    assert 0.9 < s["node_mix_entropy_mean"] < 0.93                 # 2:1 mix in the root


def test_relearn_counts_only_proposals_after_the_loss(tmp_path, monkeypatch):
    import app.utils.encryption as enc
    monkeypatch.setattr(enc, "_encryptor", FakeEnc())
    import attribution
    log = attribution.EncLog(tmp_path / "texts.enc")
    lost = "the orders service stores everything in postgresql fifteen with logical replication"
    log.append({"t": 100, "id": "prop_a", "kind": "proposal", "new": True, "content": lost, "ev": 0})
    log.append({"t": 200, "id": "m1", "kind": "lost", "content": lost})
    log.append({"t": 300, "id": "prop_b", "kind": "proposal", "new": True, "ev": 5,
                "content": "orders service stores everything in postgresql fifteen (logical replication)"})
    log.append({"t": 400, "id": "prop_c", "kind": "proposal", "new": True, "ev": 6,
                "content": "projb edge nodes use the fast path"})
    r = metrics.Run(tmp_path).relearn()
    assert r == {"new_proposals": 3, "relearned": 1, "relearn_rate": round(1 / 3, 4), "lost_texts": 1}
