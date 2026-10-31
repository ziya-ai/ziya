"""
recall_tributary (design/tributary-fold.md, slice 3).

Pure-logic tests run against app/utils/tributary_recall.py directly.  The
tool-level tests (TestRecallTool) exercise the builtin through the same
request-scoped fixture the handoff tools use; they require
app/mcp/tools/tributary_tools.py and are skipped until it is present.
"""
import asyncio
import hashlib
import json
import os
import re
import time

import pytest

from app.utils import tool_history_rewrite as thr
from app.utils.tool_history_rewrite import FoldSettings, rewrite_tool_history
from app.utils import tributary_recall as tr

F4 = "`" * 4
FENCE_RE = re.compile(r"(?m)^`{3,}tool:")
NOW_MS = int(time.time() * 1000)


def _shell(cmd, body):
    return f"{F4}tool:run_shell_command|Shell: {cmd}|bash\n$ {cmd}\n{body}\n{F4}"


def _big(tag, n=40):
    return "\n".join(f"{tag} line {i:03d} some distinctive filler text here" for i in range(n))


PROSE0 = "Let me check the config."
FINAL = "The limit is 1,000,000 (models_config.py:681)."
QUEST = (PROSE0 + "\n\n" + _shell("grep -n token_limit cfg.py", _big("grep"))
         + "\n\nTwo hits; narrowing.\n\n" + _shell("sed -n 1,40p cfg.py", _big("sed"))
         + "\n\n" + FINAL)


def _record(*msgs, cid="c1"):
    return {"id": cid, "messages": [
        {"id": f"m{i}", "role": r, "content": c, "timestamp": NOW_MS,
         **({"muted": True} if mute else {})}
        for i, (r, c, mute) in enumerate(msgs)]}


def H(c="q", mute=False):
    return ("human", c, mute)


def A(c, mute=False):
    return ("assistant", c, mute)


def _handle_of(text):
    blocks = thr.find_tool_blocks(text)
    span = text[blocks[0].start:blocks[-1].end]
    return "tb-" + hashlib.sha1(span.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# The handle in a replayed placeholder resolves against the record
# ---------------------------------------------------------------------------

def test_placeholder_handle_resolves_to_record_span():
    hist = [{"type": "human", "content": "q"}, {"type": "ai", "content": QUEST},
            {"type": "human", "content": "q"}, {"type": "ai", "content": "a"},
            {"type": "human", "content": "q"}, {"type": "ai", "content": "b"}]
    replayed = rewrite_tool_history(hist, elide_redundant=False,
                                    fold=FoldSettings(enabled=True, pressure_ratio=0.0))[1]["content"]
    m = re.search(r'recall_tributary\(handle="(tb-[0-9a-f]{10})"\)', replayed)
    assert m, replayed
    rec = _record(H(), A(QUEST), H(), A("a"), H(), A("b"))
    out = tr.recall(rec, m.group(1))
    assert out["found"] is True
    assert out["message_index"] == 1 and out["n_calls"] == 2


def test_full_mode_returns_envelopes_not_fences_and_keeps_interim_prose():
    rec = _record(H(), A(QUEST))
    out = tr.recall(rec, _handle_of(QUEST), mode="full")
    assert out["found"]
    txt = out["text"]
    assert not FENCE_RE.search(txt)
    assert txt.count("‹tool_result") == 2 and txt.count("‹/tool_result›") == 2
    assert "grep line 007" in txt and "sed line 007" in txt
    assert "Two hits; narrowing." in txt            # interim prose is part of the span
    assert PROSE0 not in txt and FINAL not in txt   # the kept text is not


def test_commands_mode_is_labels_only():
    rec = _record(H(), A(QUEST))
    out = tr.recall(rec, _handle_of(QUEST), mode="commands")
    assert out["text"].splitlines() == ["Shell: grep -n token_limit cfg.py",
                                        "Shell: sed -n 1,40p cfg.py"]
    assert "grep line 007" not in out["text"]


def test_results_mode_groups_payloads_under_labels():
    rec = _record(H(), A(QUEST))
    out = tr.recall(rec, _handle_of(QUEST), mode="results")
    txt = out["text"]
    assert txt.index("## Shell: grep -n token_limit cfg.py") < txt.index("grep line 007") \
        < txt.index("## Shell: sed -n 1,40p cfg.py") < txt.index("sed line 007")
    assert "$ grep" not in txt                       # echoed command stripped from payload
    assert "Two hits; narrowing." not in txt


def test_grep_slices_lines_and_max_chars_caps():
    rec = _record(H(), A(QUEST))
    h = _handle_of(QUEST)
    out = tr.recall(rec, h, mode="results", grep=r"line 00[12]")
    lines = out["text"].splitlines()
    assert lines and all(re.search(r"line 00[12]", l) for l in lines)
    assert len(lines) == 4                           # 2 per call
    capped = tr.recall(rec, h, mode="full", max_chars=300)
    assert capped["truncated"] is True
    assert len(capped["text"]) < 400 and "more chars" in capped["text"]


def test_unknown_handle_is_not_found_not_error():
    rec = _record(H(), A(QUEST))
    out = tr.recall(rec, "tb-0000000000")
    assert out["found"] is False and not out.get("error")
    assert "not found in this conversation" in out["message"]


def test_bad_mode_is_an_error():
    assert tr.recall(_record(H(), A(QUEST)), _handle_of(QUEST), mode="x")["error"]


def test_muted_message_still_resolves():
    rec = _record(H(), A(QUEST, mute=True), H(), A("a"))
    out = tr.recall(rec, _handle_of(QUEST))
    assert out["found"] and out["muted"] is True


def test_span_below_fold_thresholds_still_resolves():
    # Single block, never folded — but the handle is content-addressed and
    # a user or an older replay may still quote it.
    single = "Reading.\n\n" + _shell("cat f.py", _big("f", 5)) + "\n\nDone."
    rec = _record(H(), A(single))
    assert tr.recall(rec, _handle_of(single))["found"]


def test_handle_prefix_optional():
    rec = _record(H(), A(QUEST))
    assert tr.recall(rec, _handle_of(QUEST)[3:])["found"]


def test_default_is_on():
    assert FoldSettings().enabled is True


def test_project_default_is_on():
    from app.models.project import ContextManagementSettings
    cm = ContextManagementSettings()
    if not hasattr(cm, "fold_tributaries"):
        pytest.skip("app/models/project.py fold_* fields not yet applied")
    assert cm.fold_tributaries is True
    assert thr.resolve_fold_settings("/nonexistent/project/root").enabled is True


# ---------------------------------------------------------------------------
# Prompt paragraph
# ---------------------------------------------------------------------------

def test_prompt_paragraph_follows_setting(monkeypatch):
    from app.utils import session_context_prompt as scp
    monkeypatch.setattr(thr, "resolve_fold_settings",
                        lambda *a, **k: FoldSettings(enabled=True))
    monkeypatch.setattr("app.mcp.builtin_tools.is_builtin_category_enabled",
                        lambda c: True)
    on = scp.build_session_context_section(project_root="/tmp/p")
    assert "### Folded tool turns" in on and "recall_tributary" in on
    assert "belongs in your final text or in a store" in on
    monkeypatch.setattr(thr, "resolve_fold_settings",
                        lambda *a, **k: FoldSettings(enabled=False))
    off = scp.build_session_context_section(project_root="/tmp/p")
    assert "Folded tool turns" not in off


def test_prompt_paragraph_is_turn_stable(monkeypatch):
    import datetime
    from app.utils import session_context_prompt as scp
    monkeypatch.setattr(thr, "resolve_fold_settings",
                        lambda *a, **k: FoldSettings(enabled=True))
    monkeypatch.setattr("app.mcp.builtin_tools.is_builtin_category_enabled",
                        lambda c: True)
    t = datetime.datetime(2026, 1, 1, 12, 0, 0)
    a = scp.build_session_context_section(project_root="/tmp/p", now=t)
    b = scp.build_session_context_section(project_root="/tmp/p",
                                          now=t + datetime.timedelta(hours=5))
    sec = lambda s: s[s.index("### Folded tool turns"):]
    assert sec(a) == sec(b)


# ---------------------------------------------------------------------------
# The tool, through the request-scoped record lookup
# ---------------------------------------------------------------------------

def _tool_cls():
    mod = pytest.importorskip("app.mcp.tools.tributary_tools",
                              reason="tributary_tools.py not yet applied")
    return mod.RecallTributaryTool


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    pid = "p_tb_" + os.urandom(3).hex()
    (projects_dir / pid / "chats").mkdir(parents=True)
    (projects_dir / pid / "project.json").write_text(json.dumps({
        "id": pid, "name": "T", "path": str(project_root.resolve()),
        "createdAt": NOW_MS, "lastAccessedAt": NOW_MS,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): pid}))

    def write(cid, rec):
        rec = {"groupId": None, "contextIds": [], "skillIds": [],
               "additionalFiles": [], "additionalPrompt": None,
               "createdAt": NOW_MS, "lastActiveAt": NOW_MS, "_version": NOW_MS, **rec}
        (projects_dir / pid / "chats" / f"{cid}.json").write_text(json.dumps(rec))

    cid = "c_tb_" + os.urandom(3).hex()
    write(cid, {**_record(H(), A(QUEST), H(), A("a"), cid=cid), "title": "mine"})
    other = "c_other_" + os.urandom(3).hex()
    other_quest = QUEST.replace("cfg.py", "other.py")
    write(other, {**_record(H(), A(other_quest), cid=other), "title": "theirs"})

    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    from app.storage import chat_index
    chat_index.invalidate()
    from app import context as _ctx
    t1 = _ctx._request_conversation_id.set(cid)
    t2 = _ctx._request_project_root.set(str(project_root.resolve()))
    yield {"cid": cid, "other_handle": _handle_of(other_quest)}
    _ctx._request_project_root.reset(t2)
    _ctx._request_conversation_id.reset(t1)


class TestRecallTool:
    def test_resolves_own_conversation(self, env):
        tool = _tool_cls()()
        out = run(tool.execute(handle=_handle_of(QUEST), mode="commands"))
        assert out["found"] and out["text"].startswith("Shell: grep")

    def test_handle_from_another_conversation_is_not_found(self, env):
        tool = _tool_cls()()
        out = run(tool.execute(handle=env["other_handle"]))
        assert out["found"] is False and "not found in this conversation" in out["message"]

    def test_registered_in_chat_history_category(self):
        _tool_cls()
        from app.mcp.builtin_tools import get_chat_history_tools
        names = {t.model_fields["name"].default if hasattr(t, "model_fields") else t.name
                 for t in get_chat_history_tools()}
        assert "recall_tributary" in names
