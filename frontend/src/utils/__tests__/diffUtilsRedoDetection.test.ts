/**
 * Supersede detection for re-anchored corrections.
 *
 * `findSupersededDiffIndices` was purely positional: an earlier diff was
 * greyed only when a later diff for the same file overlapped its original-
 * file line range by >50%.  The most common correction an LLM makes to its
 * own failed hunk is to re-emit the SAME body under a DIFFERENT `@@` header
 * (it re-anchors after a context mismatch).  Adjacent-but-disjoint ranges
 * then read as "two independent edits" and both stay live — the user is
 * offered a hunk the model itself has retracted.
 *
 * The fix judges by change content when position says "disjoint": a later
 * diff whose substantive change lines contain ≥80% of the earlier's is a
 * redo.  Small bodies additionally require positional proximity so the same
 * one-liner added at two distant call sites is not mistaken for a redo.
 *
 * The first test is the literal case that motivated this (two appends to
 * shadow_tools.py: `@@ -296,3 +296,185 @@` then `@@ -290,6 +290,186 @@`).
 */
import { findSupersededDiffIndices, changeSignature } from '../diffUtils';

const FILE = 'app/mcp/tools/shadow_tools.py';

/** Build a single-file diff with the given hunk header and body lines. */
function diff(header: string, body: string[], file = FILE): string {
    return [
        `--- a/${file}`,
        `+++ b/${file}`,
        header,
        ...body,
    ].join('\n');
}

/** A realistic ~30-line appended body: distinct, substantive lines. */
function bigBody(prefix = 'ShadowControl'): string[] {
    const lines: string[] = [];
    for (let i = 0; i < 30; i++) {
        lines.push(`+    def method_${prefix}_${i}(self, arg_${i}: str) -> Dict[str, Any]:`);
    }
    return lines;
}

describe('re-anchored correction is superseded', () => {
    it('catches the motivating case: same appended body, adjacent disjoint ranges', () => {
        const body = bigBody();
        // First attempt: anchored past EOF with too little context.
        const first = diff('@@ -296,3 +296,185 @@', [
            '             return {"ok": True, **resp}',
            '         except Exception as e:  # noqa: BLE001',
            '             return _error(e)',
            ...body,
        ]);
        // Correction: re-anchored six lines earlier, with the no-newline fixup.
        const second = diff('@@ -290,6 +290,186 @@', [
            '             from app.shadow import client',
            '             resp = client.detach(session)',
            '             return {"ok": True, **resp}',
            '         except Exception as e:  # noqa: BLE001',
            '-            return _error(e)',
            '\\ No newline at end of file',
            '+            return _error(e)',
            ...body,
        ]);
        // Sanity: the positional detector alone would call these disjoint.
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set([0]));
    });

    it('catches a large identical body re-anchored far away in the file', () => {
        const body = bigBody();
        const first = diff('@@ -40,3 +40,33 @@', ['     ctx_a', '     ctx_b', '     ctx_c', ...body]);
        const second = diff('@@ -412,3 +412,33 @@', ['     ctx_x', '     ctx_y', '     ctx_z', ...body]);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set([0]));
    });

    it('catches a correction that extends the body (superset), not just an exact repeat', () => {
        const body = bigBody();
        const first = diff('@@ -100,2 +100,32 @@', ['     a', '     b', ...body]);
        const second = diff('@@ -120,2 +120,36 @@', [
            '     c', '     d', ...body,
            '+    def extra_helper_one(self) -> None:',
            '+    def extra_helper_two(self) -> None:',
            '+    def extra_helper_three(self) -> None:',
            '+    def extra_helper_four(self) -> None:',
        ]);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set([0]));
    });

    it('catches same body with positional overlap below the 50% threshold', () => {
        // [100,110] vs [108,130]: 3 shared lines of an 11-line hunk = 27%,
        // which the positional check alone rejects.
        const body = bigBody();
        const first = diff('@@ -100,11 +100,41 @@', [...Array(11).fill('     ctx'), ...body]);
        const second = diff('@@ -108,23 +108,53 @@', [...Array(23).fill('     ctx'), ...body]);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set([0]));
    });

    it('catches a small re-anchored one-line fix when the ranges are near', () => {
        const first = diff('@@ -50,3 +50,4 @@', ['     a', '     b', '+    self.on_control = on_control', '     c']);
        const second = diff('@@ -56,3 +56,4 @@', ['     x', '     y', '+    self.on_control = on_control', '     z']);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set([0]));
    });
});

describe('independent edits to the same file stay live', () => {
    it('keeps two different bodies at disjoint ranges', () => {
        const first = diff('@@ -10,2 +10,32 @@', ['     a', '     b', ...bigBody('Alpha')]);
        const second = diff('@@ -200,2 +200,32 @@', ['     c', '     d', ...bigBody('Beta')]);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set());
    });

    it('keeps the same small body added at two distant sites (not a redo)', () => {
        const first = diff('@@ -20,3 +20,4 @@', ['     a', '     b', '+        logger.debug("enter")', '     c']);
        const second = diff('@@ -400,3 +400,4 @@', ['     x', '     y', '+        logger.debug("enter")', '     z']);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set());
    });

    it('keeps a sequential pair (subtractive prep then additive) even with matching lines', () => {
        const first = diff('@@ -10,4 +10,1 @@', ['     keep', '-    old_line_alpha()', '-    old_line_beta()', '-    old_line_gamma()']);
        const second = diff('@@ -10,1 +10,4 @@', ['     keep', '+    new_line_alpha()', '+    new_line_beta()', '+    new_line_gamma()']);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set());
    });

    it('keeps a new-file diff followed by a small modification of that file', () => {
        const create = diff('@@ -0,0 +1,30 @@', bigBody());
        const tweak = diff('@@ -5,1 +5,1 @@', ['-    def method_ShadowControl_4(self, arg_4: str) -> Dict[str, Any]:',
                                               '+    def method_ShadowControl_4(self, arg_4: int) -> Dict[str, Any]:']);
        expect(findSupersededDiffIndices([create, tweak])).toEqual(new Set());
    });

    it('does not match on structural noise lines alone', () => {
        // Bodies that agree only on `}`, `)`, blank, `pass` must not read as a redo.
        const noise = ['+    }', '+    )', '+', '+    ]', '+    });', '+    ),'];
        const first = diff('@@ -10,1 +10,7 @@', ['     a', ...noise]);
        const second = diff('@@ -12,1 +12,7 @@', ['     b', ...noise]);
        expect(findSupersededDiffIndices([first, second])).toEqual(new Set());
    });
});

describe('changeSignature', () => {
    it('keeps sign, trims, drops headers and structural noise', () => {
        const sig = changeSignature(diff('@@ -1,2 +1,3 @@', [
            '     context_line',
            '-    removed_thing()',
            '+    added_thing()',
            '+    }',
            '+',
        ]));
        expect(sig).toEqual(new Set(['-removed_thing()', '+added_thing()']));
    });
});
