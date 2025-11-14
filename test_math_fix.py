#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.utils.diff_utils.application.patch_apply import apply_diff_with_pipeline

# The math_logging diff
diff_content = """--- a/app/components/MarkdownRenderer.tsx
+++ b/app/components/MarkdownRenderer.tsx
@@ -4776,9 +4776,14 @@
             backgroundColor: isDarkMode ? '#1a1a1a' : '#f8f9fa'
         }}>
             <div
+                role="log"
+                aria-live="polite"
+                aria-atomic="false"
+                aria-relevant="additions"
+                aria-label="Math rendering status updates"
                 style={{
                     padding: '8px 12px',
                     fontSize: '13px',
                     color: isDarkMode ? '#888' : '#666',
                     fontFamily: 'monospace'
                 }}
@@ -4788,9 +4793,14 @@
                 {mathLog}
             </div>
             <Button
+                aria-label="Clear math rendering log"
+                aria-describedby="math-log-content"
+                aria-controls="math-log-content"
+                title="Clear all math rendering status messages"
+                tabIndex={0}
                 size="small"
                 onClick={() => setMathLog('')}
                 style={{ marginTop: '8px' }}
             >
                 Clear Log
             </Button>
"""

# Read original file
with open('app/components/MarkdownRenderer.tsx', 'r') as f:
    original_content = f.read()

original_lines = len(original_content.splitlines())
print(f"Original file: {original_lines} lines")

# Apply diff
result = apply_diff_with_pipeline(
    file_path='app/components/MarkdownRenderer.tsx',
    diff_content=diff_content,
    original_content=original_content
)

if result.is_success:
    modified_lines = len(result.modified_content.splitlines())
    print(f"Result: {modified_lines} lines (expected 5073)")
    
    if modified_lines == 5073:
        print("✓ PASS: Correct line count")
        sys.exit(0)
    else:
        print(f"✗ FAIL: Expected 5073, got {modified_lines} (diff: {modified_lines - 5073})")
        sys.exit(1)
else:
    print(f"✗ FAIL: Diff application failed: {result.error}")
    sys.exit(1)
