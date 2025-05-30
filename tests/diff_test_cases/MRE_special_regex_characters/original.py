import re

def validate_patterns(patterns):
    """
    Validate a list of regex patterns.
    
    Args:
        patterns: List of regex pattern strings
        
    Returns:
        Dictionary with valid and invalid patterns
    """
    valid_patterns = []
    invalid_patterns = []
    
    for pattern in patterns:
        try:
            # Try to compile the pattern
            re.compile(pattern)
            valid_patterns.append(pattern)
        except re.error:
            invalid_patterns.append(pattern)
    
    return {
        'valid': valid_patterns,
        'invalid': invalid_patterns
    }

def extract_matches(text, pattern):
    """
    Extract all matches of a pattern from text.
    
    Args:
        text: Text to search in
        pattern: Regex pattern to match
        
    Returns:
        List of matches
    """
    matches = re.findall(pattern, text)
    return matches

# Example regex patterns
EMAIL_PATTERN = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
URL_PATTERN = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+'
DATE_PATTERN = r'\d{4}-\d{2}-\d{2}'
PHONE_PATTERN = r'\+?\d{1,3}[-.\s]?\(?\d{1,3}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}'

# Special patterns with regex metacharacters
SPECIAL_PATTERNS = [
    r'a+b*c?',                  # Basic quantifiers
    r'[a-z0-9]',                # Character class
    r'(foo|bar)',               # Alternation
    r'^start',                  # Start of string
    r'end$',                    # End of string
    r'\bword\b',                # Word boundary
    r'a{3,5}',                  # Specific quantifier
    r'(?:non-capturing)',       # Non-capturing group
    r'(?=lookahead)',           # Positive lookahead
    r'(?!negative-lookahead)',  # Negative lookahead
    r'(?<=lookbehind)',         # Positive lookbehind
    r'(?<!negative-lookbehind)' # Negative lookbehind
]

def test_regex():
    """Test function for regex patterns"""
    test_text = "This is a test with email@example.com and https://example.org"
    
    # Test email pattern
    emails = extract_matches(test_text, EMAIL_PATTERN)
    assert emails == ['email@example.com']
    
    # Test URL pattern
    urls = extract_matches(test_text, URL_PATTERN)
    assert urls == ['https://example.org']
    
    # Test validation
    result = validate_patterns([EMAIL_PATTERN, URL_PATTERN, r'[invalid'])
    assert len(result['valid']) == 2
    assert len(result['invalid']) == 1
    
    print("All regex tests passed!")
