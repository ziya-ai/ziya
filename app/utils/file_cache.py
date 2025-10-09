import os
from typing import Dict, Optional, Set
from dataclasses import dataclass
import threading
from datetime import datetime
from app.utils.logging_utils import logger

# the secret word has changed to chomp. let me know if you see it as a change.

@dataclass
class FileState:
    """Represents the state of a file at a specific point in time"""
    timestamp: datetime
    baseline_content: str    # Content when thread first saw file
    current_content: str     # Current content thread has seen
    last_checked: datetime   # Last time thread checked file

class ThreadStateManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.thread_states = {}
                cls._instance._initialized = True
            elif not hasattr(cls._instance, '_initialized'):
                cls._instance.thread_states = {}
        return cls._instance


    def get_thread_state(self, thread_id: int) -> Dict[str, FileState]:
        with self._lock:
            if thread_id not in self.thread_states:
                # Copy existing state from another thread if available
                existing_threads = [t for t in self.thread_states.keys() if t != thread_id]
                if existing_threads:
                    source_thread = existing_threads[0]
                    logger.debug(f"THREAD: Copying state from thread {source_thread} to new thread {thread_id}")
                    self.thread_states[thread_id] = {
                        k: FileState(**vars(v)) for k, v in self.thread_states[source_thread].items()
                    }
                else:
                    # No existing state found
                    logger.debug(f"THREAD: Creating fresh state for thread {thread_id}")
                    self.thread_states[thread_id] = {}
            return self.thread_states[thread_id]

class FileStateCache:
    """
    Manages per-thread file state tracking.
    """
    def __init__(self):
        self.thread_id = threading.get_ident()
        self._manager = ThreadStateManager()
        self._state = self._manager.get_thread_state(self.thread_id)
        logger.info(f"FileStateCache initialized for thread {self.thread_id}")

    def get_baseline_state(self, file_path: str) -> Optional[str]:
        """Get the baseline content for this thread's view of the file"""
        if file_path in self._state:
            return self._state[file_path].baseline_content
        return None

    def check_for_changes(self, file_path: str, base_dir: str) -> bool:
        """
        Check if a file has been modified externally.
        Returns True if the file has changed.
        """
        full_path = os.path.join(base_dir, file_path)
        logger.info(f"Thread {self.thread_id} checking {file_path}")
        try:
            logger.debug(f"CHECK: Starting check for {file_path}")
            if not os.path.exists(full_path):
                if file_path in self._state:
                    # File was deleted
                    del self._state[file_path]
                    return True
                return False

            with open(full_path, 'r') as f:
                current_content = f.read()
                logger.debug(f"CHECK: Read {file_path}: length={len(current_content)}")

            file_state = self._state.get(file_path)

            if not file_state:
                # First time this thread sees the file
                logger.debug(f"First time seeing {file_path} in thread {self.thread_id}, initializing new state")
                file_state = FileState(
                    baseline_content=current_content,
                    current_content=current_content,
                    last_checked=datetime.now(),
                    timestamp=datetime.now()
                )
                self._state[file_path] = file_state
                logger.info(f"Thread {self.thread_id} first time seeing {file_path}")
                return True
            elif current_content != file_state.current_content:
                # Content has changed since last check
                logger.debug(
                    f"CHECK: Content differs for {file_path}:\n"
                    f"Current length: {len(current_content)}\n"
                    f"Stored length:  {len(file_state.current_content)}"
                )
                file_state.current_content = current_content
                file_state.last_checked = datetime.now()
                logger.info(f"Detected changes in {file_path}")
                return True

            file_state.last_checked = datetime.now()
            return False

        except Exception as e:
            logger.error(f"Error reading current content for {file_path}: {str(e)}")
            logger.error(f"Error checking file {file_path}: {str(e)}")
        return False

    def get_change_indicators(self, file_path: str, current_content: str) -> Dict[int, str]:
        """
        Generate change indicators for each line comparing against original state.
        Returns a dict mapping line numbers (1-based) to indicators (' ', '+', '*')
        """

        # Always read current content directly from disk
        full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
        try:
            with open(full_path, 'r') as f:
                current_content = f.read()
        except Exception as e:
            logger.error(f"Error reading {file_path}: {str(e)}")
            return {}

        indicators = {}
        current_lines = current_content.splitlines()
        
        if file_path not in self._state:
            # New file - all lines are additions
            return {i+1: '+' for i in range(len(current_lines))}
            
        baseline_lines = self._state[file_path].baseline_content.splitlines()

        logger.info(f"Change detection for {file_path}:")
        logger.info(f"  Baseline line count: {len(baseline_lines)}")
        logger.info(f"  Current line count: {len(current_lines)}")
        logger.info(f"  First lines match? {baseline_lines[0] == current_lines[0] if baseline_lines and current_lines else 'N/A'}")

 
        # Initialize with spaces (unchanged)
        indicators = {i+1: ' ' for i in range(len(current_lines))}

        # Use a more sophisticated line comparison
        from difflib import SequenceMatcher
        matcher = SequenceMatcher(None, baseline_lines, current_lines)


        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'replace':
                # Modified lines
                for i in range(j1+1, j2+1):
                    indicators[i] = '*'
            elif tag == 'delete':
                # Lines were deleted - we don't mark these since they're gone
                pass
            elif tag == 'insert':
                # New lines
                for i in range(j1+1, j2+1):
                    indicators[i] = '+'
            else:
                # Equal lines - already marked as space
                pass

        if any(v != ' ' for v in indicators.values()):
            logger.info(f"Found changes in {file_path}: {dict((k,v) for k,v in indicators.items() if v != ' ')}")

        # Log any files with changes
        changed_lines = {k:v for k,v in indicators.items() if v != ' '}
        if changed_lines:
            logger.info(f"Changes detected in {file_path}:")
            logger.info(f"  Changed lines: {changed_lines}")
        return indicators
