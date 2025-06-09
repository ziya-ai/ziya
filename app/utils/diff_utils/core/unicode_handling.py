"""
Unicode handling utilities for diff application.

This module provides functions for handling Unicode characters in diffs,
particularly focusing on invisible characters that can cause issues with
diff application.
"""

# Define the list of invisible Unicode characters once to avoid duplication
INVISIBLE_UNICODE_CHARS = [
    '\u200B',  # Zero width space
    '\u200C',  # Zero width non-joiner
    '\u200D',  # Zero width joiner
    '\u200E',  # Left-to-right mark
    '\u200F',  # Right-to-left mark
    '\u2060',  # Word joiner
    '\u2061',  # Function application
    '\u2062',  # Invisible times
    '\u2063',  # Invisible separator
    '\u2064',  # Invisible plus
    '\u2065',  # Invisible separator
    '\u2066',  # Left-to-right isolate
    '\u2067',  # Right-to-left isolate
    '\u2068',  # First strong isolate
    '\u2069',  # Pop directional isolate
    '\u206A',  # Inhibit symmetric swapping
    '\u206B',  # Activate symmetric swapping
    '\u206C',  # Inhibit Arabic form shaping
    '\u206D',  # Activate Arabic form shaping
    '\u206E',  # National digit shapes
    '\u206F',  # Nominal digit shapes
    '\uFEFF',  # Zero width no-break space (BOM)
    '\u180E',  # Mongolian vowel separator
    '\u2028',  # Line separator
    '\u2029',  # Paragraph separator
    '\u202A',  # Left-to-right embedding
    '\u202B',  # Right-to-left embedding
    '\u202C',  # Pop directional formatting
    '\u202D',  # Left-to-right override
    '\u202E',  # Right-to-left override
    # Additional zero-width characters and variation selectors
    '\uFE00', '\uFE01', '\uFE02', '\uFE03', '\uFE04', '\uFE05', '\uFE06', '\uFE07',  # Variation selectors
    '\uFE08', '\uFE09', '\uFE0A', '\uFE0B', '\uFE0C', '\uFE0D', '\uFE0E', '\uFE0F',  # Variation selectors
    '\u034F',  # Combining grapheme joiner
    '\u061C',  # Arabic letter mark
    '\u115F',  # Hangul choseong filler
    '\u1160',  # Hangul jungseong filler
    '\u17B4',  # Khmer vowel inherent AQ
    '\u17B5',  # Khmer vowel inherent AA
    '\u3164',  # Hangul filler
    '\uFFA0',  # Halfwidth hangul filler
]

def contains_invisible_chars(text: str) -> bool:
    """
    Check if text contains invisible Unicode characters.
    
    Args:
        text: The text to check
        
    Returns:
        True if the text contains invisible Unicode characters, False otherwise
    """
    if not text:
        return False
        
    return any(char in text for char in INVISIBLE_UNICODE_CHARS)

def normalize_unicode(text: str) -> str:
    """
    Normalize Unicode text by removing invisible characters.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    if not text:
        return ""
        
    result = text
    for char in INVISIBLE_UNICODE_CHARS:
        result = result.replace(char, '')
    
    # Also normalize to NFC form to handle combining characters
    import unicodedata
    result = unicodedata.normalize('NFC', result)
    
    # Handle additional Unicode normalization cases
    # Convert different types of spaces to regular spaces
    space_chars = [
        '\u00A0',  # Non-breaking space
        '\u2000',  # En quad
        '\u2001',  # Em quad
        '\u2002',  # En space
        '\u2003',  # Em space
        '\u2004',  # Three-per-em space
        '\u2005',  # Four-per-em space
        '\u2006',  # Six-per-em space
        '\u2007',  # Figure space
        '\u2008',  # Punctuation space
        '\u2009',  # Thin space
        '\u200A',  # Hair space
        '\u202F',  # Narrow no-break space
        '\u205F',  # Medium mathematical space
        '\u3000',  # Ideographic space
    ]
    
    for char in space_chars:
        result = result.replace(char, ' ')
    
    return result

def handle_invisible_unicode(original_content: str, git_diff: str) -> str:
    """
    Handle invisible Unicode characters in a diff.
    This is a general-purpose handler that works for any file with invisible Unicode characters.
    
    Args:
        original_content: The original content
        git_diff: The git diff to apply
        
    Returns:
        The modified content with invisible Unicode characters handled properly
    """
    # If there are no invisible characters, return None to use regular handling
    if not contains_invisible_chars(git_diff) and not contains_invisible_chars(original_content):
        return None
    
    import re
    import logging
    logger = logging.getLogger(__name__)
    logger.debug("Handling invisible Unicode characters in diff")
    
    # Use the standard difflib apply function to apply the diff
    # This will handle the basic diff application
    from ..application.patch_apply import apply_diff_with_difflib
    
    try:
        # First try to apply the diff normally
        modified_content = apply_diff_with_difflib(original_content, git_diff)
        
        # Check if the result contains invisible characters
        if contains_invisible_chars(modified_content):
            logger.debug("Modified content contains invisible Unicode characters")
            
            # Preserve invisible characters from the original content where possible
            modified_content = preserve_invisible_chars(original_content, modified_content)
            
        return modified_content
    except Exception as e:
        logger.error(f"Error handling invisible Unicode characters: {str(e)}")
        # Fall back to standard handling
        return None

def extract_invisible_chars(text: str) -> str:
    """
    Extract only the invisible Unicode characters from text.
    
    Args:
        text: The text to extract from
        
    Returns:
        A string containing only the invisible characters
    """
    if not text:
        return ""
        
    result = ""
    for char in text:
        if char in INVISIBLE_UNICODE_CHARS:
            result += char
    
    return result

def preserve_invisible_chars(original: str, modified: str) -> str:
    """
    Preserve invisible Unicode characters from the original text in the modified text.
    
    Args:
        original: The original text with invisible characters
        modified: The modified text without invisible characters
        
    Returns:
        The modified text with invisible characters preserved
    """
    if not original or not modified:
        return modified
        
    if not contains_invisible_chars(original):
        return modified
    
    # Extract visible content from both strings
    original_visible = normalize_unicode(original)
    modified_visible = normalize_unicode(modified)
    
    # If the visible content is the same, preserve all invisible characters
    if original_visible == modified_visible:
        return original
    
    # For different content, use the mapping approach for better preservation
    return map_invisible_chars(original, modified)

def map_invisible_chars(original: str, modified: str) -> str:
    """
    Map invisible Unicode characters from the original text to the modified text
    based on surrounding visible characters.
    
    Args:
        original: The original text with invisible characters
        modified: The modified text without invisible characters
        
    Returns:
        The modified text with invisible characters mapped from the original
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if not original or not modified:
        return modified
        
    if not contains_invisible_chars(original):
        return modified
    
    # Extract visible content
    original_visible = normalize_unicode(original)
    
    # If the visible content is identical, we can just return the original
    if original_visible == normalize_unicode(modified):
        return original
    
    # If the strings are completely different, just return the modified text
    if len(original) == 0 or len(modified) == 0:
        return modified
        
    # For strings with only invisible characters, return the modified text
    if len(original_visible) == 0:
        return modified
    
    # Create a mapping of positions in the visible text to positions in the original text
    original_map = []
    visible_pos = 0
    
    for i, char in enumerate(original):
        if char not in INVISIBLE_UNICODE_CHARS:
            original_map.append((visible_pos, i))
            visible_pos += 1
    
    # Now map the invisible characters to the modified text
    result = list(modified)
    inserted = 0  # Track how many characters we've inserted
    
    # For each position in the original visible text, find the corresponding position
    # in the modified text and insert any invisible characters that follow
    for visible_pos, original_pos in original_map:
        if visible_pos >= len(original_visible) or visible_pos >= len(modified):
            break
            
        # Find invisible characters that follow this position in the original
        invisible_chars = ""
        i = original_pos + 1
        while i < len(original) and original[i] in INVISIBLE_UNICODE_CHARS:
            invisible_chars += original[i]
            i += 1
        
        # If we found invisible characters, insert them after the corresponding position
        if invisible_chars:
            # Find the position in the result
            result_pos = visible_pos + inserted
            if result_pos < len(result):
                # Insert the invisible characters
                result.insert(result_pos + 1, invisible_chars)
                inserted += 1
                logger.debug(f"Inserted invisible characters at position {result_pos + 1}")
    
    # Also check for invisible characters at the beginning of the original string
    if original and original[0] in INVISIBLE_UNICODE_CHARS:
        # Find all leading invisible characters
        leading_invisible = ""
        i = 0
        while i < len(original) and original[i] in INVISIBLE_UNICODE_CHARS:
            leading_invisible += original[i]
            i += 1
        
        if leading_invisible:
            # Insert at the beginning of the result
            result.insert(0, leading_invisible)
            inserted += 1
            logger.debug(f"Inserted leading invisible characters")
    
    return ''.join(result)
