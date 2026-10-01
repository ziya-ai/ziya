"""
Text normalization utilities for diff application.
"""

def terminate_diff_text(diff_text: str) -> str:
    """Return ``diff_text`` with a terminating newline on its final line.

    In unified-diff format a line that lacks a newline is expressed only by an
    explicit ``\\ No newline at end of file`` marker; the marker line itself is
    newline-terminated like any other. A diff *text* whose last byte is not a
    newline therefore carries no meaning -- it is almost always a markdown fence
    body or a copy/paste that lost its final newline.

    The external tools do not agree on that reading. Apple's ``patch``
    (2.0-12u11) treats an unterminated final ``+`` line as "this line has no
    newline" even when it replaces a line in the MIDDLE of the file: it joins
    the replacement with the following line and still exits 0, so the pipeline
    reports success on a corrupted file (2026-09-24: ``keywords = [...]`` and
    ``classifiers = [`` fused into one line of pyproject.toml). Every public
    diff entry point calls this before any stage sees the text.

    Empty / whitespace-only input is returned unchanged.
    """
    if diff_text and diff_text.strip() and not diff_text.endswith("\n"):
        return diff_text + "\n"
    return diff_text


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
