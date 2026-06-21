"""Verify collect_global_chat_summaries stamps the owner projectId.

Regression for the ASR-folder demotion incident: a global summary that
reached the client owner-less was re-homed under the viewing project by
syncMerge, then the dual-write persisted a SHADOW copy into the viewing
project's dir, which shadowed the real owner copy in list_chats.
Stamping the true owner projectId on the summary breaks that chain.
"""
import json
from pathlib import Path
import pytest
import app.storage.global_items as gi


def _write_chat(chats_dir, cid, *, is_global=False, group_id=None,
                project_id=None, title="t"):
    rec = {"id": cid, "title": title, "createdAt": 1, "lastActiveAt": 2,
           "messages": []}
    if is_global:
        rec["isGlobal"] = True
    if group_id is not None:
        rec["groupId"] = group_id
    if project_id is not None:
        rec["projectId"] = project_id
    (chats_dir / f"{cid}.json").write_text(json.dumps(rec))


def _write_groups(chats_dir, groups):
    payload = {"version": 1, "groups": [
        {"id": gid, "name": gid, "createdAt": 1, "parentId": parent,
         "isGlobal": glob} for (gid, parent, glob) in groups]}
    (chats_dir / "_groups.json").write_text(json.dumps(payload))


@pytest.fixture
def home(tmp_path):
    gi._summary_cache.clear()
    gi._full_cache.clear()
    gi._group_global_cache.clear()
    (tmp_path / "projects").mkdir()
    return tmp_path


def _project(home, pid):
    cdir = home / "projects" / pid / "chats"
    cdir.mkdir(parents=True)
    return cdir


def test_summary_stamps_on_disk_projectid(home):
    cdir = _project(home, "OWNER")
    _write_chat(cdir, "c1", is_global=True, project_id="OWNER")
    out = gi.collect_global_chat_summaries(home, exclude_project_id="VIEWER")
    assert len(out) == 1
    assert out[0].model_dump().get("projectId") == "OWNER"


def test_summary_falls_back_to_owner_dir_name(home):
    # Owner-less on disk (legacy record) -> must still stamp the owning
    # project DIRECTORY name, never leave it for the client to infer.
    cdir = _project(home, "OWNER-DIR")
    _write_chat(cdir, "c1", is_global=True, project_id=None)
    out = gi.collect_global_chat_summaries(home, exclude_project_id="VIEWER")
    assert len(out) == 1
    assert out[0].model_dump().get("projectId") == "OWNER-DIR"


def test_inherited_global_also_stamped(home):
    # Folder-inherited global (own isGlobal:false) - the exact ASR shape.
    cdir = _project(home, "ASR-OWNER")
    _write_groups(cdir, [("asr", None, True)])
    _write_chat(cdir, "c1", is_global=False, group_id="asr",
                project_id="ASR-OWNER")
    out = gi.collect_global_chat_summaries(home, exclude_project_id="VIEWER")
    assert len(out) == 1
    d = out[0].model_dump()
    assert d.get("projectId") == "ASR-OWNER"
    assert d.get("groupId") == "asr"
    assert d.get("isGlobal") is True


def test_stamped_projectid_survives_cache_hit(home):
    # Second call hits the per-file summary cache; the cached summary must
    # already carry projectId (the cache stores the built ChatSummary).
    cdir = _project(home, "OWNER")
    _write_chat(cdir, "c1", is_global=True, project_id="OWNER")
    gi.collect_global_chat_summaries(home, exclude_project_id="VIEWER")
    out = gi.collect_global_chat_summaries(home, exclude_project_id="VIEWER")
    assert out[0].model_dump().get("projectId") == "OWNER"
