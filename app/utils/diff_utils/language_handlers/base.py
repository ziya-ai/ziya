"""
Base classes for language handlers.
"""

from typing import List, Tuple, Optional, Type, Dict, Any


class LanguageHandler:
    """Base interface for language-specific handlers."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if this handler can process the file, False otherwise
        """
        raise NotImplementedError
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for this language.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        raise NotImplementedError
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated code structures.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        raise NotImplementedError
        
    @classmethod
    def enhance_match_confidence(cls, original_content: str, hunk_content: str, 
                               candidate_positions: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
        """
        Enhance match confidence scores based on language-specific context.
        
        Args:
            original_content: Original file content
            hunk_content: Content of the hunk to be applied
            candidate_positions: List of (position, confidence) tuples from fuzzy matching
            
        Returns:
            Updated list of (position, confidence) tuples with language-aware adjustments
        """
        # Default implementation just returns the original candidates
        return candidate_positions
        
    @classmethod
    def validate_hunk_placement(cls, original_content: str, hunk_content: str, position: int) -> Tuple[bool, Optional[str]]:
        """
        Validate if a hunk can be placed at the given position based on language-specific rules.
        
        Args:
            original_content: Original file content
            hunk_content: Content of the hunk to be applied
            position: Position where the hunk would be applied
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Default implementation allows placement anywhere
        return True, None
        
    @classmethod
    def check_for_collisions(cls, original_content: str, hunk_content: str, position: int) -> Tuple[bool, Optional[str]]:
        """
        Check if applying the hunk at the given position would create collisions like duplicate functions.
        
        Args:
            original_content: Original file content
            hunk_content: Content of the hunk to be applied
            position: Position where the hunk would be applied
            
        Returns:
            Tuple of (is_safe, error_message)
        """
        # Default implementation doesn't check for collisions
        return True, None


class LanguageHandlerRegistry:
    """Registry for language handlers."""
    
    _handlers = []
    
    @classmethod
    def register(cls, handler_class: Type[LanguageHandler]) -> Type[LanguageHandler]:
        """
        Register a language handler.
        
        Args:
            handler_class: The handler class to register
            
        Returns:
            The registered handler class (for decorator usage)
        """
        cls._handlers.append(handler_class)
        return handler_class
    
    @classmethod
    def get_handler(cls, file_path: str) -> Type[LanguageHandler]:
        """
        Get the appropriate handler for a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            The appropriate handler class for the file
        """
        from .generic import GenericTextHandler
        
        for handler in cls._handlers:
            if handler.can_handle(file_path):
                return handler
        
        # If no handler is found, return the generic handler
        # This should never happen if GenericTextHandler is registered
        return GenericTextHandler
