"""
Module for handling JSON content in diffs.
"""

import re
from typing import Optional, List, Tuple
from app.utils.logging_utils import logger

def contains_json_content(content: str) -> bool:
    """
    Check if the content contains JSON-like structures that need special handling.
    
    Args:
        content: The content to check
        
    Returns:
        True if the content contains JSON-like structures, False otherwise
    """
    if not content:
        return False
        
    # Look for common JSON patterns
    json_patterns = [
        r'JSON\.parse',
        r'JSON\.stringify',
        r'`\s*{.*}.*`',  # Template literal with object
        r'".*":\s*".*"',  # JSON key-value pair
        r'\'.*\':\s*\'.*\'',  # JSON key-value pair with single quotes
    ]
    
    for pattern in json_patterns:
        if re.search(pattern, content):
            return True
    
    return False

def preserve_json_structure(original_content: str, modified_content: str) -> str:
    """
    Preserve JSON structure in JavaScript/TypeScript files.
    
    Args:
        original_content: Original file content
        modified_content: Modified file content
        
    Returns:
        Processed modified content with preserved JSON structure
    """
    # If no JSON content, return the modified content as is
    if not contains_json_content(modified_content):
        return modified_content
        
    logger.debug("Preserving JSON structure in JavaScript/TypeScript content")
    
    # Preserve template literals with JSON content
    template_literal_pattern = r'(`\s*{[^`]*?}\s*`)'
    
    def preserve_template_literal(match):
        template_literal = match.group(1)
        # Ensure proper line breaks in the template literal
        if '\n' in template_literal:
            # Split the template literal into lines
            lines = template_literal.split('\n')
            # Preserve the indentation of each line
            for i in range(1, len(lines)):
                lines[i] = lines[i].rstrip()
            # Join the lines back together
            return '\n'.join(lines)
        return template_literal
    
    # Apply the preservation to the modified content
    result = re.sub(template_literal_pattern, preserve_template_literal, modified_content)
    
    # Handle JSON.parse and JSON.stringify calls
    json_method_pattern = r'(JSON\.(parse|stringify)\s*\([^)]*\))'
    
    def preserve_json_method(match):
        json_call = match.group(1)
        # Ensure the JSON call is properly formatted
        return json_call
    
    result = re.sub(json_method_pattern, preserve_json_method, result)
    
    # Handle escape sequences in JSON strings
    json_string_pattern = r'("(?:\\.|[^"\\])*")'
    
    def preserve_escape_sequences(match):
        json_string = match.group(1)
        # Ensure escape sequences are preserved
        return json_string
    
    result = re.sub(json_string_pattern, preserve_escape_sequences, result)
    
    return result

def fix_json_template_literals(content: str) -> str:
    """
    Fix issues with template literals containing JSON in JavaScript/TypeScript files.
    
    Args:
        content: The content to fix
        
    Returns:
        Fixed content with properly formatted template literals
    """
    # Find template literals with JSON content
    template_literal_pattern = r'(`\s*{[^`]*?}\s*`)'
    
    def fix_template_literal(match):
        template_literal = match.group(1)
        # If the template literal contains newlines, ensure they're properly formatted
        if '\n' in template_literal:
            # Remove the backticks to process the content
            json_content = template_literal[1:-1]
            
            # Split into lines and normalize indentation
            lines = json_content.split('\n')
            
            # Find the base indentation level (from the first non-empty line)
            base_indent = ''
            for line in lines[1:]:  # Skip the first line which might not have indentation
                if line and line.strip():
                    base_indent = re.match(r'^\s*', line).group(0)
                    break
            
            # Process each line
            processed_lines = []
            processed_lines.append(lines[0])  # First line remains unchanged
            
            for i in range(1, len(lines)):
                line = lines[i] if i < len(lines) else ""
                # Remove the base indentation from each line
                if line.startswith(base_indent):
                    line = line[len(base_indent):]
                processed_lines.append(line)
            
            # Reconstruct the template literal
            return '`' + '\n'.join(processed_lines) + '`'
        
        return template_literal
    
    return re.sub(template_literal_pattern, fix_template_literal, content)

def normalize_json_whitespace(content: str) -> str:
    """
    Normalize whitespace in JSON content.
    
    Args:
        content: The content to normalize
        
    Returns:
        Content with normalized JSON whitespace
    """
    # Find JSON objects in the content
    json_pattern = r'({[^{}]*(?:{[^{}]*}[^{}]*)*})'
    
    def normalize_json_object(match):
        json_obj = match.group(1)
        
        # Normalize whitespace around colons and commas
        json_obj = re.sub(r'\s*:\s*', ': ', json_obj)
        json_obj = re.sub(r'\s*,\s*', ', ', json_obj)
        
        # Normalize whitespace around brackets
        json_obj = re.sub(r'{\s+', '{ ', json_obj)
        json_obj = re.sub(r'\s+}', ' }', json_obj)
        
        return json_obj
    
    return re.sub(json_pattern, normalize_json_object, content)

def process_json_content(original_content: str, modified_content: str) -> str:
    """
    Process JSON content in JavaScript/TypeScript files.
    
    Args:
        original_content: Original file content
        modified_content: Modified file content
        
    Returns:
        Processed content with proper JSON handling
    """
    # Check if this file contains JSON content
    if not contains_json_content(original_content) and not contains_json_content(modified_content):
        return modified_content
    
    logger.info("Processing JSON content in JavaScript/TypeScript file")
    
    # Apply JSON-specific fixes
    result = preserve_json_structure(original_content, modified_content)
    result = fix_json_template_literals(result)
    result = normalize_json_whitespace(result)
    
    return result
