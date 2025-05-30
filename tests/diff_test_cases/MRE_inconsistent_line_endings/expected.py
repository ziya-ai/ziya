def process_text(text):
    """
    Process text with mixed line endings\r
    Handles CRLF and LF line endings
    """
    lines = text.splitlines()
    result = []
    
    for line in lines:
        # Skip empty lines
        if not line.strip():
            continue
        
        # Process the line\r
        # Remove whitespace
        processed = line.strip()
        result.append(processed)
    
    # Join with Unix line endings
    return "\n".join(result)

def normalize_line_endings(text):
    """
    Convert all line endings to Unix style
    """
    # Replace Windows line endings with Unix
    text = text.replace("\r\n", "\n")
    # Replace old Mac line endings with Unix
    text = text.replace("\r", "\n")
    return text
