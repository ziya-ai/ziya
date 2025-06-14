import glob
import os
import time
import signal
from typing import List, Tuple, Dict, Any, Optional
from app.utils.logging_utils import logger

from app.utils.file_utils import is_binary_file, is_document_file, is_processable_file, read_file_content
from app.utils.logging_utils import logger
from app.utils.gitignore_parser import parse_gitignore_patterns

# Simple global cache for folder structure with timestamp
_folder_cache = {'timestamp': 0, 'data': None}

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
        for pattern in additional_excludes.split(','):
            if pattern:
                ignored_patterns.append((pattern, user_codebase_dir))

    def read_gitignore(path: str) -> List[Tuple[str, str]]:
        gitignore_patterns: List[Tuple[str, str]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        gitignore_patterns.append((line, os.path.dirname(path)))
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
            patterns.extend(get_patterns_recursive(subdir))

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
            dirs[:] = [d for d in dirs if not should_ignore_fn(os.path.join(root, d)) and not d.startswith('.')]

            for file in files:
                file_path = os.path.join(root, file)
                if not should_ignore_fn(file_path) and not is_binary_file(file_path) and not file.startswith('.'):
                    file_dict[file_path] = {}

    return file_dict

def is_image_file(file_path: str) -> bool:
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.ico']
    return any(file_path.lower().endswith(ext) for ext in image_extensions)

def get_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    """
    Get the folder structure of a directory with token counts.
    
    Args:
        directory: The directory to get the structure of
        ignored_patterns: Patterns to ignore
        max_depth: Maximum depth to traverse
        
    Returns:
        Dict with folder structure including token counts
    """
    import tiktoken
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
    encoding = tiktoken.get_encoding("cl100k_base")
    
    # Ensure max_depth is at least 15 if not specified
    if max_depth <= 0:
        max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
    
    logger.debug(f"Getting folder structure for {directory} with max depth {max_depth}")
    
    # Track scanning progress
    scan_stats = {
        'directories_scanned': 0,
        'files_processed': 0,
        'start_time': time.time(),
        'slow_directories': []
    }
    
    # Set a maximum time limit for scanning (30 seconds)
    max_scan_time = 30
    
    # Define multipliers for different file types
    # These are heuristics and can be adjusted
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
        'default': 1.5  # The general multiplier we discussed
    }

    def get_file_type_multiplier(file_path: str) -> float:
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        return FILE_TYPE_MULTIPLIERS.get(ext, FILE_TYPE_MULTIPLIERS['default'])

    def count_tokens(file_path: str) -> int:
        """Count tokens in a file using tiktoken."""
        # Check if we've exceeded the time limit
        if time.time() - scan_stats['start_time'] > max_scan_time:
            return 0
            
        try:
            dir_start = time.time()
            # Skip binary files
            if not is_processable_file(file_path):
                if is_document_file(file_path):
                    logger.debug(f"Document file {file_path} failed is_processable_file check")
                dir_time = time.time() - dir_start
                if dir_time > 0.1:  # Log if binary check takes >100ms
                    logger.debug(f"Slow processable check for {file_path}: {dir_time:.2f}s")
                return 0
                
            scan_stats['files_processed'] += 1
                
            # Read file and count tokens
            content = read_file_content(file_path)
            if content:
                token_count = len(encoding.encode(content))
                # Skip files with excessive token counts (>50k tokens)
                if token_count > 50000:
                    logger.debug(f"Skipping file with excessive tokens {file_path}: {token_count} tokens")
                    return 0
                
                # Apply content-type specific multiplier
                multiplier = get_file_type_multiplier(file_path)
                adjusted_token_count = int(token_count * multiplier)
                #logger.debug(f"File: {os.path.basename(file_path)}, Original Tokens: {token_count}, Multiplier: {multiplier:.2f}, Adjusted Tokens: {adjusted_token_count}")
                return adjusted_token_count
            else:
                logger.debug(f"No content extracted from: {file_path}")
                return 0
        except Exception as e:
            logger.debug(f"Error counting tokens in {file_path}: {e}")
        return 0

    def process_dir(path: str, depth: int) -> Dict[str, Any]:
        """Process a directory recursively."""
        # Check if we've exceeded the time limit
        if time.time() - scan_stats['start_time'] > max_scan_time:
            return {'token_count': 0}
            
        if depth > max_depth:
            return {'token_count': 0}
            
        scan_stats['directories_scanned'] += 1
        dir_start_time = time.time()
        result = {'token_count': 0, 'children': {}}
        total_tokens = 0
        
        try:
            entries = os.listdir(path)
        except PermissionError:
            logger.debug(f"Permission denied for {path}")
            return {'token_count': 0}
        except OSError as e:
            logger.warning(f"OS error accessing {path}: {e}")
            return {'token_count': 0}
            
        for entry in entries:
            # Check if we've exceeded the time limit
            if time.time() - scan_stats['start_time'] > max_scan_time:
                break
                
            if entry.startswith('.'):  # Skip hidden files
                continue
                
            entry_path = os.path.join(path, entry)
            if os.path.islink(entry_path):  # Skip symlinks
                continue
                
            if should_ignore_fn(entry_path):  # Skip ignored files
                continue
                
            if os.path.isdir(entry_path):
                if depth < max_depth:
                    sub_result = process_dir(entry_path, depth + 1)
                    if sub_result['token_count'] > 0 or sub_result.get('children'):
                        result['children'][entry] = sub_result
                        total_tokens += sub_result['token_count']
            elif os.path.isfile(entry_path):
                tokens = count_tokens(entry_path)
                if tokens > 0:
                    result['children'][entry] = {'token_count': tokens}
                    total_tokens += tokens
        
        result['token_count'] = total_tokens
        
        # Log slow directory processing
        dir_time = time.time() - dir_start_time
        if dir_time > 2.0:  # Log if directory takes >2s
            scan_stats['slow_directories'].append((path, dir_time, 'slow_directory'))
            logger.warning(f"Slow directory scan for {path}: {dir_time:.2f}s ({len(entries)} entries)")
            
        return result
    
    # Process the root directory
    root_result = process_dir(directory, 1)
    
    # Return just the children of the root to match expected format
    total_time = time.time() - scan_stats['start_time']
    logger.info(f"Folder scan completed: {scan_stats['directories_scanned']} dirs, "
                f"{scan_stats['files_processed']} files in {total_time:.2f}s")
    
    if total_time >= max_scan_time:
        logger.warning(f"Folder scan timed out after {max_scan_time}s - returning partial results")
    
    if scan_stats['slow_directories']:
        logger.warning(f"Found {len(scan_stats['slow_directories'])} slow operations:")
        for path, duration, reason in scan_stats['slow_directories'][:5]:  # Log top 5
            logger.warning(f"  {path}: {duration:.2f}s ({reason})")
            
    return root_result.get('children', {})

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
    
    # Return cached results if they're fresh
    if _folder_cache['data'] is not None and cache_age < 10:
        logger.debug(f"Returning cached folder structure (age: {cache_age:.1f}s)")
        return _folder_cache['data']
    
    try:
        # Perform the actual scan with timeout protection
        logger.info(f"Starting folder scan for {directory}")
        result = get_folder_structure(directory, ignored_patterns, max_depth)
        
        # Cache the successful result
        _folder_cache['data'] = result
        _folder_cache['timestamp'] = time.time()
        
        return result
    except Exception as e:
        logger.error(f"Error during folder scan: {str(e)}")
        # Return error but don't cache it
        return {"error": f"Scan failed: {str(e)}"}
