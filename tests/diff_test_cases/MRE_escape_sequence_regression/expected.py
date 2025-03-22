def generate_text():
    """Generate text with escape sequences"""
    
    text = ""
    
    # Add some basic text
    text += "This is a test\n"
    text += "With multiple lines\n"
    
    # Add text with escape sequences
    text += "Tab character: \\t (horizontal tab)\n"
    text += "Newline character: \\n (line feed)\n"
    text += "Backslash character: \\\\ (backslash)\n"
    
    # Add text with actual escape sequences
    text += "Actual tab:\t<tab>\n"
    text += "Actual quotes: \"quoted\"\n"
    
    return text

def parse_escaped_text(text):
    """Parse text with escape sequences"""
    
    # Replace escape sequences with actual characters
    result = text
    
    # Process escape sequences in order
    replacements = [
        ("\\\\", "\\"),  # Must process backslash first
        ("\\n", "\n"),
        ("\\t", "\t")
    ]
    
    for old, new in replacements:
        result = result.replace(old, new)
    
    return result
