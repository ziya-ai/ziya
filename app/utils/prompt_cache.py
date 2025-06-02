"""
Prompt caching utilities for Bedrock API calls.

This module provides intelligent caching capabilities that work with the existing
file state management system to optimize repeated API calls with similar contexts.
"""

import hashlib
import json
import os
import time
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, asdict
from pathlib import Path

from app.utils.logging_utils import logger


@dataclass
class PromptCacheEntry:
    """Represents a cached prompt and its metadata."""
    prompt_structure_hash: str  # Hash of the prompt template/structure
    file_content_hash: str      # Hash of all file contents
    conversation_id: str        # Associated conversation
    file_paths: List[str]       # Files that contributed to this cache
    timestamp: float            # When this was cached
    ttl: float                  # Time to live in seconds
    token_count: int           # Estimated token count
    ast_context_hash: Optional[str] = None  # Hash of AST context if used
    
    def is_expired(self) -> bool:
        """Check if the cache entry has expired."""
        return time.time() > (self.timestamp + self.ttl)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PromptCacheEntry':
        """Create from dictionary."""
        return cls(**data)


class PromptCache:
    """
    Intelligent prompt cache that integrates with Ziya's file state management.
    
    This cache is designed to work with the existing FileStateManager to track
    file changes and invalidate cache entries when the underlying context changes.
    """
    
    def __init__(self, cache_dir: str = None, default_ttl: float = 3600):
        """
        Initialize the prompt cache.
        
        Args:
            cache_dir: Directory to store cache files
            default_ttl: Default time-to-live for cache entries in seconds
        """
        self.cache_dir = Path(cache_dir or os.path.join(os.path.expanduser("~"), ".ziya", "cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self.cache_file = self.cache_dir / "prompt_cache.json"
        self._cache: Dict[str, PromptCacheEntry] = {}
        self._load_cache()
    
    def _load_cache(self):
        """Load cache from disk."""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    for key, entry_data in data.items():
                        self._cache[key] = PromptCacheEntry.from_dict(entry_data)
                logger.debug(f"Loaded {len(self._cache)} prompt cache entries")
        except Exception as e:
            logger.warning(f"Failed to load prompt cache: {e}")
            self._cache = {}
    
    def _save_cache(self):
        """Save cache to disk."""
        try:
            # Clean expired entries before saving
            self._cleanup_expired()
            
            data = {key: entry.to_dict() for key, entry in self._cache.items()}
            
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save prompt cache: {e}")
    
    def _cleanup_expired(self):
        """Remove expired cache entries."""
        expired_keys = [key for key, entry in self._cache.items() if entry.is_expired()]
        for key in expired_keys:
            del self._cache[key]
        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
    
    def _hash_content(self, content: str) -> str:
        """Generate a hash for content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def _generate_cache_key(self, conversation_id: str, prompt_structure: str, 
                          file_paths: List[str]) -> str:
        """Generate a cache key based on conversation, prompt structure, and files."""
        key_data = {
            'conversation_id': conversation_id,
            'prompt_structure_hash': self._hash_content(prompt_structure),
            'file_paths': sorted(file_paths)  # Sorted for consistency
        }
        return hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
    
    def _get_file_content_hash(self, file_paths: List[str]) -> str:
        """Get a combined hash of all file contents."""
        content_parts = []
        for file_path in sorted(file_paths):  # Sort for consistency
            try:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    content_parts.append(f"{file_path}:{self._hash_content(content)}")
                else:
                    content_parts.append(f"{file_path}:NOT_FOUND")
            except Exception as e:
                logger.warning(f"Failed to hash file {file_path}: {e}")
                content_parts.append(f"{file_path}:ERROR")
        
        return self._hash_content('\n'.join(content_parts))
    
    def _extract_prompt_structure(self, full_prompt: str) -> str:
        """
        Extract the structural elements of a prompt for caching.
        
        This removes variable content like specific file contents but keeps
        the overall structure and template.
        """
        lines = full_prompt.split('\n')
        structure_lines = []
        
        for line in lines:
            # Keep structural markers
            if line.startswith('File: ') or line.startswith('SYSTEM:') or line.startswith('Human:'):
                structure_lines.append(line)
            # Keep AST context markers
            elif 'AST Analysis' in line or line.startswith('## '):
                structure_lines.append(line)
            # Keep change tracking markers
            elif 'Code Changes' in line or line.startswith('SYSTEM: '):
                structure_lines.append(line)
        
        return '\n'.join(structure_lines)
    
    def get_cached_prompt(self, conversation_id: str, full_prompt: str, 
                         file_paths: List[str], ast_context: str = None) -> Optional[str]:
        """
        Check if we have a cached version of this prompt structure.
        
        Args:
            conversation_id: The conversation this prompt belongs to
            full_prompt: The complete prompt text
            file_paths: List of files that contribute to the prompt
            ast_context: Optional AST context string
            
        Returns:
            Cached prompt if available and valid, None otherwise
        """
        prompt_structure = self._extract_prompt_structure(full_prompt)
        cache_key = self._generate_cache_key(conversation_id, prompt_structure, file_paths)
        
        entry = self._cache.get(cache_key)
        if not entry:
            return None
        
        if entry.is_expired():
            del self._cache[cache_key]
            return None
        
        # Check if file contents have changed
        current_file_hash = self._get_file_content_hash(file_paths)
        if entry.file_content_hash != current_file_hash:
            logger.debug(f"Cache miss: file contents changed for key {cache_key[:8]}...")
            del self._cache[cache_key]
            return None
        
        # Check AST context if applicable
        if ast_context:
            current_ast_hash = self._hash_content(ast_context)
            if entry.ast_context_hash != current_ast_hash:
                logger.debug(f"Cache miss: AST context changed for key {cache_key[:8]}...")
                del self._cache[cache_key]
                return None
        
        logger.info(f"Cache hit for conversation {conversation_id} (key: {cache_key[:8]}...)")
        return full_prompt  # Return the original prompt since structure matches
    
    def cache_prompt(self, conversation_id: str, full_prompt: str, 
                    file_paths: List[str], token_count: int = 0,
                    ast_context: str = None, ttl: Optional[float] = None) -> str:
        """
        Cache a prompt for future use.
        
        Args:
            conversation_id: The conversation this prompt belongs to
            full_prompt: The complete prompt text
            file_paths: List of files that contribute to the prompt
            token_count: Estimated token count for this prompt
            ast_context: Optional AST context string
            ttl: Time to live for this cache entry
            
        Returns:
            The cache key for this entry
        """
        prompt_structure = self._extract_prompt_structure(full_prompt)
        cache_key = self._generate_cache_key(conversation_id, prompt_structure, file_paths)
        
        entry = PromptCacheEntry(
            prompt_structure_hash=self._hash_content(prompt_structure),
            file_content_hash=self._get_file_content_hash(file_paths),
            conversation_id=conversation_id,
            file_paths=file_paths,
            timestamp=time.time(),
            ttl=ttl or self.default_ttl,
            token_count=token_count,
            ast_context_hash=self._hash_content(ast_context) if ast_context else None
        )
        
        self._cache[cache_key] = entry
        self._save_cache()
        
        logger.debug(f"Cached prompt for conversation {conversation_id} (key: {cache_key[:8]}...)")
        return cache_key
    
    def invalidate_conversation(self, conversation_id: str):
        """Invalidate all cache entries for a specific conversation."""
        keys_to_remove = [key for key, entry in self._cache.items() 
                         if entry.conversation_id == conversation_id]
        
        for key in keys_to_remove:
            del self._cache[key]
        
        if keys_to_remove:
            logger.info(f"Invalidated {len(keys_to_remove)} cache entries for conversation {conversation_id}")
            self._save_cache()
    
    def invalidate_files(self, file_paths: List[str]):
        """Invalidate cache entries that depend on the specified files."""
        keys_to_remove = []
        for key, entry in self._cache.items():
            if any(file_path in entry.file_paths for file_path in file_paths):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self._cache[key]
        
        if keys_to_remove:
            logger.info(f"Invalidated {len(keys_to_remove)} cache entries for files: {file_paths}")
            self._save_cache()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        self._cleanup_expired()
        total_tokens = sum(entry.token_count for entry in self._cache.values())
        
        return {
            'total_entries': len(self._cache),
            'total_cached_tokens': total_tokens,
            'cache_file_size': self.cache_file.stat().st_size if self.cache_file.exists() else 0,
            'oldest_entry': min((entry.timestamp for entry in self._cache.values()), default=0),
            'newest_entry': max((entry.timestamp for entry in self._cache.values()), default=0)
        }


# Global cache instance
_prompt_cache = None

def get_prompt_cache() -> PromptCache:
    """Get the global prompt cache instance."""
    global _prompt_cache
    if _prompt_cache is None:
        _prompt_cache = PromptCache()
    return _prompt_cache
