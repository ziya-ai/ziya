"""
Handler for JSON escape sequence issues in diffs.
"""

import re
from typing import Optional, List, Tuple
from app.utils.logging_utils import logger

def is_json_escape_sequence_issue(file_path: str, diff_content: str) -> bool:
    """
    Check if this diff involves JSON escape sequence issues.
    
    Args:
        file_path: Path to the file
        diff_content: The diff content
        
    Returns:
        True if this is a JSON escape sequence issue, False otherwise
    """
    # DISABLED - this handler is being incorrectly triggered for non-JSON files
    return False
    
    # Check if this is a JavaScript/TypeScript file
    if not file_path.endswith(('.js', '.jsx', '.ts', '.tsx', '.json')):
        return False
    
    # Check for JSON-related patterns in the diff
    json_patterns = [
        r'JSON\.parse',
        r'JSON\.stringify',
        r'`\s*{.*}.*`',  # Template literal with object
        r'".*":\s*".*"',  # JSON key-value pair
        r'\'.*\':\s*\'.*\'',  # JSON key-value pair with single quotes
        r'\\r\\n',  # Escape sequences
        r'\\n',
    ]
    
    for pattern in json_patterns:
        if re.search(pattern, diff_content):
            return True
    
    return False

def fix_json_escape_sequence_issue(file_path: str, original_content: str, modified_content: str) -> str:
    """
    Fix JSON escape sequence issues in the modified content.
    
    Args:
        file_path: Path to the file
        original_content: Original file content
        modified_content: Modified file content
        
    Returns:
        Fixed content
    """
    logger.info(f"Applying JSON escape sequence handler for {file_path}")
    
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
    
    # Fix specific issues with template literals in the test case
    if '`{' in result and '}`' in result:
        # Find all template literals with JSON content
        template_literals = re.findall(r'`({[^`]*?})`', result)
        
        for template in template_literals:
            # Format the JSON content with proper indentation
            formatted_json = format_json_content(template)
            
            # Replace the original template with the formatted one
            result = result.replace(f'`{template}`', f'`{formatted_json}`')
    
    return result

def format_json_content(json_content: str) -> str:
    """
    Format JSON content with proper indentation.
    
    Args:
        json_content: The JSON content to format
        
    Returns:
        Formatted JSON content
    """
    # Simple JSON formatter for template literals
    lines = []
    indent_level = 0
    in_string = False
    escape_next = False
    current_line = ""
    
    for char in json_content:
        if escape_next:
            current_line += char
            escape_next = False
            continue
            
        if char == '\\':
            current_line += char
            escape_next = True
            continue
            
        if char == '"' and not escape_next:
            in_string = not in_string
            current_line += char
            continue
            
        if not in_string:
            if char == '{' or char == '[':
                current_line += char
                lines.append(current_line)
                indent_level += 1
                current_line = "    " * indent_level
                continue
                
            if char == '}' or char == ']':
                if current_line.strip():
                    lines.append(current_line)
                indent_level = max(0, indent_level - 1)
                current_line = "    " * indent_level + char
                continue
                
            if char == ',':
                current_line += char
                lines.append(current_line)
                current_line = "    " * indent_level
                continue
                
            if char == ':':
                current_line += char + " "
                continue
                
            if char.isspace():
                if current_line.strip():
                    current_line += char
                continue
        
        current_line += char
    
    if current_line.strip():
        lines.append(current_line)
    
    return "\n".join(lines)
