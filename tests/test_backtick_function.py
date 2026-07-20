#!/usr/bin/env python3
"""
Regression: unescape_backticks_from_llm must preserve escaped backticks
that are genuine template-literal content.

The chatApi.ts case: a diff edits a template literal whose content is
literal backtick fences (escaped as backslash-backtick in the source).
Unescaping those would turn ``\\`\\`\\`${...}`` into ```` ```${...} ````,
producing quadruple backticks and a JavaScript syntax error.

Without file content, the multiple-consecutive-escape heuristic must
preserve; with file content supplied (the file genuinely containing the
escaped form), the file-grounded decision must also preserve.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.diff_utils.parsing.diff_parser import unescape_backticks_from_llm

_BT = chr(96)            # `
_ESC = chr(92) + _BT     # \`

_DIFF = '''diff --git a/frontend/src/apis/chatApi.ts b/frontend/src/apis/chatApi.ts
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


def test_template_literal_escapes_preserved_without_file():
    """Heuristic path (no file content): consecutive escaped backticks
    indicate genuine template-literal content and must be preserved."""
    result = unescape_backticks_from_llm(_DIFF)
    assert '````${' not in result, (
        "Function created quadruple backticks — this corrupts the "
        "template literal into a JavaScript syntax error.")
    assert (_ESC * 3) + '${' in result.replace(_ESC * 4, _ESC * 3), \
        "Escaped backticks should be preserved in template-literal content"
    # The whole diff must round-trip unchanged.
    assert result == _DIFF


def test_template_literal_escapes_preserved_with_file():
    """File-grounded path: the file genuinely contains the escaped form,
    so the removal line matches the file verbatim -> preserve."""
    file_content = (
        "export const sendPayload = async (\n"
        "    payload: any\n"
        ") => {\n"
        "            const resultContent = isCode\n"
        "                ? `" + _ESC * 4 + "${result.language}\\n${result.content}\\n" + _ESC * 4 + "`\n"
        "                : result.content;\n"
        "};\n"
    )
    result = unescape_backticks_from_llm(_DIFF, file_content=file_content)
    assert result == _DIFF, (
        "File contains the escaped form verbatim; the diff must be "
        "preserved as-is, not unescaped.")
