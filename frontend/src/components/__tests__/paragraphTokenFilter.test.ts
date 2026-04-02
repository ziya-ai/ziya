/**
 * Tests for the paragraph token filtering logic in renderTokens.
 *
 * When rendering a 'paragraph' token, renderTokens filters out genuinely
 * empty text tokens (text === "") while preserving whitespace-only tokens
 * like " " that serve as separators between inline elements (em, strong,
 * codespan).
 *
 * Bug reproduced:  `*overwrites* \`get_current_version\`` lost the space
 * between the italic and inline-code because the filter used .trim(),
 * which collapses " " to "" and drops the separator token.
 *
 * The filter logic is inlined in MarkdownRenderer.tsx's paragraph case.
 * We replicate it here for isolated unit testing.
 */

interface MockToken {
    type: string;
    text: string;
    raw?: string;
    tokens?: MockToken[];
    escaped?: boolean;
}

/**
 * Replicate the production paragraph-token filter.
 * Must stay in sync with MarkdownRenderer.tsx paragraph case.
 */
function filterParagraphTokens(pTokens: MockToken[]): MockToken[] {
    return pTokens.filter(
        t => t.type !== 'text' || t.text !== '' || t.text === '\n'
    );
}

/**
 * The OLD (buggy) filter that used .trim() — kept here so we can
 * verify the regression is real.
 */
function filterParagraphTokensBuggy(pTokens: MockToken[]): MockToken[] {
    return pTokens.filter(
        t => t.type !== 'text' || t.text.trim() !== '' || t.text === '\n'
    );
}

// --- Token fixtures mirroring what `marked.lexer` actually produces ---

/** Tokens for: `*overwrites* \`get_current_version\`` */
const emCodespanTokens: MockToken[] = [
    {
        type: 'em',
        text: 'overwrites',
        raw: '*overwrites*',
        tokens: [{ type: 'text', text: 'overwrites', raw: 'overwrites' }],
    },
    { type: 'text', text: ' ', raw: ' ', escaped: false },
    { type: 'codespan', text: 'get_current_version', raw: '`get_current_version`' },
];

/** Tokens for: `**But** there's a problem` */
const strongTextTokens: MockToken[] = [
    {
        type: 'strong',
        text: 'But',
        raw: '**But**',
        tokens: [{ type: 'text', text: 'But', raw: 'But' }],
    },
    { type: 'text', text: " there's a problem", raw: " there's a problem", escaped: false },
];

/** Tokens for a paragraph with only whitespace text tokens (should be empty) */
const onlyEmptyTextTokens: MockToken[] = [
    { type: 'text', text: '', raw: '' },
];

/** Tokens with a newline separator between inline elements */
const newlineSeparatorTokens: MockToken[] = [
    {
        type: 'strong',
        text: 'line one',
        raw: '**line one**',
        tokens: [{ type: 'text', text: 'line one', raw: 'line one' }],
    },
    { type: 'text', text: '\n', raw: '\n' },
    {
        type: 'em',
        text: 'line two',
        raw: '*line two*',
        tokens: [{ type: 'text', text: 'line two', raw: 'line two' }],
    },
];

/** Multiple spaces between inline elements (e.g. from source indentation) */
const multiSpaceTokens: MockToken[] = [
    { type: 'codespan', text: 'a', raw: '`a`' },
    { type: 'text', text: '   ', raw: '   ' },
    { type: 'codespan', text: 'b', raw: '`b`' },
];


describe('paragraph token filter', () => {
    it('preserves space between em and codespan', () => {
        const result = filterParagraphTokens(emCodespanTokens);
        expect(result).toHaveLength(3);
        expect(result[1].type).toBe('text');
        expect(result[1].text).toBe(' ');
    });

    it('old buggy filter drops space between em and codespan', () => {
        const result = filterParagraphTokensBuggy(emCodespanTokens);
        // This demonstrates the bug — the space token is lost
        expect(result).toHaveLength(2);
        expect(result.find(t => t.type === 'text' && t.text === ' ')).toBeUndefined();
    });

    it('preserves text after strong element', () => {
        const result = filterParagraphTokens(strongTextTokens);
        expect(result).toHaveLength(2);
        expect(result[1].text).toBe(" there's a problem");
    });

    it('filters out genuinely empty text tokens', () => {
        const result = filterParagraphTokens(onlyEmptyTextTokens);
        expect(result).toHaveLength(0);
    });

    it('preserves newline separator tokens', () => {
        const result = filterParagraphTokens(newlineSeparatorTokens);
        expect(result).toHaveLength(3);
        expect(result[1].text).toBe('\n');
    });

    it('preserves multi-space separator tokens', () => {
        const result = filterParagraphTokens(multiSpaceTokens);
        expect(result).toHaveLength(3);
        expect(result[1].text).toBe('   ');
    });

    it('keeps non-text tokens unconditionally', () => {
        const tokens: MockToken[] = [
            { type: 'codespan', text: 'code' },
            { type: 'strong', text: 'bold', tokens: [] },
            { type: 'em', text: 'italic', tokens: [] },
        ];
        const result = filterParagraphTokens(tokens);
        expect(result).toHaveLength(3);
    });

    it('handles mixed empty and non-empty text tokens', () => {
        const tokens: MockToken[] = [
            { type: 'text', text: '', raw: '' },         // should be filtered
            { type: 'em', text: 'a', raw: '*a*', tokens: [] },
            { type: 'text', text: ' ', raw: ' ' },       // should be kept
            { type: 'codespan', text: 'b', raw: '`b`' },
            { type: 'text', text: '', raw: '' },         // should be filtered
        ];
        const result = filterParagraphTokens(tokens);
        expect(result).toHaveLength(3); // em, text(" "), codespan
        expect(result[0].type).toBe('em');
        expect(result[1].type).toBe('text');
        expect(result[1].text).toBe(' ');
        expect(result[2].type).toBe('codespan');
    });
});
