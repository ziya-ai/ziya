"""
Generic text handler for language-agnostic operations.
"""

import re
from typing import List, Tuple, Optional, Dict, Any

from app.utils.logging_utils import logger
from .base import LanguageHandler, LanguageHandlerRegistry
from ..core.config import get_max_offset


@LanguageHandlerRegistry.register
class GenericTextHandler(LanguageHandler):
    """Handler for generic text files."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True for any file (fallback handler)
        """
        # This is the fallback handler, so it can handle any file
        return True
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for generic text.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # For generic text, we don't have specific validation rules
        # Just check that the content is not empty
        if not modified_content:
            return False, "Modified content is empty"
        
        return True, None
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated structures in generic text.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        # For generic text, we can't reliably detect duplicates
        # without knowing the language structure
        return False, []
        
    @classmethod
    def enhance_match_confidence(cls, original_content: str, hunk_content: str, 
                               candidate_positions: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
        """
        Enhance match confidence scores based on generic text patterns.
        
        Args:
            original_content: Original file content
            hunk_content: Content of the hunk to be applied
            candidate_positions: List of (position, confidence) tuples from fuzzy matching
            
        Returns:
            Updated list of (position, confidence) tuples with text-aware adjustments
        """
        if not candidate_positions:
            return []
            
        enhanced_candidates = []
        original_lines = original_content.splitlines()
        hunk_lines = hunk_content.splitlines()
        
        for position, confidence in candidate_positions:
            # Skip invalid positions
            if position < 0 or position >= len(original_lines):
                continue
                
            # Get the indentation level of the hunk and the target position
            hunk_indent = cls._get_indentation_level(hunk_lines[0]) if hunk_lines else 0
            target_indent = cls._get_indentation_level(original_lines[position]) if position < len(original_lines) else 0
            
            # Check if the indentation levels match
            indent_match = abs(hunk_indent - target_indent) <= 4  # Allow small differences
            
            # Adjust confidence based on context
            adjusted_confidence = confidence
            
            # Boost confidence if indentation matches
            if indent_match:
                adjusted_confidence += 0.05
                
            # Boost if inserting at a blank line
            if not original_lines[position].strip() if position < len(original_lines) else False:
                adjusted_confidence += 0.05
                
            # Add the adjusted candidate
            enhanced_candidates.append((position, min(1.0, adjusted_confidence)))
            
        # Sort by confidence (descending)
        enhanced_candidates.sort(key=lambda x: x[1], reverse=True)
        
        return enhanced_candidates
        
    @classmethod
    def _get_indentation_level(cls, line: str) -> int:
        """Get the indentation level of a line."""
        return len(line) - len(line.lstrip())
        
    @classmethod
    def check_for_collisions(cls, original_content: str, hunk_content: str, position: int) -> Tuple[bool, Optional[str]]:
        """
        Check if applying the hunk at the given position would create collisions.
        
        Args:
            original_content: Original file content
            hunk_content: Content of the hunk to be applied
            position: Position where the hunk would be applied
            
        Returns:
            Tuple of (is_safe, error_message)
        """
        # For generic text, we can't reliably detect collisions
        # without knowing the language structure
        return True, None
