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
            indent = ' ' * (indent_level * tab_size)
        else:
            indent = '\t' * indent_level
        
        result.append(indent + content)
    
    return '\n'.join(result)

def detect_indentation_style(text: str) -> tuple[bool, int]:
    """
    Detect the indentation style used in text.
    
    Args:
        text: The text to analyze
        
    Returns:
        A tuple of (uses_spaces, indent_size)
    """
    lines = text.splitlines()
    space_lines = 0
    tab_lines = 0
    indent_sizes = {}
    
    for line in lines:
        if line.startswith(' '):
            # Count spaces
            count = 0
            for char in line:
                if char == ' ':
                    count += 1
                else:
                    break
            
            if count > 0:
                space_lines += 1
                # Try to detect indent size
                if count not in indent_sizes:
                    indent_sizes[count] = 0
                indent_sizes[count] += 1
        elif line.startswith('\t'):
            tab_lines += 1
    
    # Determine if spaces or tabs are used
    uses_spaces = space_lines >= tab_lines
    
    # Determine indent size
    indent_size = 4  # Default
    if uses_spaces and indent_sizes:
        # Find the most common indent size
        common_sizes = sorted(indent_sizes.items(), key=lambda x: x[1], reverse=True)
        for size, count in common_sizes:
            if size in (2, 4, 8):
                indent_size = size
                break
    
    return uses_spaces, indent_size

def normalize_text_for_comparison(text: str) -> str:
    """
    Normalize text for comparison, handling whitespace and other differences.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    from ..core.unicode_handling import normalize_unicode
    
    # First normalize Unicode characters
    result = normalize_unicode(text)
    
    # Note: We intentionally do NOT normalize escape sequences here
    # because they could be the target of a fix
    
    # Remove all whitespace
    result = ''.join(result.split())
    
    return result

def normalize_line_endings(text: str, preserve_mixed: bool = False) -> str:
    """
    Normalize line endings in text.
    
    Args:
        text: The text to normalize
        preserve_mixed: Whether to preserve mixed line endings
        
    Returns:
        The normalized text
    """
    if not preserve_mixed:
        # Convert all line endings to LF
        return text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Count line ending types
    crlf_count = text.count('\r\n')
    cr_count = text.count('\r') - crlf_count  # Subtract CRLF count to avoid double counting
    lf_count = text.count('\n') - crlf_count  # Subtract CRLF count to avoid double counting
    
    # Determine the most common line ending
    if crlf_count >= max(cr_count, lf_count):
        # CRLF is most common
        return text.replace('\r\n', '\r\n').replace('\r', '\r\n').replace('\n', '\r\n')
    elif cr_count >= max(crlf_count, lf_count):
        # CR is most common
        return text.replace('\r\n', '\r').replace('\n', '\r')
    else:
        # LF is most common
        return text.replace('\r\n', '\n').replace('\r', '\n')

def strip_comments(text: str, language: str = 'python') -> str:
    """
    Strip comments from text.
    
    Args:
        text: The text to strip comments from
        language: The programming language
        
    Returns:
        The text with comments removed
    """
    import re
    
    if language == 'python':
        # Remove Python comments
        lines = text.splitlines()
        result = []
        for line in lines:
            # Remove comments starting with #
            comment_pos = line.find('#')
            if comment_pos >= 0:
                line = line[:comment_pos]
            result.append(line)
        return '\n'.join(result)
    elif language in ('javascript', 'typescript', 'java', 'c', 'cpp'):
        # Remove C-style comments
        # First, remove single-line comments
        text = re.sub(r'//.*$', '', text, flags=re.MULTILINE)
        # Then, remove multi-line comments
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        return text
    else:
        # Default: don't strip comments
        return text
