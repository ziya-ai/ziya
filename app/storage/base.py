"""
Base storage class for file-based JSON storage.
"""
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional, List
from pathlib import Path
import json
import fcntl
from contextlib import contextmanager
from app.utils.logging_utils import logger


def _sanitize_surrogates(obj):
    """Replace unpaired Unicode surrogates that can't be encoded to UTF-8."""
    if isinstance(obj, str):
        return obj.encode('utf-8', errors='replace').decode('utf-8')
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(i) for i in obj]
    return obj


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
            raw = filepath.read_bytes()
            if not raw:
                return None

            # Auto-detect encrypted vs plaintext
            from app.utils.encryption import is_encrypted, get_encryptor
            if is_encrypted(raw):
                encryptor = get_encryptor()
                plaintext = encryptor.decrypt(raw)
                return json.loads(plaintext)
            else:
                return json.loads(raw)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error reading {filepath}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error reading/decrypting {filepath}: {e}")
            return None
    
    def _write_json(self, filepath: Path, data: dict) -> None:
        """Write JSON file with optional ALE encryption and atomic write."""
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Guard: never overwrite an encrypted file we can't decrypt.
        # If the existing file has ALE magic bytes but the encryptor
        # can't read it (KEK missing/changed), refuse the write to
        # prevent silent data loss.
        if filepath.exists():
            from app.utils.encryption import is_encrypted, get_encryptor
            try:
                existing = filepath.read_bytes()
                if is_encrypted(existing):
                    enc = get_encryptor()
                    enc.decrypt(existing)  # Will raise if KEK is wrong
            except Exception as e:
                logger.error(f"🔐 REFUSING write to {filepath}: existing file is encrypted "
                             f"but cannot be decrypted ({e}). Fix your KEK or restore from "
                             f"~/.ziya/keyring_backups/ before this file can be updated.")
                return  # Silently skip — protecting the encrypted file
        
        # Determine data category from filepath for encryption policy
        category = self._infer_category(filepath)

        # Write to temp file first, then rename for atomicity
        temp_path = filepath.with_suffix('.tmp')
        try:
            from app.utils.encryption import get_encryptor
            encryptor = get_encryptor()

            plaintext = json.dumps(
                _sanitize_surrogates(data), indent=2, ensure_ascii=False
            ).encode("utf-8")

            if encryptor.is_enabled(category):
                encrypted = encryptor.encrypt(plaintext, category)
                temp_path.write_bytes(encrypted)
            else:
                temp_path.write_bytes(plaintext)

            temp_path.rename(filepath)
        except Exception as e:
            # Clean up temp file on error
            if temp_path.exists():
                temp_path.unlink()
            raise

    @staticmethod
    def _infer_category(filepath: Path) -> str:
        """Infer the encryption category from the file path."""
        name = filepath.name.lower()
        parent = filepath.parent.name.lower()
        if parent == "chats" or name.endswith("chat.json"):
            return "conversation_data"
        if "skill" in name or parent == "skills":
            return "session_data"
        if "context" in name or parent == "contexts":
            return "session_data"
        if "project" in name:
            return "session_data"
        return "session_data"
    
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
