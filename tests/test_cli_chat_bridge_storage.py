"""Storage-integration tests for app.utils.cli_chat_bridge.

Unlike test_cli_chat_bridge.py (pure conversion), these exercise the thin
storage helpers against a REAL temp ZIYA_HOME: project resolution, loading a
GUI chat as CLI history, change-signature detection, and write-back with the
id-reuse / append-don't-truncate contract the live-attach sync relies on.
"""
import importlib
import os

import pytest


@pytest.fixture
def ziya_home(tmp_path, monkeypatch):
    """Point ZIYA_HOME at a temp dir and reload path-dependent modules."""
    home = tmp_path / ".ziya"
    home.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))
    # paths.get_ziya_home reads ZIYA_HOME at call time, so no reload needed;
    # storage modules construct paths per-instance from get_ziya_home().
    import app.utils.paths as paths
    importlib.reload(paths)
    return home


@pytest.fixture
def project_with_chat(ziya_home, tmp_path):
    """Create a GUI project (rooted at a temp dir) with one 2-message chat.

    Returns (root, project_id, chat_id).
    """
    from app.storage.projects import ProjectStorage
    from app.storage.chats import ChatStorage
    from app.models.project import ProjectCreate
    from app.models.chat import ChatCreate, Message
    from app.utils.paths import get_ziya_home, get_project_dir

    root = str(tmp_path / "code")
    os.makedirs(root, exist_ok=True)

    proj = ProjectStorage(get_ziya_home()).create(ProjectCreate(path=root))
    storage = ChatStorage(get_project_dir(proj.id))
    chat = storage.create(ChatCreate(title="Design chat"))
    storage.add_message(chat.id, Message(id="m1", role="human", content="hello", timestamp=1000))
    storage.add_message(chat.id, Message(id="m2", role="assistant", content="hi there", timestamp=2000))
    return root, proj.id, chat.id


def test_resolve_project_found_and_missing(project_with_chat, tmp_path):
    from app.utils.cli_chat_bridge import resolve_project
    root, project_id, _ = project_with_chat
    proj = resolve_project(root)
    assert proj is not None
    assert proj.id == project_id
    # A directory with no registered project resolves to None
    assert resolve_project(str(tmp_path / "nonexistent")) is None


def test_list_joinable_chats(project_with_chat):
    from app.utils.cli_chat_bridge import list_joinable_chats
    root, project_id, chat_id = project_with_chat
    pid, summaries = list_joinable_chats(root)
    assert pid == project_id
    assert any(s.id == chat_id for s in summaries)


def test_list_joinable_chats_no_project(ziya_home, tmp_path):
    from app.utils.cli_chat_bridge import list_joinable_chats
    pid, summaries = list_joinable_chats(str(tmp_path / "unopened"))
    assert pid is None
    assert summaries == []


def test_load_chat_as_history(project_with_chat):
    from app.utils.cli_chat_bridge import load_chat_as_history
    _, project_id, chat_id = project_with_chat
    chat, history = load_chat_as_history(project_id, chat_id)
    assert chat is not None
    assert history == [
        {"type": "human", "content": "hello", "_timestamp": 1000},
        {"type": "ai", "content": "hi there", "_timestamp": 2000},
    ]


def test_load_missing_chat(project_with_chat):
    from app.utils.cli_chat_bridge import load_chat_as_history
    _, project_id, _ = project_with_chat
    chat, history = load_chat_as_history(project_id, "no-such-id")
    assert chat is None
    assert history == []


def test_chat_signature_advances_on_write(project_with_chat):
    from app.utils.cli_chat_bridge import chat_signature, write_back, load_chat_as_history
    _, project_id, chat_id = project_with_chat
    sig0 = chat_signature(project_id, chat_id)
    assert sig0 is not None
    assert sig0[1] == 2  # two messages

    # Append a turn via write_back → signature must advance
    _, history = load_chat_as_history(project_id, chat_id)
    history.append({"type": "human", "content": "another"})
    sig1 = write_back(project_id, chat_id, history)
    assert sig1 is not None
    assert sig1[1] == 3
    assert sig1 != sig0
    assert chat_signature(project_id, chat_id) == sig1


def test_write_back_reuses_prefix_ids(project_with_chat):
    from app.utils.cli_chat_bridge import write_back, load_chat_as_history
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_project_dir
    _, project_id, chat_id = project_with_chat

    _, history = load_chat_as_history(project_id, chat_id)
    history.append({"type": "ai", "content": "new turn"})
    write_back(project_id, chat_id, history)

    reloaded = ChatStorage(get_project_dir(project_id)).get(chat_id)
    # Original two messages keep their ids/timestamps
    assert reloaded.messages[0].id == "m1"
    assert reloaded.messages[0].timestamp == 1000
    assert reloaded.messages[1].id == "m2"
    # New turn appended with a fresh id
    assert len(reloaded.messages) == 3
    assert reloaded.messages[2].content == "new turn"
    assert reloaded.messages[2].id not in ("m1", "m2")


def test_write_back_missing_chat_returns_none(project_with_chat):
    from app.utils.cli_chat_bridge import write_back
    _, project_id, _ = project_with_chat
    assert write_back(project_id, "no-such-id", [{"type": "human", "content": "x"}]) is None


def test_chat_signature_missing_returns_none(project_with_chat):
    from app.utils.cli_chat_bridge import chat_signature
    _, project_id, _ = project_with_chat
    assert chat_signature(project_id, "no-such-id") is None
