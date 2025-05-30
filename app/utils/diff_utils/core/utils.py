"""
Utility functions for the diff_utils package.
"""

def clamp(value, min_value, max_value):
    """
    Clamp a value between a minimum and maximum.
    
    Args:
        value: The value to clamp
        min_value: The minimum allowed value
        max_value: The maximum allowed value
        
    Returns:
        The clamped value
    """
    return max(min_value, min(value, max_value))

def normalize_escapes(text):
    """
    Normalize escape sequences in text.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    # This is a placeholder - actual implementation would handle various escape sequences
    return text

def calculate_block_similarity(block1, block2):
    """
    Calculate the similarity between two blocks of text.
    
    Args:
        block1: First block of text
        block2: Second block of text
        
    Returns:
        A similarity score between 0.0 and 1.0
    """
    # Handle empty blocks
    if not block1 or not block2:
        return 0.0
    
    # Convert lists to strings if needed
    if isinstance(block1, list):
        block1_str = '\n'.join(str(line) for line in block1)
    else:
        block1_str = str(block1)
        
    if isinstance(block2, list):
        block2_str = '\n'.join(str(line) for line in block2)
    else:
        block2_str = str(block2)
    
    # Use difflib's SequenceMatcher for more accurate similarity calculation
    import difflib
    matcher = difflib.SequenceMatcher(None, block1_str, block2_str)
    similarity = matcher.ratio()
    
    # Apply stricter similarity scoring
    if len(block1_str) != len(block2_str):
        # Penalize length differences more heavily
        length_ratio = min(len(block1_str), len(block2_str)) / max(len(block1_str), len(block2_str))
        similarity = similarity * (0.5 + 0.5 * length_ratio)
    
    return similarity
