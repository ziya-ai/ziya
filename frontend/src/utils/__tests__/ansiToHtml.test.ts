import { ansiToHtml, containsAnsi, stripAnsi } from '../ansiToHtml';

describe('containsAnsi', () => {
    it('detects ANSI escape sequences', () => {
        expect(containsAnsi('\x1b[91mFAIL\x1b[0m')).toBe(true);
    });

    it('returns false for plain text', () => {
        expect(containsAnsi('hello world')).toBe(false);
    });

    it('returns false for orphaned brackets without ESC', () => {
        expect(containsAnsi('[91mFAIL[0m')).toBe(false);
    });
});

describe('ansiToHtml', () => {
    it('passes plain text through with HTML escaping', () => {
        expect(ansiToHtml('hello <world>')).toBe('hello &lt;world&gt;');
    });

    it('converts basic foreground colors', () => {
        const result = ansiToHtml('\x1b[91mFAIL\x1b[0m');
        expect(result).toContain('<span style="color:#ff3333">');
        expect(result).toContain('FAIL');
        expect(result).toContain('</span>');
        expect(result).not.toContain('\x1b');
    });

    it('converts green color', () => {
        const result = ansiToHtml('\x1b[92mPASS\x1b[0m');
        expect(result).toContain('color:#33ff33');
        expect(result).toContain('PASS');
    });

    it('handles bold', () => {
        const result = ansiToHtml('\x1b[1mBOLD\x1b[0m');
        expect(result).toContain('font-weight:bold');
    });

    it('handles combined bold + color', () => {
        const result = ansiToHtml('\x1b[1;31mERROR\x1b[0m');
        expect(result).toContain('font-weight:bold');
        expect(result).toContain('color:#cc0000');
    });

    it('preserves text between ANSI sequences', () => {
        const result = ansiToHtml('before \x1b[91mred\x1b[0m after');
        expect(result).toBe('before <span style="color:#ff3333">red</span> after');
    });

    it('handles reset correctly', () => {
        const result = ansiToHtml('\x1b[91mred\x1b[0m plain');
        expect(result).toBe('<span style="color:#ff3333">red</span> plain');
    });

    it('handles 256-color foreground', () => {
        const result = ansiToHtml('\x1b[38;5;196mtext\x1b[0m');
        expect(result).toContain('<span style="color:');
        expect(result).toContain('text');
    });

    it('handles RGB truecolor foreground', () => {
        const result = ansiToHtml('\x1b[38;2;255;128;0mtext\x1b[0m');
        expect(result).toContain('color:#ff8000');
    });

    it('handles background colors', () => {
        const result = ansiToHtml('\x1b[41mtext\x1b[0m');
        expect(result).toContain('background-color:#cc0000');
    });

    it('strips non-SGR CSI sequences', () => {
        // Cursor movement (H = cursor position) should be stripped
        const result = ansiToHtml('hello\x1b[2Jworld');
        expect(result).toBe('helloworld');
    });

    it('handles the exact test output from the bug report', () => {
        const input = '\x1b[91mtest_conversation_israwmode_false_applied\x1b[0m';
        const result = ansiToHtml(input);
        expect(result).toContain('color:#ff3333');
        expect(result).toContain('test_conversation_israwmode_false_applied');
        expect(result).not.toContain('[91m');
        expect(result).not.toContain('[0m');
    });

    it('escapes HTML entities in content', () => {
        const result = ansiToHtml('\x1b[91m<script>alert(1)</script>\x1b[0m');
        expect(result).toContain('&lt;script&gt;');
        expect(result).not.toContain('<script>');
    });
});

describe('stripAnsi', () => {
    it('removes all ANSI sequences leaving plain text', () => {
        expect(stripAnsi('\x1b[91mFAIL\x1b[0m')).toBe('FAIL');
    });

    it('returns plain text unchanged', () => {
        expect(stripAnsi('hello world')).toBe('hello world');
    });
});
