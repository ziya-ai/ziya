"""
Text normalization utilities for diff application.
"""

def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    # Replace tabs with spaces
    result = text.replace('\t', '    ')
    
    # Normalize line endings
    result = result.replace('\r\n', '\n')
    
    # Remove trailing whitespace
    lines = result.splitlines()
    result = '\n'.join(line.rstrip() for line in lines)
    
    return result

def normalize_indentation(text: str, use_spaces: bool = True, tab_size: int = 4) -> str:
    """
    Normalize indentation in text.
    
    Args:
        text: The text to normalize
        use_spaces: Whether to use spaces for indentation
        tab_size: The number of spaces per tab
        
    Returns:
        The normalized text
    """
    lines = text.splitlines()
    result = []
    
    for line in lines:
        # Count leading whitespace
        leading_space = len(line) - len(line.lstrip())
        content = line.lstrip()
        
        # Calculate indentation level
        indent_level = leading_space // tab_size if use_spaces else leading_space
        
        # Create new indentation
        if use_spaces:
            indentation = ' ' * (indent_level * tab_size)
        else:
            indentation = '\t' * indent_level
        
        result.append(indentation + content)
    
    return '\n'.join(result)

def normalize_text_for_comparison(text: str) -> str:
    """
    Normalize text for comparison, handling whitespace, invisible characters, and escape sequences.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    if not text:
        return ""
    
    # First normalize Unicode characters to handle invisible characters
    from .unicode_handling import normalize_unicode
    normalized = normalize_unicode(text)
    
    # Then normalize escape sequences - preserve literals for code comparison
    from .escape_handling import normalize_escape_sequences
    normalized = normalize_escape_sequences(normalized, preserve_literals=True)
    
    # Normalize whitespace - replace tabs with spaces for consistent comparison
    normalized = normalized.replace('\t', '    ')
    
    # Finally normalize whitespace - only trim leading/trailing
    normalized = normalized.strip()
    
    return normalized
