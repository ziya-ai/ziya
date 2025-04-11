"""
Rust-specific language handler.
"""

import re
import subprocess
from typing import List, Tuple, Optional, Dict

from app.utils.logging_utils import logger
from .base import LanguageHandler


class RustHandler(LanguageHandler):
    """Handler for Rust files."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if this is a Rust file, False otherwise
        """
        return file_path.endswith('.rs')
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for Rust.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Try to use rustc to validate syntax if available
        try:
            # Create a temporary file with the modified content
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(suffix='.rs', delete=False) as temp:
                temp.write(modified_content.encode('utf-8'))
                temp_path = temp.name
            
            try:
                # Use rustc to check syntax
                result = subprocess.run(
                    ['rustc', '--emit=metadata', '-o', '/dev/null', temp_path],
                    capture_output=True,
                    text=True,
                    timeout=5  # 5 second timeout
                )
                
                if result.returncode != 0:
                    error_msg = result.stderr.strip()
                    logger.error(f"Rust syntax validation failed for {file_path}: {error_msg}")
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
            # If rustc is not available or fails, fall back to basic validation
            logger.warning(f"Falling back to basic Rust validation: {str(e)}")
            
            # Basic validation: check for matching braces, parentheses, etc.
            is_valid, error = cls._basic_rust_validation(modified_content)
            if not is_valid:
                logger.error(f"Basic Rust validation failed for {file_path}: {error}")
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
        
        # Check for inconsistent naming conventions
        if cls._has_inconsistent_naming(modified_content):
            issues.append("Inconsistent naming conventions detected")
        
        # Check for missing lifetime specifiers
        if cls._has_missing_lifetimes(modified_content):
            issues.append("Potential missing lifetime specifiers")
        
        # Check for unsafe blocks without comments
        if cls._has_uncommented_unsafe(modified_content):
            issues.append("Unsafe blocks without explanatory comments")
        
        # Check for unused imports
        unused_imports = cls._find_unused_imports(original_content, modified_content)
        if unused_imports:
            issues.append(f"Potentially unused imports: {', '.join(unused_imports)}")
        
        return issues
    
    @classmethod
    def _has_inconsistent_naming(cls, content: str) -> bool:
        """Check for inconsistent naming conventions."""
        # Check for mixed snake_case and camelCase in functions
        snake_case_funcs = len(re.findall(r'fn\s+[a-z][a-z0-9_]*_[a-z0-9_]*\s*\(', content))
        camel_case_funcs = len(re.findall(r'fn\s+[a-z][a-z0-9_]*[A-Z][a-zA-Z0-9_]*\s*\(', content))
        
        # If both styles are used, it's inconsistent
        if snake_case_funcs > 0 and camel_case_funcs > 0:
            return True
        
        # Check for mixed naming in structs/enums
        snake_case_types = len(re.findall(r'(?:struct|enum)\s+[a-z][a-z0-9_]*_[a-z0-9_]*', content))
        pascal_case_types = len(re.findall(r'(?:struct|enum)\s+[A-Z][a-zA-Z0-9_]*', content))
        
        # In Rust, types should use PascalCase
        if snake_case_types > 0:
            return True
        
        return False
    
    @classmethod
    def _has_missing_lifetimes(cls, content: str) -> bool:
        """Check for potential missing lifetime specifiers."""
        # Look for references in struct definitions without lifetimes
        struct_refs_no_lifetime = re.search(r'struct\s+\w+\s*{[^}]*&\s*(?!\')\w+', content)
        if struct_refs_no_lifetime:
            return True
        
        # Look for references in function signatures without lifetimes
        fn_refs_no_lifetime = re.search(r'fn\s+\w+\s*\([^)]*&\s*(?!\')\w+', content)
        if fn_refs_no_lifetime:
            return True
        
        return False
    
    @classmethod
    def _has_uncommented_unsafe(cls, content: str) -> bool:
        """Check for unsafe blocks without explanatory comments."""
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if 'unsafe' in line and '{' in line:
                # Check if there's a comment on this line or the previous line
                has_comment = '//' in line
                if i > 0:
                    has_comment = has_comment or '//' in lines[i-1]
                
                if not has_comment:
                    return True
        
        return False
    
    @classmethod
    def _find_unused_imports(cls, original_content: str, modified_content: str) -> List[str]:
        """Find potentially unused imports in the modified content."""
        unused_imports = []
        
        # Extract imports
        import_pattern = r'use\s+([^;]+);'
        imports = re.findall(import_pattern, modified_content)
        
        for imp in imports:
            # Get the last part of the import path
            parts = imp.split('::')
            last_part = parts[-1].strip()
            
            # Skip wildcard imports
            if last_part == '*':
                continue
            
            # Check if it's a bracketed import list
            if '{' in last_part and '}' in last_part:
                # Extract individual items
                items = re.findall(r'([a-zA-Z0-9_]+)', last_part)
                for item in items:
                    # Check if the item is used in the code (excluding the import line)
                    content_without_imports = re.sub(import_pattern, '', modified_content)
                    if not re.search(r'\b' + re.escape(item) + r'\b', content_without_imports):
                        unused_imports.append(item)
            else:
                # Check if the import is used in the code (excluding the import line)
                content_without_imports = re.sub(import_pattern, '', modified_content)
                if not re.search(r'\b' + re.escape(last_part) + r'\b', content_without_imports):
                    unused_imports.append(last_part)
        
        return unused_imports
    
    @classmethod
    def _basic_rust_validation(cls, content: str) -> Tuple[bool, Optional[str]]:
        """
        Perform basic Rust validation by checking for matching braces, etc.
        
        Args:
            content: Rust content to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check for balanced delimiters
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
        
        # Check for common Rust syntax errors
        issues = []
        
        # Missing semicolons
        lines = content.splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            # Skip comments, empty lines, and lines that don't need semicolons
            if (not line or line.startswith('//') or line.startswith('/*') or
                line.endswith(';') or line.endswith('{') or line.endswith('}') or
                line.endswith(':') or line.startswith('#') or
                'fn ' in line or 'struct ' in line or 'enum ' in line or
                'impl ' in line or 'trait ' in line or 'mod ' in line):
                continue
            
            # Check if the next line starts with a character that suggests a missing semicolon
            if i < len(lines) - 1:
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith(('.', '+', '-', '*', '/', '&&', '||', '?')):
                    issues.append(f"Possible missing semicolon at line {i+1}")
        
        # Check for common macro syntax errors
        for i, line in enumerate(lines):
            if '!' in line and '(' in line and ')' not in line:
                # Check if the closing parenthesis is on a subsequent line
                found_closing = False
                for j in range(i+1, min(i+5, len(lines))):
                    if ')' in lines[j]:
                        found_closing = True
                        break
                
                if not found_closing:
                    issues.append(f"Possible unclosed macro at line {i+1}")
        
        if issues:
            return False, "; ".join(issues[:3])  # Return first few issues
        
        return True, None
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated functions/structs/enums in Rust code.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        # Extract definitions from both contents
        original_definitions = cls._extract_rust_definitions(original_content)
        modified_definitions = cls._extract_rust_definitions(modified_content)
        
        # Check for duplicates
        duplicates = []
        for def_type, def_dict in modified_definitions.items():
            for def_name, occurrences in def_dict.items():
                if len(occurrences) > 1:
                    # Check if it was already duplicated in the original
                    original_count = 0
                    if def_type in original_definitions and def_name in original_definitions[def_type]:
                        original_count = len(original_definitions[def_type][def_name])
                    
                    if len(occurrences) > original_count:
                        # Get line numbers for better reporting
                        line_numbers = ", ".join(str(line) for line in occurrences)
                        duplicates.append(f"{def_type} {def_name} (lines {line_numbers})")
                        logger.warning(f"{def_type} '{def_name}' appears to be duplicated after diff application at lines {line_numbers}")
        
        # Check for similar function implementations
        similar_functions = cls._detect_similar_functions(modified_content)
        for func_pair, similarity in similar_functions:
            if similarity > 0.9:  # High similarity threshold
                duplicates.append(f"Similar functions: {func_pair[0]} and {func_pair[1]} ({similarity:.2f} similarity)")
                logger.warning(f"Functions '{func_pair[0]}' and '{func_pair[1]}' appear to be very similar ({similarity:.2f} similarity)")
        
        return bool(duplicates), duplicates
    
    @classmethod
    def _extract_rust_definitions(cls, content: str) -> Dict[str, Dict[str, List[int]]]:
        """
        Extract Rust definitions (functions, structs, enums, etc.).
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping definition types to dictionaries of definition names and their line numbers
        """
        definitions = {
            'fn': {},
            'struct': {},
            'enum': {},
            'trait': {},
            'impl': {},
            'mod': {},
        }
        
        # Regex patterns for Rust definitions
        patterns = {
            'fn': r'fn\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:<[^>]*>)?\s*\(',
            'struct': r'struct\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            'enum': r'enum\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            'trait': r'trait\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            'impl': r'impl(?:\s+<[^>]*>)?\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            'mod': r'mod\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        }
        
        for i, line in enumerate(content.splitlines(), 1):
            for def_type, pattern in patterns.items():
                matches = re.finditer(pattern, line)
                for match in matches:
                    def_name = match.group(1)
                    if def_name not in definitions[def_type]:
                        definitions[def_type][def_name] = []
                    definitions[def_type][def_name].append(i)
        
        return definitions
    
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
        function_bodies = {}
        
        # Find function declarations and their bodies
        fn_pattern = r'fn\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:<[^>]*>)?\s*\([^)]*\)(?:\s*->\s*[^{]*)?(?:\s*where\s*[^{]*)?\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}'
        for match in re.finditer(fn_pattern, content, re.DOTALL):
            func_name = match.group(1)
            body = match.group(2).strip()
            function_bodies[func_name] = body
        
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
