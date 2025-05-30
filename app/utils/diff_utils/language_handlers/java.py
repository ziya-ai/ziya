"""
Java-specific language handler.
"""

import re
import subprocess
from typing import List, Tuple, Optional, Dict

from app.utils.logging_utils import logger
from .base import LanguageHandler


class JavaHandler(LanguageHandler):
    """Handler for Java files."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if this is a Java file, False otherwise
        """
        return file_path.endswith('.java')
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for Java.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Try to use javac to validate Java syntax if available
        try:
            # Create a temporary file with the modified content
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(suffix='.java', delete=False) as temp:
                temp.write(modified_content.encode('utf-8'))
                temp_path = temp.name
            
            try:
                # Use javac to check syntax
                result = subprocess.run(
                    ['javac', '-Xlint:all', temp_path],
                    capture_output=True,
                    text=True,
                    timeout=5  # 5 second timeout
                )
                
                if result.returncode != 0:
                    error_msg = result.stderr.strip()
                    logger.error(f"Java syntax validation failed for {file_path}: {error_msg}")
                    return False, error_msg
                
                return True, None
            finally:
                # Clean up the temporary file
                os.unlink(temp_path)
        except (subprocess.SubprocessError, FileNotFoundError, Exception) as e:
            # If javac is not available or fails, fall back to basic validation
            logger.warning(f"Falling back to basic Java validation: {str(e)}")
            
            # Basic validation: check for matching braces, parentheses, etc.
            is_valid, error = cls._basic_java_validation(modified_content)
            if not is_valid:
                logger.error(f"Basic Java validation failed for {file_path}: {error}")
                return False, error
            
            return True, None
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated methods/classes in Java code.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        # Extract method and class definitions from both contents
        original_methods = cls._extract_method_definitions(original_content)
        modified_methods = cls._extract_method_definitions(modified_content)
        
        # Check for duplicates
        duplicates = []
        for method_name, occurrences in modified_methods.items():
            if len(occurrences) > 1:
                # Check if it was already duplicated in the original
                original_count = len(original_methods.get(method_name, []))
                if len(occurrences) > original_count:
                    duplicates.append(method_name)
                    logger.warning(f"Method '{method_name}' appears to be duplicated after diff application")
        
        return bool(duplicates), duplicates
    
    @classmethod
    def _extract_method_definitions(cls, content: str) -> Dict[str, List[int]]:
        """
        Extract method and class definitions from Java content.
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping method/class names to lists of line numbers where they appear
        """
        # Regex patterns for different types of Java method/class definitions
        patterns = [
            # Method declarations
            r'(?:public|protected|private|static|\s) +[\w\<\>\[\]]+\s+(\w+) *\([^\)]*\)',
            # Class declarations
            r'(?:public|protected|private|static|\s) +class +(\w+)',
            # Interface declarations
            r'(?:public|protected|private|static|\s) +interface +(\w+)',
        ]
        
        methods = {}
        for i, line in enumerate(content.splitlines(), 1):
            for pattern in patterns:
                matches = re.finditer(pattern, line)
                for match in matches:
                    method_name = match.group(1)
                    if method_name not in methods:
                        methods[method_name] = []
                    methods[method_name].append(i)
        
        return methods
    
    @staticmethod
    def _basic_java_validation(content: str) -> Tuple[bool, Optional[str]]:
        """
        Perform basic Java validation by checking for matching braces, etc.
        
        Args:
            content: Java content to validate
            
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
