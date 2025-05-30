"""
Indentation handling utilities for diff application.
"""

from typing import List, Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)

def detect_indentation_style(text: str) -> Tuple[bool, int]:
    """
    Detect the indentation style used in text.
    
    Args:
        text: The text to analyze
        
    Returns:
        A tuple of (uses_spaces, indent_size)
    """
    lines = text.splitlines()
    space_lines = 0
    tab_lines = 0
    indent_sizes = {}
    
    for line in lines:
        if not line.strip():
            continue  # Skip empty lines
            
        if line.startswith(' '):
            # Count spaces
            count = 0
            for char in line:
                if char == ' ':
                    count += 1
                else:
                    break
            
            if count > 0:
                space_lines += 1
                # Try to detect indent size
                if count not in indent_sizes:
                    indent_sizes[count] = 0
                indent_sizes[count] += 1
        elif line.startswith('\t'):
            tab_lines += 1
    
    # Determine if spaces or tabs are used
    uses_spaces = space_lines >= tab_lines
    
    # Determine indent size
    indent_size = 4  # Default
    if uses_spaces and indent_sizes:
        # Find the most common indent size
        common_sizes = sorted(indent_sizes.items(), key=lambda x: x[1], reverse=True)
        for size, count in common_sizes:
            if size in (2, 4, 8):
                indent_size = size
                break
    
    return uses_spaces, indent_size

def analyze_indentation_patterns(lines: List[str]) -> Dict[str, Any]:
    """
    Analyze indentation patterns in a list of lines.
    
    Args:
        lines: List of lines to analyze
        
    Returns:
        Dictionary with indentation analysis
    """
    text = ''.join(lines)
    uses_spaces, indent_size = detect_indentation_style(text)
    
    # Analyze indentation levels
    levels = {}
    for line in lines:
        if not line.strip():
            continue  # Skip empty lines
            
        # Count leading whitespace
        leading_space = len(line) - len(line.lstrip())
        
        # Calculate indentation level
        if uses_spaces:
            level = leading_space // indent_size if indent_size > 0 else 0
        else:
            level = leading_space  # For tabs, each tab is one level
            
        if level not in levels:
            levels[level] = 0
        levels[level] += 1
    
    return {
        "uses_spaces": uses_spaces,
        "indent_size": indent_size,
        "levels": levels
    }

def adjust_indentation(line: str, source_style: Dict[str, Any], target_style: Dict[str, Any]) -> str:
    """
    Adjust indentation of a line from source style to target style.
    
    Args:
        line: Line to adjust
        source_style: Source indentation style
        target_style: Target indentation style
        
    Returns:
        Line with adjusted indentation
    """
    if not line.strip():
        return line  # Don't adjust empty lines
        
    # Count leading whitespace
    leading_space = len(line) - len(line.lstrip())
    content = line.lstrip()
    
    # Calculate indentation level based on the source style
    if source_style["uses_spaces"]:
        indent_level = leading_space // source_style["indent_size"] if source_style["indent_size"] > 0 else 0
    else:
        indent_level = leading_space  # For tabs, each tab is one level
    
    # Create new indentation using the target style
    if target_style["uses_spaces"]:
        new_indent = ' ' * (indent_level * target_style["indent_size"])
    else:
        new_indent = '\t' * indent_level
    
    # Apply the adjusted indentation
    return new_indent + content

def adjust_block_indentation(lines: List[str], source_style: Dict[str, Any], target_style: Dict[str, Any]) -> List[str]:
    """
    Adjust indentation of a block of lines from source style to target style.
    
    Args:
        lines: Lines to adjust
        source_style: Source indentation style
        target_style: Target indentation style
        
    Returns:
        Lines with adjusted indentation
    """
    return [adjust_indentation(line, source_style, target_style) for line in lines]

def preserve_relative_indentation(lines: List[str], base_indentation: int = 0) -> List[str]:
    """
    Preserve relative indentation between lines while adjusting the base indentation.
    
    Args:
        lines: Lines to adjust
        base_indentation: Base indentation level to add
        
    Returns:
        Lines with preserved relative indentation
    """
    if not lines:
        return []
        
    # Find minimum indentation
    min_indent = None
    for line in lines:
        if line.strip():  # Skip empty lines
            leading_space = len(line) - len(line.lstrip())
            if min_indent is None or leading_space < min_indent:
                min_indent = leading_space
    
    if min_indent is None:
        min_indent = 0
    
    # Adjust indentation
    result = []
    for line in lines:
        if not line.strip():
            result.append(line)  # Keep empty lines as is
        else:
            leading_space = len(line) - len(line.lstrip())
            content = line.lstrip()
            # Preserve relative indentation
            new_indent = ' ' * (base_indentation + (leading_space - min_indent))
            result.append(new_indent + content)
    
    return result
