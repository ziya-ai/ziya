"""
TypeScript-specific language handler.
"""

import re
import subprocess
from typing import List, Tuple, Optional, Dict

from app.utils.logging_utils import logger
from .base import LanguageHandler
from .javascript import JavaScriptHandler, JsonContentHandler


class TypeScriptHandler(LanguageHandler):
    """Handler for TypeScript files."""
    
    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        """
        Determine if this handler can process the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if this is a TypeScript file, False otherwise
        """
        return file_path.endswith(('.ts', '.tsx'))
    
    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that changes are valid for TypeScript.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            file_path: Path to the file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Special handling for JSON content with escape sequences
        if JsonContentHandler.contains_json_content(original_content) or JsonContentHandler.contains_json_content(modified_content):
            logger.debug(f"TypeScript file contains JSON content, applying special handling")
            modified_content = JsonContentHandler.preserve_json_structure(original_content, modified_content)
        
        # Try to use TypeScript compiler to validate syntax if available
        try:
            # Create a temporary file with the modified content
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(suffix='.ts', delete=False) as temp:
                temp.write(modified_content.encode('utf-8'))
                temp_path = temp.name
            
            try:
                # Use tsc to check syntax
                result = subprocess.run(
                    ['tsc', '--noEmit', temp_path],
                    capture_output=True,
                    text=True,
                    timeout=5  # 5 second timeout
                )
                
                if result.returncode != 0:
                    error_msg = result.stderr.strip()
                    logger.error(f"TypeScript syntax validation failed for {file_path}: {error_msg}")
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
            # If tsc is not available or fails, fall back to basic validation
            logger.warning(f"Falling back to basic TypeScript validation: {str(e)}")
            
            # Use JavaScript handler's basic validation as a fallback
            is_valid, error = JavaScriptHandler._basic_js_validation(modified_content)
            if not is_valid:
                logger.error(f"Basic TypeScript validation failed for {file_path}: {error}")
                return False, error
            
            # TypeScript-specific checks
            ts_issues = cls._check_typescript_specific_issues(modified_content)
            if ts_issues:
                return False, f"TypeScript issues: {'; '.join(ts_issues)}"
            
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
        # Start with JavaScript common issues
        issues = JavaScriptHandler._check_common_issues(original_content, modified_content)
        
        # Add TypeScript-specific checks
        ts_issues = cls._check_typescript_specific_issues(modified_content)
        issues.extend(ts_issues)
        
        return issues
    
    @classmethod
    def _check_typescript_specific_issues(cls, content: str) -> List[str]:
        """
        Check for TypeScript-specific issues.
        
        Args:
            content: Source code content
            
        Returns:
            List of issue descriptions
        """
        issues = []
        
        # Check for type annotations that might be incorrect
        # Look for type annotations with mismatched brackets
        type_annotations = re.finditer(r':\s*([A-Za-z0-9_<>[\]{}|&]+)', content)
        for match in type_annotations:
            type_str = match.group(1)
            # Check for balanced angle brackets in generics
            angle_count = 0
            for char in type_str:
                if char == '<':
                    angle_count += 1
                elif char == '>':
                    angle_count -= 1
                    if angle_count < 0:
                        line_num = content[:match.start()].count('\n') + 1
                        issues.append(f"Potentially mismatched angle brackets in type at line {line_num}")
                        break
            
            if angle_count != 0:
                line_num = content[:match.start()].count('\n') + 1
                issues.append(f"Unbalanced angle brackets in type at line {line_num}")
        
        # Check for 'any' type usage (often discouraged)
        any_types = re.finditer(r':\s*any\b', content)
        for match in any_types:
            line_num = content[:match.start()].count('\n') + 1
            issues.append(f"Use of 'any' type at line {line_num} (consider using a more specific type)")
        
        # Check for interface/type with no properties
        empty_interfaces = re.finditer(r'(interface|type)\s+([A-Za-z0-9_]+)\s*{(\s*)}', content)
        for match in empty_interfaces:
            line_num = content[:match.start()].count('\n') + 1
            issues.append(f"Empty {match.group(1)} '{match.group(2)}' at line {line_num}")
        
        return issues
        
    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """
        Detect duplicated functions/classes/interfaces in TypeScript code.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            Tuple of (has_duplicates, duplicate_identifiers)
        """
        # Start with JavaScript duplicate detection for functions
        js_has_duplicates, js_duplicates = JavaScriptHandler.detect_duplicates(original_content, modified_content)
        
        # Add TypeScript-specific duplicate detection
        ts_duplicates = cls._detect_typescript_duplicates(original_content, modified_content)
        
        # Filter out false positives for common language keywords
        filtered_duplicates = []
        reserved_keywords = {
            'if', 'for', 'while', 'switch', 'catch', 'with', 'return',
            'else', 'try', 'finally', 'do', 'in', 'of', 'new', 'typeof',
            'instanceof', 'void', 'delete', 'throw', 'yield', 'await'
        }
        
        for duplicate in js_duplicates:
            # Check if this is a false positive for a language keyword
            is_keyword = False
            for keyword in reserved_keywords:
                if keyword in duplicate and f"{keyword} (" in duplicate:
                    is_keyword = True
                    break
            
            if not is_keyword:
                filtered_duplicates.append(duplicate)
        
        all_duplicates = filtered_duplicates + ts_duplicates
        return bool(all_duplicates), all_duplicates
    
    @classmethod
    def _detect_typescript_duplicates(cls, original_content: str, modified_content: str) -> List[str]:
        """
        Detect TypeScript-specific duplicates like interfaces and types.
        
        Args:
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            List of duplicate identifiers
        """
        # Extract interfaces and types from both contents
        original_definitions = cls._extract_ts_definitions(original_content)
        modified_definitions = cls._extract_ts_definitions(modified_content)
        
        # Check for duplicates
        duplicates = []
        for def_name, occurrences in modified_definitions.items():
            if len(occurrences) > 1:
                # Check if it was already duplicated in the original
                original_count = len(original_definitions.get(def_name, []))
                if len(occurrences) > original_count:
                    # Get line numbers for better reporting
                    line_numbers = ", ".join(str(line) for line in occurrences)
                    duplicates.append(f"{def_name} (lines {line_numbers})")
                    logger.warning(f"Definition '{def_name}' appears to be duplicated after diff application at lines {line_numbers}")
        
        # Check for similar interface/type implementations
        similar_definitions = cls._detect_similar_definitions(modified_content)
        for def_pair, similarity in similar_definitions:
            if similarity > 0.9:  # High similarity threshold
                duplicates.append(f"Similar definitions: {def_pair[0]} and {def_pair[1]} ({similarity:.2f} similarity)")
                logger.warning(f"Definitions '{def_pair[0]}' and '{def_pair[1]}' appear to be very similar ({similarity:.2f} similarity)")
        
        return duplicates
    
    @classmethod
    def _extract_ts_definitions(cls, content: str) -> Dict[str, List[int]]:
        """
        Extract TypeScript-specific definitions (interfaces, types).
        
        Args:
            content: Source code content
            
        Returns:
            Dictionary mapping definition names to lists of line numbers where they appear
        """
        definitions = {}
        
        # Regex patterns for TypeScript definitions
        patterns = [
            # Interface declarations
            r'interface\s+([a-zA-Z_$][a-zA-Z0-9_$]*)',
            # Type aliases
            r'type\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=',
            # Enum declarations
            r'enum\s+([a-zA-Z_$][a-zA-Z0-9_$]*)',
            # Class declarations with decorators
            r'@\w+(?:\(.*\))?\s*class\s+([a-zA-Z_$][a-zA-Z0-9_$]*)',
        ]
        
        # Keywords that should be excluded from definition detection
        reserved_keywords = {
            'if', 'for', 'while', 'switch', 'catch', 'with', 'return',
            'else', 'try', 'finally', 'do', 'in', 'of', 'new', 'typeof',
            'instanceof', 'void', 'delete', 'throw', 'yield', 'await'
        }
        
        for i, line in enumerate(content.splitlines(), 1):
            for pattern in patterns:
                matches = re.finditer(pattern, line)
                for match in matches:
                    def_name = match.group(1)
                    # Skip if the name is a reserved keyword
                    if def_name in reserved_keywords:
                        continue
                    if def_name not in definitions:
                        definitions[def_name] = []
                    definitions[def_name].append(i)
        
        return definitions
    
    @classmethod
    def _detect_similar_definitions(cls, content: str) -> List[Tuple[Tuple[str, str], float]]:
        """
        Detect interfaces or types with similar structures.
        
        Args:
            content: Source code content
            
        Returns:
            List of tuples containing pairs of definition names and their similarity score
        """
        import difflib
        
        # Extract interface and type bodies
        definition_bodies = {}
        
        # Process interfaces
        interface_pattern = r'interface\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*{([^}]*)}'
        for match in re.finditer(interface_pattern, content, re.DOTALL):
            name = match.group(1)
            body = match.group(2).strip()
            definition_bodies[f"interface:{name}"] = body
        
        # Process type aliases
        type_pattern = r'type\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=\s*{([^}]*)}'
        for match in re.finditer(type_pattern, content, re.DOTALL):
            name = match.group(1)
            body = match.group(2).strip()
            definition_bodies[f"type:{name}"] = body
        
        # Compare definition bodies for similarity
        similar_definitions = []
        definition_names = list(definition_bodies.keys())
        
        for i in range(len(definition_names)):
            for j in range(i+1, len(definition_names)):
                name1 = definition_names[i]
                name2 = definition_names[j]
                
                body1 = definition_bodies[name1]
                body2 = definition_bodies[name2]
                
                # Skip empty or very short definitions
                if len(body1) < 10 or len(body2) < 10:
                    continue
                
                # Calculate similarity
                similarity = difflib.SequenceMatcher(None, body1, body2).ratio()
                
                # Only include pairs with significant similarity
                if similarity > 0.8:
                    # Extract just the name part without the prefix
                    clean_name1 = name1.split(':', 1)[1]
                    clean_name2 = name2.split(':', 1)[1]
                    similar_definitions.append(((clean_name1, clean_name2), similarity))
        
        return similar_definitions
