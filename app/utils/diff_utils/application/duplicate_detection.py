"""
Duplicate detection and syntax validation functions.

These functions delegate to the language handler architecture and include
additional verification to reduce false positives.
"""

import re
import difflib
from typing import Tuple, Optional, Dict, Any, List

from app.utils.logging_utils import logger
from ..language_handlers import LanguageHandlerRegistry
from ..core.text_normalization import normalize_text_for_comparison


def verify_no_duplicates(original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Verify that the modified content doesn't contain duplicate functions/methods.
    
    Args:
        original_content: Original file content
        modified_content: Modified file content
        file_path: Path to the file
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Get the appropriate language handler for this file
    handler = LanguageHandlerRegistry.get_handler(file_path)
    
    # Check for duplicates using the language-specific handler
    has_duplicates, duplicates = handler.detect_duplicates(original_content, modified_content)
    
    if has_duplicates:
        # Perform additional verification to filter out false positives
        verified_duplicates = filter_false_positive_duplicates(duplicates, original_content, modified_content, file_path)
        
        if verified_duplicates:
            error_msg = f"Applying diff would create duplicate code: {', '.join(verified_duplicates)}"
            logger.error(error_msg)
            return False, error_msg
        else:
            # All duplicates were false positives
            logger.info("Potential duplicates were detected but filtered as false positives")
            return True, None
    
    return True, None


def filter_false_positive_duplicates(duplicates: List[str], original_content: str, modified_content: str, file_path: str) -> List[str]:
    """
    Filter out false positive duplicates based on additional context analysis.
    
    Args:
        duplicates: List of detected duplicates
        original_content: Original file content
        modified_content: Modified file content
        file_path: Path to the file
        
    Returns:
        List of verified duplicates (false positives removed)
    """
    verified_duplicates = []
    
    # Check if the file is being modified or completely replaced
    if len(original_content) > 0 and len(modified_content) > 0:
        # Calculate overall similarity to detect complete replacements
        overall_similarity = difflib.SequenceMatcher(None, original_content, modified_content).ratio()
        
        # If the file is being completely replaced (very low similarity), 
        # don't flag duplicates as they're part of the new implementation
        if overall_similarity < 0.3:
            logger.info(f"File appears to be completely replaced (similarity: {overall_similarity:.2f}), ignoring duplicates")
            return []
    
    for duplicate in duplicates:
        # Extract the function/class name and line numbers if available
        name_match = re.match(r'^([a-zA-Z0-9_$]+)(?:\s*\(lines\s+([0-9, ]+)\))?', duplicate)
        
        if name_match:
            name = name_match.group(1)
            
            # Check if this is a common false positive pattern
            if is_false_positive_pattern(name, modified_content, file_path):
                logger.info(f"Filtered out false positive duplicate: {duplicate}")
                continue
            
            # Check if this is a modification of an existing function rather than a duplicate
            if is_function_modification(name, original_content, modified_content):
                logger.info(f"Detected function modification rather than duplication: {duplicate}")
                continue
            
            # If we get here, it's likely a real duplicate
            verified_duplicates.append(duplicate)
        else:
            # If we can't parse the duplicate format, include it to be safe
            verified_duplicates.append(duplicate)
    
    return verified_duplicates


def is_false_positive_pattern(name: str, content: str, file_path: str) -> bool:
    """
    Check if this is a common false positive pattern.
    
    Args:
        name: The name to check
        content: The file content
        file_path: Path to the file
        
    Returns:
        True if this is likely a false positive, False otherwise
    """
    # Common false positive patterns
    false_positive_patterns = [
        # React component props
        r'<[A-Za-z0-9_]+\s+[^>]*\b' + re.escape(name) + r'\s*=',
        
        # Import statements
        r'import\s+.*\b' + re.escape(name) + r'\b',
        r'from\s+.*\b' + re.escape(name) + r'\b',
        
        # Common variable names that might appear multiple times
        r'\b(i|j|k|x|y|z|index|key|value|item|data|result|count|total|sum|avg|min|max)\b'
    ]
    
    # Check if the name matches any of these patterns
    if any(re.search(pattern, content) for pattern in false_positive_patterns):
        return True
    
    # Check if the name is a common token in the language
    common_tokens = {
        'if', 'for', 'while', 'switch', 'catch', 'with', 'return',
        'else', 'try', 'finally', 'do', 'in', 'of', 'new', 'typeof',
        'instanceof', 'void', 'delete', 'throw', 'yield', 'await',
        'renderTokens', 'render', 'component', 'Container', 'Wrapper',
        'Handler', 'Provider', 'Context', 'useEffect', 'useState',
        'onClick', 'onChange', 'onSubmit', 'onBlur', 'onFocus'  # Common React event handlers
    }
    
    if name in common_tokens:
        return True
    
    return False


def is_function_modification(name: str, original_content: str, modified_content: str) -> bool:
    """
    Check if this is a modification of an existing function rather than a duplicate.
    
    Args:
        name: The function name
        original_content: Original file content
        modified_content: Modified file content
        
    Returns:
        True if this is likely a function modification, False otherwise
    """
    # Find all occurrences of the function in both contents
    original_matches = list(re.finditer(r'\b' + re.escape(name) + r'\b', original_content))
    modified_matches = list(re.finditer(r'\b' + re.escape(name) + r'\b', modified_content))
    
    # If the function appears the same number of times or fewer times in the modified content,
    # it's likely a modification rather than a duplicate
    if len(modified_matches) <= len(original_matches):
        return True
    
    # If the function appears exactly once more in the modified content,
    # check if it's a modification by comparing the surrounding context
    if len(modified_matches) == len(original_matches) + 1:
        # Extract contexts around each occurrence
        original_contexts = [extract_context(original_content, match.start(), 200) for match in original_matches]
        modified_contexts = [extract_context(modified_content, match.start(), 200) for match in modified_matches]
        
        # Check if any of the modified contexts is very similar to any of the original contexts
        for mod_ctx in modified_contexts:
            for orig_ctx in original_contexts:
                similarity = difflib.SequenceMatcher(None, mod_ctx, orig_ctx).ratio()
                if similarity > 0.7:  # High similarity threshold
                    return True
    
    return False


def extract_context(content: str, position: int, context_size: int) -> str:
    """
    Extract context around a position in the content.
    
    Args:
        content: The content
        position: The position
        context_size: The size of context to extract
        
    Returns:
        The extracted context
    """
    # Import here to avoid circular imports
    from ..core.config import get_context_size
    
    # If context_size is not specified, use the configured size
    if context_size <= 0:
        context_size = get_context_size('medium')
        
    start = max(0, position - context_size // 2)
    end = min(len(content), position + context_size // 2)
    return content[start:end]


def check_syntax_validity(original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Check if the modified content is syntactically valid.
    
    Args:
        original_content: Original file content
        modified_content: Modified file content
        file_path: Path to the file
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Get the appropriate language handler for this file
    handler = LanguageHandlerRegistry.get_handler(file_path)
    
    # Verify changes using the language-specific handler
    return handler.verify_changes(original_content, modified_content, file_path)
