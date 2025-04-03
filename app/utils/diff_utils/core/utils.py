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
        block1 = ''.join(block1)
    if isinstance(block2, list):
        block2 = ''.join(block2)
    
    # Simple character-based similarity
    shorter = min(len(block1), len(block2))
    longer = max(len(block1), len(block2))
    
    if longer == 0:
        return 1.0
    
    # Count matching characters
    matches = sum(1 for a, b in zip(block1, block2) if a == b)
    
    # Calculate similarity ratio
    similarity = matches / longer
    
    # Adjust for length difference
    length_ratio = shorter / longer if longer > 0 else 1.0
    adjusted_similarity = similarity * length_ratio
    
    return adjusted_similarity
