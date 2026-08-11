import { applyContinuationRewind } from '../continuationProtocol';

describe('applyContinuationRewind', () => {
    const cutoff = [
        '```diff',
        '+complete line',
        '+partial line ending at `',
    ].join('\n');

    it('removes the incomplete line using the explicit backend line offset', () => {
        expect(applyContinuationRewind(cutoff, { rewind_line: 2 })).toEqual({
            content: '```diff\n+complete line',
            applied: true,
        });
    });

    it('accepts zero as a valid rewind target', () => {
        expect(applyContinuationRewind(cutoff, { rewind_line: 0 })).toEqual({
            content: '',
            applied: true,
        });
    });

    it('rejects invalid or out-of-range offsets without deleting content', () => {
        for (const rewind_line of [-1, 1.5, 99, Number.NaN]) {
            expect(applyContinuationRewind(cutoff, { rewind_line })).toEqual({
                content: cutoff,
                applied: false,
            });
        }
    });

    it('preserves an existing trailing newline when no final line was partial', () => {
        const complete = '```diff\n+complete\n';
        expect(applyContinuationRewind(complete, { rewind_line: 3 })).toEqual({
            content: complete,
            applied: true,
        });
    });
});
