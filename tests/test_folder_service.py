"""Tests for the extracted folder_service module.

Validates that the folder caching, external path management, and file tree
operations work correctly after extraction from server.py.
"""
import os
import time
import copy
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


class TestBackgroundScanResultHandling:
    """Piece 2: cancelled/errored scans must NOT poison the cache; timeout
    partials ARE committed and served (so huge projects stay responsive)."""

    def setup_method(self):
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        svc._folder_cache.clear()
        svc._background_scan_threads.clear()
        du._scan_progress_by_dir.clear()
        du._folder_cache.clear()
        du._scan_progress["active"] = False
        du._scan_progress["cancelled"] = False

    def _run_bg_scan(self, directory, fake_result):
        """Drive the background_scan closure with a mocked scan result and
        return (folder_service cache entry, directory_util cache entry)."""
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        abs_dir = os.path.abspath(directory)
        with patch.object(du, 'get_folder_structure', return_value=fake_result), \
             patch.object(du, 'get_scan_progress', return_value={"active": False}):
            svc.get_cached_folder_structure(directory, [], 15, synchronous=False)
            t = svc._background_scan_threads.get(abs_dir)
            if t:
                t.join(timeout=5.0)
        return svc._folder_cache[abs_dir], du._folder_cache.get(abs_dir)

    def test_cancelled_result_discarded(self):
        """A scan cancelled mid-walk (_cancelled) is dropped, leaving
        scan_complete False so the next fetch re-scans."""
        entry, du_entry = self._run_bg_scan(
            "/tmp/ziya_cancel_proj",
            {"src": {"token_count": 1, "children": {}}, "_cancelled": True},
        )
        assert entry['scan_complete'] is False
        assert entry['data'] is None
        assert du_entry is None

    def test_estimation_cancel_error_dict_discarded(self):
        """A scan cancelled during estimation returns an error dict
        ({'error': ..., 'cancelled': True}) — not a tree — and is discarded."""
        entry, du_entry = self._run_bg_scan(
            "/tmp/ziya_err_proj",
            {"error": "Scan cancelled by user", "cancelled": True},
        )
        assert entry['scan_complete'] is False
        assert entry['data'] is None
        assert du_entry is None

    def test_timeout_partial_committed_and_served(self):
        """A timeout partial (_partial/_timeout) IS a usable tree and must be
        committed + cached so the project stays responsive."""
        partial = {"src": {"token_count": 1, "children": {}}, "_partial": True, "_timeout": True}
        entry, du_entry = self._run_bg_scan("/tmp/ziya_timeout_proj", partial)
        assert entry['scan_complete'] is True
        assert entry['data'] == partial
        assert entry['data'].get('_partial') is True
        assert du_entry is not None and du_entry['data'] == partial

    def test_clean_result_committed(self):
        """A normal completed scan is committed to both caches."""
        clean = {"src": {"token_count": 5, "children": {}}}
        entry, du_entry = self._run_bg_scan("/tmp/ziya_clean_proj", clean)
        assert entry['scan_complete'] is True
        assert entry['data'] == clean
        assert du_entry is not None


class TestServePartialTreeWhileScanning:
    """Piece 1B: an in-progress scan of THIS directory serves the live Phase-1
    partial tree tagged _stale_and_scanning, falling back to the empty
    sentinel only before Phase 1 has published."""

    def setup_method(self):
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        svc._folder_cache.clear()
        svc._background_scan_threads.clear()
        du._scan_progress_by_dir.clear()

    def test_serves_partial_with_stale_flag(self):
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        directory = os.path.abspath("/proj_serving")
        partial = {
            "src": {"token_count": 1, "children": {}},
            "docs": {"token_count": 0, "children": {}},
        }
        progress = {"active": True, "partial_tree": partial}
        with patch.object(du, 'get_scan_progress', return_value=progress):
            result = svc.get_cached_folder_structure(directory, [], 15)
        assert result.get('_stale_and_scanning') is True
        assert 'src' in result and 'docs' in result
        # The published partial dict itself must not be mutated with the flag.
        assert '_stale_and_scanning' not in partial

    def test_empty_sentinel_before_phase1_publishes(self):
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        directory = os.path.abspath("/proj_serving2")
        progress = {"active": True, "partial_tree": None}
        with patch.object(du, 'get_scan_progress', return_value=progress):
            result = svc.get_cached_folder_structure(directory, [], 15)
        assert result == {"_scanning": True, "children": {}}

    def test_externals_preserved_in_partial(self):
        """Externals pre-populated before the scan started ride along on the
        served partial tree."""
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        directory = os.path.abspath("/proj_serving3")
        svc._folder_cache[directory] = {
            'timestamp': 0,
            'data': {'[external]': {'children': {}, 'token_count': 5}},
            'scan_complete': False,
        }
        partial = {"src": {"token_count": 1, "children": {}}}
        progress = {"active": True, "partial_tree": partial}
        with patch.object(du, 'get_scan_progress', return_value=progress):
            result = svc.get_cached_folder_structure(directory, [], 15)
        assert result.get('_stale_and_scanning') is True
        assert '[external]' in result
        assert 'src' in result


class TestPhase1PartialPublish:
    """Piece 1A: get_folder_structure publishes the Phase-1 shallow tree to
    _scan_progress['partial_tree'] before Phase 2 and clears it on completion."""

    def setup_method(self):
        import app.utils.directory_util as du
        du._scan_progress["active"] = False
        du._scan_progress["cancelled"] = False
        du._scan_progress["partial_tree"] = None

    def test_publishes_shallow_tree_then_clears(self):
        import app.utils.directory_util as du
        from app.utils.directory_util import get_folder_structure, get_ignored_patterns

        with tempfile.TemporaryDirectory() as tmp:
            # A path deeper than BFS_DEPTH_THRESHOLD plus a shallow file.
            p = tmp
            for i in range(10):
                p = os.path.join(p, f"lvl{i}")
            os.makedirs(p)
            with open(os.path.join(p, "deep.py"), "w") as f:
                f.write("x = 1\n")
            with open(os.path.join(tmp, "top.py"), "w") as f:
                f.write("y = 2\n")

            captured = []

            # Recording dict snapshots partial_tree at the publish instant
            # (it is None before Phase 1 and None again after completion).
            class Rec(dict):
                def __setitem__(self, k, v):
                    super().__setitem__(k, v)
                    if k == 'partial_tree' and v:
                        captured.append(copy.deepcopy(v))

            real = du._scan_progress
            rec = Rec(real)
            du._scan_progress = rec
            try:
                ignored = get_ignored_patterns(tmp)
                result = get_folder_structure(tmp, ignored, max_depth=15)
            finally:
                du._scan_progress = real

            assert captured, "Phase-1 partial tree was never published"
            assert rec.get('partial_tree') is None, "partial_tree not cleared on completion"
            assert 'lvl0' in result
            assert any(('lvl0' in snap or 'top.py' in snap) for snap in captured)


class TestEstimationCancellation:
    """Pins the cancel-check inside estimate_directory_count/quick_count
    (directory_util.py:543). quick_count is a separate top-level function that
    reads the scan cancel flag with no directory threaded in today; the
    per-path (Piece 3) refactor must keep estimation short-circuiting to 0 when
    the scanning directory is cancelled. Empirically verified: a normal
    estimate returns >0, a cancelled one returns 0.
    """

    def setup_method(self):
        import app.utils.directory_util as du
        du._scan_progress["cancelled"] = False

    def teardown_method(self):
        import app.utils.directory_util as du
        du._scan_progress["cancelled"] = False

    def test_estimation_short_circuits_when_cancelled(self):
        import app.utils.directory_util as du
        from app.utils.directory_util import (
            estimate_directory_count, get_ignored_patterns,
        )
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                os.makedirs(os.path.join(tmp, f"d{i}", "sub"))
            ignored = get_ignored_patterns(tmp)

            du._scan_progress["cancelled"] = False
            normal = estimate_directory_count(tmp, ignored)
            assert normal > 0, "non-cancelled estimate should count directories"

            du._scan_progress["cancelled"] = True
            cancelled = estimate_directory_count(tmp, ignored)
            assert cancelled == 0, "cancelled estimate must short-circuit to 0"


class TestScanStateCharacterization:
    """Characterization tests locking in CURRENT scan-state behavior before the
    per-project refactor (Piece 3). These assert what the code does TODAY so the
    refactor can be proven behavior-preserving.

    Empirically verified against the live codebase before being written.
    NOTE: test_cross_project_request_cancels_active_scan documents the BUG that
    Piece 3 fixes; it will be updated (not deleted) when the refactor lands.
    """

    def setup_method(self):
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        svc._folder_cache.clear()
        svc._background_scan_threads.clear()
        du._scan_progress_by_dir.clear()
        du._folder_cache.clear()
        du._scan_progress.update(
            {"active": False, "cancelled": False, "partial_tree": None}
        )

    # --- directory_util scan-progress primitives ---

    def test_get_scan_progress_returns_copy(self):
        """Mutating the returned dict must not affect the module global."""
        import app.utils.directory_util as du
        snapshot = du.get_scan_progress()
        snapshot["active"] = True
        assert du._scan_progress["active"] is False

    def test_cancel_scan_sets_flag_and_returns_prior_active(self):
        import app.utils.directory_util as du
        du._scan_progress["active"] = True
        ret = du.cancel_scan()
        assert ret is True
        assert du._scan_progress["cancelled"] is True

    # --- folder_service serving paths ---

    def test_completed_cache_is_served(self):
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        d = os.path.abspath("/tmp/ziya_char_cachehit")
        tree = {'src': {'token_count': 1, 'children': {}}}
        svc._folder_cache[d] = {
            'timestamp': time.time(), 'data': tree, 'scan_complete': True
        }
        with patch.object(du, 'get_scan_progress', return_value={"active": False}):
            result = svc.get_cached_folder_structure(d, [], 15)
        assert result == tree

    def test_stale_cache_gets_stale_flag(self):
        """Cache older than 3600s is served with _stale=True."""
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        d = os.path.abspath("/tmp/ziya_char_stale")
        tree = {'src': {'token_count': 1, 'children': {}}}
        svc._folder_cache[d] = {
            'timestamp': time.time() - 4000, 'data': tree, 'scan_complete': True
        }
        with patch.object(du, 'get_scan_progress', return_value={"active": False}):
            result = svc.get_cached_folder_structure(d, [], 15)
        assert result.get("_stale") is True
        assert 'src' in result

    def test_dir_util_cache_is_adopted_and_promoted(self):
        """A tree present only in directory_util's cache is adopted into
        folder_service's cache and marked scan_complete."""
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        d = os.path.abspath("/tmp/ziya_char_adopt")
        tree = {'lib': {'token_count': 2, 'children': {}}}
        du._folder_cache[d] = {'timestamp': time.time(), 'data': tree}
        with patch.object(du, 'get_scan_progress', return_value={"active": False}):
            result = svc.get_cached_folder_structure(d, [], 15)
        assert result == tree
        assert svc._folder_cache[d]['scan_complete'] is True

    def test_synchronous_scan_writes_both_caches(self):
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        d = os.path.abspath("/tmp/ziya_char_sync")
        tree = {'app': {'token_count': 3, 'children': {}}}
        with patch.object(du, 'get_scan_progress', return_value={"active": False}), \
             patch.object(du, 'get_folder_structure', return_value=tree):
            result = svc.get_cached_folder_structure(d, [], 15, synchronous=True)
        assert result == tree
        assert svc._folder_cache[d]['data'] == tree
        assert du._folder_cache[d]['data'] == tree

    def test_same_dir_scanning_returns_scanning_sentinel(self):
        """A request for the dir currently scanning (no partial yet) gets the
        empty scanning sentinel and does NOT start a second scan."""
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        d = os.path.abspath("/tmp/ziya_char_samedir")
        svc._background_scan_dir = d
        with patch.object(du, 'get_scan_progress',
                          return_value={"active": True, "partial_tree": None}):
            result = svc.get_cached_folder_structure(d, [], 15)
        assert result == {"_scanning": True, "children": {}}

    def test_cross_project_scan_does_not_cancel_other_project(self):
        """Per-project (Piece 3) behavior — the FLIP of the old cross-project
        pin: requesting dir B while dir A is scanning must NOT cancel A. Each
        project scans in its own thread/slot, so a project switch no longer
        thrashes the other project's in-flight scan.
        """
        import app.services.folder_service as svc
        import app.utils.directory_util as du
        dir_a = os.path.abspath("/tmp/ziya_char_projA")
        dir_b = os.path.abspath("/tmp/ziya_char_projB")

        # A is mid-scan: its OWN per-directory slot is active.
        slot_a = du._progress_slot(dir_a)
        slot_a["active"] = True
        slot_a["cancelled"] = False

        fake_tree = {"x": {"token_count": 1, "children": {}}}
        try:
            # Requesting B reads B's own (inactive) slot, so B launches its own
            # background scan. A is never consulted and never cancelled.
            with patch.object(du, 'get_folder_structure', return_value=fake_tree):
                svc.get_cached_folder_structure(dir_b, [], 15, synchronous=False)
                t = svc._background_scan_threads.get(dir_b)
                if t:
                    t.join(timeout=5.0)
            assert du._progress_slot(dir_a)["cancelled"] is False, \
                "A's scan must NOT be cancelled by a request for B"
            assert dir_b in svc._background_scan_threads, \
                "B must get its own scan thread"
        finally:
            slot_a["active"] = False
