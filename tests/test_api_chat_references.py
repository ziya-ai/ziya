"""REST surface over conversation references (design/conversation-references.md,
slice 2): what the ActiveContextBar line and popover read and write.

Drives the real ``app.api.chats`` router against the real chat record
(``chat_env``) and asserts on the record, so a route that answered 200 while
writing nothing would fail here.  The frontend contract pinned:

  GET    .../chats/{id}/references            -> {turn, summary, references[]}
  POST   .../chats/{id}/references            {locator, title?} -> pinned ref
  POST   .../chats/{id}/references/{rid}/pin  -> promoted ref
  DELETE .../chats/{id}/references/{rid}      -> {ok}
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_context_management_tools import chat_env, _read_chat  # noqa: F401  (fixture)
from app.utils import conversation_references as cr


@pytest.fixture
def client(chat_env, monkeypatch):
    # chats.py imported these names at module load; the fixture only patched
    # app.utils.paths.
    monkeypatch.setattr("app.api.chats.get_ziya_home", lambda: chat_env["ziya_home"])
    monkeypatch.setattr("app.api.chats.get_project_dir",
                        lambda pid: chat_env["ziya_home"] / "projects" / pid)
    from app.api.chats import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _base(env):
    return f"/api/v1/projects/{env['project_id']}/chats/{env['chat_id']}/references"


def _display(tool, args):
    return {"type": "tool_display", "tool_name": tool, "args": args, "result": "ok"}


def test_get_lists_pinned_first_with_flags_and_summary(client, chat_env):
    root = chat_env["project_root"]
    p = cr.pickup_from_tool_display(_display("mcp_fetch", {"url": "https://example.com/picked"}), root)
    cr.record_turn(chat_env["chat_id"], root, [p], "", turn=1)
    cr.record_turn(chat_env["chat_id"], root, [], "see https://example.com/picked", turn=2)
    cr.pin_reference(chat_env["chat_id"], root, "src/main.py", turn=3, title="Main")

    r = client.get(_base(chat_env))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["turn"] == 1                       # empty message list on the record
    assert body["summary"] == {"pinned": 1, "picked_up": 1, "useful": 1, "faded": 0}
    refs = body["references"]
    assert [x["provenance"] for x in refs] == ["pinned", "picked_up"]
    assert refs[0]["locator"] == "src/main.py" and refs[0]["title"] == "Main"
    assert refs[1]["useful"] is True and refs[1]["faded"] is False
    assert [u["kind"] for u in refs[1]["uses"]] == ["read", "cited"]


def test_get_empty_when_nothing_recorded_and_404_for_unknown_chat(client, chat_env):
    r = client.get(_base(chat_env))
    assert r.status_code == 200
    assert r.json()["references"] == []
    assert r.json()["summary"] == {"pinned": 0, "picked_up": 0, "useful": 0, "faded": 0}
    r = client.get(f"/api/v1/projects/{chat_env['project_id']}/chats/no-such-chat/references")
    assert r.status_code == 404


def test_post_pins_and_lands_on_the_record(client, chat_env):
    r = client.post(_base(chat_env), json={"locator": "https://example.com/spec#x", "title": "Spec"})
    assert r.status_code == 200, r.text
    ref = r.json()
    assert ref["provenance"] == "pinned" and ref["locator"] == "https://example.com/spec"
    stored = cr.get_references(_read_chat(chat_env))
    assert ref["id"] in stored and stored[ref["id"]]["title"] == "Spec"
    # The turn comes from the record (no history to count): first turn.
    assert stored[ref["id"]]["added_turn"] == 1


def test_post_refuses_untrackable_locators(client, chat_env):
    for bad in ("ziya://folder/x", ".ziya/scratch.md", ""):
        r = client.post(_base(chat_env), json={"locator": bad})
        assert r.status_code == 400, (bad, r.text)
    assert cr.get_references(_read_chat(chat_env)) == {}


def test_promote_then_unpin(client, chat_env):
    root = chat_env["project_root"]
    p = cr.pickup_from_tool_display(_display("file_read", {"path": "README.md"}), root)
    cr.record_turn(chat_env["chat_id"], root, [p], "", turn=2)
    rid = next(iter(cr.get_references(_read_chat(chat_env))))

    r = client.post(f"{_base(chat_env)}/{rid}/pin")
    assert r.status_code == 200, r.text
    assert r.json()["provenance"] == "pinned"
    stored = cr.get_references(_read_chat(chat_env))[rid]
    assert stored["provenance"] == "pinned" and len(stored["uses"]) == 1   # uses survive promotion

    assert client.post(f"{_base(chat_env)}/ref_nope/pin").status_code == 404

    r = client.delete(f"{_base(chat_env)}/{rid}")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert cr.get_references(_read_chat(chat_env)) == {}
    assert client.delete(f"{_base(chat_env)}/{rid}").status_code == 404


def test_current_turn_counts_stored_human_messages(chat_env):
    assert cr.current_turn({"messages": [{"role": "human", "content": "a"},
                                         {"role": "assistant", "content": "b"},
                                         {"role": "user", "content": "c"}]}) == 3
    assert cr.current_turn({}) == 1
