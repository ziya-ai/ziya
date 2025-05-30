# lets move sanitizers here to clean up code flow and make them reusable

def sanitize_filename(filename: str) -> str:
    """
    Sanitizes a filename to ensure it's safe for filesystem operations.
    
    Args:
        filename (str): The filename to sanitize
        
    Returns:
        str: The sanitized filename
    """
    # Remove potentially dangerous characters
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    return ''.join(c for c in filename if c not in invalid_chars)

def clean_backtick_sequences(text: str) -> str:
    """
    Cleans up problematic backtick sequences while preserving content within code blocks.
    Ensures all code blocks are properly closed.
    
    Args:
        text (str): The input text containing potential backtick sequences
        
    Returns:
        str: Text with properly closed code blocks and preserved content
    """
    lines = text.split('\n')
    cleaned_lines = []
    in_code_block = False
    current_block_type = None
    
    for line in lines:
        if not in_code_block:
            if line.startswith('```'):
                # Starting a new block
                in_code_block = True
                # Capture the block type (diff, python, etc.)
                current_block_type = line[3:].strip() if len(line) > 3 else None
                cleaned_lines.append(line)
            else:
                cleaned_lines.append(line)
        else:
            # Inside a code block - collect content until closing backticks
            if line.strip() == '```':
                # Only close block if it's a bare ``` without a type specifier
                if len(line.strip()) == 3:
                    in_code_block = False
                    current_block_type = None
                    cleaned_lines.append(line)
                else:
                    # This is a nested block marker, preserve it
                    cleaned_lines.append(line)
            else:
                # Within a code block, preserve content exactly as it appears
                cleaned_lines.append(line)
    
    # If we ended with an open code block, close it
    if in_code_block:
        cleaned_lines.append('```')
        current_block_type = None
    
    return '\n'.join(cleaned_lines)
