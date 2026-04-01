"""Tests for external path addition to the folder cache.

Verifies that add_external_path_to_cache writes to the same cache entry
that api_get_folders reads from, regardless of how the project root is
resolved (env var, request header, or cwd).
"""

import os
import tempfile
import threading
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_folder_cache():
    """Reset the server-side folder cache before each test."""
    import app.server as srv
    original_cache = srv._folder_cache.copy()
    original_paths = srv._explicit_external_paths.copy()
    srv._folder_cache.clear()
    srv._explicit_external_paths.clear()
    yield
    srv._folder_cache.clear()
    srv._folder_cache.update(original_cache)
    srv._explicit_external_paths.clear()
    srv._explicit_external_paths.update(original_paths)


class TestAddExternalPathToCache:
    """Tests for add_external_path_to_cache."""

    def test_adds_external_file_to_cache(self, tmp_path):
        """An external file should appear under [external] in the cache."""
        import app.server as srv

        project_dir = str(tmp_path / "project")
        os.makedirs(project_dir)
        ext_file = tmp_path / "outside" / "data.txt"
        ext_file.parent.mkdir(parents=True)
        ext_file.write_text("hello world")

        # Seed the cache entry so add_external_path_to_cache has something to write to
        srv._folder_cache[project_dir] = {
            'timestamp': 1.0,
            'data': {'app': {'token_count': 100, 'children': {}}},
        }

        with patch('app.context.get_project_root', return_value=project_dir):
            result = srv.add_external_path_to_cache(str(ext_file))

        assert result is True
        data = srv._folder_cache[project_dir]['data']
        assert '[external]' in data, "Expected [external] key in cache data"
        # Verify nested structure: [external] > outside > data.txt
        ext_children = data['[external]']['children']
        # Walk to the file
        path_parts = str(ext_file).strip('/').split('/')
        current = ext_children
        for part in path_parts[:-1]:
            assert part in current, f"Expected '{part}' in external path structure"
            current = current[part].get('children', {})
        assert path_parts[-1] in current, "File should be at leaf of external path"

    def test_adds_external_directory_to_cache(self, tmp_path):
        """An external directory should be recursively scanned into the cache."""
        import app.server as srv

        project_dir = str(tmp_path / "project")
        os.makedirs(project_dir)
        ext_dir = tmp_path / "ext_project"
        (ext_dir / "src").mkdir(parents=True)
        (ext_dir / "src" / "main.py").write_text("print('hi')")
        (ext_dir / "README.md").write_text("# Readme")

        srv._folder_cache[project_dir] = {
            'timestamp': 1.0,
            'data': {'app': {'token_count': 50, 'children': {}}},
        }

        with patch('app.context.get_project_root', return_value=project_dir):
            result = srv.add_external_path_to_cache(str(ext_dir))

        assert result is True
        data = srv._folder_cache[project_dir]['data']
        assert '[external]' in data

        # Walk to the directory entry
        ext_children = data['[external]']['children']
        path_parts = str(ext_dir).strip('/').split('/')
        current = ext_children
        for part in path_parts:
            assert part in current, f"Expected '{part}' in path"
            current = current[part].get('children', {})

        # Verify children were scanned
        assert 'src' in current, "Expected 'src' subdirectory"
        assert 'README.md' in current, "Expected 'README.md' file"
        assert 'main.py' in current['src'].get('children', {}), "Expected 'main.py' in src/"

    def test_cache_key_matches_api_get_folders_key(self, tmp_path):
        """Cache key used by add_external_path_to_cache must match the
        key that get_cached_folder_structure uses when called with
        the same project path."""
        import app.server as srv

        project_dir = str(tmp_path / "myproject")
        os.makedirs(project_dir)
        ext_file = tmp_path / "outside.txt"
        ext_file.write_text("data")

        # Simulate what api_get_folders does: abspath of project_path param
        api_cache_key = os.path.abspath(project_dir)

        # Pre-seed cache for this key (as if a scan already ran)
        srv._folder_cache[api_cache_key] = {
            'timestamp': 1.0,
            'data': {'src': {'token_count': 10, 'children': {}}},
        }

        # add_external_path_to_cache should write to the SAME key
        with patch('app.context.get_project_root', return_value=project_dir):
            srv.add_external_path_to_cache(str(ext_file))

        # Verify the api_get_folders cache key has the external data
        assert '[external]' in srv._folder_cache[api_cache_key]['data'], \
            "External path must be in the same cache entry that api_get_folders reads"

    def test_no_cache_entry_creates_one(self, tmp_path):
        """When no cache entry exists, add_external_path_to_cache creates one."""
        import app.server as srv

        project_dir = str(tmp_path / "fresh_project")
        os.makedirs(project_dir)
        ext_file = tmp_path / "ext.txt"
        ext_file.write_text("content")

        # No pre-seeded cache
        assert project_dir not in srv._folder_cache

        with patch('app.context.get_project_root', return_value=project_dir):
            result = srv.add_external_path_to_cache(str(ext_file))

        assert result is True
        assert project_dir in srv._folder_cache
        assert '[external]' in srv._folder_cache[project_dir]['data']

    def test_multiple_external_paths_coexist(self, tmp_path):
        """Adding multiple external paths accumulates under [external]."""
        import app.server as srv

        project_dir = str(tmp_path / "proj")
        os.makedirs(project_dir)
        file_a = tmp_path / "a" / "file_a.txt"
        file_b = tmp_path / "b" / "file_b.txt"
        file_a.parent.mkdir(parents=True)
        file_b.parent.mkdir(parents=True)
        file_a.write_text("aaa")
        file_b.write_text("bbb")

        srv._folder_cache[project_dir] = {
            'timestamp': 1.0,
            'data': {},
        }

        with patch('app.context.get_project_root', return_value=project_dir):
            srv.add_external_path_to_cache(str(file_a))
            srv.add_external_path_to_cache(str(file_b))

        ext = srv._folder_cache[project_dir]['data']['[external]']['children']
        # Both paths should exist under their respective intermediate dirs
        path_a_parts = str(file_a).strip('/').split('/')
        path_b_parts = str(file_b).strip('/').split('/')
        # Just check the distinguishing directory names exist
        a_dir = path_a_parts[-2]  # 'a'
        b_dir = path_b_parts[-2]  # 'b'
        # Walk to the level that contains a/ and b/
        current = ext
        for part in path_a_parts[:-2]:
            current = current.get(part, {}).get('children', {})
        assert a_dir in current, f"Expected dir '{a_dir}' for first external path"
        assert b_dir in current, f"Expected dir '{b_dir}' for second external path"
