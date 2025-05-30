"""
Python-specific language handler.
"""

import re
import ast
import difflib
from typing import List, Tuple, Optional, Dict, Set

from app.utils.logging_utils import logger
from .base import LanguageHandler


class PythonHandler(LanguageHandler):
    """Handler for Python files."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if this is a Python file, False otherwise
        """
        return file_path.endswith('.py')
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for Python.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if the modified content is valid Python syntax
        try:
            ast.parse(modified_content, filename=file_path)
            
            # Additional verification: check for common issues
            issues = cls._check_common_issues(original_content, modified_content)
            if issues:
                return False, f"Code verification issues: {'; '.join(issues)}"
            
            # Check for duplicate functions
            has_duplicates, duplicates = cls.detect_duplicates(original_content, modified_content)
            if has_duplicates:
                return False, f"Duplicate code detected: {', '.join(duplicates)}"
            
            return True, None
        except SyntaxError as e:
            error_msg = f"Syntax error at line {e.lineno}, column {e.offset}: {e.msg}"
            logger.error(f"Python syntax validation failed for {file_path}: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Error parsing Python code: {str(e)}"
            logger.error(f"Python validation failed for {file_path}: {error_msg}")
            return False, error_msg
    
    @classmethod
    def _check_common_issues(cls, original_content: str, modified_content: str) -> List[str]:
        """
        Check for common issues in the modified content.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            List of issue descriptions
        """
        issues = []
        
        # Check for inconsistent indentation
        if cls._has_inconsistent_indentation(modified_content):
            issues.append("Inconsistent indentation detected")
        
        # Check for unbalanced parentheses/brackets
        if cls._has_unbalanced_delimiters(modified_content):
            issues.append("Unbalanced parentheses, brackets, or braces")
        
        # Check for incomplete function definitions
        if cls._has_incomplete_functions(modified_content):
            issues.append("Incomplete function definitions detected")
        
        return issues
    
    @classmethod
    def _has_inconsistent_indentation(cls, content: str) -> bool:
        """Check for inconsistent indentation."""
        lines = content.splitlines()
        indent_sizes = set()
        
        for line in lines:
            if line.strip() and not line.strip().startswith('#'):
                indent = len(line) - len(line.lstrip())
                if indent > 0:
                    indent_sizes.add(indent)
        
        # Check if we have mixed indentation (e.g., 2 and 4 spaces)
        if len(indent_sizes) > 1:
            # Allow for nested indentation (multiples of the smallest indent)
            smallest = min(indent_sizes) if indent_sizes else 0
            for size in indent_sizes:
                if size % smallest != 0:
                    return True
        
        return False
    
    @classmethod
    def _has_unbalanced_delimiters(cls, content: str) -> bool:
        """Check for unbalanced parentheses, brackets, or braces."""
        # This is a simple check - the AST parser will catch most issues
        stack = []
        pairs = {')': '(', ']': '[', '}': '{'}
        
        for char in content:
            if char in '([{':
                stack.append(char)
            elif char in ')]}':
                if not stack or stack.pop() != pairs[char]:
                    return True
        
        return len(stack) > 0
    
    @classmethod
    def _has_incomplete_functions(cls, content: str) -> bool:
        """Check for incomplete function definitions."""
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if re.match(r'^\s*def\s+\w+\s*\(.*\)\s*:', line):
                # Found a function definition, check if it has a body
                if i == len(lines) - 1:
                    # Function at the end of the file with no body
                    return True
                
                # Check the next line for indentation
                next_line = lines[i + 1]
                if not next_line.strip() or len(next_line) <= len(line) - len(line.lstrip()):
                    # Empty or not indented - incomplete function
                    return True
        
        return False
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated functions/classes in Python code.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        # Extract function and class definitions from both contents
        try:
            original_functions = cls._extract_function_definitions(original_content)
            modified_functions = cls._extract_function_definitions(modified_content)
            
            # Check for duplicates
            duplicates = []
            
            # Check for duplicate functions
            for func_name, occurrences in modified_functions.items():
                if len(occurrences) > 1:
                    # Check if it was already duplicated in the original
                    original_count = len(original_functions.get(func_name, []))
                    
                    # If there are more occurrences in the modified content than in the original,
                    # or if the occurrences are far apart, it's likely a duplicate
                    if len(occurrences) > original_count or cls._are_occurrences_far_apart(occurrences):
                        # Get line numbers for better reporting
                        line_numbers = ", ".join(str(line) for line in occurrences)
                        duplicates.append(f"{func_name} (lines {line_numbers})")
                        logger.warning(f"Function '{func_name}' appears to be duplicated after diff application at lines {line_numbers}")
            
            # Also check for methods within classes
            original_methods = cls._extract_method_definitions(original_content)
            modified_methods = cls._extract_method_definitions(modified_content)
            
            # Check for duplicate methods within the same class
            for class_name, methods in modified_methods.items():
                for method_name, occurrences in methods.items():
                    if len(occurrences) > 1:
                        # Check if it was already duplicated in the original
                        original_count = 0
                        if class_name in original_methods and method_name in original_methods[class_name]:
                            original_count = len(original_methods[class_name][method_name])
                        
                        # If there are more occurrences in the modified content than in the original,
                        # or if the occurrences are far apart, it's likely a duplicate
                        if len(occurrences) > original_count or cls._are_occurrences_far_apart(occurrences):
                            line_numbers = ", ".join(str(line) for line in occurrences)
                            duplicates.append(f"{class_name}.{method_name} (lines {line_numbers})")
                            logger.warning(f"Method '{class_name}.{method_name}' appears to be duplicated after diff application at lines {line_numbers}")
            
            return bool(duplicates), duplicates
        except Exception as e:
            logger.error(f"Error detecting Python duplicates: {str(e)}")
            return False, []
            
    @classmethod
    def _are_occurrences_far_apart(cls, occurrences: List[int]) -> bool:
        """
        Check if function/method occurrences are far apart in the file.
        
        Args:
            occurrences: List of line numbers
            
        Returns:
            True if occurrences are far apart, False otherwise
        """
        if len(occurrences) < 2:
            return False
            
        # Sort the occurrences
        sorted_occurrences = sorted(occurrences)
        
        # Check if any pair of occurrences is far apart
        for i in range(len(sorted_occurrences) - 1):
            if sorted_occurrences[i+1] - sorted_occurrences[i] > 50:  # More than 50 lines apart
                return True
                
        return False
    
    @classmethod
    def _extract_function_definitions(cls, content: str) -> Dict[str, List[int]]:
        """
        Extract function and class definitions from Python content.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping function/class names to lists of line numbers where they appear
        """
        functions = {}
        
        try:
            tree = ast.parse(content)
            
            # Find all function and class definitions
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = node.name
                    lineno = node.lineno
                    
                    if name not in functions:
                        functions[name] = []
                    functions[name].append(lineno)
        except Exception as e:
            # Fall back to regex-based extraction if AST parsing fails
            logger.warning(f"Falling back to regex-based function extraction: {str(e)}")
            functions = cls._extract_function_definitions_regex(content)
        
        return functions
    
    @classmethod
    def _extract_function_definitions_regex(cls, content: str) -> Dict[str, List[int]]:
        """
        Extract function definitions using regex as a fallback.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping function names to lists of line numbers where they appear
        """
        # Simple regex to find function and class definitions
        function_pattern = r'^\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
        class_pattern = r'^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]'
        
        functions = {}
        for i, line in enumerate(content.splitlines(), 1):
            # Check for function definitions
            match = re.search(function_pattern, line)
            if match:
                func_name = match.group(1)
                if func_name not in functions:
                    functions[func_name] = []
                functions[func_name].append(i)
                continue
            
            # Check for class definitions
            match = re.search(class_pattern, line)
            if match:
                class_name = match.group(1)
                if class_name not in functions:
                    functions[class_name] = []
                functions[class_name].append(i)
        
        return functions
    
    @classmethod
    def _extract_method_definitions(cls, content: str) -> Dict[str, Dict[str, List[int]]]:
        """
        Extract method definitions within classes.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping class names to dictionaries of method names and their line numbers
        """
        class_methods = {}
        
        try:
            tree = ast.parse(content)
            
            # Find all class definitions
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_name = node.name
                    class_methods[class_name] = {}
                    
                    # Find all method definitions within this class
                    for child_node in ast.iter_child_nodes(node):
                        if isinstance(child_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            method_name = child_node.name
                            lineno = child_node.lineno
                            
                            if method_name not in class_methods[class_name]:
                                class_methods[class_name][method_name] = []
                            class_methods[class_name][method_name].append(lineno)
        except Exception as e:
            # Fall back to regex-based extraction if AST parsing fails
            logger.warning(f"Falling back to regex-based method extraction: {str(e)}")
            class_methods = cls._extract_method_definitions_regex(content)
        
        return class_methods
    
    @classmethod
    def _extract_method_definitions_regex(cls, content: str) -> Dict[str, Dict[str, List[int]]]:
        """
        Extract method definitions using regex as a fallback.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping class names to dictionaries of method names and their line numbers
        """
        class_methods = {}
        current_class = None
        indentation_level = 0
        
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            # Check for class definitions
            class_match = re.search(r'^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]', line)
            if class_match:
                current_class = class_match.group(1)
                indentation_level = len(line) - len(line.lstrip())
                class_methods[current_class] = {}
                continue
            
            # Check for method definitions within the current class
            if current_class:
                # Check if we're still within the class based on indentation
                if line.strip() and len(line) - len(line.lstrip()) <= indentation_level:
                    # We've exited the class
                    current_class = None
                    continue
                
                # Check for method definitions
                method_match = re.search(r'^\s+def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', line)
                if method_match:
                    method_name = method_match.group(1)
                    if method_name not in class_methods[current_class]:
                        class_methods[current_class][method_name] = []
                    class_methods[current_class][method_name].append(i)
        
        return class_methods
        
    @classmethod
    def enhance_match_confidence(cls, original_content: str, hunk_content: str, 
                               candidate_positions: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
        """
        Enhance match confidence scores based on Python-specific context.
        
        Args:
            original_content: Original file content
            hunk_content: Content of the hunk to be applied
            candidate_positions: List of (position, confidence) tuples from fuzzy matching
            
        Returns:
            Updated list of (position, confidence) tuples with language-aware adjustments
        """
        if not candidate_positions:
            return []
            
        enhanced_candidates = []
        original_lines = original_content.splitlines()
        hunk_lines = hunk_content.splitlines()
        
        for position, confidence in candidate_positions:
            # Skip invalid positions
            if position < 0 or position >= len(original_lines):
                continue
                
            # Get the indentation level of the hunk and the target position
            hunk_indent = cls._get_indentation_level(hunk_lines[0]) if hunk_lines else 0
            target_indent = cls._get_indentation_level(original_lines[position]) if position < len(original_lines) else 0
            
            # Check if the indentation levels match
            indent_match = abs(hunk_indent - target_indent) <= 4  # Allow small differences
            
            # Check if we're inside a function or class definition
            in_function = cls._is_inside_function(original_lines, position)
            hunk_is_function = cls._is_function_definition(hunk_lines[0]) if hunk_lines else False
            
            # Adjust confidence based on context
            adjusted_confidence = confidence
            
            # Boost confidence if indentation matches
            if indent_match:
                adjusted_confidence += 0.05
                
            # Penalize if trying to insert a function inside another function
            if in_function and hunk_is_function:
                adjusted_confidence -= 0.1
                
            # Boost if inserting at the end of a function or class
            if cls._is_end_of_block(original_lines, position):
                adjusted_confidence += 0.05
                
            # Add the adjusted candidate
            enhanced_candidates.append((position, min(1.0, adjusted_confidence)))
            
        # Sort by confidence (descending)
        enhanced_candidates.sort(key=lambda x: x[1], reverse=True)
        
        return enhanced_candidates
        
    @classmethod
    def _get_indentation_level(cls, line: str) -> int:
        """Get the indentation level of a line."""
        return len(line) - len(line.lstrip())
        
    @classmethod
    def _is_inside_function(cls, lines: List[str], position: int) -> bool:
        """Check if a position is inside a function definition."""
        # Look backwards for a function definition
        indent_level = cls._get_indentation_level(lines[position]) if position < len(lines) else 0
        
        for i in range(position - 1, -1, -1):
            line = lines[i]
            line_indent = cls._get_indentation_level(line)
            
            # If we find a line with less indentation, check if it's a function definition
            if line_indent < indent_level:
                if re.match(r'^\s*def\s+', line) or re.match(r'^\s*class\s+', line):
                    return True
                indent_level = line_indent
                
            # If we reach the start of the file or an empty line, stop searching
            if line_indent == 0 and i > 0 and not lines[i-1].strip():
                break
                
        return False
        
    @classmethod
    def _is_function_definition(cls, line: str) -> bool:
        """Check if a line is a function definition."""
        return bool(re.match(r'^\s*def\s+', line))
        
    @classmethod
    def _is_end_of_block(cls, lines: List[str], position: int) -> bool:
        """Check if a position is at the end of a block (function or class)."""
        if position >= len(lines) or position == 0:
            return False
            
        current_indent = cls._get_indentation_level(lines[position])
        
        # Check if the next line has less indentation
        if position + 1 < len(lines):
            next_line = lines[position + 1]
            if not next_line.strip():  # Skip empty lines
                return False
            next_indent = cls._get_indentation_level(next_line)
            return next_indent < current_indent
            
        return False
        
    @classmethod
    def check_for_collisions(cls, original_content: str, hunk_content: str, position: int) -> Tuple[bool, Optional[str]]:
        """
        Check if applying the hunk at the given position would create collisions like duplicate functions.
        
        Args:
            original_content: Original file content
            hunk_content: Content of the hunk to be applied
            position: Position where the hunk would be applied
            
        Returns:
            Tuple of (is_safe, error_message)
        """
        # Create the modified content by applying the hunk at the given position
        original_lines = original_content.splitlines()
        hunk_lines = hunk_content.splitlines()
        
        # Insert the hunk at the given position
        modified_lines = original_lines[:position] + hunk_lines + original_lines[position:]
        modified_content = '\n'.join(modified_lines)
        
        # Check for duplicates
        has_duplicates, duplicates = cls.detect_duplicates(original_content, modified_content)
        
        if has_duplicates:
            return False, f"Applying this change would create duplicate functions: {', '.join(duplicates)}"
            
        return True, None
