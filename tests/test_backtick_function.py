#!/usr/bin/env python3

import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from app.utils.diff_utils.parsing.diff_parser import unescape_backticks_from_llm

def test_backtick_escaping():
    """Test the unescape_backticks_from_llm function with the problematic case"""
    
    # This is the diff content that's causing the issue
    diff_content = '''diff --git a/frontend/src/apis/chatApi.ts b/frontend/src/apis/chatApi.ts
index 1234567..89abcdef 100644
--- a/frontend/src/apis/chatApi.ts
+++ b/frontend/src/apis/chatApi.ts
@@ -8,7 +8,7 @@ export const sendPayload = async (
             // Only use code fence for actual code content (not text/markdown)
             const isCode = result.language && result.language !== 'text' && result.language !== 'markdown';
             const resultContent = isCode
-                ? `\\`\\`\\`\\`${result.language}\\n${result.content}\\n\\`\\`\\`\\``
+                ? `\\`\\`\\`${result.language}\\n${result.content}\\n\\`\\`\\``
                 : result.content;

             // Clean formatting with title and indented content'''
    
    print("Original diff content:")
    print(repr(diff_content))
    print("\nOriginal diff content (readable):")
    print(diff_content)
    
    # Process through the function
    result = unescape_backticks_from_llm(diff_content)
    
    print("\nAfter unescape_backticks_from_llm:")
    print(repr(result))
    print("\nAfter unescape_backticks_from_llm (readable):")
    print(result)
    
    # Check if the result contains the problematic quadruple backticks
    if '````${' in result:
        print("\n❌ ISSUE DETECTED: Function created quadruple backticks!")
        print("This will cause JavaScript syntax errors.")
        return False
    elif '\\`\\`\\`${' in result:
        print("\n✅ CORRECT: Escaped backticks preserved in template literal")
        return True
    else:
        print("\n⚠️  UNEXPECTED: Neither quadruple backticks nor escaped backticks found")
        return False

if __name__ == '__main__':
    success = test_backtick_escaping()
    sys.exit(0 if success else 1)
