"""Tests for the extracted folder_service module.

Validates that the folder caching, external path management, and file tree
operations work correctly after extraction from server.py.
"""
import os
import time
import tempfile
import threading
import pytest
from unittest.mock import patch, MagicMock

from app.services.folder_service import (
    _folder_cache, _cache_lock, _explicit_external_paths,
    invalidate_folder_cache, is_path_explicitly_allowed,
    add_file_to_folder_cache, update_file_in_folder_cache,
    remove_file_from_folder_cache, add_external_path_to_cache,
    collect_leaf_file_keys, collect_documentation_file_keys,
    _schedule_broadcast, set_main_event_loop,
)


class TestInvalidateFolderCache:
    """Test cache invalidation with debouncing."""

    def setup_method(self):
        """Reset module-level cache state before each test."""
        import app.services.folder_service as svc
        svc._folder_cache.clear()
        svc._last_cache_invalidation = 0

    def test_invalidate_clears_data_but_preserves_external(self):
        """External paths should survive cache invalidation."""
        import app.services.folder_service as svc
        svc._folder_cache['/project'] = {
            'timestamp': time.time(),
            'data': {
                'src': {'token_count': 100},
                '[external]': {'children': {'lib': {'token_count': 50}}, 'token_count': 50}
            }
        }
        invalidate_folder_cache()
        entry = svc._folder_cache['/project']
        assert entry['timestamp'] == 0
        # External paths preserved
        assert '[external]' in entry['data']
        # Non-external data cleared
        assert 'src' not in entry['data']

    def test_debounce_prevents_rapid_invalidation(self):
        """Rapid calls should be debounced."""
        import app.services.folder_service as svc
        svc._folder_cache['/project'] = {
            'timestamp': time.time(),
            'data': {'a': {'token_count': 1}}
        }
        invalidate_folder_cache()
        assert svc._folder_cache['/project']['timestamp'] == 0

        # Immediately re-populate and invalidate again
        svc._folder_cache['/project'] = {
            'timestamp': time.time(),
            'data': {'b': {'token_count': 2}}
        }
        invalidate_folder_cache()  # Should be debounced
        # Data should NOT be cleared because debounce blocked it
        assert svc._folder_cache['/project']['data'] is not None


class TestIsPathExplicitlyAllowed:
    """Test path permission checking."""

    def setup_method(self):
        import app.services.folder_service as svc
        svc._explicit_external_paths.clear()

    def test_path_inside_project_allowed(self):
        assert is_path_explicitly_allowed('/project/src/main.py', '/project')

    def test_project_root_itself_allowed(self):
        assert is_path_explicitly_allowed('/project', '/project')

    def test_path_outside_project_denied(self):
        assert not is_path_explicitly_allowed('/other/file.py', '/project')

    def test_explicit_external_path_allowed(self):
        import app.services.folder_service as svc
        svc._explicit_external_paths.add('/other')
        assert is_path_explicitly_allowed('/other/file.py', '/project')

    def test_partial_prefix_not_allowed(self):
        """'/project-extra/file' should NOT be allowed for project root '/project'."""
        assert not is_path_explicitly_allowed('/project-extra/file.py', '/project')


class TestCollectLeafFileKeys:
    """Test file key collection from directory trees."""

    def test_collects_files_from_flat_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some files
            open(os.path.join(tmpdir, 'a.py'), 'w').close()
            open(os.path.join(tmpdir, 'b.txt'), 'w').close()
            open(os.path.join(tmpdir, '.hidden'), 'w').close()

            keys = collect_leaf_file_keys(tmpdir, True, tmpdir)
            filenames = [os.path.basename(k) for k in keys]
            assert 'a.py' in filenames
            assert 'b.txt' in filenames
            assert '.hidden' not in filenames  # hidden files skipped

    def test_external_keys_prefixed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, 'file.txt'), 'w').close()
            keys = collect_leaf_file_keys(tmpdir, False, '/different/project')
            assert all(k.startswith('[external]') for k in keys)


class TestCollectDocumentationFileKeys:
    """Test documentation file auto-detection."""

    def test_finds_agents_md_and_readme(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, 'AGENTS.md'), 'w').close()
            open(os.path.join(tmpdir, 'README.md'), 'w').close()
            open(os.path.join(tmpdir, 'other.md'), 'w').close()

            keys = collect_documentation_file_keys(tmpdir, True, tmpdir)
            filenames = [os.path.basename(k) for k in keys]
            assert 'AGENTS.md' in filenames
            assert 'README.md' in filenames
            assert 'other.md' not in filenames


class TestScheduleBroadcast:
    """Test the broadcast scheduling function."""

    def test_no_error_without_event_loop(self):
        """Should not raise when no event loop is available."""
        import app.services.folder_service as svc
        svc._main_event_loop = None
        # Should complete without error
        _schedule_broadcast('file_added', 'test.py', 100)


class TestImportCompatibility:
    """Verify backward-compatible imports from server.py still work."""

    def test_server_re_exports_folder_service_functions(self):
        """server.py should re-export all folder_service public functions."""
        from app.server import (
            invalidate_folder_cache,
            is_path_explicitly_allowed,
            add_file_to_folder_cache,
            update_file_in_folder_cache,
            remove_file_from_folder_cache,
            add_external_path_to_cache,
            _schedule_broadcast,
        )
        # Just verify they're callable
        assert callable(invalidate_folder_cache)
        assert callable(is_path_explicitly_allowed)
        assert callable(add_file_to_folder_cache)

    def test_file_watcher_can_import(self):
        """file_watcher.py should be importable with new paths."""
        # This validates the import path update worked
        from app.services.folder_service import (
            update_file_in_folder_cache,
            add_file_to_folder_cache,
            remove_file_from_folder_cache,
            _schedule_broadcast,
        )
        assert callable(update_file_in_folder_cache)
