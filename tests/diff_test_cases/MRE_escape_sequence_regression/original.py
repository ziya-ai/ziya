def generate_text():
    """Generate text with escape sequences"""
    
    text = ""
    
    # Add some basic text
    text += "This is a test\n"
    text += "With multiple lines\n"
    
    # Add text with escape sequences
    text += "Tab character: \\t\n"
    text += "Newline character: \\n\n"
    text += "Backslash character: \\\\\n"
    
    # Add text with actual escape sequences
    text += "Actual tab:\t<tab>\n"
    text += "Actual quotes: \"quoted\"\n"
    
    return text

def parse_escaped_text(text):
    """Parse text with escape sequences"""
    
    # Replace escape sequences with actual characters
    result = text.replace("\\n", "\n")
    result = result.replace("\\t", "\t")
    result = result.replace("\\\\", "\\")
    
    return result
