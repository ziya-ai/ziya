import {
    findSupersededDiffIndices,
    findSupersededDiffParts,
} from '../diffUtils';

const fileDiff = (path: string, line: number, addition: string): string => [
    `diff --git a/${path} b/${path}`,
    `--- a/${path}`,
    `+++ b/${path}`,
    `@@ -${line},2 +${line},3 @@`,
    ' class ChatSummary(BaseModel):',
    '     delegateMeta: Optional[DelegateMeta] = None',
    `+    ${addition}`,
].join('\n');

describe('findSupersededDiffIndices', () => {
    it('marks an overlapping earlier single-file revision', () => {
        const earlier = fileDiff(
            'app/models/chat.py',
            83,
            'branchedFrom: Optional[str] = None'
        );
        const later = fileDiff(
            'app/models/chat.py',
            83,
            'branchedAtMessageIndex: Optional[int] = None'
        );

        expect(findSupersededDiffIndices([earlier, later])).toEqual(
            new Set([0])
        );
    });
});

describe('findSupersededDiffParts', () => {
    it('supersedes only the revised file inside a multi-file fence', () => {
        const originalBlock = [
            fileDiff(
                'app/models/chat.py',
                83,
                'branchedFrom: Optional[str] = None'
            ),
            fileDiff(
                'app/api/chats.py',
                48,
                'branchedFrom=chat.branchedFrom,'
            ),
            fileDiff(
                'app/storage/chats.py',
                231,
                "branchedFrom=data.get('branchedFrom'),"
            ),
        ].join('\n');
        const correction = fileDiff(
            'app/models/chat.py',
            83,
            'branchedAtMessageIndex: Optional[int] = None'
        );

        const superseded = findSupersededDiffParts([
            originalBlock,
            correction,
        ]);

        expect(superseded.get(0)).toEqual(new Set([0]));
        expect(superseded.get(0)?.has(1)).toBe(false);
        expect(superseded.get(0)?.has(2)).toBe(false);
        expect(superseded.has(1)).toBe(false);
    });

    it('retains normal block-level indices for single-file fences', () => {
        const first = fileDiff('a.ts', 10, 'first = true;');
        const second = fileDiff('b.ts', 10, 'second = true;');

        expect(findSupersededDiffParts([first, second])).toEqual(new Map());
    });
});
