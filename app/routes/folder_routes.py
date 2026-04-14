"""
Folder and file management routes.

Extracted from server.py during Phase 3b refactoring.
"""
import os
import re
import time
from app.context import get_project_root
import threading
import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from app.utils.logging_utils import logger
from app.utils.directory_util import get_ignored_patterns
from app.services.folder_service import (
    _folder_cache, _cache_lock, _explicit_external_paths,
    invalidate_folder_cache, is_path_explicitly_allowed,
    add_file_to_folder_cache, update_file_in_folder_cache,
    remove_file_from_folder_cache, add_external_path_to_cache,
    add_directory_to_folder_cache,
    collect_leaf_file_keys as _collect_leaf_file_keys,
    collect_documentation_file_keys as _collect_documentation_file_keys,
    restore_external_paths_for_project as _restore_external_paths_for_project,
    get_cached_folder_structure,
)

router = APIRouter(tags=["folders"])

class FolderRequest(BaseModel):
    model_config = {"extra": "allow"}
    directory: str
    max_depth: int = 3

class FileRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str

class FileContentRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str
    content: str

class AddExplicitPathsRequest(BaseModel):
    model_config = {"extra": "allow"}
    paths: List[str]
    add_to_context: bool = False

@router.post("/folder")
async def get_folder(request: FolderRequest):
    """Get the folder structure of a directory with improved error handling."""
    # Add timeout configuration
    timeout = int(os.environ.get("ZIYA_SCAN_TIMEOUT", "45"))
    logger.info(f"Starting folder scan with {timeout}s timeout for: {request.directory}")
    logger.info(f"Max depth: {request.max_depth}")
    
    start_time = time.time()
    logger.info(f"Starting folder scan for: {request.directory}")
    logger.info(f"Max depth: {request.max_depth}")
    
    try:
        # Special handling for home directory
        if request.directory == os.path.expanduser("~"):
            logger.warning("Home directory scan requested - this may be slow or fail")
            return {
                "error": "Home directory scans are not recommended",
                "suggestion": "Please use a specific project directory instead of your home directory"
            }
            
        # Validate the directory exists and is accessible
        if not os.path.exists(request.directory):
            logger.error(f"Directory does not exist: {request.directory}")
            return {"error": f"Directory does not exist: {request.directory}"}
            
        if not os.path.isdir(request.directory):
            logger.error(f"Path is not a directory: {request.directory}")
            return {"error": f"Path is not a directory: {request.directory}"}
            
        # Test basic access
        try:
            os.listdir(request.directory)
        except PermissionError:
            logger.error(f"Permission denied accessing: {request.directory}")
            return {"error": "Permission denied accessing directory"}
        except OSError as e:
            logger.error(f"OS error accessing {request.directory}: {e}")
            return {"error": f"Cannot access directory: {str(e)}"}
        
        # Get the ignored patterns
        ignored_patterns = get_ignored_patterns(request.directory)
        logger.info(f"Ignore patterns loaded: {len(ignored_patterns)} patterns")
        
        # Use the max_depth from the request, but ensure it's at least 15 if not specified
        max_depth = request.max_depth if request.max_depth > 0 else int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        logger.info(f"Using max depth for folder structure: {max_depth}")
        
        # Use our enhanced cached folder structure function
        result = get_cached_folder_structure(request.directory, ignored_patterns, max_depth)
        
        # Check if we got an error result
        if isinstance(result, dict) and "error" in result:
            logger.warning(f"Folder scan returned error: {result['error']}")
            
            # For timeout errors, provide more helpful response
            if result.get("timeout"):
                result["suggestion"] = f"Scan timed out after {timeout}s. Try:\n" + \
                                     "1. Increase timeout with ZIYA_SCAN_TIMEOUT environment variable\n" + \
                                     "2. Reduce max depth\n" + \
                                     "3. Add more patterns to .gitignore to exclude large directories"
                result["timeout_seconds"] = timeout
            # Add helpful context for home directory scans
            if "home" in request.directory.lower() or request.directory.endswith(os.path.expanduser("~")):
                result["suggestion"] = "Home directory scans can be very slow. Consider using a specific project directory instead."
            return result
            
        logger.info(f"Folder scan completed successfully in {time.time() - start_time:.2f}s")
        
        # Add metadata about the scan
        if isinstance(result, dict):
            result["_scan_time"] = time.time() - start_time
            result["_timeout_used"] = timeout
            
        return result
    except Exception as e:
        logger.error(f"Error in get_folder: {e}")
        return {"error": str(e)}

@router.get("/folder-progress")
async def get_folder_progress():
    """Get current folder scanning progress."""
    from app.utils.directory_util import get_scan_progress
    progress = get_scan_progress()
    
    # Only return active=True if there's actual progress to report
    if progress["active"] and not progress["progress"]:
        # No actual progress data, don't report as active
        progress["active"] = False
        progress["progress"] = {}
    
    # Add percentage if we have estimated total
    if progress.get("estimated_total", 0) > 0 and progress.get("progress", {}).get("directories", 0) > 0:
        progress["progress"]["percentage"] = min(100, int(
            (progress["progress"]["directories"] / progress["estimated_total"]) * 100
        ))
    
    return progress

@router.post("/api/cancel-scan")
async def cancel_folder_scan():
    """Cancel current folder scanning operation."""
    from app.utils.directory_util import cancel_scan
    was_active = cancel_scan()
    if was_active:
        logger.info("Folder scan cancellation requested")
    return {"cancelled": was_active}

@router.post("/api/clear-folder-cache")
async def clear_folder_cache():
    """Clear the folder structure cache."""
    global _folder_cache
    _folder_cache = {}
    logger.info("Folder cache cleared")
    return {"cleared": True}

@router.post("/api/clear-external-paths")
async def clear_external_paths():
    """Remove all external paths from the folder cache without clearing project files."""
    global _folder_cache, _cache_lock
    removed = 0
    with _cache_lock:
        for dir_key in list(_folder_cache.keys()):
            entry = _folder_cache[dir_key]
            if entry.get('data') and '[external]' in entry['data']:
                del entry['data']['[external]']
                removed += 1
    logger.info(f"🗑️ Cleared external paths from {removed} cache entries")

    # Also clear persisted external paths from the project
    try:
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home
        ps = ProjectStorage(get_ziya_home())
        project = ps.get_by_path(get_project_root())
        if project and project.settings.externalPaths:
            from app.models.project import ProjectUpdate, ProjectSettings
            ps.update(project.id, ProjectUpdate(settings=ProjectSettings(externalPaths=[])))
            logger.info(f"💾 Cleared persisted external paths from project {project.id}")
    except Exception as e:
        logger.warning(f"Failed to clear persisted external paths: {e}")

    return {"cleared": removed}

@router.post("/file")
async def get_file(request: FileRequest):
    """Get the content of a file."""
    try:
        with open(request.file_path, 'r') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        logger.error(f"Error in get_file: {e}")
        return {"error": str(e)}

@router.post("/save")
async def save_file(request: FileContentRequest):
    """Save content to a file."""
    try:
        with open(request.file_path, 'w') as f:
            f.write(request.content)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error in save_file: {e}")
        return {"error": str(e)}

@router.get('/api/default-included-folders')
async def get_default_included_folders():
    """Get the default included folders."""
    return []

@router.get('/api/browse-directory')
async def api_browse_directory(path: str = '~'):
    """
    Browse a directory on the server filesystem.
    Returns list of files and directories for the file browser dialog.
    """
    try:
        # Expand ~ to home directory
        if path.startswith('~'):
            path = os.path.expanduser(path)
        
        # Resolve to absolute path
        path = os.path.abspath(path)
        
        # Security: Validate the path exists and is a directory
        if not os.path.exists(path):
            return JSONResponse(
                status_code=404,
                content={"detail": f"Path does not exist: {path}"}
            )
        
        if not os.path.isdir(path):
            # If it's a file, return the parent directory
            path = os.path.dirname(path)
        
        # List directory contents
        entries = []
        try:
            for entry_name in sorted(os.listdir(path)):
                # Skip hidden files (starting with .)
                if entry_name.startswith('.'):
                    continue
                    
                entry_path = os.path.join(path, entry_name)
                try:
                    is_dir = os.path.isdir(entry_path)
                    size = None
                    if not is_dir:
                        try:
                            size = os.path.getsize(entry_path)
                        except OSError:
                            size = None
                    
                    entries.append({
                        "name": entry_name,
                        "path": entry_path,
                        "is_dir": is_dir,
                        "size": size
                    })
                except (PermissionError, OSError):
                    # Skip entries we can't access
                    continue
                    
        except PermissionError:
            return JSONResponse(
                status_code=403,
                content={"detail": f"Permission denied: {path}"}
            )
        
        # Sort: directories first, then files, both alphabetically
        entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
        
        return {
            "current_path": path,
            "entries": entries
        }
        
    except Exception as e:
        logger.error(f"Error browsing directory {path}: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error browsing directory: {str(e)}"}
        )

@router.post('/api/add-explicit-paths')
async def api_add_explicit_paths(request: AddExplicitPathsRequest):
    """
    Add explicit file/directory paths to the folder browser tree.
    Paths outside the workspace root will be shown with their full path prefix.
    
    If add_to_context is True, the paths are also added to the current context selection.
    """
    global _folder_cache, _cache_lock
    
    user_codebase_dir = get_project_root()
    global _explicit_external_paths
    added_paths = []
    context_keys = []  # tree keys for files that should be auto-selected
    errors = []
    
    for path in request.paths:
        try:
            # Expand ~ and resolve to absolute path
            if path.startswith('~'):
                path = os.path.expanduser(path)
            path = os.path.abspath(path)
            
            # Validate path exists
            if not os.path.exists(path):
                errors.append(f"Path does not exist: {path}")
                continue
            
            # Resolve symlinks for overlap detection during scanning.
            # We don't reject the path outright — we just warn if it
            # overlaps the project root.  The scan_directory functions
            # will skip subdirectories that resolve back into the project.
            real_path = os.path.realpath(path)
            real_codebase = os.path.realpath(user_codebase_dir)
            if real_codebase.startswith(real_path + os.sep) or real_codebase == real_path:
                logger.info(f"📂 External path '{path}' contains project root — overlapping subtrees will be skipped during scan")

            # Determine if this is inside or outside the workspace
            is_inside_workspace = path.startswith(user_codebase_dir + os.sep) or path == user_codebase_dir
            
            if is_inside_workspace:
                # For paths inside workspace, use relative path
                rel_path = os.path.relpath(path, user_codebase_dir)
            else:
                # For paths outside workspace, use full path as the key
                rel_path = path
            
            # Add to folder cache
            if os.path.isfile(path):
                success = add_file_to_folder_cache(rel_path) if is_inside_workspace else add_external_path_to_cache(path)
                if success:
                    if not is_inside_workspace:
                        _explicit_external_paths.add(path)
                    added_paths.append(path)
            elif os.path.isdir(path):
                success = add_directory_to_folder_cache(rel_path, path, is_inside_workspace)
                if success:
                    if not is_inside_workspace:
                        _explicit_external_paths.add(path)
                    added_paths.append(path)
                    # When add_to_context is requested, collect all leaf file keys
                    if request.add_to_context:
                        file_keys = _collect_leaf_file_keys(path, is_inside_workspace, user_codebase_dir)
                        context_keys.extend(file_keys)
                    # Always auto-select AGENTS.md / README.md files found
                    # in the imported hierarchy so the model sees project guidance.
                    doc_keys = _collect_documentation_file_keys(path, is_inside_workspace, user_codebase_dir)
                    for dk in doc_keys:
                        if dk not in context_keys:
                            context_keys.append(dk)
                    
        except Exception as e:
            logger.error(f"Error adding path {path}: {e}")
            errors.append(f"Error adding {path}: {str(e)}")

    # Persist external paths to the project so they survive server restart
    if added_paths:
        try:
            from app.storage.projects import ProjectStorage
            from app.utils.paths import get_ziya_home
            ps = ProjectStorage(get_ziya_home())
            project = ps.get_by_path(user_codebase_dir)
            if project:
                existing = set(project.settings.externalPaths or [])
                new_external = [p for p in added_paths
                                if not p.startswith(user_codebase_dir + os.sep)]
                if new_external and not existing.issuperset(new_external):
                    from app.models.project import ProjectUpdate, ProjectSettings
                    merged = sorted(existing | set(new_external))
                    ps.update(project.id, ProjectUpdate(
                        settings=ProjectSettings(externalPaths=merged)))
                    logger.info(f"💾 Persisted {len(new_external)} external path(s) to project {project.id}")
        except Exception as e:
            logger.warning(f"Failed to persist external paths to project: {e}")

    return {
        "added_count": len(added_paths),
        "added_paths": added_paths,
        "errors": errors if errors else None,
        "add_to_context": request.add_to_context,
        "context_keys": context_keys if context_keys else None
    }

@router.get('/api/folders-cached')
async def get_folders_cached():
    """Get folder structure from cache only - returns instantly without scanning."""
    try:
        # Get the user's codebase directory
        user_codebase_dir = get_project_root()
            
        # Get max depth from environment or use default
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            max_depth = 15
            
        # Get ignored patterns
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        
        # Import here to avoid circular imports
        from app.utils.directory_util import _folder_cache as du_folder_cache, _token_cache, _cache_lock
        
        # First check if we have any cached data at all
        with _cache_lock:
            cache_key = f"{user_codebase_dir}:{max_depth}:{hash(str(ignored_patterns))}"
            
            # Check for token cache first (most complete)
            if cache_key in _token_cache:
                logger.info("🚀 Returning cached folder structure with tokens (instant)")
                result = _token_cache[cache_key]
                if "_accurate_tokens" in result:
                    result["_accurate_token_counts"] = result["_accurate_tokens"]
                return result
                
            # Fall back to directory_util's folder cache
            dir_entry = du_folder_cache.get(user_codebase_dir)
            if dir_entry and dir_entry.get('data') is not None:
                logger.info("🚀 Returning basic folder cache from directory_util (instant)")
                return dir_entry['data']
        
        # Also check server.py's _folder_cache (scan results land here too)
        abs_dir = os.path.abspath(user_codebase_dir)
        server_entry = _folder_cache.get(abs_dir)
        if server_entry and server_entry.get('data') is not None:
            logger.info("🚀 Returning basic folder cache from server cache (instant)")
            return server_entry['data']
                
        # No cache available
        return {"error": "No cached data available"}
    except Exception as e:
        logger.error(f"Error in get_folders_cached: {e}")
        return {"error": f"Cache error: {str(e)}"}

@router.get('/api/folders-with-accurate-tokens')
async def get_folders_with_accurate_tokens():
    """Get folder structure with pre-calculated accurate token counts."""
    try:
        # Get the user's codebase directory
        user_codebase_dir = get_project_root()
            
        # Get max depth from environment or use default
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            logger.warning("Invalid ZIYA_MAX_DEPTH value, using default of 15")
            max_depth = 15
            
        # Get ignored patterns
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        logger.debug(f"Loaded {len(ignored_patterns)} ignore patterns")
        
        # Check if we have cached accurate token counts
        from app.utils.directory_util import get_cached_folder_structure_with_tokens
        result = get_cached_folder_structure_with_tokens(user_codebase_dir, ignored_patterns, max_depth)
        
        if result:
            result["_has_accurate_tokens"] = True
            # Include accurate token counts if available
            if "_accurate_tokens" in result:
                result["_accurate_token_counts"] = result["_accurate_tokens"]
                logger.info(f"Returning folder structure with {len(result['_accurate_tokens'])} accurate token counts")
            return result
            
        # Get regular folder structure
        regular_result = await api_get_folders()
        return regular_result
    except Exception as e:
        logger.error(f"Error in get_folders_with_accurate_tokens: {e}")
        return {"error": f"Unexpected error: {str(e)}"}

@router.get('/api/folders')
async def api_get_folders(refresh: bool = False, project_path: str = Query(None)):
    """Get folder structure for API compatibility with improved error handling."""
    
    # DIAGNOSTIC: Log what we're about to return
    def log_folder_contents(data, path="", max_depth=3, current_depth=0):
        if current_depth >= max_depth:
            return
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if key.startswith('_'):
                continue
            current_path = f"{path}/{key}" if path else key
            if isinstance(value, dict):
                if 'children' in value:
                    logger.debug(f"📁 {current_path}/ ({len(value.get('children', {}))} children)")
                    log_folder_contents(value.get('children', {}), current_path, max_depth, current_depth + 1)
                else:
                    token_count = value.get('token_count', 0)
                    logger.debug(f"📄 {current_path} ({token_count} tokens)")
    
    # Add cache headers to help frontend avoid unnecessary requests
    if refresh:
        # If refresh requested, invalidate caches BEFORE any processing
        logger.info("🔄 Refresh requested - invalidating caches")
        invalidate_folder_cache()
        
        # Also invalidate the gitignore patterns cache to pick up new files
        import app.utils.directory_util as dir_util
        dir_util._ignored_patterns_cache = None
        dir_util._ignored_patterns_cache_dir = None
        dir_util._ignored_patterns_cache_time = 0
        logger.info("🔄 Invalidated gitignore patterns cache")

        # Clear directory_util's folder cache too — otherwise
        # get_cached_folder_structure will find stale data there and
        # promote it back, bypassing the synchronous rescan entirely.
        dir_util._folder_cache.clear()
        logger.info("🔄 Invalidated directory_util folder cache")
    
    from fastapi import Response
    response = Response()
    response.headers["Cache-Control"] = "public, max-age=30"
    
    try:
        # Get the user's codebase directory
        if project_path:
            user_codebase_dir = os.path.abspath(project_path)
            logger.info(f"Using provided project_path: {user_codebase_dir}")
        else:
            user_codebase_dir = get_project_root()
            
        # Validate the directory exists and is accessible
        if not os.path.exists(user_codebase_dir):
            logger.error(f"Codebase directory does not exist: {user_codebase_dir}")
            return {"error": f"Directory does not exist: {user_codebase_dir}"}
            
        if not os.path.isdir(user_codebase_dir):
            logger.error(f"Codebase path is not a directory: {user_codebase_dir}")
            return {"error": f"Path is not a directory: {user_codebase_dir}"}

        # Restore persisted external paths into server-side caches
        _restore_external_paths_for_project(user_codebase_dir)
            
        # Test basic access
        try:
            os.listdir(user_codebase_dir)
        except PermissionError:
            logger.error(f"Permission denied accessing: {user_codebase_dir}")
            return {"error": "Permission denied accessing directory"}
        except OSError as e:
            logger.error(f"Permission denied accessing: {user_codebase_dir}")
            return {"error": "Permission denied accessing directory"}
        
        # Get ignored patterns (will use cache if available)
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        
        # Get max depth from environment or use default
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            logger.warning("Invalid ZIYA_MAX_DEPTH value, using default of 15")
            max_depth = 15
            
        # Get ignored patterns
        try:
            ignored_patterns = get_ignored_patterns(user_codebase_dir)
            logger.debug(f"Loaded {len(ignored_patterns)} ignore patterns")
        except re.error as e:
            logger.error(f"Invalid gitignore pattern detected: {e}")
            # Use minimal default patterns if gitignore parsing fails
            ignored_patterns = [
                (".git", user_codebase_dir),
                ("node_modules", user_codebase_dir),
                ("__pycache__", user_codebase_dir)
            ]
        
        # Check if a scan is in progress BEFORE we call get_cached_folder_structure
        from app.utils.directory_util import get_scan_progress
        scan_status_before = get_scan_progress()
        
        # Use our enhanced cached folder structure function
        result = get_cached_folder_structure(user_codebase_dir, ignored_patterns, max_depth, synchronous=refresh)
        
        # Log the structure we're returning
        has_external = '[external]' in result if isinstance(result, dict) else False
        logger.info(f"📂 api_get_folders: {len(result) if isinstance(result, dict) else 0} top-level keys, "
                     f"has_external={has_external}, cache_key={user_codebase_dir}")
        logger.debug("=== FOLDER STRUCTURE BEING RETURNED ===")
        log_folder_contents(result, max_depth=2)
        logger.debug("=== END FOLDER STRUCTURE ===")
        
        # Background calculation is automatically ensured by get_cached_folder_structure_with_tokens
        # Check if we got an error result
        if isinstance(result, dict) and "error" in result:
            logger.warning(f"Folder scan returned error: {result['error']}")
            
            # If the result is completely empty, try to return at least some basic structure
            if not result.get('children') and not result.get('token_count'):
                logger.warning("Empty result returned, creating minimal folder structure")
                result = {"_error": result['error'], "app": {"token_count": 0, "children": {}}}
                
            return result
            
        # Log a sample of the result to see if token counts are included
        sample_files = []
        def collect_sample(data, path=""):
            if isinstance(data, dict):
                for key, value in data.items():
                    current_path = f"{path}/{key}" if path else key
                    if isinstance(value, dict) and 'token_count' in value:
                        sample_files.append(f"{current_path}: {value['token_count']} tokens")
                        if len(sample_files) >= 5:  # Only collect first 5 for logging
                            return
                    elif isinstance(value, dict) and 'children' in value:
                        collect_sample(value['children'], current_path)
        
        collect_sample(result)
        if sample_files:
            logger.debug(f"Sample files with token counts: {sample_files}")
        else:
            logger.debug("No files with token counts found in folder structure")
        
        # The _scanning flag is already set by get_cached_folder_structure when appropriate
        return result
    except Exception as e:
        logger.error(f"Error in api_get_folders: {e}")
        return {"error": f"Unexpected error: {str(e)}"}

