"""
Tests for app.utils.chat_context_files — the read half of model-driven
context management.

The write half (context_add_file persisting to additionalFiles) was
already covered by tests/test_context_management_tools.py; that suite
passed while the feature did nothing, because nothing asserted the value
ever reached a prompt.  These tests cover resolution, the union, and the
write-time token limit.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.chat_context_files import (
    estimate_file_tokens,
    exceeds_auto_add_limit,
    get_model_pinned_files,
    merge_context_files,
    resolve_auto_add_token_limit,
)


# --------------------------------------------------------------------------
# merge_context_files — pure, no fixtures needed
# --------------------------------------------------------------------------

class TestMergeContextFiles:
    def test_user_files_keep_order_and_come_first(self):
        merged, added = merge_context_files(["b.py", "a.py"], ["c.py"])
        assert merged == ["b.py", "a.py", "c.py"]
        assert added == ["c.py"]

    def test_no_model_files_leaves_user_list_untouched(self):
        merged, added = merge_context_files(["a.py", "b.py"], [])
        assert merged == ["a.py", "b.py"]
        assert added == []

    def test_overlap_is_not_duplicated_and_not_reported_as_added(self):
        merged, added = merge_context_files(["a.py"], ["a.py", "b.py"])
        assert merged == ["a.py", "b.py"]
        assert added == ["b.py"]

    def test_duplicates_within_each_list_are_collapsed(self):
        merged, _ = merge_context_files(["a.py", "a.py"], ["b.py", "b.py"])
        assert merged == ["a.py", "b.py"]

    def test_none_inputs_are_tolerated(self):
        assert merge_context_files(None, None) == ([], [])
        assert merge_context_files(None, ["a.py"]) == (["a.py"], ["a.py"])

    def test_non_string_and_empty_entries_are_dropped(self):
        merged, _ = merge_context_files(["", None, 5, "a.py"], [{}, "b.py"])
        assert merged == ["a.py", "b.py"]

    def test_absolute_paths_pass_through_unrewritten(self):
        # context_add_file stores absolute paths for out-of-project files;
        # `files` uses the same convention, so no rewriting must occur.
        merged, added = merge_context_files(["rel.py"], ["/opt/shared/x.py"])
        assert merged == ["rel.py", "/opt/shared/x.py"]
        assert added == ["/opt/shared/x.py"]


# --------------------------------------------------------------------------
# get_model_pinned_files
# --------------------------------------------------------------------------

@pytest.fixture
def chat_env(tmp_path):
    """Project root + ziya home with one registered project and one chat."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    projects_dir.mkdir(parents=True)

    project_id = "p_read_test"
    project_dir = projects_dir / project_id
    project_dir.mkdir()
    (project_dir / "project.json").write_text(json.dumps({
        "id": project_id,
        "name": "ReadTest",
        "path": str(project_root.resolve()),
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): project_id})
    )
    chats_dir = project_dir / "chats"
    chats_dir.mkdir()

    def write_chat(chat_id, additional):
        record = {
            "id": chat_id, "title": "t", "groupId": None,
            "contextIds": [], "skillIds": [],
            "additionalPrompt": None, "messages": [],
            "createdAt": 0, "lastActiveAt": 0,
        }
        if additional is not None:
            record["additionalFiles"] = additional
        (chats_dir / f"{chat_id}.json").write_text(json.dumps(record))

    with patch("app.utils.paths.get_ziya_home", return_value=ziya_home), \
         patch("app.context.get_project_root_or_none",
               return_value=str(project_root.resolve())):
        yield write_chat


class TestGetModelPinnedFiles:
    def test_returns_persisted_files(self, chat_env):
        chat_env("c1", ["src/main.py", "README.md"])
        assert get_model_pinned_files("c1") == ["src/main.py", "README.md"]

    def test_missing_chat_record_returns_empty(self, chat_env):
        assert get_model_pinned_files("does-not-exist") == []

    def test_absent_field_returns_empty(self, chat_env):
        chat_env("c2", None)
        assert get_model_pinned_files("c2") == []

    def test_none_conversation_id_returns_empty(self, chat_env):
        assert get_model_pinned_files(None) == []

    def test_corrupt_non_list_field_returns_empty(self, chat_env):
        chat_env("c3", "not-a-list")
        assert get_model_pinned_files("c3") == []

    def test_non_string_entries_are_filtered(self, chat_env):
        chat_env("c4", ["ok.py", None, 7, "", "also_ok.py"])
        assert get_model_pinned_files("c4") == ["ok.py", "also_ok.py"]

    def test_does_not_register_a_project_as_a_side_effect(self, tmp_path):
        """A read path must not create a PROJECT record.

        Asserted via the absence of any project.json, not the absence of
        _path_index.json: ProjectStorage.get_by_path rebuilds and saves
        that index on a miss, so the index file legitimately appears as a
        cache write.  Registration means a project directory with a
        project.json in it, and that must not happen here.
        """
        ziya_home = tmp_path / "home"
        projects = ziya_home / "projects"
        projects.mkdir(parents=True)
        with patch("app.utils.paths.get_ziya_home", return_value=ziya_home), \
             patch("app.context.get_project_root_or_none",
                   return_value=str(tmp_path / "unregistered")):
            assert get_model_pinned_files("cX") == []
        assert list(projects.glob("*/project.json")) == []


# --------------------------------------------------------------------------
# Token limit
# --------------------------------------------------------------------------

class TestAutoAddLimit:
    def test_limit_zero_disables_check(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 200_000)
        over, _ = exceeds_auto_add_limit(str(f), 0)
        assert over is False

    def test_negative_limit_disables_check(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 200_000)
        assert exceeds_auto_add_limit(str(f), -1)[0] is False

    def test_large_file_exceeds_small_limit(self, tmp_path):
        f = tmp_path / "big.py"
        f.write_text("# comment line\n" * 20_000)
        over, tokens = exceeds_auto_add_limit(str(f), 100)
        assert over is True
        assert tokens > 100

    def test_small_file_does_not_exceed(self, tmp_path):
        f = tmp_path / "small.py"
        f.write_text("x = 1\n")
        assert exceeds_auto_add_limit(str(f), 12500)[0] is False

    def test_unknown_size_never_blocks(self, tmp_path):
        # Mirrors the frontend contract: an unmeasurable file is allowed.
        missing = str(tmp_path / "nope.py")
        assert estimate_file_tokens(missing) == 0
        assert exceeds_auto_add_limit(missing, 10)[0] is False

    def test_default_limit_matches_model_default(self, tmp_path):
        from app.models.project import ContextManagementSettings
        with patch("app.context.get_project_root_or_none", return_value=None), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
            assert (resolve_auto_add_token_limit()
                    == ContextManagementSettings().auto_add_token_limit)

    def test_project_setting_overrides_default(self, tmp_path):
        ziya_home = tmp_path / "home"
        projects_dir = ziya_home / "projects"
        projects_dir.mkdir(parents=True)
        root = tmp_path / "proj"
        root.mkdir()
        pdir = projects_dir / "p1"
        pdir.mkdir()
        (pdir / "project.json").write_text(json.dumps({
            "id": "p1", "name": "P", "path": str(root.resolve()),
            "createdAt": 0, "lastAccessedAt": 0,
            "settings": {"contextManagement": {"auto_add_token_limit": 500}},
        }))
        (projects_dir / "_path_index.json").write_text(
            json.dumps({str(root.resolve()): "p1"})
        )
        with patch("app.utils.paths.get_ziya_home", return_value=ziya_home):
            assert resolve_auto_add_token_limit(str(root.resolve())) == 500
