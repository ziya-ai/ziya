import os
import time
import threading
from typing import Dict, Set, Optional, Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from app.utils.logging_utils import logger
from app.utils.file_state_manager import FileStateManager
from app.utils.file_utils import read_file_content, is_processable_file
from app.utils.gitignore_parser import parse_gitignore_patterns
from app.utils.directory_util import get_ignored_patterns

class FileChangeHandler(FileSystemEventHandler):
    """Handler for file system events to update file state manager."""
    
    def __init__(self, file_state_manager: FileStateManager, base_dir: str, cache_invalidation_callback: Optional[Callable] = None):
        self.file_state_manager = file_state_manager
        self.base_dir = os.path.abspath(base_dir)
        self.cache_invalidation_callback = cache_invalidation_callback
        # Track modified files to avoid duplicate events
        self.recently_modified: Dict[str, float] = {}
        # Debounce period in seconds - increased for rapid editor saves
        self.debounce_period = 2.0
        # Track recent events for better debouncing
        self.recent_events: Dict[str, float] = {}
        # Cache invalidation debouncing
        self.last_cache_invalidation = 0
        
        # Initialize gitignore patterns
        self.ignored_patterns = get_ignored_patterns(self.base_dir)
        self.should_ignore_fn = parse_gitignore_patterns(self.ignored_patterns)
        logger.info(f"FileChangeHandler initialized with {len(self.ignored_patterns)} gitignore patterns")
    
    def _is_editor_temp_file(self, path: str) -> bool:
        """Check if file is a temporary editor file."""
        basename = os.path.basename(path)
        # Vi/Vim temp files
        if basename.endswith('~') or basename.startswith('.') and basename.endswith('.swp'):
            return True
        # Vi numbered temp files (like 4913)
        if basename.isdigit():
            return True
        # Emacs temp files
        if basename.startswith('#') and basename.endswith('#'):
            return True
        # Python atomic write temp files (e.g., file.pyc.4347779232)
        if '.pyc.' in basename and basename.split('.pyc.')[1].isdigit():
            return True
        # __pycache__ directory files
        if '__pycache__' in path:
            return True
        return False
        
    def _should_ignore_path(self, abs_path: str) -> bool:
        """Check if a path should be ignored based on gitignore patterns."""
        try:
            # Check if the path itself should be ignored
            if self.should_ignore_fn(abs_path):
                return True
            
            # Also check if any parent directory should be ignored
            parent_dir = os.path.dirname(abs_path)
            while parent_dir and parent_dir != self.base_dir and len(parent_dir) > len(self.base_dir):
                if self.should_ignore_fn(parent_dir):
                    return True
                parent_dir = os.path.dirname(parent_dir)
            
            return False
        except Exception as e:
            logger.warning(f"Error checking if path should be ignored: {abs_path}, {str(e)}")
            return False
        
    def _should_process_event(self, rel_path: str, event_type: str) -> bool:
        """Check if we should process this event based on debouncing."""
        current_time = time.time()
        event_key = f"{rel_path}:{event_type}"
        
        if event_key in self.recent_events:
            if current_time - self.recent_events[event_key] < self.debounce_period:
                return False
        
        self.recent_events[event_key] = current_time
        return True
        
    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events."""
        if event.is_directory:
            return
            
        # Get the relative path from the base directory
        abs_path = os.path.abspath(event.src_path)
        if not abs_path.startswith(self.base_dir):
            return
            
        rel_path = os.path.relpath(abs_path, self.base_dir)
        
        # Skip editor temp files
        if self._is_editor_temp_file(abs_path):
            return
        
        # Skip non-processable files
        if not is_processable_file(abs_path):
            return
            
        # Skip files that match gitignore patterns
        if self._should_ignore_path(abs_path):
            logger.debug(f"Ignoring modified file (matches gitignore): {rel_path}")
            return
            
        # Enhanced debouncing
        if not self._should_process_event(rel_path, "modified"):
            return
        
        logger.info(f"File modified: {rel_path}")
        
        # Read the file content
        try:
            content = read_file_content(abs_path)
            if not content:
                logger.warning(f"Failed to read content from modified file: {rel_path}")
                return
                
            # Update all conversations that include this file
            self._update_conversations(rel_path, content)
            
            self._debounced_cache_invalidation()
        except Exception as e:
            logger.error(f"Error processing modified file {rel_path}: {str(e)}")
    
    def on_created(self, event: FileSystemEvent):
        """Handle file creation events."""
        if event.is_directory:
            return
            
        # Get the relative path from the base directory
        abs_path = os.path.abspath(event.src_path)
        if not abs_path.startswith(self.base_dir):
            return
            
        rel_path = os.path.relpath(abs_path, self.base_dir)
        
        # Skip editor temp files
        if self._is_editor_temp_file(abs_path):
            return
        
        # Skip non-processable files
        if not is_processable_file(abs_path):
            return
            
        # Skip files that match gitignore patterns
        if self._should_ignore_path(abs_path):
            logger.debug(f"Ignoring created file (matches gitignore): {rel_path}")
            return
            
        if not self._should_process_event(rel_path, "created"):
            return
            
        logger.info(f"File created: {rel_path}")
        
        self._debounced_cache_invalidation()
    
    def on_deleted(self, event: FileSystemEvent):
        """Handle file deletion events."""
        if event.is_directory:
            return
            
        # Get the relative path from the base directory
        abs_path = os.path.abspath(event.src_path)
        if not abs_path.startswith(self.base_dir):
            return
            
        rel_path = os.path.relpath(abs_path, self.base_dir)
        
        # Skip editor temp files
        if self._is_editor_temp_file(abs_path):
            return
        
        # Skip files that match gitignore patterns
        if self._should_ignore_path(abs_path):
            logger.debug(f"Ignoring deleted file (matches gitignore): {rel_path}")
            return
            
        if not self._should_process_event(rel_path, "deleted"):
            return
            
        logger.info(f"File deleted: {rel_path}")
        
        self._debounced_cache_invalidation()
    
    def _debounced_cache_invalidation(self):
        """Call cache invalidation with debouncing to prevent excessive calls."""
        if not self.cache_invalidation_callback:
            return
            
        current_time = time.time()
        
        # Only call if enough time has passed since last call
        if current_time - self.last_cache_invalidation < 2.0:  # 2 second debounce
            return
            
        self.last_cache_invalidation = current_time
        self.cache_invalidation_callback()
    
    def _update_conversations(self, file_path: str, content: str):
        """Update all conversations that include this file."""
        # Periodically clean up temporary conversations (every 10 file changes)
        if not hasattr(self, '_update_count'):
            self._update_count = 0
        self._update_count += 1
        if self._update_count % 10 == 0:
            self.file_state_manager.cleanup_temporary_conversations()
        
        # Find all conversations that include this file
        updated_conversations = []
        
        for conv_id, files in self.file_state_manager.conversation_states.items():
            # Skip temporary precision_ conversations
            if conv_id.startswith('precision_'):
                continue
                
            if file_path in files:
                # Update the file state
                changed_lines = self.file_state_manager.update_file_state(conv_id, file_path, content)
                if changed_lines:
                    updated_conversations.append(conv_id)
                    logger.info(f"Updated file {file_path} in conversation {conv_id} with {len(changed_lines)} changed lines")
        
        if updated_conversations:
            logger.info(f"Updated file {file_path} in {len(updated_conversations)} conversations")
            # Save the updated state
            self.file_state_manager._save_state()
        else:
            logger.debug(f"File {file_path} not found in any active conversations")


class FileWatcher:
    """Watches for file system changes and updates file state manager."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FileWatcher, cls).__new__(cls)
                cls._instance.initialized = False
        return cls._instance
    
    def initialize(self, file_state_manager: FileStateManager, base_dir: str, cache_invalidation_callback: Optional[Callable] = None):
        """Initialize the file watcher."""
        if self.initialized:
            return
            
        self.base_dir = os.path.abspath(base_dir)
        self.file_state_manager = file_state_manager
        self.event_handler = FileChangeHandler(file_state_manager, self.base_dir, cache_invalidation_callback)
        
        # Add a special handler for .gitignore files
        self.gitignore_handler = GitignoreChangeHandler(self.event_handler)
        
        self.observer = Observer()
        self.observer.schedule(self.event_handler, self.base_dir, recursive=True)
        self.observer.schedule(self.gitignore_handler, self.base_dir, recursive=True)
        self.observer.start()
        self.initialized = True
        
        logger.info(f"File watcher initialized for directory: {self.base_dir}")
    
    def stop(self):
        """Stop the file watcher."""
        if not self.initialized:
            return
            
        self.observer.stop()
        self.observer.join()
        self.initialized = False
        
        logger.info("File watcher stopped")


class GitignoreChangeHandler(FileSystemEventHandler):
    """Handler for .gitignore file changes to update ignore patterns."""
    
    def __init__(self, file_change_handler: FileChangeHandler):
        self.file_change_handler = file_change_handler
        self.debounce_period = 1.0
        self.last_refresh_time = 0
        
    def on_modified(self, event: FileSystemEvent):
        """Handle .gitignore file modification events."""
        if event.is_directory:
            return
            
        # Check if this is a .gitignore file
        if not os.path.basename(event.src_path) == '.gitignore':
            return
            
        # Debounce to avoid multiple refreshes
        current_time = time.time()
        if current_time - self.last_refresh_time < self.debounce_period:
            return
            
        self.last_refresh_time = current_time
        
        logger.info(f".gitignore file modified: {event.src_path}")
        self._refresh_gitignore_patterns()
    
    def on_created(self, event: FileSystemEvent):
        """Handle .gitignore file creation events."""
        if event.is_directory:
            return
            
        # Check if this is a .gitignore file
        if not os.path.basename(event.src_path) == '.gitignore':
            return
            
        logger.info(f".gitignore file created: {event.src_path}")
        self._refresh_gitignore_patterns()
    
    def _refresh_gitignore_patterns(self):
        """Refresh gitignore patterns in the file change handler."""
        try:
            base_dir = self.file_change_handler.base_dir
            self.file_change_handler.ignored_patterns = get_ignored_patterns(base_dir)
            self.file_change_handler.should_ignore_fn = parse_gitignore_patterns(self.file_change_handler.ignored_patterns)
            logger.info(f"Refreshed gitignore patterns: {len(self.file_change_handler.ignored_patterns)} patterns")
        except Exception as e:
            logger.error(f"Error refreshing gitignore patterns: {str(e)}")


# Singleton instance
_file_watcher = None

def get_file_watcher() -> FileWatcher:
    """Get the file watcher singleton instance."""
    global _file_watcher
    if _file_watcher is None:
        _file_watcher = FileWatcher()
    return _file_watcher


def initialize_file_watcher(file_state_manager: FileStateManager, base_dir: Optional[str] = None, cache_invalidation_callback: Optional[Callable] = None):
    """Initialize the file watcher with the given file state manager."""
    if base_dir is None:
        base_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    
    watcher = get_file_watcher()
    watcher.initialize(file_state_manager, base_dir, cache_invalidation_callback)
    return watcher
