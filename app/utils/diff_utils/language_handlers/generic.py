"""
Generic text handler for language-agnostic operations.
"""

import re
from typing import List, Tuple, Optional, Dict

from app.utils.logging_utils import logger
from .base import LanguageHandler


class GenericTextHandler(LanguageHandler):
    """Fallback handler for any text file."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True as this is the fallback handler
        """
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
        # Basic verification that works for any text file
        # Check if the file is not empty after changes
        if not modified_content and original_content:
            return False, "Modified content is empty but original content was not"
        
        return True, None
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated patterns in generic text.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        # Basic pattern detection for generic text
        # Look for repeated blocks of text that weren't repeated in the original
        
        # This is a simple implementation that looks for repeated lines
        # A more sophisticated implementation would look for repeated blocks
        original_lines = original_content.splitlines()
        modified_lines = modified_content.splitlines()
        
        # Count occurrences of each line
        original_counts = cls._count_line_occurrences(original_lines)
        modified_counts = cls._count_line_occurrences(modified_lines)
        
        # Find lines that appear more times in the modified content
        duplicates = []
        for line, count in modified_counts.items():
            if count > 1 and count > original_counts.get(line, 0):
                # Only include non-trivial lines (not just whitespace or common patterns)
                if len(line.strip()) > 10 and not cls._is_common_pattern(line):
                    duplicates.append(line[:40] + "..." if len(line) > 40 else line)
        
        return bool(duplicates), duplicates
    
    @staticmethod
    def _count_line_occurrences(lines: List[str]) -> Dict[str, int]:
        """
        Count occurrences of each line.
        
        Args:
            lines: List of lines
            
        Returns:
            Dictionary mapping lines to their occurrence count
        """
        counts = {}
        for line in lines:
            line = line.strip()
            if line:  # Skip empty lines
                counts[line] = counts.get(line, 0) + 1
        return counts
    
    @staticmethod
    def _is_common_pattern(line: str) -> bool:
        """
        Check if a line is a common pattern that shouldn't be flagged as a duplicate.
        
        Args:
            line: Line to check
            
        Returns:
            True if the line is a common pattern, False otherwise
        """
        # Skip common patterns like import statements, blank lines, comments, etc.
        common_patterns = [
            r"^\s*import\s+",
            r"^\s*from\s+.+\s+import\s+",
            r"^\s*#",
            r"^\s*//",
            r"^\s*\*",
            r"^\s*\{",
            r"^\s*\}",
            r"^\s*\)",
            r"^\s*\(",
            r"^\s*return\s+",
            r"^\s*if\s+",
            r"^\s*else",
            r"^\s*for\s+",
            r"^\s*while\s+",
        ]
        
        for pattern in common_patterns:
            if re.match(pattern, line):
                return True
        
        return False
