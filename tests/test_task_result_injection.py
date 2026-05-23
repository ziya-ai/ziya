"""
Tests for app.server._inject_task_results — synthetic system messages
that surface terminal task-card runs into the model's chat history.

Covers:
  - Terminal run produces a system message with summary + decisions
  - In-flight run produces nothing
  - Anchor-message-id binding is spliced after the matching message
  - Unanchored binding falls back to chronological splicing by created_at
  - Multiple bindings are inserted in correct order
  - Missing chat / project / run cases all degrade silently
"""

import json
import os
import time
import uuid
from pathlib import Path

import pytest


def _make_env(tmp_path):
    """Build a project + chat directory layout that ChatStorage etc. will read."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    projects_dir.mkdir(parents=True)
    project_id = "p_test_" + uuid.uuid4().hex[:8]
    project_dir = projects_dir / project_id
    project_dir.mkdir()
    project_record = {
        "id": project_id,
        "name": "Test",
        "path": str(project_root.resolve()),
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }
    (project_dir / "project.json").write_text(json.dumps(project_record))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): project_id})
    )
    (project_dir / "chats").mkdir()
    (project_dir / "task_runs").mkdir()
    return {
        "project_root": str(project_root.resolve()),
        "ziya_home": ziya_home,
        "project_id": project_id,
        "project_dir": project_dir,
    }


def _write_chat(env, chat_id, messages):
    """messages: list of dicts with {id, role, content, timestamp}"""
    record = {
        "id": chat_id,
        "title": "T",
        "groupId": None,
        "contextIds": [], "skillIds": [],
        "additionalFiles": [], "additionalPrompt": None,
        "messages": messages,
        "createdAt": int(time.time() * 1000),
        "lastActiveAt": int(time.time() * 1000),
    }
    chat_path = env["project_dir"] / "chats" / f"{chat_id}.json"
    chat_path.write_text(json.dumps(record))
    return chat_path


def _write_card(env, card_id, name):
    cards_dir = env["project_dir"] / "task_cards"
    cards_dir.mkdir(exist_ok=True)
    record = {
        "id": card_id, "name": name, "description": "",
        "root": {"block_type": "task", "id": "b1", "name": "Root",
                 "instructions": "", "body": []},
        "tags": [], "is_template": False, "source": "custom",
        "created_at": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
        "run_count": 0,
    }
    (cards_dir / f"{card_id}.json").write_text(json.dumps(record))


def _write_run(env, run_id, card_id, status, summary, decisions):
    artifact = None
    if summary or decisions:
        artifact = {
            "summary": summary, "decisions": decisions or [],
            "outputs": [], "tokens": 0, "tool_calls": 0,
            "duration_ms": 100, "created_at": time.time(),
            "failed": status == "failed",
        }
    record = {
        "id": run_id,
        "card_id": card_id,
        "source_conversation_id": None,
        "status": status,
        "created_at": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
        "block_states": {}, "artifact": artifact,
    }
    (env["project_dir"] / "task_runs" / f"{run_id}.json").write_text(json.dumps(record))


def _write_binding(env, chat_id, binding_id, card_id, run_id, anchor_message_id, created_at):
    bindings_path = env["project_dir"] / "chats" / f"{chat_id}.bindings.json"
    existing = []
    if bindings_path.exists():
        existing = json.loads(bindings_path.read_text())
    existing.append({
        "id": binding_id, "chat_id": chat_id, "card_id": card_id, "run_id": run_id,
        "anchor_message_id": anchor_message_id, "created_at": created_at,
    })
    bindings_path.write_text(json.dumps(existing))


@pytest.fixture
def env_with_chat(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: env["ziya_home"])
    monkeypatch.setattr(
        "app.plugins.data_retention.get_retention_enforcer",
        lambda: type("X", (), {"is_expired": lambda *a, **kw: False})(),
        raising=False,
    )
    from app.context import set_project_root
    set_project_root(env["project_root"])
    return env


# ── Tests ─────────────────────────────────────────────────────────

def test_terminal_done_run_injects_system_message(env_with_chat):
    env = env_with_chat
    chat_id = "c_" + uuid.uuid4().hex[:8]
    msg_id = "m_" + uuid.uuid4().hex[:8]
    _write_chat(env, chat_id, [
        {"id": msg_id, "role": "human", "content": "Run task X",
         "timestamp": 1000},
    ])
    _write_card(env, "card_1", "MyTask")
    _write_run(env, "run_1", "card_1", "done",
               summary="Refactored auth module", decisions=["Used pattern A"])
    _write_binding(env, chat_id, "b1", "card_1", "run_1",
                   anchor_message_id=msg_id, created_at=1500)

    history = [{"type": "human", "content": "Run task X", "_timestamp": 1000}]
    from app.server import _inject_task_results
    _inject_task_results(history, chat_id)

    # Should have inserted one system message after the human message
    assert len(history) == 2
    assert history[0]["type"] == "human"
    assert history[1].get("role") == "system"
    body = history[1]["content"]
    assert "MyTask" in body
    assert "done" in body
    assert "Refactored auth module" in body
    assert "Used pattern A" in body


def test_running_state_does_not_inject(env_with_chat):
    env = env_with_chat
    chat_id = "c_" + uuid.uuid4().hex[:8]
    _write_chat(env, chat_id, [{"id": "m1", "role": "human",
                                "content": "hi", "timestamp": 1000}])
    _write_card(env, "card_1", "Task")
    _write_run(env, "run_1", "card_1", "running", summary="", decisions=[])
    _write_binding(env, chat_id, "b1", "card_1", "run_1",
                   anchor_message_id="m1", created_at=1500)

    history = [{"type": "human", "content": "hi", "_timestamp": 1000}]
    from app.server import _inject_task_results
    _inject_task_results(history, chat_id)

    # No injection — only the original message remains.
    assert len(history) == 1


def test_unanchored_binding_uses_chronological_splice(env_with_chat):
    env = env_with_chat
    chat_id = "c_" + uuid.uuid4().hex[:8]
    _write_chat(env, chat_id, [
        {"id": "m1", "role": "human", "content": "first", "timestamp": 1000},
        {"id": "m2", "role": "assistant", "content": "ok", "timestamp": 2000},
        {"id": "m3", "role": "human", "content": "third", "timestamp": 3000},
    ])
    _write_card(env, "card_1", "T")
    _write_run(env, "run_1", "card_1", "done",
               summary="result", decisions=[])
    # Created between m2 (ts=2000) and m3 (ts=3000) — should land
    # right after m2.
    _write_binding(env, chat_id, "b1", "card_1", "run_1",
                   anchor_message_id=None, created_at=2500)

    history = [
        {"type": "human", "content": "first", "_timestamp": 1000},
        {"type": "ai", "content": "ok", "_timestamp": 2000},
        {"type": "human", "content": "third", "_timestamp": 3000},
    ]
    from app.server import _inject_task_results
    _inject_task_results(history, chat_id)

    assert len(history) == 4
    # Order: human(first), ai(ok), system(task), human(third)
    assert history[0]["type"] == "human"
    assert history[1]["type"] == "ai"
    assert history[2].get("role") == "system"
    assert "result" in history[2]["content"]
    assert history[3]["type"] == "human" and history[3]["content"] == "third"


def test_no_bindings_is_noop(env_with_chat):
    env = env_with_chat
    chat_id = "c_" + uuid.uuid4().hex[:8]
    _write_chat(env, chat_id, [{"id": "m1", "role": "human",
                                "content": "hi", "timestamp": 1000}])
    history = [{"type": "human", "content": "hi", "_timestamp": 1000}]
    from app.server import _inject_task_results
    _inject_task_results(history, chat_id)
    assert len(history) == 1


def test_missing_chat_degrades_silently(env_with_chat):
    history = [{"type": "human", "content": "hi", "_timestamp": 1000}]
    from app.server import _inject_task_results
    _inject_task_results(history, "nonexistent-chat-id")
    assert len(history) == 1


def test_no_conversation_id_is_noop():
    from app.server import _inject_task_results
    history = [{"type": "human", "content": "hi"}]
    _inject_task_results(history, "")
    assert len(history) == 1


def test_multiple_bindings_inserted_in_order(env_with_chat):
    env = env_with_chat
    chat_id = "c_" + uuid.uuid4().hex[:8]
    _write_chat(env, chat_id, [
        {"id": "m1", "role": "human", "content": "a", "timestamp": 1000},
        {"id": "m2", "role": "assistant", "content": "b", "timestamp": 2000},
    ])
    _write_card(env, "card_1", "First")
    _write_card(env, "card_2", "Second")
    _write_run(env, "run_1", "card_1", "done", summary="r1", decisions=[])
    _write_run(env, "run_2", "card_2", "done", summary="r2", decisions=[])
    # Both anchored to m1 — both should land between m1 and m2.
    _write_binding(env, chat_id, "b1", "card_1", "run_1",
                   anchor_message_id="m1", created_at=1100)
    _write_binding(env, chat_id, "b2", "card_2", "run_2",
                   anchor_message_id="m1", created_at=1200)

    history = [
        {"type": "human", "content": "a", "_timestamp": 1000},
        {"type": "ai", "content": "b", "_timestamp": 2000},
    ]
    from app.server import _inject_task_results
    _inject_task_results(history, chat_id)

    assert len(history) == 4
    assert history[0]["content"] == "a"
    assert history[1].get("role") == "system"
    assert history[2].get("role") == "system"
    assert history[3]["content"] == "b"
    # Both task summaries present (order between the two synthetics
    # is implementation-defined but must contain both)
    contents = [history[1]["content"], history[2]["content"]]
    assert any("r1" in c for c in contents)
    assert any("r2" in c for c in contents)
