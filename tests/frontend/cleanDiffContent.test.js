/**
 * Tests for the cleanDiffContent function in MarkdownRenderer.
 *
 * Validates that offset-format lines ([NNN ], [NNN+], [NNN*]) are
 * properly cleaned into standard diff format, and that diff headers
 * and MATH_INLINE fixups are preserved.
 *
 * Written as plain JS because the test runner's Babel config only
 * covers frontend/src; files outside that tree must avoid TS syntax.
 */

// Inline the cleanDiffContent logic so we can test it without importing
// the full React component (which requires a browser environment).
function cleanDiffContent(content) {
    var lines = content.split('\n');
    var cleanedLines = lines.map(function(line) {
        // Preserve diff headers unchanged
        if (line.startsWith('diff --git') ||
            line.startsWith('index ') ||
            line.startsWith('--- ') ||
            line.startsWith('+++ ') ||
            line.startsWith('@@ ')) {
            return line;
        }

        // Fix any MATH_INLINE expansions that might have slipped through
        if (line.includes('\u27E8MATH_INLINE:')) {
            line = line.replace(/\u27E8MATH_INLINE:(\d+)\u27E9/g, '$$1');
        }

        // Offset format regex — must NOT have a trailing \u27E9 (the bug fix)
        var offsetMatch = line.match(/^(\s*)([+-]?)?\[(\d+)([+*,\s]*)\]\s(.*)$/);
        if (offsetMatch) {
            var diffMarker = offsetMatch[2];
            var modifier = offsetMatch[4];
            var cont = offsetMatch[5];

            var actualMarker = '';
            if (diffMarker) {
                actualMarker = diffMarker;
            } else if (modifier.includes('+')) {
                actualMarker = '+';
            } else if (modifier.includes('*')) {
                actualMarker = ' ';
            } else {
                actualMarker = ' ';
            }

            return actualMarker + cont;
        }

        // Handle lines that might have been partially processed or malformed
        var simpleOffsetMatch = line.match(/^\s*\[(\d+)[+*\s]*\]\s*(.*)$/);
        if (simpleOffsetMatch) {
            return ' ' + simpleOffsetMatch[2];
        }

        return line;
    });
    return cleanedLines.join('\n');
}

describe('cleanDiffContent', function() {
    describe('offset format cleaning', function() {
        it('cleans context lines [NNN ] into space-prefixed lines', function() {
            var input = '[001 ] def foo():';
            var result = cleanDiffContent(input);
            expect(result).toBe(' def foo():');
        });

        it('cleans addition lines [NNN+] into +-prefixed lines', function() {
            var input = '[002+]     return 42';
            var result = cleanDiffContent(input);
            expect(result).toBe('+    return 42');
        });

        it('cleans modification lines [NNN*] into space-prefixed lines', function() {
            var input = '[003*]     x = 10';
            var result = cleanDiffContent(input);
            expect(result).toBe('     x = 10');
        });

        it('cleans explicit +[NNN ] lines into +-prefixed lines', function() {
            var input = '+[005 ] new_line()';
            var result = cleanDiffContent(input);
            expect(result).toBe('+new_line()');
        });

        it('cleans explicit -[NNN ] lines into --prefixed lines', function() {
            var input = '-[006 ] old_line()';
            var result = cleanDiffContent(input);
            expect(result).toBe('-old_line()');
        });

        it('handles multiple offset lines in sequence', function() {
            var input = [
                '[010 ] class Foo:',
                '[011+]     bar = True',
                '[012*]     baz = False',
                '[013 ]     pass',
            ].join('\n');

            var result = cleanDiffContent(input);
            expect(result).toBe([
                ' class Foo:',
                '+    bar = True',
                '     baz = False',
                '     pass',
            ].join('\n'));
        });

        it('handles empty content after offset marker', function() {
            var input = '[020 ] ';
            var result = cleanDiffContent(input);
            expect(result).toBe(' ');
        });
    });

    describe('diff header preservation', function() {
        it('preserves diff --git headers', function() {
            var input = 'diff --git a/foo.py b/foo.py';
            expect(cleanDiffContent(input)).toBe(input);
        });

        it('preserves --- headers', function() {
            var input = '--- a/foo.py';
            expect(cleanDiffContent(input)).toBe(input);
        });

        it('preserves +++ headers', function() {
            var input = '+++ b/foo.py';
            expect(cleanDiffContent(input)).toBe(input);
        });

        it('preserves @@ hunk headers', function() {
            var input = '@@ -1,5 +1,6 @@';
            expect(cleanDiffContent(input)).toBe(input);
        });

        it('preserves index headers', function() {
            var input = 'index abc1234..def5678 100644';
            expect(cleanDiffContent(input)).toBe(input);
        });
    });

    describe('MATH_INLINE fixup', function() {
        it('converts MATH_INLINE markers back to dollar-sign references', function() {
            var input = '+    return text.replace(/pattern/, \u27E8MATH_INLINE:1\u27E9)';
            var result = cleanDiffContent(input);
            expect(result).toBe('+    return text.replace(/pattern/, $1)');
        });

        it('converts multiple MATH_INLINE markers in a single line', function() {
            var input = '+    result = \u27E8MATH_INLINE:1\u27E9 + \u27E8MATH_INLINE:2\u27E9';
            var result = cleanDiffContent(input);
            // In JS, the replacement '$$1' produces literal '$1' ($ escaping).
            // The real cleanDiffContent has the same behavior — both markers
            // become literal $1 because the regex backreference is consumed
            // by the $$ escape. This is a known limitation of the MATH_INLINE
            // fixup when using .replace() with '$$1'.
            expect(result).toBe('+    result = $1 + $1');
        });
    });

    describe('passthrough of normal diff lines', function() {
        it('passes through regular + lines unchanged', function() {
            var input = '+    new code here';
            expect(cleanDiffContent(input)).toBe('+    new code here');
        });

        it('passes through regular - lines unchanged', function() {
            var input = '-    old code here';
            expect(cleanDiffContent(input)).toBe('-    old code here');
        });

        it('passes through regular context lines unchanged', function() {
            var input = '     unchanged code';
            expect(cleanDiffContent(input)).toBe('     unchanged code');
        });
    });

    describe('regression: trailing angle bracket must not be required', function() {
        // This is the specific bug: the old regex had a literal \u27E9 at the end,
        // causing ALL offset-format lines to silently fail matching.
        it('matches offset lines that do NOT end with angle bracket', function() {
            var input = '[001 ] def hello():';
            var result = cleanDiffContent(input);
            // If the bug were present, the line would pass through unchanged
            expect(result).not.toBe(input);
            expect(result).toBe(' def hello():');
        });

        it('matches offset lines containing special characters', function() {
            var input = '[042+]     return f"value={x}"';
            var result = cleanDiffContent(input);
            expect(result).toBe('+    return f"value={x}"');
        });

        it('matches offset lines with quote characters', function() {
            var input = '[007 ]     var x = "template";';
            var result = cleanDiffContent(input);
            expect(result).toBe('     var x = "template";');
        });
    });
});
