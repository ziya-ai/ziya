"""Seam tests: explicit message endpoints + id-merge bulk-sync, against the
real app/api/chats.py router and a real ChatStorage in tmp_path.

These reproduce the 2026-09-25 audit (design/conversation-single-writer.md):
before the change, deletes and edit-and-resubmit never reached disk, and a
client that missed an answer overwrote it.  Every test here fails against the
pre-change router (no rewind/delete/patch routes; wholesale bulk-sync).
"""
from tests.test_api_chats import ziya_home, project_dir, client, _make_chat  # noqa: F401  (fixtures)


def M(i, role, content):
    return {"id": f"m{i}", "role": role, "content": content, "timestamp": 1000 + i}


BASE = [M(0, "human", "q0"), M(1, "assistant", "a0"),
        M(2, "human", "q1"), M(3, "assistant", "a1"),
        M(4, "human", "q2"), M(5, "assistant", "a2")]


def _seed(tc, pid, msgs=BASE, v=1000):
    r = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("c", "T", version=v, messages=msgs)]})
    assert r.status_code == 200


def _disk(tc, pid):
    r = tc.get(f"/api/v1/projects/{pid}/chats/c").json()
    return [m["id"] for m in r["messages"]], r


class TestExplicitEndpoints:
    def test_delete_reaches_disk(self, client):
        tc, pid = client
        _seed(tc, pid)
        r = tc.request("DELETE", f"/api/v1/projects/{pid}/chats/c/messages/m3")
        assert r.status_code == 200, r.text
        ids, rec = _disk(tc, pid)
        assert ids == ["m0", "m1", "m2", "m4", "m5"]
        assert rec["_version"] > 1000

    def test_rewind_with_edit_reaches_disk(self, client):
        tc, pid = client
        _seed(tc, pid)
        r = tc.post(f"/api/v1/projects/{pid}/chats/c/rewind",
                    json={"messageId": "m0", "text": "q0 EDITED"})
        assert r.status_code == 200, r.text
        ids, rec = _disk(tc, pid)
        assert ids == ["m0"]
        assert rec["messages"][0]["content"] == "q0 EDITED"

    def test_patch_in_place_reaches_disk(self, client):
        tc, pid = client
        _seed(tc, pid)
        r = tc.patch(f"/api/v1/projects/{pid}/chats/c/messages/m2", json={"content": "q1 fixed"})
        assert r.status_code == 200, r.text
        ids, rec = _disk(tc, pid)
        assert ids == [m["id"] for m in BASE]
        assert rec["messages"][2]["content"] == "q1 fixed"

    def test_legacy_locator_via_body(self, client):
        """A message the browser holds without the server's id."""
        tc, pid = client
        _seed(tc, pid)
        r = tc.request("DELETE", f"/api/v1/projects/{pid}/chats/c/messages/_",
                       json={"index": 3, "role": "assistant", "contentHead": "a1", "contentLength": 2})
        assert r.status_code == 200, r.text
        assert _disk(tc, pid)[0] == ["m0", "m1", "m2", "m4", "m5"]

    def test_missing_chat_404(self, client):
        tc, pid = client
        r = tc.request("DELETE", f"/api/v1/projects/{pid}/chats/nope/messages/m0")
        assert r.status_code == 404
        assert r.json().get("detail") == "Chat not found"   # not merely "no such route"

    def test_rewind_non_human_400(self, client):
        tc, pid = client
        _seed(tc, pid)
        r = tc.post(f"/api/v1/projects/{pid}/chats/c/rewind", json={"messageId": "m1"})
        assert r.status_code == 400


class TestIdMergeBulkSync:
    def test_lost_answer_race(self, client):
        """Browser holds [.. q2] without a2 and adds q3: same count as disk.
        The old wholesale replace overwrote a2."""
        tc, pid = client
        _seed(tc, pid)
        stale = BASE[:5] + [M(6, "human", "q3")]
        _seed(tc, pid, stale, v=2000)
        ids, rec = _disk(tc, pid)
        assert ids == ["m0", "m1", "m2", "m3", "m4", "m5", "m6"]
        # Merged list differs from what the client sent, so the version moves
        # past the client's and its next pull converges.
        assert rec["_version"] > 2000

    def test_stale_tab_cannot_resurrect_deleted(self, client):
        tc, pid = client
        _seed(tc, pid)
        tc.request("DELETE", f"/api/v1/projects/{pid}/chats/c/messages/m3")
        _seed(tc, pid, BASE, v=9_999_999_999_999)   # another tab's old full copy
        assert "m3" not in _disk(tc, pid)[0]

    def test_stale_tab_cannot_resurrect_rewound(self, client):
        tc, pid = client
        _seed(tc, pid)
        tc.post(f"/api/v1/projects/{pid}/chats/c/rewind", json={"messageId": "m2"})
        _seed(tc, pid, BASE, v=9_999_999_999_999)
        assert _disk(tc, pid)[0] == ["m0", "m1", "m2"]

    def test_server_owned_tombstones_survive_client_push(self, client):
        tc, pid = client
        _seed(tc, pid)
        tc.request("DELETE", f"/api/v1/projects/{pid}/chats/c/messages/m3")
        _, rec = _disk(tc, pid)
        forged = _make_chat("c", "T", version=9_999_999_999_999, messages=BASE)
        forged["_deletedMessages"] = {"ids": [], "fps": []}
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": [forged]})
        assert "m3" not in _disk(tc, pid)[0]

    def test_positive_control_normal_append(self, client):
        tc, pid = client
        _seed(tc, pid)
        _seed(tc, pid, BASE + [M(6, "human", "q3")], v=2000)
        ids, rec = _disk(tc, pid)
        assert ids[-1] == "m6" and len(ids) == 7
        assert rec["_version"] == 2000   # nothing differed, client version stands
