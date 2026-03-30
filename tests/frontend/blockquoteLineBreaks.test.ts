/**
 * Tests for soft line-break preservation in blockquotes and styled paragraphs.
 *
 * When the marked tokenizer splits lines at inline markup boundaries, it emits
 * standalone "\n" text tokens between styled elements. MarkdownRenderer converts
 * these to <br/> so verse / poetry / multi-line styled content preserves its
 * line structure.
 *
 * These tests verify the token-level patterns that the renderer relies on.
 */
import { marked } from 'marked';

type AnyToken = { type: string; text?: string; tokens?: AnyToken[]; raw?: string };

/** Extract the inline tokens from the first paragraph inside a blockquote. */
function blockquoteInlineTokens(md: string): AnyToken[] {
    const tokens = marked.lexer(md) as AnyToken[];
    const bq = tokens.find(t => t.type === 'blockquote');
    if (!bq) throw new Error('No blockquote found');
    const para = bq.tokens!.find(t => t.type === 'paragraph');
    if (!para) throw new Error('No paragraph inside blockquote');
    return para.tokens!;
}

/** Extract inline tokens from the first paragraph (no blockquote). */
function paragraphInlineTokens(md: string): AnyToken[] {
    const tokens = marked.lexer(md) as AnyToken[];
    const para = tokens.find(t => t.type === 'paragraph');
    if (!para) throw new Error('No paragraph found');
    return para.tokens!;
}

/** True if the token list contains a standalone "\n" text token. */
function hasStandaloneNewline(tokens: AnyToken[]): boolean {
    return tokens.some(t => t.type === 'text' && t.text === '\n');
}

/** Count standalone "\n" text tokens. */
function countStandaloneNewlines(tokens: AnyToken[]): number {
    return tokens.filter(t => t.type === 'text' && t.text === '\n').length;
}

describe('Standalone newline text tokens in styled content', () => {

    describe('blockquotes with each line fully wrapped in inline markup', () => {
        it('should produce standalone \\n tokens between em-wrapped lines', () => {
            const tokens = blockquoteInlineTokens(
                '> *line one*\n> *line two*\n> *line three*'
            );
            expect(hasStandaloneNewline(tokens)).toBe(true);
            expect(countStandaloneNewlines(tokens)).toBe(2);
        });

        it('should produce standalone \\n tokens between strong-wrapped lines', () => {
            const tokens = blockquoteInlineTokens(
                '> **bold one**\n> **bold two**'
            );
            expect(hasStandaloneNewline(tokens)).toBe(true);
            expect(countStandaloneNewlines(tokens)).toBe(1);
        });

        it('should produce standalone \\n tokens with nested em+strong (verse)', () => {
            // The original Beck lyrics case
            const tokens = blockquoteInlineTokens(
                '> *From a plexiglass **prism***\n> *Biochemical **jism***\n> *Hits you with its **rhythm***'
            );
            expect(hasStandaloneNewline(tokens)).toBe(true);
            expect(countStandaloneNewlines(tokens)).toBe(2);
        });

        it('should produce standalone \\n tokens between code-span lines', () => {
            const tokens = blockquoteInlineTokens(
                '> `code one`\n> `code two`'
            );
            expect(hasStandaloneNewline(tokens)).toBe(true);
        });

        it('should produce standalone \\n tokens between links', () => {
            const tokens = blockquoteInlineTokens(
                '> [link one](url1)\n> [link two](url2)'
            );
            expect(hasStandaloneNewline(tokens)).toBe(true);
        });
    });

    describe('plain prose blockquotes should NOT have standalone \\n tokens', () => {
        it('should embed \\n inside a single text token for unstyled prose', () => {
            const tokens = blockquoteInlineTokens(
                '> This is a regular blockquote\n> that spans multiple lines\n> without any special formatting.'
            );
            expect(hasStandaloneNewline(tokens)).toBe(false);
            // The \n should be embedded in the text content
            expect(tokens.length).toBe(1);
            expect(tokens[0].type).toBe('text');
            expect(tokens[0].text).toContain('\n');
        });
    });

    describe('non-blockquote paragraphs with styled lines', () => {
        it('should also produce standalone \\n tokens for fully-styled lines', () => {
            const tokens = paragraphInlineTokens(
                '**bold one**\n**bold two**'
            );
            expect(hasStandaloneNewline(tokens)).toBe(true);
        });

        it('should NOT produce standalone \\n for mixed plain+styled text', () => {
            const tokens = paragraphInlineTokens(
                'plain text and *italic* end\nmore **bold** text'
            );
            // The \n is embedded in a text token like " end\nmore "
            expect(hasStandaloneNewline(tokens)).toBe(false);
        });
    });

    describe('edge cases', () => {
        it('should handle single-line blockquote with no newlines', () => {
            const tokens = blockquoteInlineTokens('> *just one line*');
            expect(hasStandaloneNewline(tokens)).toBe(false);
        });

        it('should handle empty emphasis', () => {
            // Two empty emphasis markers with newline between
            const tokens = blockquoteInlineTokens('> **a**\n> **b**');
            expect(hasStandaloneNewline(tokens)).toBe(true);
        });

        it('should handle mix of styled and blank lines in blockquote', () => {
            // Blank line between blockquote lines creates separate paragraphs
            const md = '> *line one*\n>\n> *line two*';
            const bqTokens = (marked.lexer(md) as AnyToken[]).find(t => t.type === 'blockquote')!.tokens!;
            // With blank line, marked creates two separate paragraphs
            const paragraphs = bqTokens.filter(t => t.type === 'paragraph');
            expect(paragraphs.length).toBe(2);
        });
    });
});
