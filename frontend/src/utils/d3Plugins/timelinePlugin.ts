/**
 * Timeline / chronology layout engine.
 *
 * A pure spec -> SVG-string transformer: no DOM, no mounting, no React, so the
 * whole layout is unit-testable in Node.  The plugin wrapper
 * (plugins/d3/timelinePlugin.ts) owns mounting, the error card and theme
 * selection -- the same split the railroad and packet plugins use.
 *
 * WHY THIS EXISTS ALONGSIDE MERMAID'S GANTT
 * Mermaid's gantt is the only temporal layout in the stack and it assumes a
 * project plan: day-granularity bias, no instants, no eras, no sub-day or
 * multi-century spans.  The question shapes this serves -- an incident
 * reconstruction in milliseconds, a geologic column in millions of years,
 * overlapping dynasties, a trace waterfall -- are all the same primitive
 * (lanes of intervals and instants against a real axis) at wildly different
 * scales, which is exactly what a gantt cannot express.
 *
 * TWO AXIS MODES, AND WHY THE SECOND IS STRUCTURALLY REQUIRED
 * A JavaScript Date spans only +/-8.64e15 ms, i.e. years -271821..275760
 * (verified).  4.5 Gya is therefore not merely awkward to represent, it is
 * `Invalid Date`.  So a timeline that claims to cover geologic time cannot use
 * a date axis for it, and `numeric` mode is a distinct kind rather than a
 * convenience: bare numbers on a linear axis with a unit label (Mya, ms since
 * T0, generation, revision).  `time` mode handles calendar dates, where month
 * lengths and leap years are irregular and d3's calendar-aware ticks earn
 * their keep.
 *
 * PARSING IS A WHITELIST, NOT A COERCION
 * Every convenient way to turn model output into a date or number has a
 * silent-wrongness mode, all verified against the running engine:
 *
 *   new Date('circa 1914')  -> 1914-01-01  (garbage ACCEPTED, not rejected)
 *   Date.parse('-500')      -> year  500   (BCE sign silently dropped)
 *   Date.parse('500')       -> 00:07:52Z   (local mean time offset appears)
 *   Date.UTC(50, 0, 1)      -> year 1950   (two-digit-year remapping)
 *   new Date('1914-07-28 00:00:00') -> shifts by the VIEWER's timezone
 *   Number('') / Number(null) / Number([]) -> 0  (missing lands at the origin)
 *
 * A validity check of "is the result NaN" catches none of the first four.  So
 * accepted forms are matched against explicit patterns and constructed through
 * UTC setters, and anything else is an error naming the value.  That rejects
 * 'July 28, 1914' -- a real loss -- because its parse depends on the reader's
 * timezone, which would make the same spec render differently on two machines.
 */
/**
 * THE d3 DEPENDENCY IS INJECTED, NOT IMPORTED.
 *
 * Calendar tick placement is the whole reason a real scale library is worth
 * using here -- month lengths and leap years are irregular, so hand-rolled
 * ticks are wrong at exactly the boundaries a historical axis lands on.  But it
 * cannot be reached with a static `import ... from 'd3'`: d3 v7 and every one of
 * its sub-packages ship as `"type": "module"` with `main: src/index.js`, and the
 * project's jest (react-scripts) does not transform node_modules, so any module
 * in a test's import graph that names 'd3' fails the whole suite with
 * "Unexpected token 'export'".  Verified: switching to `d3-scale` does not help,
 * because it is equally ESM-only, and no existing test imports d3 at all --
 * every other d3 consumer reaches it at RUNTIME (`await import('d3')` in
 * D3Renderer and the flamegraph wrapper), which is why this never surfaced
 * before.
 *
 * Injection follows that same precedent while keeping the engine synchronous and
 * unit-testable: `D3RenderPlugin.render` is already handed a resolved d3
 * instance, so the wrapper passes the three functions straight through, and a
 * test supplies them from d3's UMD build (`require('d3/dist/d3.js')`, CJS and
 * therefore parseable as-is).  Both paths get the REAL d3, not a stub, so the
 * asserted tick behaviour is the behaviour the browser gets.
 */

/** The d3 surface this engine uses; supplied by the caller. */
export interface TimelineD3 {
    scaleUtc: () => any;
    scaleLinear: () => any;
    utcFormat: (specifier: string) => (d: Date) => string;
}

// ---------------------------------------------------------------------------
// Geometry.  Changing one of these changes asserted test numbers; change both
// together or not at all.
// ---------------------------------------------------------------------------
const PAD = 10;              // outer margin
const TITLE_H = 24;          // reserved when a title is present
const ERA_STRIP_H = 20;      // era label strip above the lanes
const MARKER_STRIP_H = 15;   // marker label strip, below the era strip
const SUBROW_H = 22;         // one packed row inside a lane
const LANE_GAP = 12;         // vertical gap between lanes
const BAR_H = 14;            // interval bar height
const AXIS_H = 30;           // tick marks + tick labels
const INSTANT_R = 5;         // instant diamond half-diagonal
const LABEL_PAD = 6;         // gap between a mark and its outside label
const MIN_BAR_W = 2;         // a zero-length interval stays visible
const CHAR_W = 6.4;          // advance of 12px system sans, for label widths
const LANE_LABEL_MIN = 56;
const LANE_LABEL_MAX = 190;
const DEFAULT_WIDTH = 820;   // chat column width

/** Estimated rendered width of a label at the engine's 12px label size. */
export function labelWidth(text: string): number {
    return Math.ceil(text.length * CHAR_W);
}

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------
export interface TimelineTheme {
    axis: string;
    tick: string;
    text: string;
    muted: string;
    laneRule: string;
    barFill: string;
    barStroke: string;
    instantFill: string;
    eraFill: string;
    eraText: string;
    markerLine: string;
    surface: string;
}

export function timelineTheme(isDark: boolean): TimelineTheme {
    return isDark
        ? {
            axis: '#8b949e',
            tick: '#6e7681',
            text: '#e6edf3',
            muted: '#9198a1',
            laneRule: '#30363d',
            barFill: '#1f6feb',
            barStroke: '#58a6ff',
            instantFill: '#d29922',
            eraFill: '#8b949e',
            eraText: '#c9d1d9',
            markerLine: '#f85149',
            surface: '#0d1117',
        }
        : {
            axis: '#57606a',
            tick: '#8c959f',
            text: '#1f2328',
            muted: '#656d76',
            laneRule: '#d8dee4',
            barFill: '#54aeff',
            barStroke: '#0969da',
            instantFill: '#bf8700',
            eraFill: '#57606a',
            eraText: '#424a53',
            markerLine: '#cf222e',
            surface: '#ffffff',
        };
}

export function escapeXml(s: string): string {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * Colours reach SVG attributes, and the spec is model-authored, so they are
 * whitelisted rather than merely escaped.  Escaping alone would be safe, but a
 * malformed colour then lands in the attribute and the shape silently falls
 * back to the default fill -- indistinguishable from "the colour I asked for
 * was ignored".  Returning null lets the caller use the themed default
 * knowingly.
 */
export function safeColor(v: unknown): string | null {
    if (typeof v !== 'string') return null;
    const s = v.trim();
    if (/^#[0-9a-fA-F]{3,8}$/.test(s)) return s;
    if (/^rgba?\(\s*[\d.%\s,/]+\)$/.test(s)) return s;
    if (/^[a-zA-Z]{3,24}$/.test(s)) return s;   // CSS colour keyword
    return null;
}

// ---------------------------------------------------------------------------
// Lenient JSON, so a trailing comma or a stray fence is not a dead render.
// ---------------------------------------------------------------------------
function stripFence(text: string): string {
    // The fence delimiter is written as a character class rather than three
    // literal backticks so this source can itself be quoted inside a markdown
    // fence (a patch, a doc example) without terminating it early.
    const m = text.trim().match(/^[`]{3}[a-zA-Z0-9_-]*\s*\n([\s\S]*?)\n?[`]{3}\s*$/);
    return m ? m[1] : text;
}

/**
 * JSON.parse tolerant of the common model slips (trailing commas, comments,
 * smart quotes, stray fences).  Returns undefined rather than throwing so a
 * streaming caller can use it as an is-it-complete probe.
 */
export function lenientJsonParse(text: string): any | undefined {
    if (typeof text !== 'string') return undefined;
    const t = stripFence(text)
        .replace(/[\u201C\u201D]/g, '"')
        .replace(/[\u2018\u2019]/g, "'");
    try { return JSON.parse(t); } catch { /* fall through */ }
    let out = '';
    let inStr = false;
    for (let i = 0; i < t.length; i++) {
        const c = t[i];
        if (inStr) {
            out += c;
            if (c === '\\') { out += t[i + 1] ?? ''; i++; }
            else if (c === '"') inStr = false;
            continue;
        }
        if (c === '"') { inStr = true; out += c; continue; }
        if (c === '/' && t[i + 1] === '/') {
            while (i < t.length && t[i] !== '\n') i++;
            out += '\n';
            continue;
        }
        if (c === '/' && t[i + 1] === '*') {
            i += 2;
            while (i < t.length && !(t[i] === '*' && t[i + 1] === '/')) i++;
            i++;
            continue;
        }
        out += c;
    }
    out = out.replace(/,\s*([\]}])/g, '$1');
    try { return JSON.parse(out); } catch { return undefined; }
}

// ---------------------------------------------------------------------------
// Value parsing
// ---------------------------------------------------------------------------
export type AxisKind = 'time' | 'numeric';

export const TIMELINE_VOCAB =
    'title, scale, unit, direction, width, lanes, eras, items, markers';
export const ITEM_VOCAB = 'lane, label, start, end, at, color, note';

const NUM_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
const YEAR_RE = /^-?\d{1,6}$/;
const YM_RE = /^(-?\d{1,6})-(\d{2})$/;
const YMD_RE = /^(-?\d{1,6})-(\d{2})-(\d{2})$/;
// A datetime must be explicitly UTC (trailing Z) or carry an offset; a naked
// local datetime is refused because its meaning depends on the reader.
const DT_RE =
    /^(-?\d{1,6})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|[+-]\d{2}:?\d{2})$/;

const ACCEPTED_TIME_FORMS =
    'a year (1914 or "1914", "-500" for BCE), "YYYY-MM", "YYYY-MM-DD", or ' +
    '"YYYY-MM-DDTHH:MM[:SS[.mmm]]Z" (an explicit Z or +HH:MM offset is ' +
    'required, because a naked local time renders differently for each reader)';

/**
 * Build a UTC epoch-ms value from calendar parts without the two-digit-year
 * remapping.  Date.UTC(50, 0, 1) is 1950 (verified), so a year below 100 --
 * every date in antiquity -- would land nineteen centuries late.  Setting the
 * year through setUTCFullYear is the documented escape.
 */
export function utcFromParts(
    year: number, month = 1, day = 1,
    h = 0, min = 0, s = 0, ms = 0,
): number {
    const d = new Date(0);
    d.setUTCFullYear(year, month - 1, day);
    d.setUTCHours(h, min, s, ms);
    return d.getTime();
}

/**
 * Parse one spec value into a plottable number: epoch ms in `time` mode, the
 * bare number in `numeric` mode.  Throws with an actionable message rather
 * than returning NaN, because a NaN coordinate produces an SVG that renders
 * blank with no console error -- the worst available failure.
 */
export function parseValue(raw: unknown, kind: AxisKind, where: string): number {
    if (raw === null || raw === undefined || raw === '') {
        throw new Error(
            `${where}: missing value. Number()/Date() would turn this into 0 ` +
            `or the epoch and place the item at the origin, so it is refused.`,
        );
    }

    if (kind === 'numeric') {
        if (typeof raw === 'number') {
            if (!Number.isFinite(raw)) {
                throw new Error(`${where}: ${raw} is not a finite number`);
            }
            return raw;
        }
        if (typeof raw === 'string' && NUM_RE.test(raw.trim())) {
            return Number(raw.trim());
        }
        throw new Error(
            `${where}: ${JSON.stringify(raw)} is not a number. This timeline ` +
            `is on a numeric axis; use a number, or set "scale": "time" and ` +
            `supply dates.`,
        );
    }

    // time mode
    if (typeof raw === 'number') {
        if (!Number.isInteger(raw) || Math.abs(raw) > 999999) {
            throw new Error(
                `${where}: the bare number ${raw} is read as a YEAR on a date ` +
                `axis, and ${raw} is not a plausible year. Use ${ACCEPTED_TIME_FORMS}.`,
            );
        }
        return utcFromParts(raw);
    }
    if (typeof raw !== 'string') {
        throw new Error(
            `${where}: ${JSON.stringify(raw)} is not a date. Use ${ACCEPTED_TIME_FORMS}.`,
        );
    }
    const s = raw.trim();

    if (YEAR_RE.test(s)) return utcFromParts(parseInt(s, 10));

    const ym = s.match(YM_RE);
    if (ym) return utcFromParts(parseInt(ym[1], 10), Number(ym[2]));

    const ymd = s.match(YMD_RE);
    if (ymd) {
        return utcFromParts(parseInt(ymd[1], 10), Number(ymd[2]), Number(ymd[3]));
    }

    const dt = s.match(DT_RE);
    if (dt) {
        const base = utcFromParts(
            parseInt(dt[1], 10), Number(dt[2]), Number(dt[3]),
            Number(dt[4]), Number(dt[5]), Number(dt[6] ?? 0),
            Number((dt[7] ?? '').padEnd(3, '0') || 0),
        );
        const off = dt[8];
        if (off === 'Z') return base;
        const m = off.replace(':', '');
        const sign = m[0] === '-' ? -1 : 1;
        const mins = Number(m.slice(1, 3)) * 60 + Number(m.slice(3, 5));
        return base - sign * mins * 60000;
    }

    throw new Error(
        `${where}: ${JSON.stringify(raw)} is not an accepted date. ` +
        `new Date() would accept some of these and silently guess ` +
        `(new Date("circa 1914") is 1914-01-01), so only explicit forms are ` +
        `taken: ${ACCEPTED_TIME_FORMS}.`,
    );
}

/** Every raw value in the spec, so the axis kind can be inferred from all of them. */
function collectRawValues(raw: any): unknown[] {
    const out: unknown[] = [];
    const push = (v: unknown) => { if (v !== undefined && v !== null && v !== '') out.push(v); };
    for (const it of Array.isArray(raw?.items) ? raw.items : []) {
        push(it?.start); push(it?.end); push(it?.at);
    }
    for (const e of Array.isArray(raw?.eras) ? raw.eras : []) {
        push(e?.start); push(e?.end);
    }
    for (const m of Array.isArray(raw?.markers) ? raw.markers : []) {
        push(m?.at); push(m?.start);
    }
    return out;
}

/**
 * Choose the axis kind.
 *
 * A value that is a bare number could be either a year or a quantity, so the
 * presence of ANY unambiguous date form decides for the whole timeline and
 * bare numbers in that spec are then read as years.  That resolves the mixed
 * spec (`start: 1914, end: "1918-11-11"`) into the reading a human intends,
 * instead of erroring or -- worse -- picking numeric mode and turning the
 * date string into NaN.
 */
export function detectAxisKind(raw: any): AxisKind {
    const explicit = typeof raw?.scale === 'string' ? raw.scale.trim().toLowerCase() : '';
    if (explicit === 'time' || explicit === 'temporal' || explicit === 'date') return 'time';
    if (explicit === 'numeric' || explicit === 'linear' || explicit === 'number') return 'numeric';
    if (explicit && explicit !== 'auto') {
        throw new Error(
            `"scale": ${JSON.stringify(raw.scale)} is not recognised; use ` +
            `"time", "numeric", or omit it for auto-detection.`,
        );
    }
    for (const v of collectRawValues(raw)) {
        if (typeof v === 'string' && !NUM_RE.test(v.trim())) return 'time';
    }
    return 'numeric';
}

// ---------------------------------------------------------------------------
// Normalized model
// ---------------------------------------------------------------------------
export interface NormalizedItem {
    lane: string;
    label: string;
    start: number;
    /** null marks an instant rather than an interval. */
    end: number | null;
    color: string | null;
    note: string | null;
}
export interface NormalizedEra {
    label: string;
    start: number;
    end: number;
    color: string | null;
}
export interface NormalizedMarker {
    label: string;
    at: number;
    color: string | null;
}
export interface NormalizedTimeline {
    title: string | null;
    kind: AxisKind;
    unit: string | null;
    /** True when the axis runs high-to-low, as any "ago" unit does. */
    descending: boolean;
    width: number;
    lanes: string[];
    items: NormalizedItem[];
    eras: NormalizedEra[];
    markers: NormalizedMarker[];
}

export type Direction = 'ascending' | 'descending';

/**
 * Read an explicit axis direction, or null for auto-detection.
 *
 * This field exists because "Mya" -- millions of years AGO -- counts DOWN, so
 * the idiomatic way to write a geologic era is start 4567, end 4031. An engine
 * that treats end < start as an error refuses the natural spelling of the
 * single use case a numeric axis was added for.
 */
export function parseDirection(v: unknown): Direction | null {
    if (v === undefined || v === null || v === '') return null;
    if (typeof v !== 'string') {
        throw new Error(`"direction": ${JSON.stringify(v)} must be a string.`);
    }
    const s = v.trim().toLowerCase();
    if (s === 'ascending' || s === 'asc' || s === 'normal' || s === 'forward') {
        return 'ascending';
    }
    if (s === 'descending' || s === 'desc' || s === 'reverse' || s === 'ago') {
        return 'descending';
    }
    throw new Error(
        `"direction": ${JSON.stringify(v)} is not recognised; use ` +
        `"ascending" or "descending" (an "ago" axis such as Mya is descending).`,
    );
}

function checkKeys(obj: any, allowed: string, where: string): void {
    const ok = new Set(allowed.split(',').map(s => s.trim()));
    for (const k of Object.keys(obj || {})) {
        if (!ok.has(k)) {
            throw new Error(
                `${where}: unknown key ${JSON.stringify(k)}. Accepted keys are: ${allowed}.`,
            );
        }
    }
}

export function normalizeTimelineSpec(raw: any): NormalizedTimeline {
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new Error(
            `a timeline spec must be a JSON object with an "items" array; got ` +
            `${Array.isArray(raw) ? 'an array' : typeof raw}.`,
        );
    }
    checkKeys(raw, TIMELINE_VOCAB, 'timeline');

    const kind = detectAxisKind(raw);
    const rawItems = Array.isArray(raw.items) ? raw.items : [];
    if (rawItems.length === 0) {
        throw new Error('a timeline needs at least one entry in "items".');
    }

    const items: NormalizedItem[] = rawItems.map((it: any, i: number) => {
        const where = `items[${i}]`;
        if (it === null || typeof it !== 'object' || Array.isArray(it)) {
            throw new Error(`${where}: each item must be an object with ${ITEM_VOCAB}.`);
        }
        checkKeys(it, ITEM_VOCAB, where);

        const hasAt = it.at !== undefined && it.at !== null && it.at !== '';
        const hasStart = it.start !== undefined && it.start !== null && it.start !== '';
        if (hasAt && hasStart) {
            throw new Error(
                `${where}: has both "at" and "start". "at" marks an instant and ` +
                `"start" opens an interval; pick one.`,
            );
        }
        if (!hasAt && !hasStart) {
            throw new Error(`${where}: needs "start" (interval) or "at" (instant).`);
        }

        const start = parseValue(hasAt ? it.at : it.start, kind,
                                `${where}.${hasAt ? 'at' : 'start'}`);
        let end: number | null = null;
        if (!hasAt && it.end !== undefined && it.end !== null && it.end !== '') {
            end = parseValue(it.end, kind, `${where}.end`);
        }

        return {
            lane: typeof it.lane === 'string' ? it.lane : '',
            label: typeof it.label === 'string' ? it.label : '',
            start,
            end,
            color: safeColor(it.color),
            note: typeof it.note === 'string' && it.note ? it.note : null,
        };
    });

    // ---- axis direction, and the interval-order check that depends on it ---
    //
    // Whether `end < start` is a mistake is not decidable per item: on a
    // descending "ago" axis it is the idiomatic spelling (Hadean is 4567 -> 4031
    // Mya), and on an ascending axis it is a swap. So the decision is made from
    // the WHOLE set, and only the genuinely ambiguous readings are refused:
    //
    //   every interval reversed  -> a descending axis, accepted
    //   some reversed, some not  -> one of them is wrong; refuse and name them
    //   exactly one, unaided     -> indistinguishable from a typo; ask
    //
    // Note this is not about rendering: packLane orders pixels, so a reversed
    // interval draws correctly either way. It is about not silently redrawing
    // an author's mistake as though it were intended.
    const explicitDir = parseDirection(raw.direction);
    const reversed: number[] = [];
    let forward = 0;
    items.forEach((p, i) => {
        if (p.end === null) return;
        if (p.end < p.start) reversed.push(i);
        else if (p.end > p.start) forward++;
    });

    const nameThem = (idx: number[]) =>
        idx.map(i => `items[${i}]${items[i].label ? ` (${items[i].label})` : ''}`).join(', ');

    let descending: boolean;
    if (explicitDir === 'descending') {
        descending = true;
    } else if (explicitDir === 'ascending') {
        descending = false;
        if (reversed.length) {
            throw new Error(
                `"direction" is "ascending" but ${nameThem(reversed)} has "end" ` +
                `before "start". Swap those, or drop the explicit direction.`,
            );
        }
    } else if (reversed.length && forward) {
        throw new Error(
            `${nameThem(reversed)} has "end" before "start" while other ` +
            `intervals run forward, so the axis direction is contradictory and ` +
            `one of them must be wrong. Swap the reversed ones, or set ` +
            `"direction": "descending" if the axis really counts down (e.g. Mya).`,
        );
    } else if (reversed.length === 1) {
        throw new Error(
            `${nameThem(reversed)} has "end" before "start", and it is the only ` +
            `interval, so a swapped pair and a deliberately descending axis are ` +
            `indistinguishable. Swap them, or set "direction": "descending".`,
        );
    } else {
        descending = reversed.length >= 2;
    }

    const eras: NormalizedEra[] = (Array.isArray(raw.eras) ? raw.eras : [])
        .map((e: any, i: number) => {
            const where = `eras[${i}]`;
            checkKeys(e, 'label, start, end, color', where);
            const start = parseValue(e?.start, kind, `${where}.start`);
            const end = parseValue(e?.end, kind, `${where}.end`);
            return {
                label: typeof e?.label === 'string' ? e.label : '',
                start: Math.min(start, end),
                end: Math.max(start, end),
                color: safeColor(e?.color),
            };
        });

    const markers: NormalizedMarker[] = (Array.isArray(raw.markers) ? raw.markers : [])
        .map((m: any, i: number) => {
            const where = `markers[${i}]`;
            checkKeys(m, 'label, at, color', where);
            return {
                label: typeof m?.label === 'string' ? m.label : '',
                at: parseValue(m?.at, kind, `${where}.at`),
                color: safeColor(m?.color),
            };
        });

    // Declared order wins; lanes seen only in items are APPENDED rather than
    // dropped, because dropping them would lose data with nothing to show for
    // it -- the item would be absent from a diagram that reported no problem.
    const declared: string[] = Array.isArray(raw.lanes)
        ? raw.lanes.filter((l: any) => typeof l === 'string')
        : [];
    const lanes = [...declared];
    for (const it of items) if (!lanes.includes(it.lane)) lanes.push(it.lane);

    let width = DEFAULT_WIDTH;
    if (typeof raw.width === 'number' && Number.isFinite(raw.width)) {
        width = Math.max(320, Math.min(3000, Math.round(raw.width)));
    }

    return {
        title: typeof raw.title === 'string' && raw.title ? raw.title : null,
        kind,
        unit: typeof raw.unit === 'string' && raw.unit ? raw.unit : null,
        descending,
        width,
        lanes,
        items,
        eras,
        markers,
    };
}

// ---------------------------------------------------------------------------
// Packing
// ---------------------------------------------------------------------------
export interface PlacedItem {
    item: NormalizedItem;
    row: number;
    x1: number;
    x2: number;
    /** Right edge including an outside label, i.e. the space actually consumed. */
    extent: number;
    labelInside: boolean;
    labelX: number;
    labelAnchor: 'start' | 'middle' | 'end';
}

/**
 * Greedy first-fit packing of one lane's marks into sub-rows.
 *
 * Two things here are load-bearing and were both wrong in the first draft:
 *
 * 1. GEOMETRY IS COMPUTED BEFORE SORTING, AND THE SORT IS ON PIXELS.  Sorting
 *    by `start` looks equivalent but is not on a descending axis, where value
 *    order is the reverse of pixel order: first-fit then walks right-to-left,
 *    every mark collides with the one before it, and a contiguous geologic
 *    column comes out as a four-row staircase instead of one row.  It renders
 *    without error, which is exactly why it needs a test.
 *
 * 2. THE OCCUPIED EXTENT INCLUDES AN OUTSIDE LABEL.  A two-day event on a
 *    decade-long axis is a sub-pixel bar with a 90px label beside it, so bars
 *    that never touch can produce labels that overlap completely.
 *
 * Fitting is strict non-overlap with no cosmetic gap, because abutting
 * intervals (one era ending where the next begins) are genuinely contiguous
 * and must share a row; a minimum gap would stack them.
 */
export function packLane(
    items: NormalizedItem[],
    xOf: (v: number) => number,
    plotRight: number,
): PlacedItem[] {
    const prepared = items.map(item => {
        const isInstant = item.end === null;
        const rawX1 = xOf(item.start);
        const rawX2 = isInstant ? rawX1 : xOf(item.end as number);
        // A reversed AXIS (numeric, high-to-low) maps start right of end, so
        // order the pixels rather than assuming the value order survived.
        let x1 = Math.min(rawX1, rawX2);
        let x2 = Math.max(rawX1, rawX2);
        if (isInstant) {
            x1 -= INSTANT_R;
            x2 += INSTANT_R;
        } else if (x2 - x1 < MIN_BAR_W) {
            x2 = x1 + MIN_BAR_W;
        }

        const lw = item.label ? labelWidth(item.label) : 0;
        const barW = x2 - x1;
        const labelInside = !isInstant && lw > 0 && barW >= lw + 2 * LABEL_PAD;

        let extent = x2;
        let labelX = x1 + LABEL_PAD;
        let labelAnchor: 'start' | 'middle' | 'end' = 'start';
        if (labelInside) {
            labelX = x1 + barW / 2;
            labelAnchor = 'middle';
        } else if (lw > 0) {
            // Outside label: to the right, unless that would run past the plot,
            // in which case flip it to the left so it is not clipped away.
            if (x2 + LABEL_PAD + lw <= plotRight) {
                labelX = x2 + LABEL_PAD;
                labelAnchor = 'start';
                extent = x2 + LABEL_PAD + lw;
            } else {
                labelX = x1 - LABEL_PAD;
                labelAnchor = 'end';
                extent = x2;
            }
        }
        return { item, x1, x2, extent, labelInside, labelX, labelAnchor };
    });

    prepared.sort((a, b) => a.x1 - b.x1);

    const rowEnds: number[] = [];
    const placed: PlacedItem[] = [];
    for (const p of prepared) {
        let row = 0;
        // 0.01 absorbs float noise so an exactly-abutting bar is not judged to
        // overlap by a rounding error.
        while (row < rowEnds.length && rowEnds[row] > p.x1 + 0.01) row++;
        rowEnds[row] = p.extent;
        placed.push({ ...p, row });
    }
    return placed;
}

// ---------------------------------------------------------------------------
// Axis
// ---------------------------------------------------------------------------
/**
 * Tick label format chosen from the visible SPAN.
 *
 * d3's default multi-scale tickFormat varies the format per tick, which reads
 * oddly on a timeline (a 2-second span labels its first tick '2026' and the
 * next '.500').  One format for the whole axis is the predictable choice.
 */
export function timeTickFormat(
    spanMs: number,
    utcFormat: TimelineD3['utcFormat'],
): (d: Date) => string {
    const DAY = 86400000;
    /**
     * Format, then repair the YEAR token.
     *
     * Two defects are fixed in one place because both are the year: %Y
     * zero-pads to four digits, so a CE year under 1000 reads '0100'; and an
     * astronomical year <= 0 is a BCE year (year 0 is 1 BCE), which a
     * historical axis should spell out rather than render as a negative
     * number.  Substituting whatever %Y actually produced -- rather than
     * short-circuiting the whole format -- keeps the month and day intact, so
     * a month-granularity BCE axis still reads 'Mar 501 BCE'.
     */
    const withYear = (fmt: string) => (d: Date) => {
        const s = utcFormat(fmt)(d);
        if (!fmt.includes('%Y')) return s;
        const y = d.getUTCFullYear();
        const produced = utcFormat('%Y')(d);
        const label = y <= 0 ? `${1 - y} BCE` : String(y);
        return s.replace(produced, label);
    };
    if (spanMs > 3 * 365 * DAY) return withYear('%Y');
    if (spanMs > 90 * DAY) return withYear('%b %Y');
    if (spanMs > 3 * DAY) return withYear('%d %b');
    if (spanMs > 6 * 3600000) return withYear('%d %b %H:%M');
    if (spanMs > 3 * 60000) return withYear('%H:%M');
    if (spanMs > 3000) return withYear('%H:%M:%S');
    return withYear('%H:%M:%S.%L');
}

/** Trim float noise from linear ticks (0.30000000000000004). */
export function numericTickFormat(unit: string | null): (v: number) => string {
    return (v: number) => {
        const s = Math.abs(v) >= 1e6 || (v !== 0 && Math.abs(v) < 1e-3)
            ? v.toExponential(2)
            : String(Math.round(v * 1e6) / 1e6);
        return unit ? `${s} ${unit}` : s;
    };
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
export interface RenderedTimeline {
    svg: string;
    title: string | null;
    kind: AxisKind;
    descending: boolean;
    width: number;
    height: number;
    laneCount: number;
    itemCount: number;
}

export function renderTimelineSvg(
    definition: string | object,
    isDark: boolean,
    d3: TimelineD3,
): RenderedTimeline {
    if (!d3 || typeof d3.scaleUtc !== 'function' || typeof d3.scaleLinear !== 'function'
        || typeof d3.utcFormat !== 'function') {
        // Named explicitly rather than left to blow up as "scaleUtc is not a
        // function" deep in axis construction, because the caller that forgot
        // the argument is several frames away by then.
        throw new Error(
            'internal: renderTimelineSvg needs a d3 instance providing ' +
            'scaleUtc, scaleLinear and utcFormat.',
        );
    }
    const raw = typeof definition === 'string'
        ? lenientJsonParse(definition)
        : definition;
    if (raw === undefined) {
        throw new Error('could not parse the timeline spec as JSON.');
    }
    const spec = normalizeTimelineSpec(raw);
    const th = timelineTheme(isDark);

    // ---- domain over everything that occupies the axis ---------------------
    let lo = Infinity;
    let hi = -Infinity;
    const see = (v: number) => { if (v < lo) lo = v; if (v > hi) hi = v; };
    for (const it of spec.items) { see(it.start); if (it.end !== null) see(it.end); }
    for (const e of spec.eras) { see(e.start); see(e.end); }
    for (const m of spec.markers) see(m.at);

    // A single instant, or several at one value, gives a zero-width domain: the
    // scale then maps every mark to one pixel and the labels stack into an
    // unreadable pile. Pad to a real span instead.
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
        throw new Error('no plottable values found in the timeline.');
    }
    if (hi === lo) {
        const pad = spec.kind === 'time' ? 86400000 : (Math.abs(lo) || 1) * 0.5;
        lo -= pad;
        hi += pad;
    } else {
        const pad = (hi - lo) * 0.02;
        lo -= pad;
        hi += pad;
    }

    // ---- horizontal frame -------------------------------------------------
    const laneLabelW = spec.lanes.some(l => l)
        ? Math.max(LANE_LABEL_MIN, Math.min(LANE_LABEL_MAX,
            Math.max(...spec.lanes.map(l => labelWidth(l))) + 14))
        : 0;
    const plotLeft = PAD + laneLabelW;
    const plotRight = spec.width - PAD;
    const plotW = Math.max(80, plotRight - plotLeft);

    // A descending axis is expressed by inverting the DOMAIN rather than the
    // range, so ticks come back in reading order (4567 first) and every
    // consumer of xOf stays direction-agnostic.
    const d0 = spec.descending ? hi : lo;
    const d1 = spec.descending ? lo : hi;
    const scale = spec.kind === 'time'
        ? d3.scaleUtc().domain([new Date(d0), new Date(d1)]).range([plotLeft, plotLeft + plotW])
        : d3.scaleLinear().domain([d0, d1]).range([plotLeft, plotLeft + plotW]);
    const xOf = (v: number): number => {
        const px = spec.kind === 'time'
            ? (scale as any)(new Date(v))
            : (scale as any)(v);
        // A NaN coordinate yields an SVG that renders blank with no error, so
        // it is converted into a thrown message instead of being emitted.
        if (!Number.isFinite(px)) {
            throw new Error(`internal: value ${v} mapped to a non-finite coordinate`);
        }
        return px;
    };

    // ---- pack each lane ---------------------------------------------------
    const byLane = new Map<string, NormalizedItem[]>();
    for (const l of spec.lanes) byLane.set(l, []);
    for (const it of spec.items) byLane.get(it.lane)!.push(it);

    const packedByLane = spec.lanes.map(l =>
        packLane(byLane.get(l) || [], xOf, plotRight));
    const rowsByLane = packedByLane.map(p =>
        Math.max(1, p.reduce((m, x) => Math.max(m, x.row + 1), 0)));

    // ---- vertical frame ---------------------------------------------------
    const titleH = spec.title ? TITLE_H : 0;
    const eraH = spec.eras.length ? ERA_STRIP_H : 0;
    // Marker labels need their OWN reserved strip rather than being drawn at
    // the top of the plot. Without it a marker label lands at the same height
    // as the first lane's row and collides with any bar that happens to sit
    // near that value -- invisible in a sample where the first lane is empty
    // there, which is exactly how it passed a visual check.
    const markerH = spec.markers.some(m => m.label) ? MARKER_STRIP_H : 0;
    const stripTop = PAD + titleH;
    const plotTop = stripTop + eraH + markerH;
    const laneTops: number[] = [];
    let y = plotTop;
    for (const rows of rowsByLane) {
        laneTops.push(y);
        y += rows * SUBROW_H + LANE_GAP;
    }
    const plotBottom = y - LANE_GAP;
    const axisY = plotBottom + 8;
    const height = axisY + AXIS_H + PAD;

    // ---- emit -------------------------------------------------------------
    const out: string[] = [];
    out.push(
        `<svg xmlns="http://www.w3.org/2000/svg" width="${spec.width}" ` +
        `height="${height}" viewBox="0 0 ${spec.width} ${height}" ` +
        `font-family="system-ui, -apple-system, sans-serif">`,
    );

    if (spec.title) {
        out.push(
            `<text x="${PAD}" y="${PAD + 15}" font-size="15" font-weight="600" ` +
            `fill="${th.text}">${escapeXml(spec.title)}</text>`,
        );
    }

    // Era bands behind everything.
    for (const e of spec.eras) {
        const a = xOf(e.start);
        const b = xOf(e.end);
        const x1 = Math.min(a, b);
        const w = Math.max(1, Math.abs(b - a));
        const fill = e.color || th.eraFill;
        out.push(
            `<rect x="${r(x1)}" y="${stripTop}" width="${r(w)}" ` +
            `height="${r(plotBottom - stripTop)}" fill="${escapeXml(fill)}" ` +
            `fill-opacity="${isDark ? 0.14 : 0.09}"/>`,
        );
        // Leading edge rule. Consecutive eras abut, and at band opacity two
        // adjacent bands in the same colour merge into one block distinguishable
        // only by their labels -- so the boundary has to be drawn, not implied.
        out.push(
            `<line class="era-edge" x1="${r(x1)}" y1="${stripTop}" x2="${r(x1)}" ` +
            `y2="${r(plotBottom)}" stroke="${escapeXml(fill)}" stroke-width="1" ` +
            `stroke-opacity="${isDark ? 0.55 : 0.4}"/>`,
        );
        if (e.label) {
            out.push(
                `<text x="${r(x1 + 4)}" y="${stripTop + 14}" font-size="11" ` +
                `fill="${th.eraText}">${escapeXml(e.label)}</text>`,
            );
        }
    }

    // Lane labels, baselines and marks.
    spec.lanes.forEach((lane, li) => {
        const top = laneTops[li];
        if (lane) {
            out.push(
                `<text x="${PAD}" y="${r(top + 15)}" font-size="12" font-weight="500" ` +
                `fill="${th.muted}">${escapeXml(lane)}</text>`,
            );
        }
        if (li > 0) {
            out.push(
                `<line x1="${PAD}" y1="${r(top - LANE_GAP / 2)}" x2="${plotRight}" ` +
                `y2="${r(top - LANE_GAP / 2)}" stroke="${th.laneRule}" stroke-width="1"/>`,
            );
        }

        for (const p of packedByLane[li]) {
            const rowY = top + p.row * SUBROW_H;
            const midY = rowY + SUBROW_H / 2;
            const note = p.item.note
                ? `<title>${escapeXml(p.item.note)}</title>`
                : '';

            if (p.item.end === null) {
                const cx = (p.x1 + p.x2) / 2;
                const fill = p.item.color || th.instantFill;
                out.push(
                    `<polygon points="${r(cx)},${r(midY - INSTANT_R)} ` +
                    `${r(cx + INSTANT_R)},${r(midY)} ${r(cx)},${r(midY + INSTANT_R)} ` +
                    `${r(cx - INSTANT_R)},${r(midY)}" fill="${escapeXml(fill)}" ` +
                    `stroke="${escapeXml(fill)}">${note}</polygon>`,
                );
            } else {
                const fill = p.item.color || th.barFill;
                out.push(
                    `<rect x="${r(p.x1)}" y="${r(midY - BAR_H / 2)}" ` +
                    `width="${r(p.x2 - p.x1)}" height="${BAR_H}" rx="3" ` +
                    `fill="${escapeXml(fill)}" fill-opacity="${isDark ? 0.55 : 0.75}" ` +
                    `stroke="${escapeXml(p.item.color || th.barStroke)}" ` +
                    `stroke-width="1">${note}</rect>`,
                );
            }

            if (p.item.label) {
                out.push(
                    `<text x="${r(p.labelX)}" y="${r(midY + 4)}" font-size="12" ` +
                    `text-anchor="${p.labelAnchor}" fill="${th.text}">` +
                    `${escapeXml(p.item.label)}</text>`,
                );
            }
        }
    });

    // Markers on top of the marks, under the axis.
    for (const m of spec.markers) {
        const x = xOf(m.at);
        const stroke = m.color || th.markerLine;
        out.push(
            `<line x1="${r(x)}" y1="${r(stripTop + eraH)}" x2="${r(x)}" ` +
            `y2="${r(plotBottom)}" stroke="${escapeXml(stroke)}" stroke-width="1" ` +
            `stroke-dasharray="4 3"/>`,
        );
        if (m.label) {
            const lw = labelWidth(m.label);
            const flip = x + 4 + lw > plotRight;
            out.push(
                `<text x="${r(flip ? x - 4 : x + 4)}" y="${r(stripTop + eraH + 11)}" ` +
                `font-size="11" text-anchor="${flip ? 'end' : 'start'}" ` +
                `fill="${escapeXml(stroke)}">${escapeXml(m.label)}</text>`,
            );
        }
    }

    // Axis line, ticks, labels.
    out.push(
        `<line x1="${plotLeft}" y1="${r(axisY)}" x2="${r(plotLeft + plotW)}" ` +
        `y2="${r(axisY)}" stroke="${th.axis}" stroke-width="1"/>`,
    );
    const tickCount = Math.max(2, Math.min(10, Math.floor(plotW / 90)));
    if (spec.kind === 'time') {
        const ticks = (scale as any).ticks(tickCount) as Date[];
        const fmt = timeTickFormat(hi - lo, d3.utcFormat);
        for (const t of ticks) {
            const x = xOf(t.getTime());
            out.push(tick(x, axisY, fmt(t), th));
        }
    } else {
        const ticks = (scale as any).ticks(tickCount) as number[];
        const fmt = numericTickFormat(spec.unit);
        for (const t of ticks) out.push(tick(xOf(t), axisY, fmt(t), th));
    }

    out.push('</svg>');

    return {
        svg: out.join('\n'),
        title: spec.title,
        kind: spec.kind,
        descending: spec.descending,
        width: spec.width,
        height,
        laneCount: spec.lanes.length,
        itemCount: spec.items.length,
    };
}

function tick(x: number, axisY: number, label: string, th: TimelineTheme): string {
    return (
        `<line x1="${r(x)}" y1="${r(axisY)}" x2="${r(x)}" y2="${r(axisY + 5)}" ` +
        `stroke="${th.tick}" stroke-width="1"/>` +
        `<text x="${r(x)}" y="${r(axisY + 18)}" font-size="11" ` +
        `text-anchor="middle" fill="${th.muted}">${escapeXml(label)}</text>`
    );
}

/** Round to 2dp so the SVG stays compact and diffable. */
function r(n: number): number {
    return Math.round(n * 100) / 100;
}
