import { parseMockupFence, fenceBaseLang, fenceModifiers } from '../mockupFence';

describe('fenceBaseLang', () => {
    it('takes the first token of the info string', () => {
        expect(fenceBaseLang('html-mockup figure')).toBe('html-mockup');
        expect(fenceBaseLang('  MOCKUP   Inline ')).toBe('mockup');
    });

    it('handles a bare language and empty input', () => {
        expect(fenceBaseLang('mermaid')).toBe('mermaid');
        expect(fenceBaseLang('')).toBe('');
        expect(fenceBaseLang(undefined)).toBe('');
        expect(fenceBaseLang(null)).toBe('');
    });
});

describe('fenceModifiers', () => {
    it('returns the tokens after the language', () => {
        expect(fenceModifiers('html-mockup figure')).toEqual(['figure']);
        expect(fenceModifiers('html-mockup Figure Extra')).toEqual(['figure', 'extra']);
    });

    it('returns nothing for a bare language', () => {
        expect(fenceModifiers('html-mockup')).toEqual([]);
        expect(fenceModifiers(undefined)).toEqual([]);
    });
});

describe('parseMockupFence', () => {
    it('recognises every mockup language alias', () => {
        for (const lang of ['html-mockup', 'ui-mockup', 'mockup']) {
            expect(parseMockupFence(lang)).toEqual({ isMockup: true, variant: 'mockup' });
        }
    });

    it('defaults to the framed mockup variant', () => {
        expect(parseMockupFence('html-mockup').variant).toBe('mockup');
    });

    it('selects the figure variant from any accepted modifier', () => {
        for (const mod of ['figure', 'inline', 'bare', 'nochrome', 'no-chrome', 'plain']) {
            expect(parseMockupFence(`html-mockup ${mod}`)).toEqual({
                isMockup: true, variant: 'figure',
            });
        }
    });

    it('is case-insensitive on both language and modifier', () => {
        expect(parseMockupFence('HTML-Mockup FIGURE')).toEqual({
            isMockup: true, variant: 'figure',
        });
    });

    it('tolerates extra whitespace between tokens', () => {
        expect(parseMockupFence('  html-mockup    figure  ')).toEqual({
            isMockup: true, variant: 'figure',
        });
    });

    it('ignores an unrecognised modifier rather than dropping the block', () => {
        // The block must still render; only the variant falls back.
        expect(parseMockupFence('html-mockup wat')).toEqual({
            isMockup: true, variant: 'mockup',
        });
    });

    it('finds a figure modifier past an unrecognised one', () => {
        expect(parseMockupFence('html-mockup wat figure').variant).toBe('figure');
    });

    it('rejects non-mockup languages', () => {
        for (const lang of ['mermaid', 'python', 'diff', '', undefined]) {
            expect(parseMockupFence(lang).isMockup).toBe(false);
        }
    });

    it('does not treat a language that merely contains "mockup" as one', () => {
        expect(parseMockupFence('mockups').isMockup).toBe(false);
        expect(parseMockupFence('my-mockup').isMockup).toBe(false);
    });

    it('reports variant "mockup" for a non-mockup language', () => {
        // Callers gate on isMockup; a meaningful variant here would invite
        // reading it without that check.
        expect(parseMockupFence('mermaid figure').variant).toBe('mockup');
    });
});
