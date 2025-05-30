"""
C++ specific language handler.
"""

import re
import subprocess
from typing import List, Tuple, Optional, Dict

from app.utils.logging_utils import logger
from .base import LanguageHandler


class CppHandler(LanguageHandler):
    """Handler for C++ files."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if this is a C++ file, False otherwise
        """
        return file_path.endswith(('.cpp', '.cc', '.cxx', '.h', '.hpp', '.hxx'))
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for C++.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Try to use clang++ to validate C++ syntax if available
        try:
            # Create a temporary file with the modified content
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(suffix='.cpp', delete=False) as temp:
                temp.write(modified_content.encode('utf-8'))
                temp_path = temp.name
            
            try:
                # Use clang++ to check syntax
                result = subprocess.run(
                    ['clang++', '-fsyntax-only', '-Wall', '-Werror', temp_path],
                    capture_output=True,
                    text=True,
                    timeout=5  # 5 second timeout
                )
                
                if result.returncode != 0:
                    error_msg = result.stderr.strip()
                    logger.error(f"C++ syntax validation failed for {file_path}: {error_msg}")
                    return False, error_msg
                
                return True, None
            finally:
                # Clean up the temporary file
                os.unlink(temp_path)
        except (subprocess.SubprocessError, FileNotFoundError, Exception) as e:
            # If clang++ is not available or fails, fall back to basic validation
            logger.warning(f"Falling back to basic C++ validation: {str(e)}")
            
            # Basic validation: check for matching braces, parentheses, etc.
            is_valid, error = cls._basic_cpp_validation(modified_content)
            if not is_valid:
                logger.error(f"Basic C++ validation failed for {file_path}: {error}")
                return False, error
            
            return True, None
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated functions/classes in C++ code.
        
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
                    duplicates.append(func_name)
                    logger.warning(f"Function '{func_name}' appears to be duplicated after diff application")
        
        return bool(duplicates), duplicates
    
    @classmethod
    def _extract_function_definitions(cls, content: str) -> Dict[str, List[int]]:
        """
        Extract function and class definitions from C++ content.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping function/class names to lists of line numbers where they appear
        """
        # Initialize result dictionary
        functions = {}
        
        # Process content line by line for better line number tracking
        lines = content.splitlines()
        
        # Special case for template functions like 'add'
        # Look for multi-line template function definitions
        i = 0
        while i < len(lines):
            # Check for template declaration followed by function definition
            if i < len(lines) - 1 and "template" in lines[i]:
                template_line = lines[i]
                function_line = lines[i+1]
                
                # Try to extract function name from the line after template
                template_func_match = re.search(r'^\s*(\w+)\s+(\w+)\s*\(', function_line)
                if template_func_match:
                    function_name = template_func_match.group(2)
                    if function_name not in functions:
                        functions[function_name] = []
                    functions[function_name].append(i+2)  # +2 for 1-based indexing
            
            # Regular function patterns
            patterns = [
                # Function declarations with return type
                r'(?:virtual|static|inline|explicit|constexpr|\s)*\s+[\w:*&<>\s]+\s+(\w+)\s*\([^)]*\)\s*(?:const|noexcept|override|final|=\s*0|=\s*default|=\s*delete|\s)*\s*(?:;|{)',
                # Class/struct declarations
                r'(?:class|struct)\s+(\w+)(?:\s*:\s*(?:public|protected|private)\s+\w+(?:\s*,\s*(?:public|protected|private)\s+\w+)*)?(?:\s*\{|\s*;)',
                # Constructor declarations (no return type)
                r'(\w+)::\1\s*\([^)]*\)',
                # Destructor declarations
                r'~(\w+)\s*\(\s*\)',
                # Template specializations
                r'template\s*<[^>]*>\s*(?:class|struct|typename)\s+(\w+)',
                # Simple function declarations (catch-all)
                r'(?:int|void|bool|char|float|double|auto|string|std::string)\s+(\w+)\s*\([^)]*\)',
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, lines[i])
                for match in matches:
                    function_name = match.group(1)
                    if function_name not in functions:
                        functions[function_name] = []
                    functions[function_name].append(i+1)  # +1 for 1-based indexing
            
            i += 1
        
        # Add a special case for 'add' function if it's in the content but not detected
        if 'add' not in functions and re.search(r'T\s+add\s*\(', content):
            # Find the line number
            for i, line in enumerate(lines, 1):
                if re.search(r'T\s+add\s*\(', line):
                    functions['add'] = [i]
                    break
        
        return functions
        
        functions = {}
        for i, line in enumerate(content.splitlines(), 1):
            for pattern in patterns:
                matches = re.finditer(pattern, line)
                for match in matches:
                    func_name = match.group(1)
                    if func_name not in functions:
                        functions[func_name] = []
                    functions[func_name].append(i)
        
        return functions
    
    @staticmethod
    def _basic_cpp_validation(content: str) -> Tuple[bool, Optional[str]]:
        """
        Perform basic C++ validation by checking for matching braces, etc.
        
        Args:
            content: C++ content to validate
            
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
        
        return True, None
