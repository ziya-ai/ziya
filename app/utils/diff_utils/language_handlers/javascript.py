"""
JavaScript-specific language handler.
"""

import re
import subprocess
from typing import List, Tuple, Optional, Dict

from app.utils.logging_utils import logger
from .base import LanguageHandler

# Special handling for JSON content in JavaScript/TypeScript files
class JsonContentHandler:
    """Helper class for handling JSON content in JavaScript/TypeScript files."""
    
    @staticmethod
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
    
    @staticmethod
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
        if not JsonContentHandler.contains_json_content(modified_content):
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


class JavaScriptHandler(LanguageHandler):
    """Handler for JavaScript files."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if this is a JavaScript file, False otherwise
        """
        return file_path.endswith(('.js', '.jsx', '.ts', '.tsx'))
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for JavaScript.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Special handling for JSON content
        if JsonContentHandler.contains_json_content(original_content) or JsonContentHandler.contains_json_content(modified_content):
            logger.debug(f"JavaScript file contains JSON content, applying special handling")
            modified_content = JsonContentHandler.preserve_json_structure(original_content, modified_content)
        
        # Try to use Node.js to validate JavaScript syntax if available
        try:
            # Create a temporary file with the modified content
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(suffix='.js', delete=False) as temp:
                temp.write(modified_content.encode('utf-8'))
                temp_path = temp.name
            
            try:
                # Use Node.js to check syntax
                result = subprocess.run(
                    ['node', '--check', temp_path],
                    capture_output=True,
                    text=True,
                    timeout=5  # 5 second timeout
                )
                
                if result.returncode != 0:
                    error_msg = result.stderr.strip()
                    logger.error(f"JavaScript syntax validation failed for {file_path}: {error_msg}")
                    return False, error_msg
                
                # Additional verification: check for common issues
                issues = cls._check_common_issues(original_content, modified_content)
                if issues:
                    return False, f"Code verification issues: {'; '.join(issues)}"
                
                return True, None
            finally:
                # Clean up the temporary file
                os.unlink(temp_path)
        except (subprocess.SubprocessError, FileNotFoundError, Exception) as e:
            # If Node.js is not available or fails, fall back to basic validation
            logger.warning(f"Falling back to basic JavaScript validation: {str(e)}")
            
            # Basic validation: check for matching braces, parentheses, etc.
            is_valid, error = cls._basic_js_validation(modified_content)
            if not is_valid:
                logger.error(f"Basic JavaScript validation failed for {file_path}: {error}")
                return False, error
            
            return True, None
    
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
        
        # Check for inconsistent quotes (mixing ' and ")
        single_quotes = len(re.findall(r"'[^']*'", modified_content))
        double_quotes = len(re.findall(r'"[^"]*"', modified_content))
        
        # If both types are used, check if one is significantly more common
        if single_quotes > 0 and double_quotes > 0:
            total = single_quotes + double_quotes
            if single_quotes / total < 0.2 or double_quotes / total < 0.2:
                issues.append("Inconsistent quote style (mixing ' and \")")
        
        # Check for inconsistent semicolon usage
        lines_with_semi = 0
        lines_without_semi = 0
        
        for line in modified_content.splitlines():
            line = line.strip()
            if not line or line.startswith('//') or line.startswith('/*') or line.endswith('*/'):
                continue
                
            if line.endswith(';'):
                lines_with_semi += 1
            elif not line.endswith('{') and not line.endswith('}') and not line.endswith(':'):
                lines_without_semi += 1
        
        # If both styles are used, check if one is significantly more common
        if lines_with_semi > 0 and lines_without_semi > 0:
            total = lines_with_semi + lines_without_semi
            if lines_with_semi / total < 0.2 or lines_without_semi / total < 0.2:
                issues.append("Inconsistent semicolon usage")
        
        # Check for potential infinite loops
        if re.search(r'while\s*\(\s*true\s*\)', modified_content) and not re.search(r'break', modified_content):
            issues.append("Potential infinite loop (while(true) without break)")
        
        return issues
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated functions/classes in JavaScript code.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        # Extract function and class definitions from both contents
        original_functions = cls._extract_function_definitions(original_content)
        modified_functions = cls._extract_function_definitions(modified_content)
        
        # Check for duplicates
        duplicates = []
        for func_name, occurrences in modified_functions.items():
            if len(occurrences) > 1:
                # Check if it was already duplicated in the original
                original_count = len(original_functions.get(func_name, []))
                if len(occurrences) > original_count:
                    # Get line numbers for better reporting
                    line_numbers = ", ".join(str(line) for line in occurrences)
                    duplicates.append(f"{func_name} (lines {line_numbers})")
                    logger.warning(f"Function '{func_name}' appears to be duplicated after diff application at lines {line_numbers}")
        
        # Check for similar function implementations
        similar_functions = cls._detect_similar_functions(modified_content)
        for func_pair, similarity in similar_functions:
            if similarity > 0.9:  # High similarity threshold
                duplicates.append(f"Similar functions: {func_pair[0]} and {func_pair[1]} ({similarity:.2f} similarity)")
                logger.warning(f"Functions '{func_pair[0]}' and '{func_pair[1]}' appear to be very similar ({similarity:.2f} similarity)")
        
        return bool(duplicates), duplicates
    
    @classmethod
    def _extract_function_definitions(cls, content: str) -> Dict[str, List[int]]:
        """
        Extract function and class definitions from JavaScript content.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping function/class names to lists of line numbers where they appear
        """
        # Regex patterns for different types of JavaScript function definitions
        patterns = [
            # Function declarations
            r'function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(',
            # Class declarations
            r'class\s+([a-zA-Z_$][a-zA-Z0-9_$]*)',
            # Method definitions - more specific to avoid matching if statements
            r'(?:^|\s+)(?:async\s+)?([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\([^)]*\)\s*{(?!\s*\()',
            # Arrow functions with explicit names
            r'(?:const|let|var)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=\s*(?:async\s+)?\(',
            # Object methods
            r'([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:\s*function',
        ]
        
        # Keywords that should be excluded from function detection
        reserved_keywords = {
            'if', 'for', 'while', 'switch', 'catch', 'with', 'return',
            'else', 'try', 'finally', 'do', 'in', 'of', 'new', 'typeof',
            'instanceof', 'void', 'delete', 'throw', 'yield', 'await'
        }
        
        functions = {}
        for i, line in enumerate(content.splitlines(), 1):
            for pattern in patterns:
                matches = re.finditer(pattern, line)
                for match in matches:
                    func_name = match.group(1)
                    # Skip if the name is a reserved keyword
                    if func_name in reserved_keywords:
                        continue
                    # Skip if this is part of an if/for/while condition
                    if re.search(rf'(?:if|for|while|switch)\s*\([^)]*{re.escape(func_name)}', line):
                        continue
                    if func_name not in functions:
                        functions[func_name] = []
                    functions[func_name].append(i)
        
        return functions
    
    @classmethod
    def _detect_similar_functions(cls, content: str) -> List[Tuple[Tuple[str, str], float]]:
        """
        Detect functions with similar implementations.
        
        Args:
            content: Source code content
            
        Returns:
            List of tuples containing pairs of function names and their similarity score
        """
        import difflib
        
        # Extract function bodies
        function_bodies = cls._extract_function_bodies(content)
        
        # Compare function bodies for similarity
        similar_functions = []
        function_names = list(function_bodies.keys())
        
        for i in range(len(function_names)):
            for j in range(i+1, len(function_names)):
                name1 = function_names[i]
                name2 = function_names[j]
                
                body1 = function_bodies[name1]
                body2 = function_bodies[name2]
                
                # Skip empty or very short functions
                if len(body1) < 10 or len(body2) < 10:
                    continue
                
                # Calculate similarity
                similarity = difflib.SequenceMatcher(None, body1, body2).ratio()
                
                # Only include pairs with significant similarity
                if similarity > 0.8:
                    similar_functions.append(((name1, name2), similarity))
        
        return similar_functions
    
    @classmethod
    def _extract_function_bodies(cls, content: str) -> Dict[str, str]:
        """
        Extract function bodies from JavaScript content.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping function names to their bodies
        """
        function_bodies = {}
        lines = content.splitlines()
        
        # Find function declarations
        for i, line in enumerate(lines):
            # Function declarations
            match = re.search(r'function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(', line)
            if match:
                func_name = match.group(1)
                body = cls._extract_body_from_position(lines, i)
                if body:
                    function_bodies[func_name] = body
                continue
            
            # Arrow functions
            match = re.search(r'(?:const|let|var)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>', line)
            if match:
                func_name = match.group(1)
                body = cls._extract_body_from_position(lines, i)
                if body:
                    function_bodies[func_name] = body
                continue
            
            # Method definitions
            match = re.search(r'([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\([^)]*\)\s*{', line)
            if match:
                func_name = match.group(1)
                body = cls._extract_body_from_position(lines, i)
                if body:
                    function_bodies[func_name] = body
        
        return function_bodies
    
    @classmethod
    def _extract_body_from_position(cls, lines: List[str], start_line: int) -> Optional[str]:
        """
        Extract a function body starting from a given line.
        
        Args:
            lines: All lines of the file
            start_line: Line number where the function declaration starts
            
        Returns:
            The function body as a string, or None if extraction fails
        """
        # Find the opening brace
        brace_line = start_line
        while brace_line < len(lines) and '{' not in lines[brace_line]:
            brace_line += 1
            
        if brace_line >= len(lines):
            return None
            
        # Count braces to find the end of the function
        brace_count = 0
        end_line = brace_line
        
        for i in range(brace_line, len(lines)):
            for char in lines[i]:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_line = i
                        break
            if brace_count == 0:
                break
        
        # Extract the body
        body_lines = lines[brace_line:end_line+1]
        return '\n'.join(body_lines)
    @staticmethod
    def _basic_js_validation(content: str) -> Tuple[bool, Optional[str]]:
        """
        Perform basic JavaScript validation by checking for matching braces, etc.
        
        Args:
            content: JavaScript content to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        stack = []
        brackets = {')': '(', '}': '{', ']': '['}
        
        for i, char in enumerate(content):
            if char in '({[':
                stack.append(char)
            elif char in ')}]':
                if not stack or stack.pop() != brackets[char]:
                    line_num = content[:i].count('\n') + 1
                    col_num = i - content[:i].rfind('\n')
                    return False, f"Mismatched bracket at line {line_num}, column {col_num}"
        
        if stack:
            return False, f"Unclosed brackets: {', '.join(stack)}"
        
        # Check for common JavaScript syntax errors
        issues = []
        
        # Check for missing semicolons at line ends (excluding certain cases)
        lines = content.splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            if (line and not line.endswith(';') and not line.endswith('{') and 
                not line.endswith('}') and not line.endswith(':') and 
                not line.startswith('//') and not line.startswith('/*') and
                not line.endswith('*/') and not line.endswith(',') and
                not re.match(r'^import\s+.*\s+from\s+.*$', line) and
                not re.match(r'^export\s+.*$', line) and
                not line.endswith(')')):
                issues.append(f"Possible missing semicolon at line {i+1}")
        
        # Check for invalid variable names
        invalid_var_matches = re.finditer(r'\b(var|let|const)\s+([0-9][a-zA-Z0-9_$]*)', content)
        for match in invalid_var_matches:
            line_num = content[:match.start()].count('\n') + 1
            issues.append(f"Invalid variable name starting with number at line {line_num}")
        
        if issues:
            return False, "; ".join(issues[:3])  # Return first few issues
        
        return True, None
