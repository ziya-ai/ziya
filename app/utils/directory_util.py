import glob
import os
import time
import threading
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

# Add new globals for background scanning
_scan_thread: Optional[threading.Thread] = None
_scan_lock = threading.Lock()
    
# Track visited directories to prevent infinite loops
_visited_directories = set()

def get_ignored_patterns(directory: str) -> List[Tuple[str, str]]:
    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", directory)
    
    # Check if we're using include-only mode
    include_only_dirs = os.environ.get("ZIYA_INCLUDE_ONLY_DIRS", "")
    if include_only_dirs:
        logger.info(f"Using include-only mode with patterns: {include_only_dirs}")
        # In include-only mode, we ignore everything except the specified directories/patterns
        # First, create a pattern that matches everything
        ignored_patterns: List[Tuple[str, str]] = [
            ("*", user_codebase_dir)  # Ignore everything by default
        ]
        
        # Then, for each specified directory or pattern, add a negation pattern
        for include_pattern in include_only_dirs.split(','):
            include_pattern = include_pattern.strip()
            if not include_pattern:
                continue
                
            # Check if this is a wildcard pattern (e.g., *.py)
            is_wildcard = '*' in include_pattern or '?' in include_pattern
            
            if is_wildcard:
                # For wildcard patterns, we need to handle them differently
                # The gitignore parser already supports wildcards with negation
                logger.info(f"Including files matching pattern: {include_pattern}")
                
                # Add negation pattern for the wildcard
                # In gitignore syntax, !*.py means "don't ignore Python files"
                ignored_patterns.append(("!" + include_pattern, user_codebase_dir))
                
                # If it's a file extension pattern like *.py, also include it in all subdirectories
                if include_pattern.startswith('*.'):
                    ignored_patterns.append(("!**/" + include_pattern, user_codebase_dir))
                    logger.info(f"Also including pattern in all subdirectories: **/{include_pattern}")
            else:
                # For directory/file paths, include the path and all its subdirectories
                logger.info(f"Including only: {include_pattern} and its subdirectories")
                ignored_patterns.append(("!" + include_pattern, user_codebase_dir))
                # Also explicitly include all subdirectories and files
                ignored_patterns.append(("!" + include_pattern + "/**", user_codebase_dir))
        
        # Return early - in include-only mode we don't use the standard exclusion patterns
        return ignored_patterns
    
    # Standard exclusion patterns (used when not in include-only mode)
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
                
            # Skip directories with problematic characters that cause regex errors
            dir_name = os.path.basename(subdir.rstrip('/'))
            if '[' in dir_name or ']' in dir_name:
                logger.debug(f"Skipping directory with brackets: {subdir}")
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
# These are calibrated to compensate for systematic underestimation in token counting
# Base multiplier of ~2.0 to address observed 50% shortfall
FILE_TYPE_MULTIPLIERS = {
    # Code files - fine-tuned based on validation testing
    '.py': 1.0, '.js': 1.0, '.ts': 1.0, '.java': 1.0, '.cpp': 1.0,
    '.c': 1.0, '.h': 1.0, '.rs': 1.0, '.go': 1.0, '.php': 1.0,
    '.jsx': 1.0, '.tsx': 1.0, '.rb': 1.0, '.cs': 1.0, '.swift': 1.0,
    # Structured data files (typically denser)
    '.json': 1.2, '.xml': 1.1,
    # Markup files
    '.html': 0.9, '.css': 1.0,
    # Configuration files
    '.yaml': 1.1, '.yml': 1.1, '.toml': 0.8,
    # Documentation
    '.md': 0.9, '.txt': 1.0,
    # Logs
    '.log': 0.9,
    # Default for other text-based files
    'default': 1.0
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
    
    # Check if we need to include external paths
    include_dirs = os.environ.get("ZIYA_INCLUDE_DIRS", "")
    if include_dirs:
        logger.info(f"Processing external paths: {include_dirs}")
        external_paths = include_dirs.split(',')
        
        # Process each external path
        for ext_path in external_paths:
            ext_path = ext_path.strip()
            if not ext_path:
                continue
                
            # Skip if path doesn't exist
            if not os.path.exists(ext_path):
                logger.warning(f"External path does not exist: {ext_path}")
                continue
                
            # Skip if not a directory
            if not os.path.isdir(ext_path):
                logger.warning(f"External path is not a directory: {ext_path}")
                continue
                
            logger.info(f"Including external path: {ext_path}")
            
            # Process the external directory
            ext_result = process_dir(ext_path, 1)
            
            # Add the external directory to the root result
            if ext_result['token_count'] > 0 or ext_result.get('children'):
                # Use the basename as the key
                basename = os.path.basename(ext_path)
                # If the basename already exists, use the full path
                if basename in root_result.get('children', {}):
                    basename = ext_path.replace('/', '_').replace('\\', '_')
                
                # Add to root result
                if 'children' not in root_result:
                    root_result['children'] = {}
                root_result['children'][basename] = ext_result
                root_result['token_count'] += ext_result['token_count']
                logger.info(f"Added external path {ext_path} as {basename} with {ext_result['token_count']} tokens")
    
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
                  '.woff', '.woff2', '.ttf', '.eot', '.zip', '.key', '.crt', '.p12', '.pfx',
                  '.der', '.pem'}:  # Added certificate and key file extensions
            return 0
        
        # Estimate tokens based on file size and type
        # Use floating point division for better accuracy, especially on small files
        # Base assumption: ~4.1 characters per token (validated from testing)
        base_chars_per_token = 4.1  # Updated based on validation testing
        estimated_tokens = file_size / base_chars_per_token
        
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
    _scan_progress["cancelled"] = True
    return _scan_progress["active"]

    def start_background_scan(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int):
        """Starts the folder scan in a background thread if not already running."""
        global _scan_thread, _folder_cache
    
        def scan_task():
            logger.info(f"Background folder scan started for {directory}.")
            # get_folder_structure updates _scan_progress globally
            result = get_folder_structure(directory, ignored_patterns, max_depth)
            
            # Cache the result
            with _scan_lock:
                _folder_cache['data'] = result
                _folder_cache['timestamp'] = time.time()
                try:
                    _folder_cache['directory_mtime'] = os.path.getmtime(directory)
                except OSError:
                    _folder_cache['directory_mtime'] = time.time()
            logger.info("Background folder scan finished and result cached.")
    
        with _scan_lock:
            if _scan_thread and _scan_thread.is_alive():
                logger.info("Folder scan is already in progress.")
                return
    
            _scan_thread = threading.Thread(target=scan_task, daemon=True, name="FolderScanThread")
            _scan_thread.start()
            logger.info("Started background folder scan thread.")
    
    
    def get_cached_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
        """
        Get folder structure. Returns cached data if available and fresh.
        If cache is stale, it starts a background scan and returns stale data or a scanning indicator.
        """
        global _folder_cache, _scan_thread
    
        current_time = time.time()
        cache_age = current_time - _folder_cache['timestamp']
        
        try:
            current_dir_mtime = os.path.getmtime(directory)
            directory_changed = current_dir_mtime > _folder_cache['directory_mtime']
        except OSError:
            directory_changed = True
            current_dir_mtime = 0

            # Return fresh cache immediately
            if _folder_cache['data'] is not None and cache_age < 10 and not directory_changed:
                logger.info(f"Returning fresh folder structure cache (age: {cache_age:.1f}s)")
            return _folder_cache['data']
        
            # If cache is stale or doesn't exist, manage background scan
            with _scan_lock:
                is_scanning = _scan_thread and _scan_thread.is_alive()
                
                if not is_scanning:
                    logger.info("Cache is stale or missing. Starting new background scan.")
                    start_background_scan(directory, ignored_patterns, max_depth)
                else:
                    logger.info("Folder scan is already in progress.")
        
            # Return current state (stale cache or empty dict) and indicate scanning
            if _folder_cache['data']:
                logger.info("Returning stale cache while scan runs in background.")
                return {**_folder_cache['data'], "_stale_and_scanning": True}
            else:
                logger.info("No cache available. Indicating scan is in progress.")
                return {"_scanning": True}
