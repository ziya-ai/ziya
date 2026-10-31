"""
Standing grants with a path-scope ladder (design/scope-consent.md D1–D4).

The defect: the panel's "this conversation / session / always" choice felt
like it learned nothing.  Exact-path matching meant every new file asked
again, and process memory meant a restart forgot every grant.  These tests
pin the replacement: grants match by prefix at the rung the human chose,
persist in <root>/.ziya/consent-grants.json, and the sentence the panel
shows is the one stored and echoed on ``consent_answered``.
"""

import asyncio
import json
import os

import pytest

from app.utils import consent_gate as cg
from app.utils import consent_grants as gr


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    for d in ("frontend/src/components", "frontend/src/utils", "app/utils", "docs"):
        (r / d).mkdir(parents=True)
    (r / "frontend/src/components/Panel.tsx").write_text("x")
    (r / "frontend/src/components/Other.tsx").write_text("x")
    (r / "frontend/src/utils/store.ts").write_text("x")
    (r / "app/utils/gate.py").write_text("x")
    (r / "docs/a.md").write_text("x")
    (r / "frontend/node_modules").mkdir()
    for i in range(50):
        (r / "frontend/node_modules" / f"m{i}.js").write_text("x")
    return str(r)


PANEL = "frontend/src/components/Panel.tsx"
OTHER = "frontend/src/components/Other.tsx"
STORE = "frontend/src/utils/store.ts"
GATE = "app/utils/gate.py"


# ── the ladder ────────────────────────────────────────────────────────────

def test_ladder_walks_from_the_file_to_the_project_root(root):
    ladder = gr.scope_ladder(root, [PANEL])
    assert [(r["kind"], r.get("path")) for r in ladder] == [
        ("file", None),
        ("dir", "frontend/src/components"),
        ("dir", "frontend/src"),
        ("dir", "frontend"),
        ("project", ""),
    ]
    assert ladder[0]["paths"] == [PANEL]
    by = {r.get("path"): r for r in ladder[1:]}
    assert by["frontend/src/components"]["files"] == 2
    assert by["frontend/src"]["files"] == 3
    assert by["frontend"]["files"] == 3, "node_modules is not counted"
    assert by[""]["files"] == 5


def test_ladder_for_several_files_starts_at_their_common_ancestor(root):
    ladder = gr.scope_ladder(root, [PANEL, STORE])
    assert ladder[0] == {"kind": "file", "paths": [PANEL, STORE]}
    assert ladder[1]["path"] == "frontend/src"
    assert [r.get("path") for r in ladder[1:]] == ["frontend/src", "frontend", ""]


def test_ladder_leaves_floor_paths_out(root):
    assert gr.scope_ladder(root, [".git/config", "../outside"]) == []
    ladder = gr.scope_ladder(root, [".git/config", PANEL])
    assert ladder[0]["paths"] == [PANEL]


def test_rules_are_attached_per_rung_and_duration(root):
    ladder = gr.ladder_with_rules(gr.scope_ladder(root, [PANEL]), "file_write")
    f, d, _, _, p = ladder
    assert f["rules"]["conversation"] == (
        "`file_write` may write `frontend/src/components/Panel.tsx` for the rest of this "
        "conversation without asking.")
    assert d["rules"]["project"] == (
        "`file_write` may write anything under `frontend/src/components/` in every "
        "conversation of this project, until revoked without asking.")
    assert "anywhere in this project" in p["rules"]["conversation"]
    assert "`.git`" in p["rules"]["conversation"]
    assert gr.rule_sentence("file_write", "once", None, [PANEL]) == "", "once stores nothing"
    assert gr.rule_sentence("git", "conversation", None, None, "commit") == (
        "`git` `commit` will run without asking for the rest of this conversation.")


def test_rung_for_only_accepts_what_the_ladder_offered(root):
    ladder = gr.scope_ladder(root, [PANEL])
    assert gr.rung_for(ladder, None) is None
    assert gr.rung_for(ladder, "frontend/src")["kind"] == "dir"
    assert gr.rung_for(ladder, "frontend/src/")["path"] == "frontend/src"
    assert gr.rung_for(ladder, "")["kind"] == "project"
    assert gr.rung_for(ladder, "app") is None, "not an ancestor: falls back to the exact file"
    assert gr.rung_for(ladder, "frontend/src/components/Panel.tsx") is None


# ── prefix matching ───────────────────────────────────────────────────────

def test_a_dir_grant_covers_new_files_under_it_and_nothing_beside_it(root):
    g = gr.GrantStore()
    g.remember_paths("conversation", "w", "c", root, [PANEL], rung="frontend/src")
    assert g.covers_paths("w", "c", root, [PANEL])
    assert g.covers_paths("w", "c", root, [OTHER]), "a file never shown, but under the rung"
    assert g.covers_paths("w", "c", root, [STORE])
    assert g.covers_paths("w", "c", root, ["frontend/src/new/deep/file.ts"]), "need not exist yet"
    assert not g.covers_paths("w", "c", root, [GATE])
    assert not g.covers_paths("w", "c", root, [PANEL, GATE]), "every path must be covered"
    assert not g.covers_paths("w", "c2", root, [PANEL]), "conversation grant: another chat is cold"
    assert not g.covers_paths("other", "c", root, [PANEL]), "per tool"


def test_a_file_grant_is_still_exact(root):
    g = gr.GrantStore()
    g.remember_paths("conversation", "w", "c", root, [PANEL], rung=None)
    assert g.covers_paths("w", "c", root, [PANEL])
    assert not g.covers_paths("w", "c", root, [OTHER])


def test_a_project_grant_covers_everything_but_the_floor(root):
    g = gr.GrantStore()
    g.remember_paths("project", "w", "c", root, [PANEL], rung="")
    assert g.covers_paths("w", "c2", root, [GATE, STORE, "brand/new.txt"]), "project: every chat"
    assert not g.covers_paths("w", "c", root, [".git/config"])
    assert not g.covers_paths("w", "c", root, ["../outside.txt"])
    assert not g.covers_paths("w", "c", str(os.path.dirname(root)), [GATE]), "bound to its root"


def test_once_and_floor_rungs_store_nothing(root):
    g = gr.GrantStore()
    assert g.remember_paths("once", "w", "c", root, [PANEL], rung="frontend") == []
    assert g.remember_paths("conversation", "w", "c", root, [PANEL], rung=".git") == []
    assert g.remember_paths("conversation", "w", None, root, [PANEL], rung="frontend") == [], \
        "a conversation grant needs a conversation"
    assert g.list("c", root) == []


# ── persistence ───────────────────────────────────────────────────────────

def test_grants_survive_a_new_store_via_the_project_file(root):
    g = gr.GrantStore()
    g.remember_paths("project", "file_write", "c", root, [PANEL], rung="frontend/src",
                     rule="the rule")
    g.remember("conversation", "git", "commit", "c", root, rule="commit rule")
    f = os.path.join(root, ".ziya", "consent-grants.json")
    assert os.path.isfile(f)
    data = json.loads(open(f).read())
    assert {x["tool"] for x in data["grants"]} == {"file_write", "git"}

    fresh = gr.GrantStore()  # a restarted server
    assert fresh.covers_paths("file_write", "c-other", root, [OTHER])
    assert fresh.covers("git", "commit", "c", root)
    assert not fresh.covers("git", "commit", "c-other", root), "conversation grant stays scoped"
    listed = fresh.list("c", root)
    assert sorted(x["rule"] for x in listed) == ["commit rule", "the rule"]
    assert next(x for x in listed if x["tool"] == "file_write")["path_display"] == "frontend/src"


def test_usage_is_counted_and_persisted(root):
    g = gr.GrantStore()
    g.remember_paths("project", "w", "c", root, [PANEL], rung="frontend")
    g.covers_paths("w", "c", root, [PANEL])
    g.covers_paths("w", "c", root, [STORE, OTHER])
    assert gr.GrantStore().list("c", root)[0]["used"] == 2, "one use per covering call"


def test_revoke_and_forget_conversation_remove_grants_and_persist(root):
    g = gr.GrantStore()
    kept = g.remember_paths("project", "w", "c", root, [PANEL], rung="frontend")[0]
    g.remember_paths("conversation", "w", "c", root, [GATE])
    g.remember_paths("conversation", "w", "c2", root, [GATE])
    assert len(g.list("c", root)) == 2
    g.forget_conversation("c")
    assert [x["id"] for x in g.list("c", root)] == [kept.id]
    assert len(gr.GrantStore().list("c2", root)) == 2, "c2's grant untouched, on disk too"
    assert g.revoke(kept.id, root)
    assert not g.revoke(kept.id, root)
    assert gr.GrantStore().list("c", root) == []


def test_a_missing_root_directory_keeps_grants_in_memory_only(tmp_path):
    g = gr.GrantStore()
    ghost = str(tmp_path / "nope")
    g.remember_paths("project", "w", "c", ghost, ["a.py"], rung="")
    assert g.covers_paths("w", "c", ghost, ["b.py"])
    assert not os.path.exists(ghost)


def test_an_unreadable_grants_file_means_no_grants(root):
    os.makedirs(os.path.join(root, ".ziya"))
    open(os.path.join(root, ".ziya", "consent-grants.json"), "w").write("{not json")
    assert gr.GrantStore().list("c", root) == []


# ── D3: the panel remembers the rung ──────────────────────────────────────

def test_suggested_rung_is_the_last_one_chosen_when_it_contains_the_new_path(root):
    g = gr.GrantStore()
    assert g.suggested_rung("c", "w", root, [OTHER]) is None
    g.note_rung("c", "w", root, "frontend/src")
    assert g.suggested_rung("c", "w", root, [OTHER]) == "frontend/src"
    assert g.suggested_rung("c", "w", root, [STORE, OTHER]) == "frontend/src"
    assert g.suggested_rung("c", "w", root, [GATE]) is None, "outside the rung: default"
    assert g.suggested_rung("c2", "w", root, [OTHER]) is None
    assert g.suggested_rung("c", "x", root, [OTHER]) is None
    g.note_rung("c", "w", root, "")
    assert g.suggested_rung("c", "w", root, [GATE]) == ""
    g.note_rung("c", "w", root, None)
    assert g.suggested_rung("c", "w", root, [GATE]) == "", "None (exact file) leaves the memory alone"


# ── the gate seam ─────────────────────────────────────────────────────────

class _Writer:
    name = "writer"
    scope_preflight = True

    def __init__(self, paths):
        self._paths = paths

    def preflight(self, **args):
        return {"op": "write", "outside_policy": [{"path": p, "reason": "x"} for p in self._paths]}


@pytest.fixture
def gate(tmp_path):
    cg.reset_for_tests(tmp_path / "consents")
    yield
    cg.reset_for_tests()


async def _run(paths, tid, root, conv="c", answer=None):
    gen = cg.consent_gate(tool_name="writer", tool_id=tid, args={}, conversation_id=conv,
                          tool=_Writer(paths), project_root=root)
    first = await gen.__anext__()
    if first["type"] != "consent_opened" or not answer:
        # Either decided silently, or the test only wants to know a panel
        # opened: draining further would wait on a human.
        await gen.aclose()
        return first, None, None
    cg.ledger().record_answer(first["request_id"], "approve", **answer)
    rest = [e async for e in gen]
    answered = next((e for e in rest if e["type"] == "consent_answered"), None)
    decision = next(e for e in rest if e["type"] == cg.DECISION_EVENT)
    return first, answered, decision


@pytest.mark.asyncio
async def test_opened_carries_the_ladder_and_answered_echoes_the_chosen_rule(gate, root):
    opened, answered, decision = await _run(
        [PANEL], "t1", root, answer={"scope": "conversation", "path_scope": "frontend/src"})
    ladder = opened["payload"]["scope_ladder"]
    assert [r.get("path") for r in ladder[1:]] == ["frontend/src/components", "frontend/src", "frontend", ""]
    assert "suggested_scope" not in opened["payload"], "first time: the panel defaults to the file"
    chosen = next(r for r in ladder if r.get("path") == "frontend/src")
    assert answered["rule"] == chosen["rules"]["conversation"]
    assert answered["path_scope"] == "frontend/src"
    assert decision["write_grant"] == {"root": root, "paths": [PANEL]}, "this call: exactly the shown path"

    # The point of the exercise: a DIFFERENT file under the rung does not ask.
    first, _, _ = await _run([OTHER], "t2", root)
    assert first["type"] == cg.DECISION_EVENT and first["approved"] and not first["asked"]
    assert first["write_grant"]["paths"] == [OTHER]
    # ...and one outside it does, preselected at the rung last chosen.
    first, _, _ = await _run([GATE], "t3", root)
    assert first["type"] == "consent_opened"
    assert "suggested_scope" not in first["payload"]
    first, _, _ = await _run([STORE], "t4", root, conv="c2")
    assert first["type"] == "consent_opened", "another conversation starts cold"


@pytest.mark.asyncio
async def test_a_rung_not_on_the_ladder_falls_back_to_the_exact_file(gate, root):
    _, answered, _ = await _run([PANEL], "t1", root,
                                answer={"scope": "conversation", "path_scope": "app"})
    assert answered["path_scope"] == "app"
    assert "`frontend/src/components/Panel.tsx`" in answered["rule"]
    first, _, _ = await _run([OTHER], "t2", root)
    assert first["type"] == "consent_opened"
    first, _, _ = await _run([GATE], "t3", root)
    assert first["type"] == "consent_opened", "'app' was never granted"


@pytest.mark.asyncio
async def test_once_at_a_wide_rung_stores_no_grant_but_the_panel_remembers_the_rung(gate, root):
    _, answered, _ = await _run([PANEL], "t1", root, answer={"scope": "once", "path_scope": "frontend"})
    assert answered["rule"] == ""
    first, _, _ = await _run([STORE], "t2", root)
    assert first["type"] == "consent_opened"
    assert first["payload"]["suggested_scope"] == "frontend"


@pytest.mark.asyncio
async def test_a_project_grant_survives_a_restart_of_the_gate(gate, root):
    await _run([PANEL], "t1", root, answer={"scope": "project", "path_scope": ""})
    cg.grants().reset()  # drop memory, keep the file: what a restart does
    first, _, _ = await _run([GATE], "t2", root, conv="c-later")
    assert first["type"] == cg.DECISION_EVENT and first["approved"]
    # The floor stays: a project grant never covers .git, so this is not
    # decided silently.  (It asks, with an empty ladder: nothing here is
    # grantable, and execution refuses the floor regardless of the answer.)
    first, _, _ = await _run([".git/HEAD"], "t3", root, conv="c-later")
    assert first["type"] == "consent_opened"
    assert first["payload"]["scope_ladder"] == []


def test_api_rejects_the_retired_scopes():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.api.consent import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    for scope in ("session", "always"):
        r = c.post("/api/consent/conv:c:t", json={"decision": "approve", "scope": scope})
        assert r.status_code == 422, scope
