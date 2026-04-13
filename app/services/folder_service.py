"""
Folder and file tree cache management service.

Extracted from server.py to provide a clean service boundary for:
- Folder structure caching with background scanning
- File tree incremental updates (add/modify/remove)
- External path management for files outside project root
- Real-time WebSocket broadcasting of file tree changes

All state is module-level (singleton pattern) — functions are stateless
operations on that shared state, safe for concurrent access via locks.
"""
import asyncio
import os
import re
import time
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from app.utils.logging_utils import logger
from app.utils.directory_util import get_ignored_patterns


# Folder structure cache, keyed by absolute directory path
_folder_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

# External paths explicitly added by the user (outside project root)
_explicit_external_paths: set = set()

# Background folder scan state
_background_scan_thread = None
_background_scan_dir = None

# Cache invalidation debouncing
_last_cache_invalidation = 0
_cache_invalidation_debounce = 2.0  # seconds

# Track which projects have had external paths restored this session
_restored_projects: set = set()

# Active WebSocket connections for file tree updates
active_file_tree_connections: set = set()

# Reference to the main event loop for cross-thread async scheduling.
# Set by server.py during lifespan startup.
_main_event_loop = None


def set_main_event_loop(loop):
    """Called by server.py during startup to provide the event loop reference."""
    global _main_event_loop
    _main_event_loop = loop


async def broadcast_file_tree_update(event_type: str, rel_path: str, token_count: int = 0):
    """Broadcast file tree updates to all connected WebSocket clients."""
    if not active_file_tree_connections:
        logger.debug(f"No active WebSocket connections for file tree updates - {event_type}: {rel_path}")
        return

    logger.info(f"Broadcasting {event_type} for {rel_path} to {len(active_file_tree_connections)} client(s)")

    message = {
        'type': event_type,
        'path': rel_path,
        'token_count': token_count,
        'timestamp': int(time.time() * 1000)
    }

    disconnected = set()
    for ws in active_file_tree_connections:
        try:
            await ws.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to send to client: {e}")
            disconnected.add(ws)

    for ws in disconnected:
        active_file_tree_connections.discard(ws)


def _schedule_broadcast(event_type: str, rel_path: str, token_count: int = 0):
    """Schedule a broadcast, handling the case where no event loop is running."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_file_tree_update(event_type, rel_path, token_count))
    except RuntimeError:
        if _main_event_loop is not None and _main_event_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                broadcast_file_tree_update(event_type, rel_path, token_count),
                _main_event_loop
            )
        else:
            logger.debug(f"Skipping broadcast for {event_type}: {rel_path} (no main event loop)")


def invalidate_folder_cache():
    """Invalidate the folder structure cache with debouncing. Drops oversized external paths."""
    global _last_cache_invalidation
    current_time = time.time()

    with _cache_lock:
        if current_time - _last_cache_invalidation < _cache_invalidation_debounce:
            return

        for dir_key in list(_folder_cache.keys()):
            entry = _folder_cache[dir_key]
            external_paths = None
            if entry.get('data') and '[external]' in entry['data']:
                ext = entry['data']['[external]']
                ext_str_len = len(str(ext)) if ext else 0
                if ext_str_len < 1_000_000:
                    external_paths = ext
                else:
                    logger.warning(f"Dropping oversized external paths ({ext_str_len} chars) during cache invalidation")
            if external_paths is not None:
                entry['data'] = {'[external]': external_paths}
            else:
                entry['data'] = None
            entry['timestamp'] = 0
        logger.debug(f"Cache invalidated for {len(_folder_cache)} project(s)")
        _last_cache_invalidation = current_time

    # Also clear directory_util's cache to prevent stale data promotion
    try:
        import app.utils.directory_util as dir_util
        dir_util._folder_cache.clear()
    except Exception:
        pass


def is_path_explicitly_allowed(resolved_path: str, user_codebase_dir: str) -> bool:
    """
    Return True if resolved_path is permitted for file operations.

    A path is allowed when it is:
      1. Inside the project root (normal case), OR
      2. Under a path that was explicitly added via /api/add-explicit-paths.
    """
    if resolved_path.startswith(user_codebase_dir + os.sep) or resolved_path == user_codebase_dir:
        return True
    for external in _explicit_external_paths:
        if resolved_path.startswith(external + os.sep) or resolved_path == external:
            return True
    return False


def add_file_to_folder_cache(rel_path: str, base_dir: str = None) -> bool:
    """Add a newly created file to the folder cache without full rescan."""
    if base_dir:
        project_root = os.path.abspath(base_dir)
    else:
        from app.context import get_project_root
        project_root = os.path.abspath(get_project_root())
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False

    try:
        full_path = os.path.join(project_root, rel_path)
        from app.utils.directory_util import estimate_tokens_fast
        token_count = estimate_tokens_fast(full_path)
        path_parts = rel_path.split(os.sep)

        with _cache_lock:
            current_level = entry['data']
            for part in path_parts[:-1]:
                if part not in current_level:
                    current_level[part] = {'children': {}, 'token_count': 0}
                node = current_level[part]
                if 'children' not in node:
                    node['children'] = {}
                current_level = node['children']
            filename = path_parts[-1]
            current_level[filename] = {'token_count': token_count}
            logger.info(f"Added file to cache: {rel_path} ({token_count} tokens)")
            _schedule_broadcast('file_added', rel_path, token_count)
            return True

    except Exception as e:
        logger.error(f"Failed to add file to cache: {rel_path}, error: {e}")
        return False


def update_file_in_folder_cache(rel_path: str, base_dir: str = None) -> bool:
    """Update token count for modified file in cache."""
    if base_dir:
        project_root = os.path.abspath(base_dir)
    else:
        from app.context import get_project_root
        project_root = os.path.abspath(get_project_root())
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False

    try:
        full_path = os.path.join(project_root, rel_path)
        from app.utils.directory_util import estimate_tokens_fast
        token_count = estimate_tokens_fast(full_path)
        path_parts = rel_path.split(os.sep)

        with _cache_lock:
            current_level = entry['data']
            for part in path_parts[:-1]:
                if part not in current_level:
                    return False
                node = current_level[part]
                if 'children' not in node:
                    return False
                current_level = node['children']
            filename = path_parts[-1]
            if filename in current_level:
                current_level[filename]['token_count'] = token_count
                logger.debug(f"Updated file in cache: {rel_path} ({token_count} tokens)")
                _schedule_broadcast('file_modified', rel_path, token_count)
                return True
    except Exception as e:
        logger.error(f"Failed to update file in cache: {rel_path}, error: {e}")
    return False


def remove_file_from_folder_cache(rel_path: str, base_dir: str = None) -> bool:
    """Remove deleted file from cache."""
    if base_dir:
        project_root = os.path.abspath(base_dir)
    else:
        from app.context import get_project_root
        project_root = os.path.abspath(get_project_root())
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False

    try:
        path_parts = rel_path.split(os.sep)
        with _cache_lock:
            current_level = entry['data']
            for part in path_parts[:-1]:
                if part not in current_level:
                    return False
                node = current_level[part]
                if 'children' not in node:
                    return False
                current_level = node['children']
            filename = path_parts[-1]
            if filename in current_level:
                del current_level[filename]
                logger.info(f"Removed file from cache: {rel_path}")
                _schedule_broadcast('file_deleted', rel_path, 0)
                return True
    except Exception as e:
        logger.error(f"Failed to remove file from cache: {rel_path}, error: {e}")
    return False


def add_external_path_to_cache(full_path: str) -> bool:
    """Add an external file or directory (outside workspace) to the folder cache."""
    from app.context import get_project_root
    user_codebase_dir = os.path.abspath(get_project_root())
    real_codebase = os.path.realpath(user_codebase_dir)

    with _cache_lock:
        if user_codebase_dir not in _folder_cache:
            _folder_cache[user_codebase_dir] = {'timestamp': 0, 'data': None}
        if _folder_cache[user_codebase_dir]['data'] is None:
            _folder_cache[user_codebase_dir]['data'] = {}
            _folder_cache[user_codebase_dir]['timestamp'] = time.time()
            logger.info("Initialized empty folder cache for external path addition")

    try:
        from app.utils.directory_util import estimate_tokens_fast

        if os.path.isdir(full_path):
            _ext_entry_count = 0
            _EXT_MAX_ENTRIES = 10_000

            def scan_directory(dir_path, max_depth=10, current_depth=0):
                nonlocal _ext_entry_count
                if current_depth >= max_depth or _ext_entry_count >= _EXT_MAX_ENTRIES:
                    return {'children': {}, 'token_count': 0}
                real_dir = os.path.realpath(dir_path)
                if real_dir.startswith(real_codebase + os.sep) or real_dir == real_codebase:
                    logger.info(f"Skipping '{dir_path}' — resolves into project root")
                    return {'children': {}, 'token_count': 0}
                result = {'children': {}, 'token_count': 0}
                total_tokens = 0
                try:
                    for entry_name in os.listdir(dir_path):
                        if entry_name.startswith('.'):
                            continue
                        _ext_entry_count += 1
                        if _ext_entry_count >= _EXT_MAX_ENTRIES:
                            logger.warning(f"External path scan hit {_EXT_MAX_ENTRIES} entry limit at {dir_path}")
                            break
                        entry_path = os.path.join(dir_path, entry_name)
                        try:
                            if os.path.isfile(entry_path):
                                tc = estimate_tokens_fast(entry_path)
                                result['children'][entry_name] = {'token_count': tc}
                                total_tokens += tc
                            elif os.path.isdir(entry_path):
                                sub = scan_directory(entry_path, max_depth, current_depth + 1)
                                result['children'][entry_name] = sub
                                total_tokens += sub.get('token_count', 0)
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    pass
                result['token_count'] = total_tokens
                return result

            dir_structure = scan_directory(full_path)
            token_count = dir_structure.get('token_count', 0)
        else:
            token_count = estimate_tokens_fast(full_path)
            dir_structure = None

        with _cache_lock:
            if '[external]' not in _folder_cache[user_codebase_dir]['data']:
                _folder_cache[user_codebase_dir]['data']['[external]'] = {'children': {}, 'token_count': 0}
            path_parts = full_path.strip('/').split('/')
            current_level = _folder_cache[user_codebase_dir]['data']['[external]']['children']
            for part in path_parts[:-1]:
                if part not in current_level:
                    current_level[part] = {'children': {}, 'token_count': 0}
                if 'children' not in current_level[part]:
                    current_level[part]['children'] = {}
                current_level = current_level[part]['children']
            filename = path_parts[-1]
            if dir_structure:
                current_level[filename] = dir_structure
            else:
                current_level[filename] = {'token_count': token_count}
            logger.info(f"Added external path to cache: {full_path} ({token_count} tokens)")
            _schedule_broadcast('file_added', f"[external]{full_path}", token_count)
            return True

    except Exception as e:
        logger.error(f"Failed to add external path to cache: {full_path}, error: {e}")
        return False


def add_directory_to_folder_cache(rel_path: str, full_path: str, is_inside_workspace: bool) -> bool:
    """Add a directory and its contents to the folder cache."""
    from app.context import get_project_root
    project_root = get_project_root()
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False

    try:
        from app.utils.directory_util import estimate_tokens_fast

        if not is_inside_workspace:
            return add_external_path_to_cache(full_path)

        real_codebase = os.path.realpath(project_root)
        _scan_visited = set()
        _scan_deadline = time.time() + 15.0

        def scan_directory(dir_path, max_depth=10, current_depth=0):
            if current_depth >= max_depth or time.time() > _scan_deadline:
                return {'children': {}, 'token_count': 0}
            try:
                real_dir = os.path.realpath(dir_path)
            except (OSError, ValueError):
                return {'children': {}, 'token_count': 0}
            if real_dir in _scan_visited:
                return {'children': {}, 'token_count': 0}
            if real_dir.startswith(real_codebase + os.sep) or real_dir == real_codebase:
                return {'children': {}, 'token_count': 0}
            _scan_visited.add(real_dir)
            result = {'children': {}, 'token_count': 0}
            total_tokens = 0
            try:
                for entry_name in os.listdir(dir_path):
                    if entry_name.startswith('.'):
                        continue
                    entry_path = os.path.join(dir_path, entry_name)
                    try:
                        if os.path.isfile(entry_path):
                            tc = estimate_tokens_fast(entry_path)
                            result['children'][entry_name] = {'token_count': tc}
                            total_tokens += tc
                        elif os.path.isdir(entry_path):
                            sub = scan_directory(entry_path, max_depth, current_depth + 1)
                            result['children'][entry_name] = sub
                            total_tokens += sub.get('token_count', 0)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                pass
            result['token_count'] = total_tokens
            return result

        dir_structure = scan_directory(full_path)

        with _cache_lock:
            path_parts = rel_path.split(os.sep)
            current_level = entry['data']
            for part in path_parts[:-1]:
                if part not in current_level:
                    current_level[part] = {'children': {}, 'token_count': 0}
                if 'children' not in current_level[part]:
                    current_level[part]['children'] = {}
                current_level = current_level[part]['children']
            dirname = path_parts[-1] if path_parts else os.path.basename(full_path)
            if 'children' not in dir_structure:
                dir_structure['children'] = {}
            current_level[dirname] = dir_structure
            logger.info(f"Added directory to cache: {rel_path} ({dir_structure.get('token_count', 0)} tokens)")
            _schedule_broadcast('file_added', rel_path, dir_structure.get('token_count', 0))
            return True

    except Exception as e:
        logger.error(f"Failed to add directory to cache: {rel_path}, error: {e}")
        return False


def collect_leaf_file_keys(dir_path: str, is_inside_workspace: bool, user_codebase_dir: str, max_files: int = 5000) -> list:
    """Walk a directory and return tree keys for every leaf file."""
    keys = []
    count = 0
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in files:
            if fname.startswith('.'):
                continue
            count += 1
            if count > max_files:
                logger.warning(f"collect_leaf_file_keys hit {max_files} file limit for {dir_path}")
                return keys
            full = os.path.join(root, fname)
            if is_inside_workspace:
                key = os.path.relpath(full, user_codebase_dir)
            else:
                key = "[external]" + full
            keys.append(key)
    return keys


def collect_documentation_file_keys(dir_path: str, is_inside_workspace: bool, user_codebase_dir: str, max_depth: int = 10) -> list:
    """Walk a directory and return tree keys for AGENTS.md and README.md files."""
    DOC_FILES = {'AGENTS.md', 'README.md'}
    keys: list = []
    for root, dirs, files in os.walk(dir_path):
        depth = root[len(dir_path):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in files:
            if fname in DOC_FILES:
                full = os.path.join(root, fname)
                if is_inside_workspace:
                    key = os.path.relpath(full, user_codebase_dir)
                else:
                    key = "[external]" + full
                keys.append(key)
    if keys:
        logger.info(f"Auto-context: found {len(keys)} documentation file(s) in {dir_path}")
    return keys


def get_cached_folder_structure(directory: str, ignored_patterns, max_depth: int, synchronous: bool = False) -> Dict[str, Any]:
    """Get folder structure with caching and background scanning.

    Checks both this module's _folder_cache and directory_util's cache
    to avoid redundant scans. Writes results to both.
    """
    global _background_scan_thread, _background_scan_dir

    from app.utils.directory_util import get_folder_structure, get_scan_progress
    import app.utils.directory_util as dir_util

    directory = os.path.abspath(directory)

    if directory not in _folder_cache:
        _folder_cache[directory] = {'timestamp': 0, 'data': None}

    cache_entry = _folder_cache[directory]
    current_time = time.time()
    cache_age = current_time - cache_entry['timestamp']

    scan_status = get_scan_progress()
    is_scanning = scan_status.get("active", False)

    if is_scanning and _background_scan_dir == directory:
        return {"_scanning": True, "children": {}}

    if is_scanning and _background_scan_dir != directory:
        logger.info(f"Cancelling scan for {_background_scan_dir}, switching to {directory}")
        from app.utils.directory_util import cancel_scan
        cancel_scan()
        if _background_scan_thread and _background_scan_thread.is_alive():
            _background_scan_thread.join(timeout=2.0)

    if cache_entry['data'] is not None:
        if cache_age > 3600:
            return {**cache_entry['data'], "_stale": True}
        return cache_entry['data']

    dir_util_entry = dir_util._folder_cache.get(directory)
    if dir_util_entry and isinstance(dir_util_entry, dict):
        data = dir_util_entry.get('data')
        if data is not None:
            cache_entry['data'] = data
            cache_entry['timestamp'] = dir_util_entry.get('timestamp', current_time)
            return data

    if synchronous:
        logger.info(f"Synchronous folder scan for {directory}")
        try:
            result = get_folder_structure(directory, ignored_patterns, max_depth)
            cache_entry['data'] = result
            cache_entry['timestamp'] = time.time()
            dir_util._folder_cache[directory] = {
                'timestamp': cache_entry['timestamp'],
                'data': result,
                'directory_mtime': 0
            }
            return result
        except Exception as e:
            logger.error(f"Synchronous scan failed: {e}")
            return {"error": str(e)}

    if _background_scan_thread is None or not _background_scan_thread.is_alive():
        _background_scan_dir = directory
        def background_scan():
            from app.utils.directory_util import _scan_progress
            scan_start = time.time()
            logger.info(f"Background folder scan starting for {directory}")
            _scan_progress["active"] = True
            _scan_progress["start_time"] = scan_start
            _scan_progress["last_update"] = scan_start
            _scan_progress["progress"] = {"directories": 0, "files": 0, "elapsed": 0}
            try:
                result = get_folder_structure(directory, ignored_patterns, max_depth)
                _scan_progress["last_update"] = time.time()
                cache_entry['data'] = result
                cache_entry['timestamp'] = time.time()
                dir_util._folder_cache[directory] = {
                    'timestamp': cache_entry['timestamp'],
                    'data': result,
                    'directory_mtime': 0
                }
                logger.info(f"Background folder scan completed in {time.time() - scan_start:.1f}s")
            except Exception as e:
                logger.error(f"Background folder scan error: {e}", exc_info=True)
            finally:
                _scan_progress["active"] = False

        if _background_scan_thread and _background_scan_thread.is_alive():
            logger.warning("Abandoning stuck background scan thread")

        _background_scan_thread = threading.Thread(target=background_scan, daemon=True)
        _background_scan_thread.start()
        logger.info("Started background folder scan")

    return {"_scanning": True, "children": {}}


def restore_external_paths_for_project(project_root: str) -> None:
    """Re-populate server-side external path caches from persisted project data."""
    global _explicit_external_paths
    abs_root = os.path.abspath(project_root)
    if abs_root in _restored_projects:
        return
    _restored_projects.add(abs_root)

    try:
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home
        ps = ProjectStorage(get_ziya_home())
        project = ps.get_by_path(abs_root)
        if not project or not project.settings.externalPaths:
            return
        restored = 0
        for ext_path in project.settings.externalPaths:
            if os.path.exists(ext_path) and ext_path not in _explicit_external_paths:
                _explicit_external_paths.add(ext_path)
                add_external_path_to_cache(ext_path)
                restored += 1
        if restored:
            logger.info(f"Restored {restored} persisted external path(s) for project {project.id}")
    except Exception as e:
        logger.warning(f"Failed to restore external paths: {e}")
