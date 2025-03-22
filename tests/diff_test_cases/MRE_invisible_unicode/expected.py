def format_string(text):
    """
    Format a string by removing extra whitespace and normalizing quotes.
    
    Args:
        text: The input string to format
        
    Returns:
        Formatted string
    """
    # Remove leading/trailing whitespace
    text = text.strip()
    
    # Replace multiple spaces with single space
    text = ' '.join(text.split())
    
    # Normalize quotes (replace single with double)
    text = text.replace("'", "\"")
    
    # Remove invisible Unicode characters
    text = ''.join(c for c in text if ord(c) >= 32 or c in '\n\r\t')
    
    return text

def validate_input(text):
    """
    Validate that the input string meets requirements.
    
    Args:
        text: The input string to validate
        
    Returns:
        True if valid, False otherwise
    """
    # Check if string is empty
    if not text:
        return False
    
    # Check if string contains invalid characters
    invalid_chars = ["<", ">", "&", "$", "\u200B", "\u200C", "\u200D", "\uFEFF"]
    for char in invalid_chars:
        if char in text:
            return False
    
    return True
