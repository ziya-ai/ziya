import glob
import os
import time
import re
import threading
import signal
from typing import List, Tuple, Dict, Any, Optional
import mimetypes
from pathlib import Path

from app.utils.file_utils import is_binary_file, is_document_file, is_processable_file, read_file_content
from app.utils.document_extractor import is_tool_backed_file
from app.utils.logging_utils import logger
from app.utils.gitignore_parser import parse_gitignore_patterns

# Enhanced global cache for folder structure with directory modification tracking
_folder_cache = {
    'timestamp': 0, 
    'data': None, 
    'directory_mtime': 0
}

# Global progress tracking
_scan_progress = {"active": False, "progress": {}, "cancelled": False, "start_time": 0, "last_update": 0, "estimated_total": 0}

# Add new globals for background scanning
_scan_thread: Optional[threading.Thread] = None
_scan_lock = threading.Lock()
    
# Track visited directories to prevent infinite loops
_visited_directories = set()

# Global cache for ignored patterns to avoid re-scanning on every API call
_ignored_patterns_cache: Optional[List[Tuple[str, str]]] = None
_ignored_patterns_cache_dir: Optional[str] = None
_ignored_patterns_cache_time: float = 0
IGNORED_PATTERNS_CACHE_TTL = 3600  # 1 hour - gitignore files rarely change

def get_ignored_patterns(directory: str) -> List[Tuple[str, str]]:
    global _ignored_patterns_cache, _ignored_patterns_cache_dir, _ignored_patterns_cache_time
    
    # Check cache first - avoid rescanning entirely
    current_time = time.time()
    if (_ignored_patterns_cache is not None and 
        _ignored_patterns_cache_dir == directory and
        (current_time - _ignored_patterns_cache_time) < IGNORED_PATTERNS_CACHE_TTL):
        logger.debug(f"♻️ Using cached gitignore patterns ({len(_ignored_patterns_cache)} patterns)")
        return _ignored_patterns_cache
    
    logger.info(f"🔍 Building gitignore patterns for {directory}...")
    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", directory)
    
    # CRITICAL: Warn users EARLY if scanning will be slow
    import sys
    home_dir = os.path.expanduser("~")
    is_home_or_near_home = (
        os.path.abspath(directory) == home_dir or
        os.path.abspath(directory).startswith(home_dir + os.sep) and
        len(os.path.abspath(directory).replace(home_dir, '').split(os.sep)) <= 2
    )
    
    # Quick sampling: check how many entries are in the root
    try:
        root_entries = len(os.listdir(directory))
    except (PermissionError, OSError):
        root_entries = 0
    
    # Warn if this looks like it will be slow
    if is_home_or_near_home or root_entries > 50:
        print("\n" + "="*70, file=sys.stderr)
        print("⚠️  SCANNING LARGE DIRECTORY - THIS MAY TAKE SEVERAL MINUTES", file=sys.stderr)
        print("="*70, file=sys.stderr)
        print(f"📁 Directory: {directory}", file=sys.stderr)
        print(f"📊 Root contains {root_entries} entries", file=sys.stderr)
        print("\n💡 TIP: For faster startup, start Ziya in a project root directory", file=sys.stderr)
        print("   Or use: --include-only <path> to scan specific directories", file=sys.stderr)
        print("="*70 + "\n", file=sys.stderr)
        
        # Show progress as we process gitignore patterns
        print("🔍 Scanning for .gitignore files...", file=sys.stderr, flush=True)
    
    scan_start_time = time.time()
    
    # Get include directories that should override default exclusions
    include_dirs = os.environ.get("ZIYA_INCLUDE_DIRS", "")
    include_patterns_override = set()
    if include_dirs:
        logger.info(f"Include directories specified (will override defaults): {include_dirs}")
        for include_path in include_dirs.split(','):
            include_path = include_path.strip()
            if not include_path:
                continue
            
            # Normalize the path to handle both absolute and relative paths
            if os.path.isabs(include_path):
                # For absolute paths, get the basename for pattern matching
                basename = os.path.basename(include_path.rstrip(os.sep))
                include_patterns_override.add(basename)
            else:
                # For relative paths, use as-is
                include_patterns_override.add(include_path.rstrip(os.sep))
            
            logger.info(f"Will override default exclusions for pattern: {include_path}")
    
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
                # For directory/file paths, we need to un-ignore all parent directories
                # for the gitignore parser to allow traversal down to the target path
                
                # Split the path into components
                path_parts = include_pattern.split('/')
                
                # Add negation patterns for each parent directory leading to the target
                # This is for nested paths like 'Package/src' to work
                # Without this, gitignore won't traverse into 'Package' to find 'src'
                accumulated_path = ""
                for i, part in enumerate(path_parts):
                    if i == 0:
                        accumulated_path = part
                    else:
                        accumulated_path = accumulated_path + "/" + part
                    
                    # Add negation for this path level
                    ignored_patterns.append(("!" + accumulated_path, user_codebase_dir))
                    logger.info(f"Including parent path for traversal: {accumulated_path}")
                
                # Add the wildcard pattern for all subdirectories of the final target
                # This is in addition to the individual parent patterns above
                logger.info(f"Including only: {include_pattern} and its subdirectories")
                ignored_patterns.append(("!" + include_pattern + "/**", user_codebase_dir))
                
        # Return early - in include-only mode we don't use the standard exclusion patterns
        return ignored_patterns
    
    # Standard exclusion patterns (used when not in include-only mode)
    ignored_patterns: List[Tuple[str, str]] = [
        # Common large directories that should always be skipped
        ("Library", user_codebase_dir),  # Skip entire Library directory on macOS
        ("Library/Developer", user_codebase_dir),
        ("Library/Caches", user_codebase_dir),
        ("Library/Application Support", user_codebase_dir),
        (".Trash", user_codebase_dir),
        ("Downloads", user_codebase_dir),
        ("Applications", user_codebase_dir),
        
        # Standard project exclusions
        ("poetry.lock", user_codebase_dir),
        ("package-lock.json", user_codebase_dir),
        (".DS_Store", user_codebase_dir),
        (".git", user_codebase_dir),
        (".svn", user_codebase_dir),
        (".hg", user_codebase_dir),
        (".cache", user_codebase_dir),
        (".cargo", user_codebase_dir),
        (".npm", user_codebase_dir),
        ("node_modules", user_codebase_dir),
        ("dist", user_codebase_dir),
        ("__pycache__", user_codebase_dir),
        ("*.pyc", user_codebase_dir),
        (".venv", user_codebase_dir), # Common virtual environment folder
        ("venv", user_codebase_dir),  # Common virtual environment folder
        (".vscode", user_codebase_dir), # VSCode settings
        (".idea", user_codebase_dir),   # JetBrains IDE settings
        ("target", user_codebase_dir),  # Rust build artifacts
        ("pkg", user_codebase_dir),     # Go packages
        ("vendor", user_codebase_dir),  # Vendor dependencies
        (".pytest_cache", user_codebase_dir),
    ]
    
    # Filter out patterns that are overridden by --include
    if include_patterns_override:
        original_count = len(ignored_patterns)
        ignored_patterns = [
            (pattern, base) for pattern, base in ignored_patterns
            if pattern not in include_patterns_override and 
               not any(pattern.startswith(override + os.sep) or pattern == override 
                      for override in include_patterns_override)
        ]
        removed_count = original_count - len(ignored_patterns)
        if removed_count > 0:
            logger.info(f"Removed {removed_count} default exclusion patterns due to --include overrides")
            logger.info(f"Overridden patterns: {[p for p, _ in ignored_patterns if p in include_patterns_override]}")
    
    # Add additional exclude directories from environment variable if it exists
    additional_excludes = os.environ.get("ZIYA_ADDITIONAL_EXCLUDE_DIRS", "")
    if additional_excludes:
        logger.info(f"Processing additional excludes: {additional_excludes}")
        for pattern in additional_excludes.split(','):
            pattern = pattern.strip()
            if pattern:
                ignored_patterns.append((pattern, user_codebase_dir))
                logger.info(f"Added exclude pattern: {pattern}")
    
    scan_duration = time.time() - scan_start_time
    
    # Warn if we found an excessive number of patterns (performance killer)
    if len(ignored_patterns) > 1000:
        logger.warning(f"⚠️ Found {len(ignored_patterns)} gitignore patterns - this will slow down scanning significantly")
        logger.warning(f"💡 Consider using --include-only to scan specific directories only")
        import sys
        print(f"\n⚠️ Found {len(ignored_patterns)} gitignore patterns - scanning will be slower", file=sys.stderr)
        print(f"💡 Tip: Use 'ziya --include-only path/to/project' for faster startup\n", file=sys.stderr, flush=True)
    
    # Only log at debug level to reduce noise
    pattern_msg = f"Total ignore patterns: {len(ignored_patterns)}"
    logger.debug(pattern_msg)
    
    # If scanning took more than 5 seconds, warn the user
    if scan_duration > 5.0:
        print(f"\n⏱️  .gitignore scan took {scan_duration:.1f}s", file=sys.stderr)
        print("💡 Tip: Large directory trees slow down startup significantly", file=sys.stderr)
        print(f"   Consider starting Ziya in a smaller directory\n", file=sys.stderr, flush=True)
    
    for pattern, base in ignored_patterns:
        logger.debug(f"Ignore pattern: {pattern} (base: {base})")
    
    # Cache the results for future calls
    _ignored_patterns_cache = ignored_patterns
    _ignored_patterns_cache_dir = directory
    _ignored_patterns_cache_time = time.time()

    def read_gitignore(path: str) -> List[Tuple[str, str]]:
        gitignore_patterns: List[Tuple[str, str]] = []
        
        # Show progress for very large scans
        if scan_duration > 10.0 and (time.time() - scan_start_time) % 5 < 0.1:
            print(f"\r⏳ Still scanning... ({time.time() - scan_start_time:.0f}s)", 
                  end='', file=sys.stderr, flush=True)
        
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
        """Recursively find gitignore patterns with optimizations for speed."""
        patterns: List[Tuple[str, str]] = []
        
        # Periodic progress for deep recursion
        if (time.time() - scan_start_time) > 10.0 and (time.time() - scan_start_time) % 3 < 0.1:
            print(f"\r🔍 Scanning deep directories... ({time.time() - scan_start_time:.0f}s elapsed)", 
                  end='', file=sys.stderr, flush=True)
        
        gitignore_path = os.path.join(path, ".gitignore")
        if os.path.exists(gitignore_path):
            patterns.extend(read_gitignore(gitignore_path))
        
        # Calculate current depth relative to starting directory
        try:
            relative_path = os.path.relpath(path, directory)
            current_depth = 0 if relative_path == '.' else len(relative_path.split(os.sep))
        except ValueError:
            # Can't calculate relative path (different drives on Windows)
            current_depth = 0
        
        # Limit recursion depth for gitignore scanning (5 levels should be more than enough)
        MAX_GITIGNORE_DEPTH = 5
        if current_depth >= MAX_GITIGNORE_DEPTH:
            logger.debug(f"Reached max gitignore scan depth at {path}")
            return patterns
        
        # Use os.scandir() for faster iteration than glob
        try:
            entries = os.scandir(path)
        except (PermissionError, OSError) as e:
            logger.debug(f"Cannot access directory {path}: {e}")
            return patterns
        
        for entry in entries:
            # Skip non-directories
            if not entry.is_dir(follow_symlinks=False):
                continue
            
            subdir = entry.path + os.sep
            
            # Skip symlinks to prevent infinite loops
            if entry.is_symlink():
                logger.debug(f"Skipping symlink directory: {subdir}")
                continue
                
            # Skip directories with problematic characters that cause regex errors
            dir_name = entry.name
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

    # Skip recursive gitignore scanning for home directories - too slow and not useful
    home_dir = os.path.expanduser("~")
    is_home = os.path.abspath(directory).startswith(home_dir) and os.path.abspath(directory) == home_dir
    
    if is_home:
        logger.info("Skipping recursive .gitignore scan for home directory (using defaults only)")
    else:
        # Only scan recursively for project directories
        ignored_patterns.extend(get_patterns_recursive(directory))
        logger.debug(f"Found {len(ignored_patterns)} total patterns after recursive scan")
    
    # CRITICAL: Add negation patterns for --include paths to override ALL gitignore patterns
    # This must happen AFTER all gitignore patterns are collected
    if include_patterns_override:
        logger.info(f"Adding negation patterns to force inclusion of: {include_patterns_override}")
        for include_path in include_patterns_override:
            # Add negation pattern for the path itself
            ignored_patterns.append((f"!{include_path}", user_codebase_dir))
            # Add negation pattern for all contents within the path
            ignored_patterns.append((f"!{include_path}/**", user_codebase_dir))
            
            # Also add negation patterns for parent directories to ensure traversal
            # This is necessary because gitignore won't traverse into ignored parent dirs
            if '/' in include_path:
                path_parts = include_path.split('/')
                accumulated_path = ""
                for i, part in enumerate(path_parts[:-1]):  # Exclude the last part (already handled above)
                    if i == 0:
                        accumulated_path = part
                    else:
                        accumulated_path = accumulated_path + "/" + part
                    ignored_patterns.append((f"!{accumulated_path}", user_codebase_dir))
            
            logger.info(f"Added negation patterns for --include path: {include_path}")
    
    # Cache the results for future calls
    _ignored_patterns_cache = ignored_patterns
    _ignored_patterns_cache_dir = directory
    _ignored_patterns_cache_time = time.time()
    
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


def detect_large_directory_and_warn(directory: str) -> None:
    """Detect if we're scanning a potentially large directory and warn the user."""
    import sys
    
    # Check if this is a home directory
    home_dir = os.path.expanduser("~")
    is_home_or_near_home = (
        os.path.abspath(directory) == home_dir or
        os.path.abspath(directory).startswith(home_dir + os.sep) and
        len(os.path.abspath(directory).replace(home_dir, '').split(os.sep)) <= 2
    )
    
    # Quick sampling: check how many entries are in the root
    try:
        root_entries = len(os.listdir(directory))
    except (PermissionError, OSError):
        root_entries = 0
    
    # Warn if this looks like it will be slow
    if is_home_or_near_home or root_entries > 50:
        print("\n" + "="*70, file=sys.stderr)
        print("⚠️  SCANNING LARGE DIRECTORY - THIS MAY TAKE SEVERAL MINUTES", file=sys.stderr)
        print("="*70, file=sys.stderr)
        print(f"📁 Directory: {directory}", file=sys.stderr)
        print(f"📊 Root contains {root_entries} entries", file=sys.stderr)
        print("\n💡 TIP: For faster startup, start Ziya in a project root directory", file=sys.stderr)
        print("   Or use: --include-only <path> to scan specific directories", file=sys.stderr)
        print("="*70 + "\n", file=sys.stderr)


def estimate_directory_count(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int = 3) -> int:
    """Quick estimate of total directories to scan (only go 3 levels deep for estimate)."""
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
    count = 0
    
    # CRITICAL: Skip Library directory in estimation to avoid hanging
    if 'Library' in directory or directory.endswith('/Library'):
        logger.info("Skipping Library directory in estimation")
        return 0
    
    def quick_count(path: str, depth: int) -> int:
        # Even more aggressive depth limit for estimation
        if depth > 2:  # Only scan 2 levels deep for estimate
            return 0
        
        # Check for cancellation
        if _scan_progress.get("cancelled"):
            return 0
        
        nonlocal count
        try:
            entries = os.scandir(path)
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.is_symlink():
                    continue
                entry_path = entry.path
                
                # Skip Library and other known slow directories
                basename = entry.name
                if basename in {'Library', 'Applications', 'Downloads', '.Trash'}:
                    continue
                
                if should_ignore_fn(entry_path):
                    continue
                count += 1
                quick_count(entry_path, depth + 1)
                
                # Add a limit to prevent estimation from taking too long
                if count > 1000:  # Stop counting after 1000 dirs found
                    return count
        except (PermissionError, OSError):
            pass
        return count
    
    # Set a short timeout for estimation
    import signal
    
    def timeout_handler(signum, frame):
        raise TimeoutError("Estimation timeout")
    
    # Only use timeout on Unix-like systems
    if hasattr(signal, 'SIGALRM'):
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(5)  # 5 second timeout for estimation
        try:
            count = quick_count(directory, 0)
        except TimeoutError:
            logger.warning("Estimation timed out after 5s, skipping estimate")
            count = 0
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windows or other systems - just run without timeout
        count = quick_count(directory, 0)
    
    # Extrapolate to full depth
    if count > 0:
        # Scale estimate based on actual max_depth vs estimation depth
        scaled = count * (max_depth // 2) if max_depth > 2 else count
        return min(scaled, count * 10)  # Cap at 10x to avoid ridiculous estimates
    return 0


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
    import traceback
    with open('/tmp/scan_debug.txt', 'a') as f:
        f.write(f"\n\n=== get_folder_structure CALLED at {time.time()} ===\n")
        f.write(f"Directory: {directory}\n")
        f.write(f"Call stack:\n")
        for line in traceback.format_stack()[:-1]:
            f.write(line)
        f.write(f"=== END CALL STACK ===\n\n")
    
    logger.debug(f"🔍 PERF: Starting ULTRA-FAST folder scan for directory: {directory}")
    
    logger.debug(f"🔍 PERF: Starting ULTRA-FAST folder scan for directory: {directory}")
    
    # Check if we have cached results first
    cached_result = get_cached_folder_structure_with_tokens(directory, ignored_patterns, max_depth)
    if cached_result:
        return cached_result
    
    # Warn users if this looks like it will be slow
    detect_large_directory_and_warn(directory)

    # Use compatibility layer for tiktoken with automatic fallbacks
    from app.utils.tiktoken_compat import tiktoken
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
    _scan_progress["cancelled"] = False
    _scan_progress["start_time"] = time.time()
    _scan_progress["last_update"] = time.time()
    
    # Check for early cancellation before expensive operations
    if _scan_progress.get("cancelled"):
        logger.info("Scan cancelled before starting")
        return {"error": "Scan cancelled by user", "cancelled": True}
    
    logger.info("Estimating directory count for progress tracking...")
    # Quick estimate of total work for better progress reporting
    try:
        # Skip estimation for home directories - it's too slow
        home_dir = os.path.expanduser("~")
        is_home = os.path.abspath(directory).startswith(home_dir)
        
        if is_home:
            logger.info("Skipping estimation for home directory (too large)")
            estimated_total = 0
        else:
            estimated_total = estimate_directory_count(directory, ignored_patterns)
        
        # Check cancellation after estimation
        if _scan_progress.get("cancelled"):
            logger.info("Scan cancelled during estimation")
            _scan_progress["active"] = False
            return {"error": "Scan cancelled by user", "cancelled": True}
        
    except Exception as e:
        logger.warning(f"Failed to estimate directory count: {e}")
        estimated_total = 0
    
    _scan_progress["estimated_total"] = estimated_total
    logger.info(f"Estimated ~{estimated_total} directories to scan" if estimated_total > 0 else "Starting scan without estimate (will show raw counts)")
    # Set a maximum time limit for scanning (45 seconds default, configurable)
    max_scan_time = int(os.environ.get("ZIYA_SCAN_TIMEOUT", "45"))  # Increased default, configurable
    
    # Track progress for intelligent timeout
    last_progress_check = {'time': time.time(), 'directories': 0}
    
    # Add progress tracking
    last_progress_time = time.time()
    progress_interval = 2.0  # Log progress every 2 seconds
    last_stderr_progress_time = time.time()

    def process_dir(path: str, depth: int) -> Dict[str, Any]:
        """Process a directory recursively."""
        nonlocal last_progress_check  # Declare nonlocal so we can modify it
        
        # CRITICAL: Skip known problematic directories BEFORE any expensive operations
        # Check basename first (fastest check)
        dir_basename = os.path.basename(path.rstrip(os.sep))
        
        # AGGRESSIVE SKIP: Library directory alone causes 90% of slowness in home dirs
        if dir_basename in {
            'Library',  # Skip entire Library directory - single biggest slowdown
            'CoreSimulator', 'Containers', 'Caches', 'Logs',
            'Application Support', 'Developer', 'News', 'Trial',
            '.Trash', 'Downloads', 'Applications', 'Movies', 'Music', 'Pictures'
        }:
            logger.debug(f"Early skip of known slow directory: {path}")
            return {'token_count': 0}
        
        # Also check depth immediately to avoid processing deep paths
        if depth > max_depth:
            return {'token_count': 0}
        
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
            _scan_progress["active"] = False
            return {'token_count': 0, 'cancelled': True}
        
        # Intelligent timeout: Only abort if we're NOT making progress
        current_time = time.time()
        elapsed = current_time - scan_stats['start_time']
        
        # Check progress every 10 seconds
        if elapsed - (last_progress_check['time'] - scan_stats['start_time']) > 10:
            dirs_scanned_since_check = scan_stats['directories_scanned'] - last_progress_check['directories']
            
            if dirs_scanned_since_check > 0:
                # Making progress - extend timeout
                last_progress_check = {
                    'time': current_time,
                    'directories': scan_stats['directories_scanned']
                }
                logger.debug(f"Progress detected: {dirs_scanned_since_check} dirs in last 10s, continuing scan")
            elif elapsed > max_scan_time:
                # No progress AND timeout exceeded
                logger.warning(f"No progress in 10s and timeout reached ({elapsed:.1f}s), aborting")
                return {'token_count': 0, 'timeout': True}
        
        # Hard timeout at 2x the configured timeout regardless of progress
        if elapsed > max_scan_time * 2:
            logger.warning(f"Timeout reached at {elapsed:.1f}s while processing {path}")
            return {'token_count': 0, 'timeout': True}
        
        # Log progress periodically
        nonlocal last_progress_time
        
        scan_stats['directories_scanned'] += 1
        
        # Update global progress immediately
        _scan_progress["progress"] = {
            "directories": scan_stats['directories_scanned'],
            "files": scan_stats['files_processed'],
            "elapsed": int(elapsed)
        }
        _scan_progress["last_update"] = time.time()
        
        result = {'token_count': 0, 'children': {}}
        total_tokens = 0
        
        try:
            entries = os.listdir(path)
            logger.debug(f"📁 Directory {path} has {len(entries)} entries: {entries}")
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
            
            # Progress logging
            current_time = time.time()
            elapsed = current_time - scan_stats['start_time']
            if current_time - last_progress_time > progress_interval:
                progress_pct = f" ({scan_stats['directories_scanned']}/{estimated_total}, {(scan_stats['directories_scanned']/estimated_total*100):.0f}%)" if estimated_total > 0 else ""
                logger.info(f"📊 Scan progress: {scan_stats['directories_scanned']} dirs{progress_pct}, {scan_stats['files_processed']} files in {elapsed:.1f}s")
                last_progress_time = current_time
                
                # Also print to stderr every 5 seconds for user visibility
                nonlocal last_stderr_progress_time
                if current_time - last_stderr_progress_time > 5.0:
                    import sys
                    print(f"\r⏳ Scanning... {scan_stats['directories_scanned']} directories, "
                          f"{scan_stats['files_processed']} files ({elapsed:.0f}s elapsed)",
                          end='', file=sys.stderr, flush=True)
                    last_stderr_progress_time = current_time
                    if elapsed > 30:
                        print("\n💡 This is taking a while. Consider starting Ziya in a smaller directory.", file=sys.stderr)
                
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
                file_start = time.time()
                tokens = estimate_tokens_fast(entry_path)
                file_time = time.time() - file_start
                if file_time > 0.5:  # Log if a single file takes >0.5s
                    logger.warning(f"🔍 PERF: Slow token estimation for {entry}: {file_time*1000:.1f}ms")
                
                logger.debug(f"📄 File {entry}: tokens={tokens}")
                if tokens > 0 or tokens == -1:  # Include tool-backed files (marked as -1)
                    scan_stats['files_processed'] += 1
                    result['children'][entry] = {'token_count': tokens}
                    if tokens > 0:  # Only add positive tokens to total
                        total_tokens += tokens
                else:
                    logger.debug(f"⏭️  Skipping file {entry} - zero tokens")
        
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
    # Note: The ignore pattern override above handles paths within the codebase
    # This section handles absolute paths outside the codebase directory
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
    import sys
    completion_msg = (f"✅ Folder scan completed: {scan_stats['directories_scanned']} dirs, "
                     f"{scan_stats['files_processed']} files in {total_time:.2f}s")
    logger.info(completion_msg)
    
    # Clear the progress line and print completion
    print("\r" + " "*100 + "\r" + completion_msg, file=sys.stderr)
    print("", file=sys.stderr)  # Add newline
    
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
    logger.debug(f"root_result keys: {list(root_result.keys())}")
    logger.debug(f"root_result has {len(root_result.get('children', {}))} children")
    
    # Add metadata flags at the top level, not inside children
    if total_time >= max_scan_time:
        logger.warning(f"Folder scan timed out after {max_scan_time}s, returning partial results with {len(result)} entries")
        # Return metadata alongside children, not inside children
        return {
            **result,
            '_timeout': True,
            '_partial': True
        }
    
    # Check if cancelled
    if _scan_progress.get("cancelled"):
        return {
            **result,
            '_cancelled': True
        }
    
    _visited_directories.clear()
    logger.info(f"Returning folder structure with {len(result)} top-level entries")
    return result

# Add new fast estimation function
def estimate_tokens_fast(file_path: str) -> int:
    """Fast token estimation based on file size and type."""
    try:
        # Check for tool-backed files first (before any file I/O)
        from app.utils.document_extractor import is_tool_backed_file
        if is_tool_backed_file(file_path):
            return -1  # Special marker for tool-backed files
        
        # Get file size
        file_size = os.path.getsize(file_path)
        
        # For very large files (>10MB), estimate tokens without reading
        # This prevents hanging on huge files while keeping them visible
        if file_size > 10 * 1024 * 1024:  # 10MB
            logger.info(f"Large file detected: {file_path} ({file_size / (1024*1024):.1f}MB) - using size-based estimate")
            # Rough estimate: 4.1 chars per token
            base_chars_per_token = 4.1
            estimated_tokens = int(file_size / base_chars_per_token)
            multiplier = get_file_type_multiplier(file_path)
            return int(estimated_tokens * multiplier)
        
        # Quick binary check using file extension
        _, ext = os.path.splitext(file_path.lower())
        if ext in {'.pyc', '.pyo', '.pyd', '.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.pcap', '.pcapng',
                  '.core', '.bin', '.exe', '.dll', '.so', '.dylib', '.class',
                  '.woff', '.woff2', '.ttf', '.eot', '.zip', '.key', '.crt', '.p12', '.pfx',
                  '.der', '.pem', '.stl', '.obj', '.fbx', '.blend'}:  # Binary 3D formats only
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
        logger.debug(f"🔍 PERF: Ensuring background token calculation for {directory}")
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
            logger.debug("🔍 PERF: Returning cached folder structure with tokens")
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
        logger.debug(f"🔍 PERF: Cached folder structure with {len(result)} entries")

def start_background_token_calculation(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int):
    """Start background thread to calculate accurate token counts (with deduplication)."""
    global _background_thread, _background_thread_lock
    
    with _background_thread_lock:
        # Check if a background thread is already running
        if _background_thread is not None and _background_thread.is_alive():
            logger.debug("🔍 PERF: Background token calculation already in progress, skipping duplicate request")
            return
        
        logger.debug(f"🔍 PERF: Starting new background token calculation thread for {directory}")
    
    def calculate_accurate_tokens():
        try:
            logger.debug(f"🔍 PERF: Starting background accurate token calculation for {directory}")
            
            # Early return is still in place - remove it when ready to re-enable
            logger.debug("🔍 PERF: Background token calculation temporarily disabled")
            return
            
            # Use compatibility layer for tiktoken with automatic fallbacks
            from app.utils.tiktoken_compat import tiktoken
            
            if tiktoken is None:
                return result
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
                                    logger.debug(f"🔍 PERF: Background processed {files_processed} files so far")
                    except Exception as e:
                        logger.debug(f"Error processing {file_path}: {e}")
                        continue
            
            # Update cache with accurate counts
            logger.debug(f"🔍 PERF: Background calculation complete: {files_processed} files processed")
            # Store accurate counts in a separate cache for API access
            logger.debug(f"🔍 PERF: Storing {len(accurate_counts)} accurate token counts in cache")
            global _accurate_token_cache
            _accurate_token_cache = accurate_counts
            
            # Also update the folder structure cache with accurate counts
            cache_key = f"{directory}:{max_depth}:{hash(str(ignored_patterns))}"
            with _cache_lock:
                if cache_key in _token_cache:
                    _token_cache[cache_key]["_accurate_tokens"] = accurate_counts
                    logger.debug(f"🔍 PERF: Updated folder cache with {len(accurate_counts)} accurate token counts")
            
        except Exception as e:
            logger.error(f"Background token calculation failed: {e}")
        finally:
            # Clean up thread reference when done
            global _background_thread
            with _background_thread_lock:
                _background_thread = None
            logger.debug("🔍 PERF: Background token calculation thread completed")
    
    with _background_thread_lock:
        _background_thread = threading.Thread(target=calculate_accurate_tokens, daemon=True, name="TokenCalculation")
        _background_thread.start()
        logger.debug(f"🔍 PERF: Background thread started: {_background_thread.name}")

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
    # Use compatibility layer for tiktoken with automatic fallbacks
    from app.utils.tiktoken_compat import tiktoken
    
    try:
        # Tool-backed files should return special marker
        if is_tool_backed_file(file_path):
            return -1  # Special marker
        
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

def is_scan_healthy() -> bool:
    """Check if the current scan is healthy (making progress and not stuck)."""
    global _scan_progress, _scan_thread
    
    if not _scan_thread or not _scan_thread.is_alive():
        return False
        
    current_time = time.time()
    start_time = _scan_progress.get("start_time", 0)
    last_update = _scan_progress.get("last_update", 0)
    
    # Consider scan unhealthy if:
    # 1. Running for more than 5 minutes (300 seconds)
    # 2. No progress update in the last 2 minutes (120 seconds)
    if start_time == 0:
        return True  # No start time recorded, assume healthy
        
    return (current_time - start_time < 300) and (current_time - last_update < 120)

def get_basic_folder_structure(directory: str) -> Dict[str, Any]:
    """
    Get a basic folder structure as fallback when full scanning fails.
    This provides minimal functionality instead of perpetual scanning state.
    """
    try:
        basic_structure = {"children": {}}
        
        # Try to list immediate directory contents only
        for item in os.listdir(directory):
            if item.startswith('.'):
                continue  # Skip hidden files/folders
            item_path = os.path.join(directory, item)
            if os.path.isdir(item_path):
                basic_structure["children"][item] = {"children": {}, "token_count": 0}
            elif os.path.isfile(item_path):
                basic_structure["children"][item] = {"token_count": 0}
                
        return basic_structure
    except Exception as e:
        logger.error(f"Even basic folder structure failed: {e}")
        return {"children": {}, "_error": f"Directory access failed: {str(e)}"}

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
        
        # Check if scan is healthy if it appears to be running
        if is_scanning and not is_scan_healthy():
            logger.warning("Scan appears stuck, cleaning up and restarting")
            _scan_progress["active"] = False
            _scan_progress["error"] = "Scan timed out or stalled"  
            # Abandon stuck thread (don't join)
            _scan_thread = None
            is_scanning = False
        
        if not is_scanning:
            logger.info("Cache is stale or missing. Starting new background scan.")
            start_background_scan(directory, ignored_patterns, max_depth)
            # Give scan a moment to start
            time.sleep(0.1)
            return {"_scanning": True, "children": {}}
        else:
            logger.info("Folder scan is already in progress.")
            # Return current state (stale cache or empty dict) and indicate scanning
            if _folder_cache['data']:
                logger.info("Returning stale cache while scan runs in background.")
                return {**_folder_cache['data'], "_stale_and_scanning": True}
            else:
                logger.info("No cache available. Indicating scan is in progress.")
                return {"_scanning": True, "children": {}}
                return {"_scanning": True}
