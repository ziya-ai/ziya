import {
    isInlineMathContent,
    processInlineMath,
    decodeInlineMathMarker,
    MATH_INLINE_MARKER_PREFIX,
    MATH_INLINE_MARKER_SPLIT_RE,
    isInlineMathMarker,
} from '../inlineMathClassifier';

/**
 * The marker payload is base64-encoded (see inlineMathClassifier.ts), so the
 * LaTeX is no longer legible in the emitted string. Assert on the DECODED
 * payload instead of a literal marker: that keeps these tests pinned to
 * behaviour (which spans are treated as math) rather than to the encoding,
 * which is an implementation detail of marker transport.
 */
function decodedMath(segment: string): string[] {
    return processInlineMath(segment)
        .split(MATH_INLINE_MARKER_SPLIT_RE)
        .filter(part => part && isInlineMathMarker(part))
        .map(part => decodeInlineMathMarker(part) as string);
}

describe('isInlineMathContent — genuine math', () => {
    it('single variables', () => {
        expect(isInlineMathContent('x')).toBe(true);
        expect(isInlineMathContent('A')).toBe(true);
        expect(isInlineMathContent('c')).toBe(true);
    });
    it('LaTeX commands (strong signal, bypass prose gate)', () => {
        expect(isInlineMathContent('\\frac{1}{2}')).toBe(true);
        expect(isInlineMathContent('\\alpha + \\beta')).toBe(true);
        // STRONG signal must win even with a run of English words inside \text
        expect(isInlineMathContent('\\text{the quick brown fox}')).toBe(true);
    });
    it('math symbols (strong signal)', () => {
        expect(isInlineMathContent('∑ from one to infinity')).toBe(true);
        expect(isInlineMathContent('|μ| ≤ 1')).toBe(true);
    });
    it('compact algebra (weak signal, no prose words)', () => {
        expect(isInlineMathContent('a + b')).toBe(true);
        expect(isInlineMathContent('x = 0')).toBe(true);
        expect(isInlineMathContent('Sc/r')).toBe(true);
    });
});

describe('isInlineMathContent — currency / prose rejection', () => {
    it('rejects currency runs from the reported lease bug', () => {
        expect(isInlineMathContent('900 refundable security deposit + ')).toBe(false);
        expect(isInlineMathContent('300 non-refundable cleaning fee** (= ')).toBe(false);
        expect(isInlineMathContent('200 after the 5th, +')).toBe(false);
        expect(isInlineMathContent('100 after the 8th, +')).toBe(false);
        expect(isInlineMathContent('75/day after) is far over')).toBe(false);
    });
    it('rejects regex back-references ($1, $2)', () => {
        expect(isInlineMathContent('1')).toBe(false);
        expect(isInlineMathContent('2')).toBe(false);
    });
    it('rejects code-context spans via the match guard', () => {
        expect(isInlineMathContent('x', 'foo.replace($x$, y)')).toBe(false);
        expect(isInlineMathContent('x', 'shell $x$ here')).toBe(false);
        expect(isInlineMathContent('a + b', 'run command $a + b$')).toBe(false);
    });
    it('does not treat URLs/paths as algebra', () => {
        expect(isInlineMathContent('https://example.com/a')).toBe(false);
        expect(isInlineMathContent('a/b://c')).toBe(false);
    });
    it('single-operator prose ("after the") is not algebra', () => {
        expect(isInlineMathContent('cats and dogs')).toBe(false);
    });
});

describe('processInlineMath — full segment transformation', () => {
    const LEASE = [
        'Deposit = $900 refundable security deposit + $300 non-refundable cleaning fee (= $1,200 total).',
        "The current draft ($200 after the 5th, +$100 after the 8th, +$75/day after) is far over Seattle's limit.",
    ].join('\n');

    it('emits no MATH_INLINE markers for the currency-laden lease text', () => {
        const out = processInlineMath(LEASE);
        expect(out).not.toContain('⟨MATH_INLINE');
    });

    it('leaves the lease text byte-identical', () => {
        expect(processInlineMath(LEASE)).toBe(LEASE);
    });

    it('still converts genuine inline math', () => {
        expect(decodedMath('the value $x = 0$ holds')).toEqual(['x = 0']);
        expect(decodedMath('$\\frac{1}{2}$ cup')).toEqual(['\\frac{1}{2}']);
        expect(decodedMath('let $x$ vary')).toEqual(['x']);
    });

    it('KaTeX adjacency kills the "$5 + $5" currency caveat at the source', () => {
        // space just inside a delimiter ⇒ never matched as a math span
        expect(processInlineMath('pay $5 + $5 today')).not.toContain('⟨MATH_INLINE');
    });

    it('adjacency: leading/trailing space inside delimiters is not math', () => {
        expect(processInlineMath('$ x = 0 $')).not.toContain('⟨MATH_INLINE');
    });

    it('mixed line: real math renders, adjacent currency stays literal', () => {
        const out = processInlineMath('cost $5 but $x$ is unknown');
        expect(out).toContain(MATH_INLINE_MARKER_PREFIX);
        expect(decodedMath('cost $5 but $x$ is unknown')).toEqual(['x']);
        expect(out).toContain('cost $5 but');
    });
});

/**
 * Reported bug: a compensation table whose cells hold bolded currency figures
 * rendered as KaTeX plus a leaked base64 marker payload
 * ("$255,100/yr| **MjU1LDEwMC95ciB8ICoq320,000/yr**").
 *
 * Root cause is classification, not transport. In `| $255,100/yr | **$320,000/yr** |`
 * the span between the two `$` is `255,100/yr | **`, which
 *   - passes KaTeX adjacency (digit after the opener, `*` before the closer), and
 *   - trips hasAlgebraicNotation because it contains a letter run ("yr") and `/`,
 * while the prose gate does not fire because "yr" is only two letters, so
 * proseWordCount is 0.
 *
 * The span also spans a table-cell `|`, so once it becomes a marker the row
 * loses a column — which is why the corruption is visible rather than merely
 * mis-styled.
 */
describe('isInlineMathContent — bolded currency in table cells', () => {
    it('rejects spans that swallowed a bold delimiter', () => {
        expect(isInlineMathContent('255,100/yr | **')).toBe(false);
        expect(isInlineMathContent('316,409 (Annual 26) | **')).toBe(false);
        expect(isInlineMathContent('0 | **')).toBe(false);
    });

    it('rejects comma-grouped currency figures without a LaTeX command', () => {
        expect(isInlineMathContent('255,100/yr')).toBe(false);
        expect(isInlineMathContent('320,000/yr')).toBe(false);
        expect(isInlineMathContent('580,000 vs 700,000/yr')).toBe(false);
    });

    it('still accepts grouped digits inside real LaTeX', () => {
        expect(isInlineMathContent('\\text{1,200 total}')).toBe(true);
    });
});

describe('processInlineMath — compensation table row', () => {
    const TABLE = [
        '| | What kind of number it is | Currently | **Ask** | Floor | Effect on run rate |',
        '|---|---|---|---|---|---|',
        '| **1. Base salary** | Annual rate, permanent | $255,100/yr | **$320,000/yr** | $290,000 | +$64,900/yr, immediately |',
        '| **2. Off-cycle retention grant** | One-time grant, total value | $0 | **$600,000 total**, vesting over 3 yrs | $400,000 over 2 yrs | **None** — temporary by design |',
        '| **3. Forward refresh floor** | Size of each future annual grant | $316,409 (Annual 26) | **$700,000/yr** | $580,000 | +$383,591/yr, phasing in from CY2028 |',
    ].join('\n');

    it('emits no markers anywhere in the table', () => {
        expect(processInlineMath(TABLE)).not.toContain('⟨MATH_INLINE');
    });

    it('leaves the table byte-identical', () => {
        expect(processInlineMath(TABLE)).toBe(TABLE);
    });

    it('positive control: genuine math in a table cell still converts', () => {
        expect(decodedMath('| rate | $\\lambda/\\mu$ | ok |')).toEqual(['\\lambda/\\mu']);
    });
});

/**
 * Structural invariant: preprocessing must not change a table's column count.
 *
 * The `**` and comma-currency guards fix the REPORTED row, but they are
 * content heuristics — they do not establish the invariant. A span that opens
 * in one cell and closes in the next still gets swallowed whole whenever the
 * content happens to look like algebra:
 *
 *   | $99/yr | *$120/yr* |
 *
 * The span between the first two `$` is `99/yr | *`. It passes KaTeX adjacency
 * (digit after the opener, `*` before the closer), contains no `**`, and has
 * no comma-grouped digits — so both new guards stay silent. It trips
 * hasAlgebraicNotation on "yr" + "/", and proseWordCount is 0 because "yr" is
 * two letters. The marker then absorbs the cell `|` and the row collapses from
 * two columns to one.
 *
 * These assertions are made against marked's ACTUAL cell count rather than the
 * intermediate marker string, because the column count is the thing the user
 * sees go wrong. A test that only compared strings would not distinguish
 * "marker present" from "row structurally damaged".
 */
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { marked: markedForTables } = require('marked/lib/marked.umd.js');

/**
 * Per-row map of which cells hold content.
 *
 * Cell COUNT is deliberately not the metric: marked pads a short row out to the
 * header width, so a row that collapsed from two cells to one still reports
 * length 2. Measuring count therefore passes against the broken code and
 * certifies the bug. What actually changes is occupancy — the swallowed cell
 * becomes empty while its neighbour absorbs the merged text:
 *
 *   raw    -> [["$99/yr", "*$120/yr*"]]
 *   broken -> [["MARKER120/yr*", ""]]
 */
function cellOccupancy(markdown: string): boolean[][] {
    return markedForTables.lexer(markdown)
        .filter((t: any) => t.type === 'table')
        .flatMap((t: any) => t.rows.map((r: any[]) =>
            r.map((c: any) => c.text.trim().length > 0)));
}

describe('processInlineMath — table column structure is preserved', () => {
    const table = (row: string) => `| a | b |\n|---|---|\n${row}`;

    it('a span crossing a cell boundary does not collapse the row', () => {
        const md = table('| $99/yr | *$120/yr* |');
        expect(cellOccupancy(processInlineMath(md))).toEqual(cellOccupancy(md));
    });

    it('leaves the crossing-span row byte-identical', () => {
        const row = '| $99/yr | *$120/yr* |';
        expect(processInlineMath(table(row))).toBe(table(row));
    });

    it('column count is preserved for the reported compensation row', () => {
        const md = [
            '| a | b | c |',
            '|---|---|---|',
            '| $255,100/yr | **$320,000/yr** | $290,000 |',
        ].join('\n');
        expect(cellOccupancy(processInlineMath(md))).toEqual(cellOccupancy(md));
    });

    it('positive control: math WITHIN one cell still renders as math', () => {
        const md = table('| $\\lambda/\\mu$ | plain |');
        expect(decodedMath(md)).toEqual(['\\lambda/\\mu']);
        expect(cellOccupancy(processInlineMath(md))).toEqual(cellOccupancy(md));
    });

    it('does not cell-split ordinary prose that merely contains a pipe', () => {
        // No delimiter row, so this is not a table: a pipe inside genuine
        // inline math must survive untouched.
        expect(decodedMath('the value $a|b$ holds')).toEqual(['a|b']);
    });

    it('a thematic break is not mistaken for a table delimiter row', () => {
        // If `---` were read as a delimiter row, the pipe-bearing line above it
        // would be cell-split and this math would be destroyed.
        expect(decodedMath('cost $9|b$ here\n\n---\n\nnext')).toEqual(['9|b']);
    });
});
