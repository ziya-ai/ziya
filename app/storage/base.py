"""
Base storage class for file-based JSON storage.
"""
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional, List
from pathlib import Path
import json
import fcntl
from contextlib import contextmanager

T = TypeVar('T')

class BaseStorage(ABC, Generic[T]):
    """Abstract base class for file-based storage with locking."""
    
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def _file_lock(self, filepath: Path, mode: str = 'r'):
        """Context manager for file locking to handle concurrent access."""
        # Ensure parent directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # Create file if it doesn't exist for write modes
        if mode in ('w', 'a') and not filepath.exists():
            filepath.touch()
        
        with open(filepath, mode) as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                yield f
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    
    def _read_json(self, filepath: Path) -> Optional[dict]:
        """Read JSON file with locking."""
        if not filepath.exists():
            return None
        try:
            with self._file_lock(filepath, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            from app.utils.logging_utils import logger
            logger.error(f"Error reading {filepath}: {e}")
            return None
    
    def _write_json(self, filepath: Path, data: dict) -> None:
        """Write JSON file with locking and atomic write."""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to temp file first, then rename for atomicity
        temp_path = filepath.with_suffix('.tmp')
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)
            temp_path.rename(filepath)
        except Exception as e:
            # Clean up temp file on error
            if temp_path.exists():
                temp_path.unlink()
            raise
    
    @abstractmethod
    def get(self, id: str) -> Optional[T]:
        """Get a single entity by ID."""
        pass
    
    @abstractmethod
    def list(self) -> List[T]:
        """List all entities."""
        pass
    
    @abstractmethod
    def create(self, data: dict) -> T:
        """Create a new entity."""
        pass
    
    @abstractmethod
    def update(self, id: str, data: dict) -> Optional[T]:
        """Update an existing entity."""
        pass
    
    @abstractmethod
    def delete(self, id: str) -> bool:
        """Delete an entity."""
        pass
