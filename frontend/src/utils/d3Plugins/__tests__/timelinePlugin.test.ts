/**
 * Unit tests for the timeline layout engine.
 *
 * DOM-free on purpose: the engine is a pure spec -> SVG-string function, so
 * layout regressions (NaN coordinates, vanished marks, overlapping labels,
 * broken escaping) are caught in Node without jsdom or a browser.
 *
 * The bias of this suite is deliberate. Structural assertions -- "the profile
 * is registered", "the wrapper wraps" -- are cheap and were exactly what let a
 * sibling LaTeX profile pass fifteen tests while being unable to render
 * anything at all. So most of what follows targets SILENT wrongness: input a
 * naive implementation accepts and then draws incorrectly or not at all, with
 * no error to notice. Each such test is paired with a PREMISE test proving the
 * underlying JS behaviour really is that treacherous, so the assertion cannot
 * quietly stop testing anything if the platform changes.
 */
import {
    detectAxisKind,
    escapeXml,
    labelWidth,
    lenientJsonParse,
    normalizeTimelineSpec,
    numericTickFormat,
    packLane,
    parseDirection,
    parseValue,
    renderTimelineSvg as renderWithD3,
    safeColor,
    timeTickFormat as timeTickFormatWithD3,
    utcFromParts,
    NormalizedItem,
} from '../timelinePlugin';

/**
 * The REAL d3, reached through its UMD build.
 *
 * `require` of the CJS bundle rather than `import ... from 'd3'` because d3 v7
 * and all its sub-packages are `"type": "module"` / ESM-only, and the project's
 * jest does not transform node_modules -- a bare 'd3' import anywhere in a
 * test's graph fails the entire suite with "Unexpected token 'export'".
 * dist/d3.js is UMD, so it loads untransformed.  Deliberately not a stub: the
 * calendar-aware tick placement asserted below is precisely the behaviour worth
 * pinning, and a stub would assert the stub.
 */
// eslint-disable-next-line @typescript-eslint/no-var-requires
const d3 = require('d3/dist/d3.js');

/**
 * Curried so the ~30 assertions below read as they would if the engine still
 * imported d3 itself.  The injection is threaded here, once.
 */
const renderTimelineSvg = (definition: string | object, isDark: boolean) =>
    renderWithD3(definition, isDark, d3);
const timeTickFormat = (spanMs: number) =>
    timeTickFormatWithD3(spanMs, d3.utcFormat);

const yearOf = (ms: number) => new Date(ms).getUTCFullYear();

/** Every numeric attribute value in an SVG string. */
const numericAttrs = (svg: string): string[] =>
    [...svg.matchAll(/(?:x|y|x1|x2|y1|y2|width|height|rx)="([^"]*)"/g)].map(m => m[1]);

/**
 * Interval bars as {x, w}.
 *
 * The leading \s on each attribute is not cosmetic. `width="([\d.]+)"` with a
 * greedy prefix happily matches the tail of `stroke-width="1"`, so an earlier
 * version of this helper reported every bar as 1px wide -- and the assertions
 * using it PASSED, because "min width > 0" is true of 1. A test that reads the
 * wrong attribute certifies whatever the code does.
 */
function barRects(svg: string): Array<{ x: number; w: number }> {
    // No \s immediately after <rect: x is the FIRST attribute, so consuming the
    // single space before it leaves nothing for the later \sx="" to match.
    const bars = [...svg.matchAll(/<rect[^>]*\sx="([\d.]+)"[^>]*\swidth="([\d.]+)"/g)]
        .map(m => ({ x: Number(m[1]), w: Number(m[2]) }));
    // Self-check: stroke-width is 1 on every bar, so an all-1 result means the
    // regex is reading the stroke and this helper is measuring nothing.
    if (bars.length > 1 && bars.every(b => b.w === 1)) {
        throw new Error('barRects is matching stroke-width, not width');
    }
    return bars;
}

// ===========================================================================
// PREMISES -- the platform behaviours the parser exists to defend against.
// If any of these start passing on their own, the corresponding guard below
// has stopped testing anything and should be revisited.
// ===========================================================================
describe('premises: raw JS date/number handling really is this treacherous', () => {
    it('Date.parse drops the BCE sign', () => {
        expect(new Date(Date.parse('-500')).getUTCFullYear()).toBe(500);
    });

    it('Date.UTC remaps two-digit years into the 1900s', () => {
        expect(new Date(Date.UTC(50, 0, 1)).getUTCFullYear()).toBe(1950);
    });

    it('new Date() silently accepts prose', () => {
        expect(Number.isNaN(new Date('circa 1914').getTime())).toBe(false);
    });

    it('Number() turns missing values into zero', () => {
        expect(Number('')).toBe(0);
        expect(Number(null)).toBe(0);
        expect(Number([])).toBe(0);
    });

    it('a JS Date cannot represent geologic time at all', () => {
        // This is why `numeric` is a distinct axis kind rather than a nicety.
        const gya = -4.5e9 * 365.2425 * 24 * 3600 * 1000;
        expect(Number.isNaN(new Date(gya).getTime())).toBe(true);
    });
});

// ===========================================================================
// Value parsing
// ===========================================================================
describe('utcFromParts dodges the two-digit-year remapping', () => {
    it('year 50 is year 50, not 1950', () => {
        expect(yearOf(utcFromParts(50))).toBe(50);
    });

    it('negative years are preserved', () => {
        expect(yearOf(utcFromParts(-500))).toBe(-500);
    });

    it('ordinary years are unaffected', () => {
        expect(new Date(utcFromParts(1914, 7, 28)).toISOString())
            .toBe('1914-07-28T00:00:00.000Z');
    });
});

describe('parseValue in time mode', () => {
    it('reads a bare number as a year', () => {
        expect(yearOf(parseValue(1914, 'time', 'x'))).toBe(1914);
    });

    it('keeps the BCE sign that Date.parse discards', () => {
        expect(yearOf(parseValue('-500', 'time', 'x'))).toBe(-500);
    });

    it('accepts year, year-month, date and explicit-offset datetime', () => {
        expect(new Date(parseValue('1914', 'time', 'x')).toISOString())
            .toBe('1914-01-01T00:00:00.000Z');
        expect(new Date(parseValue('1914-07', 'time', 'x')).toISOString())
            .toBe('1914-07-01T00:00:00.000Z');
        expect(new Date(parseValue('1914-07-28', 'time', 'x')).toISOString())
            .toBe('1914-07-28T00:00:00.000Z');
        expect(new Date(parseValue('2026-01-02T03:04:05.678Z', 'time', 'x')).toISOString())
            .toBe('2026-01-02T03:04:05.678Z');
    });

    it('applies a numeric UTC offset', () => {
        expect(new Date(parseValue('2026-01-02T03:00+02:00', 'time', 'x')).toISOString())
            .toBe('2026-01-02T01:00:00.000Z');
    });

    it('REJECTS prose that new Date() would have accepted', () => {
        expect(() => parseValue('circa 1914', 'time', 'items[0].start'))
            .toThrow(/not an accepted date/);
        expect(() => parseValue('circa 1914', 'time', 'items[0].start'))
            .toThrow(/items\[0\]\.start/);
    });

    it('REJECTS a naked local datetime, whose meaning depends on the reader', () => {
        expect(() => parseValue('1914-07-28 00:00:00', 'time', 'x')).toThrow();
        expect(() => parseValue('July 28, 1914', 'time', 'x')).toThrow();
    });

    it('REJECTS missing values instead of placing them at the epoch', () => {
        for (const bad of [null, undefined, '']) {
            expect(() => parseValue(bad, 'time', 'x')).toThrow(/missing value/);
        }
    });

    it('refuses an implausible year rather than plotting it', () => {
        expect(() => parseValue(20260101, 'time', 'x')).toThrow(/not a plausible year/);
    });
});

describe('parseValue in numeric mode', () => {
    it('accepts numbers and numeric strings including exponentials', () => {
        expect(parseValue(4500, 'numeric', 'x')).toBe(4500);
        expect(parseValue('4500', 'numeric', 'x')).toBe(4500);
        expect(parseValue('4.5e3', 'numeric', 'x')).toBe(4500);
        expect(parseValue(-273.15, 'numeric', 'x')).toBe(-273.15);
    });

    it('REJECTS a date string rather than coercing it to NaN', () => {
        // Number('1914-07-28') is NaN, which would place the mark nowhere and
        // draw an invisible bar with no error reported.
        expect(() => parseValue('1914-07-28', 'numeric', 'x'))
            .toThrow(/is not a number/);
    });

    it('REJECTS the values Number() would turn into 0 or 1', () => {
        for (const bad of ['', null, undefined]) {
            expect(() => parseValue(bad, 'numeric', 'x')).toThrow(/missing value/);
        }
        expect(() => parseValue(true, 'numeric', 'x')).toThrow();
        expect(() => parseValue([], 'numeric', 'x')).toThrow();
        expect(() => parseValue(NaN, 'numeric', 'x')).toThrow(/finite/);
    });
});

// ===========================================================================
// Axis kind
// ===========================================================================
describe('detectAxisKind', () => {
    it('all-numeric values give a numeric axis', () => {
        expect(detectAxisKind({ items: [{ start: 4500, end: 4000 }] })).toBe('numeric');
    });

    it('any unambiguous date form makes the whole timeline temporal', () => {
        expect(detectAxisKind({ items: [{ start: '1914-07-28' }] })).toBe('time');
    });

    it('a MIXED spec resolves to time, with bare numbers read as years', () => {
        // The alternative readings are both worse: erroring rejects the reading
        // a human plainly intends, and choosing numeric turns the date string
        // into NaN and loses the item silently.
        const spec = normalizeTimelineSpec({
            items: [{ lane: 'a', label: 'WWI', start: 1914, end: '1918-11-11' }],
        });
        expect(spec.kind).toBe('time');
        expect(yearOf(spec.items[0].start)).toBe(1914);
        expect(yearOf(spec.items[0].end as number)).toBe(1918);
    });

    it('honours an explicit scale and its aliases', () => {
        expect(detectAxisKind({ scale: 'time', items: [{ start: 1 }] })).toBe('time');
        expect(detectAxisKind({ scale: 'numeric', items: [{ start: '2020-01-01' }] }))
            .toBe('numeric');
        expect(detectAxisKind({ scale: 'Temporal', items: [{ start: 1 }] })).toBe('time');
    });

    it('rejects an unrecognised scale rather than falling back silently', () => {
        expect(() => detectAxisKind({ scale: 'logarithmic', items: [] }))
            .toThrow(/not recognised/);
    });
});

// ===========================================================================
// The silent-vanish cases
// ===========================================================================
describe('marks that would disappear without an error', () => {
    it('REFUSES a lone reversed interval as ambiguous, naming both remedies', () => {
        // With one interval a swapped pair and a deliberately descending axis
        // are indistinguishable, so the engine asks instead of guessing.
        const bad = { items: [{ label: 'bad', start: '1918-11-11', end: '1914-07-28' }] };
        expect(() => normalizeTimelineSpec(bad)).toThrow(/only.*interval/s);
        expect(() => normalizeTimelineSpec(bad)).toThrow(/items\[0\] \(bad\)/);
        expect(() => normalizeTimelineSpec(bad)).toThrow(/"direction": "descending"/);
    });

    it('REFUSES a spec where some intervals are reversed and others are not', () => {
        // The valuable case: the set contradicts itself, so one of them is a
        // mistake no matter which reading is intended.
        expect(() => normalizeTimelineSpec({
            items: [
                { label: 'fwd', start: 1000, end: 2000 },
                { label: 'rev', start: 4000, end: 3000 },
            ],
        })).toThrow(/contradictory/);
        expect(() => normalizeTimelineSpec({
            items: [
                { label: 'fwd', start: 1000, end: 2000 },
                { label: 'rev', start: 4000, end: 3000 },
            ],
        })).toThrow(/items\[1\] \(rev\)/);
    });

    it('accepts a zero-length interval and still draws a visible bar', () => {
        const out = renderTimelineSvg({
            items: [
                { label: 'zero', start: '2026-01-01', end: '2026-01-01' },
                { label: 'other', start: '2026-01-05', end: '2026-01-09' },
            ],
        }, false);
        const widths = barRects(out.svg).map(b => b.w);
        expect(widths.length).toBe(2);
        expect(Math.min(...widths)).toBeGreaterThanOrEqual(2);
    });

    it('pads a zero-span domain instead of stacking every mark on one pixel', () => {
        const out = renderTimelineSvg({
            items: [{ label: 'only', at: '2026-01-01' }],
        }, false);
        const xs = [...out.svg.matchAll(/<polygon points="([\d.]+),/g)].map(m => Number(m[1]));
        expect(xs.length).toBe(1);
        expect(Number.isFinite(xs[0])).toBe(true);
        // With a padded domain the single instant lands mid-plot, not at an edge.
        expect(xs[0]).toBeGreaterThan(100);
    });

    it('appends an undeclared lane rather than dropping its items', () => {
        const spec = normalizeTimelineSpec({
            lanes: ['Declared'],
            items: [
                { lane: 'Declared', label: 'a', at: 1 },
                { lane: 'Surprise', label: 'b', at: 2 },
            ],
        });
        expect(spec.lanes).toEqual(['Declared', 'Surprise']);
        const out = renderTimelineSvg({
            lanes: ['Declared'],
            items: [
                { lane: 'Declared', label: 'a', at: 1 },
                { lane: 'Surprise', label: 'b', at: 2 },
            ],
        }, false);
        expect(out.svg).toContain('Surprise');
        expect(out.itemCount).toBe(2);
    });

    it('emits no non-finite coordinate anywhere', () => {
        const out = renderTimelineSvg({
            title: 'mixed',
            items: [
                { lane: 'L1', label: 'span', start: '2026-01-01', end: '2026-03-01' },
                { lane: 'L1', label: 'point', at: '2026-02-01' },
                { lane: 'L2', label: 'other', start: '2026-01-15', end: '2026-01-20' },
            ],
            eras: [{ label: 'Q1', start: '2026-01-01', end: '2026-03-31' }],
            markers: [{ label: 'cut', at: '2026-02-14' }],
        }, true);
        for (const v of numericAttrs(out.svg)) {
            expect(v).not.toMatch(/NaN|Infinity|undefined/);
        }
        expect(out.svg).not.toMatch(/NaN|Infinity|undefined/);
    });
});

// ===========================================================================
// Packing
// ===========================================================================
describe('lane packing', () => {
    const item = (label: string, start: number, end: number | null): NormalizedItem => ({
        lane: '', label, start, end, color: null, note: null,
    });

    it('overlapping intervals land on different rows', () => {
        const xOf = (v: number) => v;
        const placed = packLane(
            [item('a', 0, 100), item('b', 50, 150)], xOf, 1000);
        expect(placed[0].row).toBe(0);
        expect(placed[1].row).toBe(1);
    });

    it('non-overlapping intervals share a row', () => {
        const xOf = (v: number) => v;
        const placed = packLane(
            [item('a', 0, 100), item('b', 400, 500)], xOf, 1000);
        expect(placed.map(p => p.row)).toEqual([0, 0]);
    });

    it('packs on the LABEL extent, not just the bar', () => {
        // The trap: two 1px bars 20px apart never overlap, but their outside
        // labels are ~100px wide and would print straight over each other.
        // Packing on bar geometry alone puts both on row 0 and the diagram
        // looks fine while being unreadable.
        const xOf = (v: number) => v;
        const a = item('a long descriptive label', 0, 1);
        const b = item('another long label here', 20, 21);
        const placed = packLane([a, b], xOf, 1000);
        expect(labelWidth(a.label)).toBeGreaterThan(50);
        expect(placed[1].row).toBe(1);
    });

    it('flips a label that would overflow the right edge', () => {
        const xOf = (v: number) => v;
        const placed = packLane([item('trailing label', 940, 950)], xOf, 960);
        expect(placed[0].labelAnchor).toBe('end');
        expect(placed[0].labelX).toBeLessThan(950);
    });

    it('puts the label inside a bar that is wide enough', () => {
        const xOf = (v: number) => v;
        const placed = packLane([item('fits', 0, 400)], xOf, 1000);
        expect(placed[0].labelInside).toBe(true);
        expect(placed[0].labelAnchor).toBe('middle');
    });

    it('orders pixels, not values, so a reversed axis still packs', () => {
        // Geologic time runs 4500 -> 0 left to right, so xOf(start) is to the
        // RIGHT of xOf(end); assuming value order would give a negative width.
        const xOf = (v: number) => 1000 - v;
        const placed = packLane([item('Hadean', 4500, 4000)], xOf, 1000);
        expect(placed[0].x2).toBeGreaterThan(placed[0].x1);
    });

    it('abutting intervals share a row instead of stepping down', () => {
        // One era ending exactly where the next begins is contiguous, not
        // overlapping. A cosmetic minimum gap in the fit test stacks them, and
        // the result renders happily as a staircase.
        const xOf = (v: number) => v;
        const placed = packLane(
            [item('a', 0, 100), item('b', 100, 200), item('c', 200, 300)],
            xOf, 1000);
        expect(placed.map(p => p.row)).toEqual([0, 0, 0]);
    });

    it('sorts by PIXEL so a descending axis does not degenerate to one row each', () => {
        // The bug this replaces: sorting by `start` walks a descending axis
        // right-to-left, so first-fit finds every row already occupied and each
        // mark gets its own. Four contiguous eons became a four-row staircase,
        // with no error and a plausible-looking diagram.
        const xOf = (v: number) => 1000 - v / 10;   // descending
        const placed = packLane([
            item('Hadean', 4567, 4031),
            item('Archean', 4031, 2500),
            item('Proterozoic', 2500, 538.8),
            item('Phanerozoic', 538.8, 0),
        ], xOf, 1000);
        expect(placed.map(p => p.row)).toEqual([0, 0, 0, 0]);
    });
});

// ===========================================================================
// Axis formatting
// ===========================================================================
describe('tick formatting', () => {
    const DAY = 86400000;

    it('picks one format for the whole axis, chosen by span', () => {
        expect(timeTickFormat(10 * 365 * DAY)(new Date('2026-03-04T05:06:07Z')))
            .toBe('2026');
        expect(timeTickFormat(5 * DAY)(new Date('2026-03-04T05:06:07Z')))
            .toMatch(/04 Mar/);
        expect(timeTickFormat(2000)(new Date('2026-03-04T05:06:07.089Z')))
            .toBe('05:06:07.089');
    });

    it('labels pre-Common-Era years as BCE rather than negatives', () => {
        // Astronomical year 0 is 1 BCE.
        expect(timeTickFormat(1e12)(new Date(utcFromParts(-500)))).toBe('501 BCE');
        expect(timeTickFormat(1e12)(new Date(utcFromParts(0)))).toBe('1 BCE');
    });

    it('does not zero-pad a Common-Era year under 1000', () => {
        // %Y pads to four digits, so an unrepaired axis reads '0100'.
        expect(timeTickFormat(1e12)(new Date(utcFromParts(100)))).toBe('100');
        expect(timeTickFormat(1e12)(new Date(utcFromParts(476)))).toBe('476');
    });

    it('keeps the month on a BCE axis at month granularity', () => {
        // Short-circuiting the whole format on a BCE year would silently drop
        // the month, turning every tick of a within-year axis into the same
        // label.
        expect(timeTickFormat(120 * 86400000)(new Date(utcFromParts(-500, 3))))
            .toBe('Mar 501 BCE');
    });

    it('numeric ticks carry the unit and drop float noise', () => {
        expect(numericTickFormat('Mya')(4500)).toBe('4500 Mya');
        expect(numericTickFormat(null)(0.30000000000000004)).toBe('0.3');
    });
});

// ===========================================================================
// Geologic column: the case a date axis structurally cannot serve
// ===========================================================================
describe('numeric axis carries deep time', () => {
    const geologic = {
        title: 'Geologic eons',
        scale: 'numeric',
        unit: 'Mya',
        items: [
            { lane: 'Eon', label: 'Hadean', start: 4567, end: 4031 },
            { lane: 'Eon', label: 'Archean', start: 4031, end: 2500 },
            { lane: 'Eon', label: 'Proterozoic', start: 2500, end: 538.8 },
            { lane: 'Eon', label: 'Phanerozoic', start: 538.8, end: 0 },
        ],
        markers: [{ label: 'Great Oxidation', at: 2400 }],
    };

    it('renders a geologic column written the idiomatic "ago" way', () => {
        // Every interval counts DOWN because Mya means "millions of years ago".
        // Refusing that spelling would reject the one use case a numeric axis
        // was added for.
        const out = renderTimelineSvg(geologic, false);
        expect(out.kind).toBe('numeric');
        expect(out.descending).toBe(true);
        expect(out.itemCount).toBe(4);
        expect(out.svg).toContain('Mya');
        expect(out.svg).toContain('Phanerozoic');
        expect(out.svg).not.toMatch(/NaN/);
    });

    it('puts the OLDEST end of a descending axis on the left', () => {
        // The assertion that actually distinguishes a reversed axis from an
        // ascending one; without it the spec could render mirror-imaged and
        // every other assertion here would still pass.
        const spec = normalizeTimelineSpec(geologic);
        const out = renderTimelineSvg(geologic, false);
        const bars = barRects(out.svg);
        expect(spec.descending).toBe(true);
        expect(bars.length).toBe(4);
        // Hadean (4567-4031 Mya) is the leftmost bar; Phanerozoic (538.8-0) the
        // rightmost, and it must reach the right-hand end of the plot.
        const leftmost = bars.reduce((a, b) => (a.x <= b.x ? a : b));
        const rightmost = bars.reduce((a, b) => (a.x + a.w >= b.x + b.w ? a : b));
        // Hadean (4567-4031 = 536 Myr) is narrower than Proterozoic
        // (2500-538.8 = 1961 Myr), so a mirrored axis is detectable.
        const widest = bars.reduce((a, b) => (a.w >= b.w ? a : b));
        expect(leftmost.w).toBeLessThan(widest.w);
        expect(rightmost.x + rightmost.w).toBeGreaterThan(out.width * 0.9);
        expect(leftmost.x).toBeLessThan(out.width * 0.2);
    });

    it('draws a contiguous eon sequence on ONE row', () => {
        // The end-to-end form of the pixel-sort fix: four abutting eons must
        // occupy a single lane row, not four.
        const out = renderTimelineSvg(geologic, false);
        const ys = new Set(
            [...out.svg.matchAll(/<rect[^>]*\sy="([\d.]+)"/g)].map(m => m[1]),
        );
        expect(ys.size).toBe(1);
    });

    it('an ascending numeric axis is unaffected', () => {
        const out = renderTimelineSvg({
            scale: 'numeric',
            unit: 'ms',
            items: [
                { lane: 'req', label: 'parse', start: 0, end: 12 },
                { lane: 'req', label: 'query', start: 12, end: 240 },
            ],
        }, false);
        expect(out.descending).toBe(false);
    });
});

describe('the auto-detection boundary, and the BCE seam behind it', () => {
    const antiquity = (extra: object) => ({
        lanes: ['Rome'],
        items: [
            { lane: 'Rome', label: 'Republic', start: '-509', end: '-27' },
            { lane: 'Rome', label: 'Empire', start: '-27', end: '476' },
        ],
        ...extra,
    });

    it('a years-only spec falls to a NUMERIC axis, because "-509" is a number', () => {
        // Documenting the boundary rather than pretending it is not there:
        // auto-detection promotes to a date axis only on an unambiguous date
        // form (a '-' separator or a 'T'), and '-509' is a numeric string.
        expect(normalizeTimelineSpec(antiquity({})).kind).toBe('numeric');
    });

    it('scale:"time" reaches the calendar axis and BCE labels END TO END', () => {
        // The seam worth asserting: timeTickFormat's BCE branch was covered by
        // a unit test while being UNREACHABLE from any real spec, because
        // detection never chose a time axis for year-only input. A passing
        // formatter test says nothing about whether a fence can get there.
        const out = renderTimelineSvg(antiquity({ scale: 'time' }), false);
        expect(out.kind).toBe('time');
        expect(out.svg).toMatch(/\d+ BCE/);
        expect(out.svg).not.toMatch(/>-\d+</);      // no raw negative years
        expect(out.svg).not.toMatch(/>0\d{3}</);    // no zero-padded CE years
    });

    it('a date separator anywhere promotes the whole spec to a time axis', () => {
        expect(normalizeTimelineSpec({
            items: [{ start: '-509', end: '0476-01-01' }],
        }).kind).toBe('time');
    });
});

describe('explicit axis direction', () => {
    it('accepts a lone reversed interval once direction is declared', () => {
        const out = renderTimelineSvg({
            scale: 'numeric',
            direction: 'descending',
            items: [{ label: 'Hadean', start: 4567, end: 4031 }],
        }, false);
        expect(out.descending).toBe(true);
        expect(out.svg).not.toMatch(/NaN/);
    });

    it('reports a reversed interval when the axis is declared ascending', () => {
        expect(() => normalizeTimelineSpec({
            direction: 'ascending',
            items: [
                { label: 'a', start: 10, end: 5 },
                { label: 'b', start: 20, end: 15 },
            ],
        })).toThrow(/"direction" is "ascending"/);
    });

    it('accepts the aliases and refuses anything else', () => {
        expect(parseDirection('ago')).toBe('descending');
        expect(parseDirection('reverse')).toBe('descending');
        expect(parseDirection('ASC')).toBe('ascending');
        expect(parseDirection(undefined)).toBeNull();
        expect(() => parseDirection('sideways')).toThrow(/not recognised/);
    });
});

// ===========================================================================
// Escaping and colour handling
// ===========================================================================
describe('untrusted spec content', () => {
    it('escapes markup in labels', () => {
        const out = renderTimelineSvg({
            title: '<script>x</script>',
            items: [{ label: 'a "quoted" & <tag>', at: 1 }],
        }, false);
        expect(out.svg).not.toContain('<script>');
        expect(out.svg).toContain('&lt;script&gt;');
        expect(out.svg).toContain('&quot;quoted&quot;');
        expect(out.svg).toContain('&amp;');
    });

    it('escapeXml covers the attribute-breaking characters', () => {
        expect(escapeXml('a"b<c>d&e')).toBe('a&quot;b&lt;c&gt;d&amp;e');
    });

    it('safeColor accepts real colours and refuses junk', () => {
        expect(safeColor('#ff0055')).toBe('#ff0055');
        expect(safeColor('rebeccapurple')).toBe('rebeccapurple');
        expect(safeColor('rgb(1, 2, 3)')).toBe('rgb(1, 2, 3)');
        expect(safeColor('"><script>')).toBeNull();
        expect(safeColor('url(#x)')).toBeNull();
        expect(safeColor(42)).toBeNull();
    });

    it('falls back to the theme colour when a colour is refused', () => {
        const out = renderTimelineSvg({
            items: [{ label: 'x', start: 1, end: 2, color: '"><script>alert(1)</script>' }],
        }, false);
        expect(out.svg).not.toContain('<script>');
        expect(out.svg).toContain('#54aeff');   // light-theme bar fill
    });
});

// ===========================================================================
// Vocabulary errors: a model inventing a key must be taught, not just failed
// ===========================================================================
describe('actionable errors', () => {
    it('names the accepted top-level keys', () => {
        expect(() => normalizeTimelineSpec({ itmes: [] }))
            .toThrow(/title, scale, unit, direction, width, lanes, eras, items, markers/);
        expect(() => normalizeTimelineSpec({ itmes: [] })).toThrow(/"itmes"/);
    });

    it('names the accepted item keys', () => {
        expect(() => normalizeTimelineSpec({ items: [{ labe: 'typo', at: 1 }] }))
            .toThrow(/lane, label, start, end, at, color, note/);
    });

    it('refuses an item with both at and start', () => {
        expect(() => normalizeTimelineSpec({ items: [{ at: 1, start: 2 }] }))
            .toThrow(/both "at" and "start"/);
    });

    it('refuses an item with neither', () => {
        expect(() => normalizeTimelineSpec({ items: [{ label: 'x' }] }))
            .toThrow(/needs "start".*or "at"/);
    });

    it('refuses an empty timeline', () => {
        expect(() => normalizeTimelineSpec({ items: [] })).toThrow(/at least one/);
        expect(() => normalizeTimelineSpec([])).toThrow(/must be a JSON object/);
    });
});

// ===========================================================================
// Parsing tolerance and the streaming gate
// ===========================================================================
describe('the injected d3 dependency', () => {
    // These pin the reason for the injection, which is otherwise invisible: the
    // engine used to `import ... from 'd3'` and that broke the whole suite under
    // the project's jest (ESM in node_modules, untransformed). A future edit that
    // "tidies" the parameter away by reintroducing the import would pass every
    // other test in this file and fail the runner, so the contract is asserted.
    it('is the real d3, not a stub', () => {
        // If this ever becomes a stub, every calendar-tick assertion below
        // silently starts testing the stub instead of d3.
        expect(typeof d3.scaleUtc).toBe('function');
        expect(typeof d3.scaleLinear).toBe('function');
        expect(typeof d3.utcFormat).toBe('function');
        // A property no plausible hand-rolled stub would have: real calendar
        // tick snapping to round years.
        const ticks = d3.scaleUtc()
            .domain([new Date(Date.UTC(1914, 3, 7)), new Date(Date.UTC(1939, 7, 2))])
            .range([0, 800])
            .ticks(6)
            .map((d: Date) => d.getUTCFullYear());
        expect(ticks.every((y: number) => y % 5 === 0)).toBe(true);
    });

    it('is named in the error when absent, rather than failing deep in the axis', () => {
        const spec = { items: [{ label: 'a', start: 1, end: 2 }] };
        // Undefined, and the two shapes a partial refactor produces.
        expect(() => (renderWithD3 as any)(spec, false))
            .toThrow(/scaleUtc, scaleLinear and utcFormat/);
        expect(() => (renderWithD3 as any)(spec, false, {}))
            .toThrow(/scaleUtc, scaleLinear and utcFormat/);
        expect(() => (renderWithD3 as any)(spec, false, { scaleLinear: () => {} }))
            .toThrow(/scaleUtc, scaleLinear and utcFormat/);
    });

    it('the engine source names no d3 import, so jest can parse it', () => {
        // The actual regression guard. A static import of d3 (or any of its
        // ESM-only sub-packages) makes this file unloadable, so the assertion
        // has to be on the SOURCE rather than on behaviour.
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const fs = require('fs');
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const path = require('path');
        // Both candidates so this reads the engine whether the suite runs from
        // __tests__/ or beside it; the length self-check below turns "found
        // neither" into a failure rather than a vacuous pass.
        const candidates = [
            path.join(__dirname, '..', 'timelinePlugin.ts'),
            path.join(__dirname, 'timelinePlugin.ts'),
        ];
        const found = candidates.find((p: string) => fs.existsSync(p));
        expect(found).toBeDefined();
        const src = fs.readFileSync(found as string, 'utf8');
        expect(src.length).toBeGreaterThan(1000);           // read the real file
        expect(src).not.toMatch(/^\s*import\s[^;]*\sfrom\s*['"]d3(-[a-z-]+)?['"]/m);
        expect(src).not.toMatch(/^\s*(const|let|var)\s[^=]*=\s*require\(\s*['"]d3['"]/m);
    });
});

describe('lenient JSON parsing', () => {
    it('tolerates trailing commas, comments and a stray fence', () => {
        expect(lenientJsonParse('{"a": 1,}')).toEqual({ a: 1 });
        expect(lenientJsonParse('{"a": 1 // note\n}')).toEqual({ a: 1 });
        expect(lenientJsonParse('```timeline\n{"a": 1}\n```')).toEqual({ a: 1 });
    });

    it('returns undefined for a partial body so streaming can wait', () => {
        expect(lenientJsonParse('{"items": [{"label": "half')).toBeUndefined();
    });
});

// ===========================================================================
// Frame geometry
// ===========================================================================
describe('frame', () => {
    it('grows in height with lane count', () => {
        const one = renderTimelineSvg({ items: [{ lane: 'A', label: 'a', at: 1 }] }, false);
        const two = renderTimelineSvg({
            items: [
                { lane: 'A', label: 'a', at: 1 },
                { lane: 'B', label: 'b', at: 2 },
            ],
        }, false);
        expect(two.height).toBeGreaterThan(one.height);
        expect(two.laneCount).toBe(2);
    });

    it('grows in height when a lane needs extra packed rows', () => {
        const flat = renderTimelineSvg({
            items: [
                { lane: 'A', label: 'a', start: 0, end: 10 },
                { lane: 'A', label: 'b', start: 500, end: 510 },
            ],
        }, false);
        const stacked = renderTimelineSvg({
            items: [
                { lane: 'A', label: 'a', start: 0, end: 500 },
                { lane: 'A', label: 'b', start: 100, end: 600 },
            ],
        }, false);
        expect(stacked.height).toBeGreaterThan(flat.height);
    });

    it('clamps an absurd requested width', () => {
        expect(normalizeTimelineSpec({ width: 99999, items: [{ at: 1 }] }).width).toBe(3000);
        expect(normalizeTimelineSpec({ width: 10, items: [{ at: 1 }] }).width).toBe(320);
    });

    it('declares matching width/height and viewBox', () => {
        const out = renderTimelineSvg({ items: [{ label: 'a', at: 1 }] }, false);
        expect(out.svg).toContain(`width="${out.width}"`);
        expect(out.svg).toContain(`height="${out.height}"`);
        expect(out.svg).toContain(`viewBox="0 0 ${out.width} ${out.height}"`);
    });

    it('draws a boundary rule per era so abutting bands stay distinguishable', () => {
        // Two consecutive eras in the default colour merge into one block at
        // band opacity; the diagram looks deliberate and reads as a single era.
        const out = renderTimelineSvg({
            eras: [
                { label: 'Great War', start: '1914-07-28', end: '1918-11-11' },
                { label: 'Interwar', start: '1918-11-11', end: '1939-09-01' },
            ],
            items: [{ lane: 'x', label: 'a', start: '1920-01-01', end: '1925-01-01' }],
        }, true);
        const edges = [...out.svg.matchAll(/class="era-edge"/g)];
        expect(edges.length).toBe(2);
    });

    it('reserves a strip for marker labels so they cannot overlap lane content', () => {
        // Drawn at the top of the PLOT, a marker label shares its height with
        // the first lane's row: fine while that lane happens to be empty near
        // the marker, and a collision as soon as it is not. A sample with an
        // empty first lane there renders cleanly and hides this completely.
        const spec = {
            title: 'collision',
            scale: 'numeric',
            items: [{ lane: 'edge', label: 'work spanning the marker', start: 190, end: 210 }],
            markers: [{ label: 'p99 budget', at: 200 }],
        };
        const out = renderTimelineSvg(spec, false);
        const m = /<text[^>]*\sy="([\d.]+)"[^>]*>p99 budget</.exec(out.svg);
        expect(m).not.toBeNull();
        const markerY = Number(m![1]);
        const barYs = [...out.svg.matchAll(/<rect[^>]*\sy="([\d.]+)"/g)]
            .map(x => Number(x[1]));
        expect(barYs.length).toBe(1);
        expect(markerY).toBeLessThan(barYs[0]);
    });

    it('grows in height to make room for a marker strip', () => {
        const withMarker = renderTimelineSvg({
            items: [{ label: 'a', start: 1, end: 2 }],
            markers: [{ label: 'cut', at: 1.5 }],
        }, false);
        const withUnlabelledMarker = renderTimelineSvg({
            items: [{ label: 'a', start: 1, end: 2 }],
            markers: [{ at: 1.5 }],
        }, false);
        expect(withMarker.height).toBeGreaterThan(withUnlabelledMarker.height);
    });

    it('themes differently for dark and light', () => {
        const d = renderTimelineSvg({ items: [{ label: 'a', start: 1, end: 2 }] }, true);
        const l = renderTimelineSvg({ items: [{ label: 'a', start: 1, end: 2 }] }, false);
        expect(d.svg).not.toBe(l.svg);
        expect(d.svg).toContain('#e6edf3');
        expect(l.svg).toContain('#1f2328');
    });
});
