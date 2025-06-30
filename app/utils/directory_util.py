import glob
import os
import time
import threading
import concurrent.futures
import signal
from typing import List, Tuple, Dict, Any, Optional
from app.utils.logging_utils import logger
import re

from app.utils.file_utils import is_binary_file, is_document_file, is_processable_file, read_file_content
from app.utils.logging_utils import logger
from app.utils.gitignore_parser import parse_gitignore_patterns

# Enhanced global cache for folder structure with directory modification tracking
_folder_cache = {
    'timestamp': 0, 
    'data': None, 
    'directory_mtime': 0
}

# Global progress tracking
_scan_progress = {"active": False, "progress": {}, "cancelled": False}

# Track visited directories to prevent infinite loops
_visited_directories = set()

def get_ignored_patterns(directory: str) -> List[Tuple[str, str]]:
    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", directory)
    ignored_patterns: List[Tuple[str, str]] = [
        ("poetry.lock", user_codebase_dir),
        ("package-lock.json", user_codebase_dir),
        (".DS_Store", user_codebase_dir),
        (".git", user_codebase_dir),
        ("node_modules", user_codebase_dir),
        ("build", user_codebase_dir),
        ("dist", user_codebase_dir),
        ("__pycache__", user_codebase_dir),
        ("*.pyc", user_codebase_dir),
        (".venv", user_codebase_dir), # Common virtual environment folder
        ("venv", user_codebase_dir),  # Common virtual environment folder
        (".vscode", user_codebase_dir), # VSCode settings
        (".idea", user_codebase_dir),   # JetBrains IDE settings
    ]
    
    # Add additional exclude directories from environment variable if it exists
    additional_excludes = os.environ.get("ZIYA_ADDITIONAL_EXCLUDE_DIRS", "")
    if additional_excludes:
        logger.info(f"Processing additional excludes: {additional_excludes}")
        for pattern in additional_excludes.split(','):
            pattern = pattern.strip()
            if pattern:
                ignored_patterns.append((pattern, user_codebase_dir))
                logger.info(f"Added exclude pattern: {pattern}")
    
    logger.info(f"Total ignore patterns: {len(ignored_patterns)}")
    for pattern, base in ignored_patterns:
        logger.debug(f"Ignore pattern: {pattern} (base: {base})")

    def read_gitignore(path: str) -> List[Tuple[str, str]]:
        gitignore_patterns: List[Tuple[str, str]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        try:
                            # Test if the pattern would create a valid regex
                            from app.utils.gitignore_parser import rule_from_pattern
                            test_rule = rule_from_pattern(line, base_path=os.path.dirname(path))
                            if test_rule:
                                gitignore_patterns.append((line, os.path.dirname(path)))
                        except re.error as e:
                            logger.warning(f"Skipping invalid gitignore pattern '{line}' in {path}:{line_number}: {e}")
        except FileNotFoundError:
            logger.debug(f".gitignore not found at {path}")
        except Exception as e:
            logger.warning(f"Error reading .gitignore at {path}: {e}")
        return gitignore_patterns

    def get_patterns_recursive(path: str) -> List[Tuple[str, str]]:
        patterns: List[Tuple[str, str]] = []
        gitignore_path = os.path.join(path, ".gitignore")
        if os.path.exists(gitignore_path):
            patterns.extend(read_gitignore(gitignore_path))
        
        for subdir in glob.glob(os.path.join(path, "*/")):
            # Skip symlinks to prevent infinite loops
            if os.path.islink(subdir.rstrip('/')):
                logger.debug(f"Skipping symlink directory: {subdir}")
                continue
            try:
                patterns.extend(get_patterns_recursive(subdir))
            except re.error as e:
                logger.warning(f"Skipping directory due to regex error: {subdir} - {e}")
                continue

        return patterns

    root_gitignore_path = os.path.join(user_codebase_dir, ".gitignore")
    if os.path.exists(root_gitignore_path) and os.path.isfile(root_gitignore_path):
        ignored_patterns.extend(read_gitignore(root_gitignore_path))

    ignored_patterns.extend(get_patterns_recursive(directory))
    return ignored_patterns


def get_complete_file_list(user_codebase_dir: str, ignored_patterns: List[str], included_relative_dirs: List[str]) -> Dict[str, Dict]:
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
    file_dict: Dict[str, Dict] = {}
    for pattern in included_relative_dirs:
        for root, dirs, files in os.walk(os.path.normpath(os.path.join(user_codebase_dir, pattern))):
            # Filter out ignored directories and hidden directories
            # Also filter out symlinks to prevent following them into ignored directories
            dirs[:] = [d for d in dirs 
                      if not should_ignore_fn(os.path.join(root, d)) 
                      and not d.startswith('.') 
                      and not os.path.islink(os.path.join(root, d))]

            for file in files:
                file_path = os.path.join(root, file)
                if not should_ignore_fn(file_path) and not is_binary_file(file_path) and not file.startswith('.'):
                    file_dict[file_path] = {}

    return file_dict

def is_image_file(file_path: str) -> bool:
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.ico']
    return any(file_path.lower().endswith(ext) for ext in image_extensions)

# Define multipliers for different file types
FILE_TYPE_MULTIPLIERS = {
    # Code files (tend to have higher token density)
    '.py': 1.8, '.js': 1.8, '.ts': 1.8, '.java': 1.8, '.cpp': 1.8,
    '.c': 1.8, '.h': 1.8, '.rs': 1.8, '.go': 1.8, '.php': 1.8,
    '.jsx': 1.8, '.tsx': 1.8, '.rb': 1.8, '.cs': 1.8, '.swift': 1.8,
    # Markup & Structured Data
    '.html': 1.4, '.css': 1.6, '.xml': 1.4, '.json': 1.3, 
    '.yaml': 1.3, '.yml': 1.3, '.md': 1.3,
    # Logs and Plain Text
    '.log': 1.2, '.txt': 1.2,
    # Default for other text-based files
    'default': 1.5  # The general multiplier
}
 
 
def get_file_type_multiplier(file_path: str) -> float:
    """Get the token density multiplier for a given file type."""
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    return FILE_TYPE_MULTIPLIERS.get(ext, FILE_TYPE_MULTIPLIERS['default'])


def get_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    """
    Get the folder structure of a directory with token counts.
    Now returns immediately with estimated counts, accurate counts calculated in background.
    
    Args:
        directory: The directory to get the structure of
        ignored_patterns: Patterns to ignore
        max_depth: Maximum depth to traverse
        
    Returns:
        Dict with folder structure including token counts
    """
    logger.info(f"🔍 PERF: Starting ULTRA-FAST folder scan for directory: {directory}")
    
    # Check if we have cached results first
    cached_result = get_cached_folder_structure_with_tokens(directory, ignored_patterns, max_depth)
    if cached_result:
        return cached_result
    
    import tiktoken
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
    
    # Ensure max_depth is at least 15 if not specified
    if max_depth <= 0:
        max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
    
    logger.info(f"Getting folder structure for {directory} with max depth {max_depth}")
    
    # Track scanning progress
    scan_stats = {
        'directories_scanned': 0,
        'files_processed': 0,
        'start_time': time.time(),
        'slow_directories': []
    }
    
    # Update global progress
    global _scan_progress
    _scan_progress["active"] = True
    _scan_progress["cancelled"] = False
    _scan_progress["progress"] = {
        "directories": 0,
        "files": 0,
        "elapsed": 0
    }
    
    # Set a maximum time limit for scanning (30 seconds)
    max_scan_time = int(os.environ.get("ZIYA_SCAN_TIMEOUT", "45"))  # Increased default, configurable
    
    # Add progress tracking
    last_progress_time = time.time()
    progress_interval = 2.0  # Log progress every 2 seconds

    def process_dir(path: str, depth: int) -> Dict[str, Any]:
        """Process a directory recursively."""
        # Resolve symlinks to detect loops
        real_path = os.path.realpath(path)
        
        # Check for infinite loops using real path
        if real_path in _visited_directories:
            logger.debug(f"Skipping already visited directory (symlink loop): {path} -> {real_path}")
            return {'token_count': 0}
        
        _visited_directories.add(real_path)
        
        dir_start_time = time.time()
        
        # Check for cancellation
        if _scan_progress.get("cancelled"):
            logger.info("Scan cancelled by user request")
            return {'token_count': 0, 'cancelled': True}
        
        # Check if we've exceeded the time limit
        current_time = time.time()
        elapsed = current_time - scan_stats['start_time']
        
        if elapsed > max_scan_time:
            logger.warning(f"Timeout reached at {elapsed:.1f}s while processing {path}")
            return {'token_count': 0, 'timeout': True}
            
        # Log progress periodically
        nonlocal last_progress_time
        
        # Update global progress
        _scan_progress["progress"] = {
            "directories": scan_stats['directories_scanned'],
            "files": scan_stats['files_processed'],
            "elapsed": int(elapsed)
        }
        
        if depth > max_depth:
            return {'token_count': 0}
            
        scan_stats['directories_scanned'] += 1
        
        result = {'token_count': 0, 'children': {}}
        total_tokens = 0
        
        
        try:
            entries = os.listdir(path)
        except PermissionError:
            logger.debug(f"Permission denied for {path}")
            dir_time = time.time() - dir_start_time
            if dir_time > 0.1:
                logger.warning(f"🔍 PERF: Permission error check took {dir_time*1000:.1f}ms for {path}")
            return {'token_count': 0}
        except OSError as e:
            logger.warning(f"OS error accessing {path}: {e}")
            return {'token_count': 0}
            
        for entry in entries:
            # Check for cancellation in tight loop
            if _scan_progress.get("cancelled"):
                logger.info("Scan cancelled during entry processing")
                break
            
            # Check if we've exceeded the time limit
            current_time = time.time()
            elapsed = current_time - scan_stats['start_time']
            
            if elapsed > max_scan_time:
                logger.warning(f"Timeout during entry processing in {path}")
                break
                
            # Progress logging
            if current_time - last_progress_time > progress_interval:
                logger.info(f"Scan progress: {scan_stats['directories_scanned']} dirs, {scan_stats['files_processed']} files in {elapsed:.1f}s")
                last_progress_time = current_time
                
            if entry.startswith('.'):  # Skip hidden files
                continue
                
            entry_start = time.time()
            entry_path = os.path.join(path, entry)
            path_join_time = time.time() - entry_start
            if path_join_time > 0.01:
                logger.warning(f"🔍 PERF: Slow path join for {entry}: {path_join_time*1000:.1f}ms")
            
            # Skip symlinks to prevent infinite loops
            if os.path.islink(entry_path):  # Skip symlinks
                logger.debug(f"Skipping symlink: {entry_path}")
                continue
                
            ignore_start = time.time()
            if should_ignore_fn(entry_path):  # Skip ignored files
                ignore_time = time.time() - ignore_start
                if ignore_time > 0.01:
                    logger.warning(f"🔍 PERF: Slow gitignore check for {entry}: {ignore_time*1000:.1f}ms")
                logger.debug(f"Ignoring path: {entry_path}")
                continue
                
            if os.path.isdir(entry_path):
                if depth < max_depth:
                    sub_result = process_dir(entry_path, depth + 1)
                    if sub_result['token_count'] > 0 or sub_result.get('children'):
                        result['children'][entry] = sub_result
                        total_tokens += sub_result['token_count'] 
                    else:
                        # Skip directories beyond max depth
                        pass
            elif os.path.isfile(entry_path):
                tokens = estimate_tokens_fast(entry_path)
                if tokens > 0:
                    scan_stats['files_processed'] += 1  # Fix: increment counter for processed files
                    result['children'][entry] = {'token_count': tokens}
                    total_tokens += tokens
        
        result['token_count'] = total_tokens
        
        # Remove from visited set when exiting this directory
        _visited_directories.discard(real_path)
        
        # Log slow directory processing
        dir_time = time.time() - dir_start_time
        if dir_time > 2.0:  # Log if directory takes >2s
            scan_stats['slow_directories'].append((path, dir_time, 'slow_directory'))
            logger.warning(f"Slow directory scan for {path}: {dir_time:.2f}s ({len(entries)} entries)")
            
        return result

    # Clear visited directories at start of scan
    global _visited_directories
    _visited_directories.clear()
    
    # Process the root directory
    root_result = process_dir(directory, 1)
    
    # Mark scanning as complete
    _scan_progress["active"] = False
    
    # Return just the children of the root to match expected format
    # Clear visited directories after scan
    _visited_directories.clear()
    
    total_time = time.time() - scan_stats['start_time']
    logger.info(f"Folder scan completed: {scan_stats['directories_scanned']} dirs, "
                f"{scan_stats['files_processed']} files in {total_time:.2f}s")
    
    if total_time >= max_scan_time:
        logger.warning(f"Folder scan timed out after {max_scan_time}s")
        logger.info(f"Partial results: {scan_stats['directories_scanned']} dirs, {scan_stats['files_processed']} files")
        # Ensure we return whatever we have so far
        if not root_result.get('children'):
            logger.error("No partial results available after timeout")
            return {"error": f"Scan timed out after {max_scan_time}s with no results", "timeout": True}
    
    if scan_stats['slow_directories']:
        logger.warning(f"Found {len(scan_stats['slow_directories'])} slow operations:")
        for path, duration, reason in scan_stats['slow_directories'][:5]:  # Log top 5
            logger.warning(f"  {path}: {duration:.2f}s ({reason})")
            
    result = root_result.get('children', {})
    if total_time >= max_scan_time:
        logger.warning(f"Folder scan timed out after {max_scan_time}s, returning partial results with {len(result)} entries")
        result['_timeout'] = True
        result['_partial'] = True
    
    # Check if cancelled
    if _scan_progress.get("cancelled"):
        result['_cancelled'] = True
    _visited_directories.clear()
    logger.info(f"Returning folder structure with {len(result)} top-level entries")
    return result

# Add new fast estimation function
def estimate_tokens_fast(file_path: str) -> int:
    """Fast token estimation based on file size and type."""
    try:
        # Get file size
        file_size = os.path.getsize(file_path)
        
        # Skip very large files immediately
        if file_size > 1024 * 1024:  # 1MB
            return 0
        
        # Quick binary check using file extension
        _, ext = os.path.splitext(file_path.lower())
        if ext in {'.pyc', '.pyo', '.pyd', '.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg',
                  '.core', '.bin', '.exe', '.dll', '.so', '.dylib', '.class',
                  '.woff', '.woff2', '.ttf', '.eot', '.zip'}:
            return 0
        
        # Estimate tokens based on file size and type
        # Rough approximation: 8 characters per token on average
        # Adjusted to 50% of original estimate based on observed accuracy
        estimated_tokens = file_size // 8
        
        # Apply file type multiplier
        multiplier = get_file_type_multiplier(file_path)
        return int(estimated_tokens * multiplier)
    except (OSError, IOError):
        return 0

# Add caching system
_token_cache = {}
_cache_timestamp = 0
_cache_lock = threading.Lock()

# Global thread management for background token calculation
_background_thread = None
_background_thread_lock = threading.Lock()

# Global cache for accurate token counts
_accurate_token_cache = {}

def ensure_background_token_calculation(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int):
    """
    Ensure background token calculation is running for the given directory.
    This is the single entry point for starting background calculation.
    """
    if not is_background_calculation_running():
        logger.info(f"🔍 PERF: Ensuring background token calculation for {directory}")
        start_background_token_calculation(directory, ignored_patterns, max_depth)
    else:
        logger.debug("🔍 PERF: Background token calculation already running")

def get_cached_folder_structure_with_tokens(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Optional[Dict[str, Any]]:
    """Get cached folder structure if available and fresh."""
    global _token_cache, _cache_timestamp
    
    cache_key = f"{directory}:{max_depth}:{hash(str(ignored_patterns))}"
    current_time = time.time()
    
    with _cache_lock:
        if cache_key in _token_cache and (current_time - _cache_timestamp) < 300:  # 5 minute cache
            logger.info("🔍 PERF: Returning cached folder structure with tokens")
            # Even with cached results, ensure background calculation is running for freshness
            ensure_background_token_calculation(directory, ignored_patterns, max_depth)
            return _token_cache[cache_key]
    
    return None

def cache_folder_structure_with_tokens(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int, result: Dict[str, Any]):
    """Cache folder structure with tokens."""
    global _token_cache, _cache_timestamp
    
    cache_key = f"{directory}:{max_depth}:{hash(str(ignored_patterns))}"
    
    with _cache_lock:
        _token_cache[cache_key] = result
        _cache_timestamp = time.time()
        logger.info(f"🔍 PERF: Cached folder structure with {len(result)} entries")

def start_background_token_calculation(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int):
    """Start background thread to calculate accurate token counts (with deduplication)."""
    global _background_thread, _background_thread_lock
    
    with _background_thread_lock:
        # Check if a background thread is already running
        if _background_thread is not None and _background_thread.is_alive():
            logger.info("🔍 PERF: Background token calculation already in progress, skipping duplicate request")
            return
        
        logger.info(f"🔍 PERF: Starting new background token calculation thread for {directory}")
    
    def calculate_accurate_tokens():
        try:
            logger.info(f"🔍 PERF: Starting background accurate token calculation for {directory}")
            
            # Early return is still in place - remove it when ready to re-enable
            logger.info("🔍 PERF: Background token calculation temporarily disabled")
            return
            
            # The rest of the function remains the same but won't execute due to return above
            # Remove the return statement above when you want to re-enable background calculation
            
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
            
            accurate_counts = {}
            files_processed = 0
            
            for root, dirs, files in os.walk(directory):
                # Filter directories
                dirs[:] = [d for d in dirs 
                          if not should_ignore_fn(os.path.join(root, d)) 
                          and not d.startswith('.') 
                          and not os.path.islink(os.path.join(root, d))]
                
                for file in files:
                    file_path = os.path.join(root, file)
                    if should_ignore_fn(file_path) or file.startswith('.'):
                        continue
                    
                    if not is_processable_file(file_path):
                        continue
                    
                    try:
                        content = read_file_content(file_path)
                        if content:
                            token_count = len(encoding.encode(content))
                            if token_count <= 50000:  # skip huge files
                                multiplier = get_file_type_multiplier(file_path)
                                adjusted_count = int(token_count * multiplier)
                                
                                # Store relative path
                                rel_path = os.path.relpath(file_path, directory)
                                accurate_counts[rel_path] = adjusted_count
                                files_processed += 1
                                
                                if files_processed % 1000 == 0:
                                    logger.info(f"🔍 PERF: Background processed {files_processed} files so far")
                    except Exception as e:
                        logger.debug(f"Error processing {file_path}: {e}")
                        continue
            
            # Update cache with accurate counts
            logger.info(f"🔍 PERF: Background calculation complete: {files_processed} files processed")
            # Store accurate counts in a separate cache for API access
            logger.info(f"🔍 PERF: Storing {len(accurate_counts)} accurate token counts in cache")
            global _accurate_token_cache
            _accurate_token_cache = accurate_counts
            
            # Also update the folder structure cache with accurate counts
            cache_key = f"{directory}:{max_depth}:{hash(str(ignored_patterns))}"
            with _cache_lock:
                if cache_key in _token_cache:
                    _token_cache[cache_key]["_accurate_tokens"] = accurate_counts
                    logger.info(f"🔍 PERF: Updated folder cache with {len(accurate_counts)} accurate token counts")
            
        except Exception as e:
            logger.error(f"Background token calculation failed: {e}")
        finally:
            # Clean up thread reference when done
            global _background_thread
            with _background_thread_lock:
                _background_thread = None
            logger.info("🔍 PERF: Background token calculation thread completed")
    
    with _background_thread_lock:
        _background_thread = threading.Thread(target=calculate_accurate_tokens, daemon=True, name="TokenCalculation")
        _background_thread.start()
        logger.info(f"🔍 PERF: Background thread started: {_background_thread.name}")

def is_background_calculation_running() -> bool:
    """Check if background token calculation is currently running."""
    global _background_thread, _background_thread_lock
    
    with _background_thread_lock:
        return _background_thread is not None and _background_thread.is_alive()

def get_background_calculation_status() -> dict:
    """Get status of background token calculation."""
    return {
        "running": is_background_calculation_running(),
        "thread_name": _background_thread.name if _background_thread and _background_thread.is_alive() else None
    }

def get_accurate_token_count(file_path: str) -> int:
    """
    Get accurate token count for a specific file using tiktoken.
    This is the slow but precise method - use sparingly.
    """
    import tiktoken
    
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        
        # Skip binary files
        if not is_processable_file(file_path):
            logger.debug(f"Skipping binary/unprocessable file: {file_path}")
            return 0
                
        # Read file and count tokens
        content = read_file_content(file_path)
        if content:
            token_count = len(encoding.encode(content))
            
            # Skip files with excessive token counts (>50k tokens)
            if token_count > 50000:
                logger.debug(f"File has excessive tokens {file_path}: {token_count} tokens")
                logger.info(f"Limiting token count for large file {os.path.basename(file_path)}: {token_count} -> 0")
                return 0
            
            return token_count
        return 0
    except Exception as e:
        logger.debug(f"Error counting tokens in {file_path}: {e}")
        return 0

def get_scan_progress():
    """Get current scan progress."""
    return _scan_progress.copy()

def cancel_scan():
    """Cancel current scan operation."""
    global _scan_progress
    if not isinstance(_scan_progress, dict):
        # Initialize if not properly set
        _scan_progress = {"active": False, "progress": {}, "cancelled": False}
    _scan_progress["cancelled"] = True
    return _scan_progress["active"]

def get_cached_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    """
    Get folder structure with caching and timeout protection.
    
    This function will:
    1. Return cached results if they're fresh (less than 10 seconds old)
    2. Return an error message if scanning is taking too long
    3. Cache results for future requests
    
    Args:
        directory: The directory to scan
        ignored_patterns: Patterns to ignore
        max_depth: Maximum depth to traverse
        
    Returns:
        Dict with folder structure or error message
    """
    global _folder_cache
    
    # Check if we already have a fresh cache (less than 10 seconds old)
    current_time = time.time()
    cache_age = current_time - _folder_cache['timestamp']
    logger.info(f"Folder cache age: {cache_age:.2f}s, directory: {directory}")
    
    # Check if directory has been modified since last cache
    try:
        current_dir_mtime = os.path.getmtime(directory)
        directory_changed = current_dir_mtime > _folder_cache['directory_mtime']
    except OSError:
        # If we can't get mtime, assume directory changed
        directory_changed = True
        logger.warning(f"Could not get mtime for directory: {directory}")
        current_dir_mtime = current_time
    
    # Return cached results if they're fresh AND directory hasn't been modified
    if (_folder_cache['data'] is not None and 
        cache_age < 10 and 
        not directory_changed):
        logger.debug(f"Returning cached folder structure (age: {cache_age:.1f}s)")
        logger.info(f"Returning cached folder structure with {len(_folder_cache['data'])} top-level entries")
        return _folder_cache['data']
    
    if directory_changed:
        logger.info(f"Directory modification detected, invalidating cache")
        logger.info(f"Current mtime: {current_dir_mtime}, cached mtime: {_folder_cache['directory_mtime']}")
    
    try:
        # Perform the actual scan with timeout protection
        logger.info(f"Starting folder scan for {directory}")
        result = get_folder_structure(directory, ignored_patterns, max_depth)
        
        # Cache the successful result
        _folder_cache['data'] = result
        _folder_cache['timestamp'] = time.time()
        logger.info(f"Updated folder cache with {len(result)} top-level entries")
        _folder_cache['directory_mtime'] = current_dir_mtime
        
        return result
    except Exception as e:
        logger.error(f"Error during folder scan: {str(e)}")
        # Return error but don't cache it
        return {"error": f"Scan failed: {str(e)}"}
