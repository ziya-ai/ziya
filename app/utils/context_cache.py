"""
Context caching utilities for Bedrock models.

This module provides functionality to cache large contexts (like codebase content)
using Bedrock's context caching feature to reduce token costs and improve performance.
"""

import hashlib
import json
import os
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.utils.file_state_manager import FileStateManager
from app.utils.logging_utils import logger


@dataclass
class CachedContext:
    """Represents a cached context with metadata."""
    cache_id: str
    content_hash: str
    token_count: int
    created_at: datetime
    ttl_seconds: int
    expires_at: datetime
    conversation_id: str
    file_paths: List[str]

@dataclass
class ContextSplit:
    """Represents a split context with stable and dynamic parts."""
    stable_content: str
    stable_files: List[str]
    dynamic_content: str
    dynamic_files: List[str]
    stable_cache_id: Optional[str] = None


class ContextCacheManager:
    """Manages context caching for Bedrock models."""
    
    def __init__(self):
        self.cache_store: Dict[str, CachedContext] = {}
        self.default_ttl = 3600  # 1 hour default TTL
        self.max_cache_entries = 1000  # Prevent unbounded growth
        self.max_cache_memory_mb = 500  # Memory limit
        self.min_cache_size = 10000  # Minimum tokens to cache
        self.file_state_manager = FileStateManager()
        self.cache_stats = {"hits": 0, "misses": 0, "splits": 0, "tokens_cached": 0}
        
    def should_cache_context(self, content: str, model_config: Dict[str, Any]) -> bool:
        """
        Determine if context should be cached based on size and model support.
        
        Args:
            content: The content to potentially cache
            model_config: Model configuration
            
        Returns:
            bool: True if context should be cached
        """
        # Check if model supports context caching
        if not model_config.get("supports_context_caching", False):
            return False
            
        # Check if content is large enough to benefit from caching
        import tiktoken
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            token_count = len(encoding.encode(content))
            return token_count >= self.min_cache_size
        except Exception as e:
            logger.warning(f"Error counting tokens for caching decision: {e}")
            # Fall back to character count estimation
            return len(content) >= self.min_cache_size * 4
    
    def split_context_by_file_changes(
        self, 
        conversation_id: str, 
        full_context: str, 
        file_paths: List[str]
    ) -> ContextSplit:
        """
        Split context into stable (unchanged) and dynamic (changed) parts.
        
        Args:
            conversation_id: Conversation identifier
            full_context: Complete context content
            file_paths: List of all file paths in context
        
        Returns:
            ContextSplit: Split context with stable and dynamic parts
        """
        # Only operate on the actual codebase content, not the entire system message
        # Extract just the codebase section after "Below is the current codebase of the user:"
        codebase_start = full_context.find("Below is the current codebase of the user:")
        if codebase_start == -1:
            # If we can't find the codebase section, disable caching
            logger.warning("Could not find codebase section for caching, disabling cache split")
            return ContextSplit(stable_content="", stable_files=[], 
                              dynamic_content=full_context, dynamic_files=file_paths)
        
        codebase_content = full_context[codebase_start:]
        
        # Get file changes from the file state manager
        changed_files = set()
        unchanged_files = set()
        # Analyze each file individually
        for file_path in file_paths:
            if self._has_recent_changes(conversation_id, file_path):
                changed_files.add(file_path)
                logger.debug(f"File {file_path} has recent changes - will not cache")
            else:
                unchanged_files.add(file_path)
                logger.debug(f"File {file_path} unchanged - eligible for caching")
        
        logger.info(f"Context split: {len(unchanged_files)} stable files, {len(changed_files)} dynamic files")
        if unchanged_files:
            logger.info(f"✓ CACHE: Stable files: {sorted(list(unchanged_files)[:5])}{'...' if len(unchanged_files) > 5 else ''}")
        if changed_files:
            logger.info(f"⚡ DYNAMIC: Changed files: {sorted(list(changed_files)[:5])}{'...' if len(changed_files) > 5 else ''}")
        
        self.cache_stats["splits"] += 1
        
        # Split the context content
        stable_content_parts = []
        dynamic_content_parts = []
        
        # Parse the context to extract file sections
        file_sections = self._parse_context_by_files(codebase_content)
        
        for file_path, content in file_sections.items():
            if file_path in unchanged_files:
                stable_content_parts.append(content)
            else:
                dynamic_content_parts.append(content)
        
        # Calculate token savings
        if stable_content_parts:
            # DEBUG: Check what's actually in stable vs dynamic content
            stable_joined = "\n\n".join(stable_content_parts)
            dynamic_joined = "\n\n".join(dynamic_content_parts)
            
            print(f"=== CACHE CONTENT ANALYSIS ===")
            print(f"Stable files: {len(unchanged_files)}, content: {len(stable_joined)} chars")
            print(f"Dynamic files: {len(changed_files)}, content: {len(dynamic_joined)} chars")
            print(f"Stable file markers: {stable_joined.count('File: ')}")
            print(f"Dynamic file markers: {dynamic_joined.count('File: ')}")
            
            import tiktoken
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
                stable_tokens = len(encoding.encode("\n\n".join(stable_content_parts)))
                self.cache_stats["tokens_cached"] += stable_tokens
                logger.info(f"💰 CACHE BENEFIT: ~{stable_tokens:,} tokens will be cached")
            except Exception:
                pass
        
        return ContextSplit(
            stable_content="\n".join(stable_content_parts),  # Use single newline, not double
            stable_files=list(unchanged_files),
            dynamic_content="\n".join(dynamic_content_parts),  # Use single newline, not double
            dynamic_files=list(changed_files)
        )
    
    def _has_file_changes(self, conversation_id: str, file_path: str) -> bool:
        """
        Check if a file has changes in the current conversation.
        Returns True if file has ANY changes since conversation started.
        """
        if conversation_id not in self.file_state_manager.conversation_states:
            return True
        
        state = self.file_state_manager.conversation_states[conversation_id].get(file_path)
        return bool(state and state.line_states) if state else True
    
    def _has_recent_changes(self, conversation_id: str, file_path: str) -> bool:
        """Check if file has changes since last context submission."""
        return self.file_state_manager.has_changes_since_last_context_submission(
                conversation_id, file_path
        )
            
    def mark_context_submitted(self, conversation_id: str) -> None:
        """Mark that context has been submitted for this conversation."""
        self.file_state_manager.mark_context_submission(conversation_id)

    def _parse_context_by_files(self, context: str) -> Dict[str, str]:
        """
        Parse context content and split by file sections.
        Excludes template examples wrapped in <!-- TEMPLATE EXAMPLE --> comments.
        
        Args:
            context: Full context content
            
        Returns:
            Dict mapping file paths to their content sections
        """
        file_sections = {}
        current_file = None
        current_content = []
        in_template_example = False
        
        for line in context.split('\n'):
            # Check for template example markers
            if '<!-- TEMPLATE EXAMPLE START -->' in line:
                in_template_example = True
                continue
            elif '<!-- TEMPLATE EXAMPLE END -->' in line:
                in_template_example = False
                continue
            
            # Skip lines within template examples
            if in_template_example:
                continue
                
            if line.startswith('File: '):
                # Save previous file section
                if current_file and current_content:
                    file_sections[current_file] = '\n'.join(current_content)
                
                # Start new file section
                current_file = line[6:]  # Remove "File: " prefix
                current_content = [line]  # Include the File: line
            elif current_file:
                current_content.append(line)
        
        # Save the last file section
        if current_file and current_content:
            file_sections[current_file] = '\n'.join(current_content)
            
        return file_sections
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get caching statistics."""
        return {
            **self.cache_stats,
            "cache_entries": len(self.cache_store),
            "estimated_token_savings": self.cache_stats["tokens_cached"]
        }
    
    def get_content_hash(self, content: str, file_paths: List[str]) -> str:
        """Generate a hash for the content and file paths."""
        combined = content + "|".join(sorted(file_paths))
        return hashlib.sha256(combined.encode()).hexdigest()
    
    def get_cached_context(
        self, 
        conversation_id: str, 
        content: str, 
        file_paths: List[str]
    ) -> Optional[CachedContext]:
        """
        Get cached context if available and not expired.
        
        Args:
            conversation_id: Conversation identifier
            content: Content to check for cache
            file_paths: List of file paths in the content
            
        Returns:
            CachedContext if found and valid, None otherwise
        """
        content_hash = self.get_content_hash(content, file_paths)
        cache_key = f"{conversation_id}:{content_hash}"
        
        cached = self.cache_store.get(cache_key)
        if not cached:
            return None
            
        # Check if cache has expired
        if datetime.now() > cached.expires_at:
            logger.info(f"Cache expired for conversation {conversation_id}")
            del self.cache_store[cache_key]
            return None
            
        logger.info(f"Found valid cached context for conversation {conversation_id}")
        return cached
    
    def cache_context(
        self,
        conversation_id: str,
        content: str,
        file_paths: List[str],
        token_count: int,
        ttl_seconds: Optional[int] = None
    ) -> CachedContext:
        """
        Cache context for future use.
        
        Args:
            conversation_id: Conversation identifier
            content: Content to cache
            file_paths: List of file paths in the content
            token_count: Number of tokens in the content
            ttl_seconds: Time to live in seconds
            
        Returns:
            CachedContext: The cached context object
        """
        if ttl_seconds is None:
            ttl_seconds = self.default_ttl
            
        content_hash = self.get_content_hash(content, file_paths)
        cache_key = f"{conversation_id}:{content_hash}"
        
        now = datetime.now()
        cached_context = CachedContext(
            cache_id=cache_key,
            content_hash=content_hash,
            token_count=token_count,
            created_at=now,
            ttl_seconds=ttl_seconds,
            expires_at=now + timedelta(seconds=ttl_seconds),
            conversation_id=conversation_id,
            file_paths=file_paths.copy()
        )
        
        self.cache_store[cache_key] = cached_context
        
        # Enforce cache limits
        if len(self.cache_store) > self.max_cache_entries:
            # Remove oldest entries
            oldest_keys = sorted(self.cache_store.keys(), 
                               key=lambda k: self.cache_store[k].created_at)[:100]
            for key in oldest_keys:
                del self.cache_store[key]
        
        logger.info(f"Cached context for conversation {conversation_id}: {token_count} tokens, TTL {ttl_seconds}s")
        
        return cached_context
    
    def cleanup_expired_cache(self) -> int:
        """Remove expired cache entries and return count of removed entries."""
        now = datetime.now()
        expired_keys = [
            key for key, cached in self.cache_store.items()
            if now > cached.expires_at
        ]
        
        for key in expired_keys:
            del self.cache_store[key]
            
        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
            
        return len(expired_keys)


# Global cache manager instance
_context_cache_manager = None


def get_context_cache_manager() -> ContextCacheManager:
    """Get the global context cache manager instance."""
    global _context_cache_manager
    if _context_cache_manager is None:
        _context_cache_manager = ContextCacheManager()
    return _context_cache_manager
