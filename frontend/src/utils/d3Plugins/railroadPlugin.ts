/**
 * Railroad (syntax) diagram layout engine.
 *
 * A pure spec -> SVG-string transformer: no DOM, no D3, no external
 * dependencies, so the whole box model is unit-testable in Node.  The
 * geometry (box model, arc plumbing, Choice/OneOrMore layout math) is
 * transcribed from Tab Atkins' railroad-diagrams
 * (https://github.com/tabatkins/railroad-diagrams, CC0-1.0) rather than
 * imported: the npm package is a single 2015-era 1.0.0 global-style script
 * with no ESM build and no types, and transcribing the subset we expose
 * also buys dark-mode theming and inline-attribute styling so a saved SVG
 * is self-contained.
 *
 * The plugin wrapper (plugins/d3/railroadPlugin.ts) owns mounting, error
 * cards, and rule headings; everything here is deterministic layout.
 */

// ---------------------------------------------------------------------------
// Box-model constants, verbatim from the reference implementation.  The unit
// tests assert geometry derived from these; change both sides together or
// not at all.
// ---------------------------------------------------------------------------
const AR = 10;              // ARC_RADIUS
const VS = 8;               // VERTICAL_SEPARATION
const CHAR_W = 8.5;         // CHAR_WIDTH: monospace advance at font-size 14
const COMMENT_CHAR_W = 7;   // COMMENT_CHAR_WIDTH: italic text runs narrower

// ---------------------------------------------------------------------------
// Theme and low-level SVG emission.
// ---------------------------------------------------------------------------

export interface RailroadTheme {
    stroke: string;
    text: string;
    comment: string;
    group: string;
    terminalFill: string;
    nonterminalFill: string;
}

export function railroadTheme(isDark: boolean): RailroadTheme {
    return isDark
        ? {
            stroke: '#8b949e',
            text: '#e6edf3',
            comment: '#9198a1',
            group: '#6e7681',
            terminalFill: '#21262d',
            nonterminalFill: '#161b22',
        }
        : {
            stroke: '#555555',
            text: '#141414',
            comment: '#6a737d',
            group: '#999999',
            terminalFill: '#eef2f6',
            nonterminalFill: '#ffffff',
        };
}

export function escapeXml(s: string): string {
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** Collects SVG fragments; owns all theming so nodes stay geometry-only. */
class Emit {
    readonly parts: string[] = [];
    constructor(readonly theme: RailroadTheme) {}

    path(d: string): void {
        this.parts.push(
            `<path d="${d}" fill="none" stroke="${this.theme.stroke}" ` +
            `stroke-width="2"/>`);
    }

    box(x: number, y: number, w: number, h: number, rx: number,
        kind: 'terminal' | 'nonterminal' | 'group'): void {
        if (kind === 'group') {
            this.parts.push(
                `<rect x="${x}" y="${y}" width="${w}" height="${h}" ` +
                `rx="${rx}" ry="${rx}" fill="none" ` +
                `stroke="${this.theme.group}" stroke-width="1" ` +
                `stroke-dasharray="4 4"/>`);
            return;
        }
        const fill = kind === 'terminal'
            ? this.theme.terminalFill : this.theme.nonterminalFill;
        this.parts.push(
            `<rect x="${x}" y="${y}" width="${w}" height="${h}" ` +
            `rx="${rx}" ry="${rx}" fill="${fill}" ` +
            `stroke="${this.theme.stroke}" stroke-width="2"/>`);
    }

    text(s: string, x: number, y: number,
         kind: 'terminal' | 'nonterminal' | 'comment'): void {
        const italic = kind === 'comment' ? ' font-style="italic"' : '';
        const fill = kind === 'comment' ? this.theme.comment : this.theme.text;
        this.parts.push(
            `<text x="${x}" y="${y}" text-anchor="middle" ` +
            `font-family="monospace" font-size="14" fill="${fill}"${italic}>` +
            `${escapeXml(s)}</text>`);
    }
}

/**
 * Chainable SVG path builder — the reference implementation's Path, with
 * the same right/left/up/down clamping and quarter-arc vocabulary.
 */
class P {
    private d: string;
    constructor(x: number, y: number) {
        this.d = `M ${x} ${y}`;
    }
    m(dx: number, dy: number): P { this.d += ` m ${dx} ${dy}`; return this; }
    h(val: number): P { this.d += ` h ${val}`; return this; }
    right(val: number): P { return this.h(Math.max(0, val)); }
    left(val: number): P { return this.h(-Math.max(0, val)); }
    v(val: number): P { this.d += ` v ${val}`; return this; }
    down(val: number): P { return this.v(Math.max(0, val)); }
    up(val: number): P { return this.v(-Math.max(0, val)); }
    /**
     * Quarter-circle arc.  The two-letter compass code names the entry and
     * exit directions (e.g. 'ne': travelling north, leave heading east) —
     * verbatim from the reference implementation.
     */
    arc(sweep: string): P {
        let x = AR;
        let y = AR;
        if (sweep[0] === 'e' || sweep[1] === 'w') x *= -1;
        if (sweep[0] === 's' || sweep[1] === 'n') y *= -1;
        const cw = (sweep === 'ne' || sweep === 'es'
            || sweep === 'sw' || sweep === 'wn') ? 1 : 0;
        this.d += ` a ${AR} ${AR} 0 0 ${cw} ${x} ${y}`;
        return this;
    }
    addTo(out: Emit): void { out.path(this.d); }
}

/** Split surplus width evenly (the reference's centre alignment). */
function determineGaps(outer: number, inner: number): [number, number] {
    const diff = outer - inner;
    return [diff / 2, diff / 2];
}

// ---------------------------------------------------------------------------
// Layout nodes.  Each computes its own box (width / height / up / down) at
// construction and draws itself into an Emit in format().
// ---------------------------------------------------------------------------

export abstract class RRNode {
    width = 0;
    /** Vertical drop from entry line to exit line (0 for all current nodes). */
    height = 0;
    up = 0;
    down = 0;
    needsSpace = false;
    abstract format(x: number, y: number, width: number, out: Emit): void;
}

export class RTerminal extends RRNode {
    constructor(readonly label: string) {
        super();
        this.width = label.length * CHAR_W + 20;
        this.up = 11;
        this.down = 11;
        this.needsSpace = true;
    }
    format(x: number, y: number, width: number, out: Emit): void {
        const gaps = determineGaps(width, this.width);
        new P(x, y).h(gaps[0]).addTo(out);
        new P(x + gaps[0] + this.width, y).h(gaps[1]).addTo(out);
        out.box(x + gaps[0], y - this.up, this.width, this.up + this.down,
            10, 'terminal');
        out.text(this.label, x + gaps[0] + this.width / 2, y + 4, 'terminal');
    }
}

export class RNonTerminal extends RRNode {
    constructor(readonly label: string) {
        super();
        this.width = label.length * CHAR_W + 20;
        this.up = 11;
        this.down = 11;
        this.needsSpace = true;
    }
    format(x: number, y: number, width: number, out: Emit): void {
        const gaps = determineGaps(width, this.width);
        new P(x, y).h(gaps[0]).addTo(out);
        new P(x + gaps[0] + this.width, y).h(gaps[1]).addTo(out);
        out.box(x + gaps[0], y - this.up, this.width, this.up + this.down,
            0, 'nonterminal');
        out.text(this.label, x + gaps[0] + this.width / 2, y + 4,
            'nonterminal');
    }
}

export class RComment extends RRNode {
    constructor(readonly label: string) {
        super();
        this.width = label.length * COMMENT_CHAR_W + 10;
        this.up = 8;
        this.down = 8;
        this.needsSpace = true;
    }
    format(x: number, y: number, width: number, out: Emit): void {
        const gaps = determineGaps(width, this.width);
        new P(x, y).h(gaps[0]).addTo(out);
        new P(x + gaps[0] + this.width, y + this.height).h(gaps[1]).addTo(out);
        out.text(this.label, x + gaps[0] + this.width / 2, y + 5, 'comment');
    }
}

export class RSkip extends RRNode {
    format(x: number, y: number, width: number, out: Emit): void {
        new P(x, y).right(width).addTo(out);
    }
}

export class RSequence extends RRNode {
    constructor(readonly items: RRNode[]) {
        super();
        for (const item of this.items) {
            this.width += item.width + (item.needsSpace ? 20 : 0);
            this.up = Math.max(this.up, item.up - this.height);
            this.height += item.height;
            this.down = Math.max(this.down - item.height, item.down);
        }
        if (this.items[0].needsSpace) this.width -= 10;
        if (this.items[this.items.length - 1].needsSpace) this.width -= 10;
        this.needsSpace = true;
    }
    format(x: number, y: number, width: number, out: Emit): void {
        const gaps = determineGaps(width, this.width);
        new P(x, y).h(gaps[0]).addTo(out);
        new P(x + gaps[0] + this.width, y + this.height).h(gaps[1]).addTo(out);
        x += gaps[0];
        for (let i = 0; i < this.items.length; i++) {
            const item = this.items[i];
            if (item.needsSpace && i > 0) {
                new P(x, y).h(10).addTo(out);
                x += 10;
            }
            item.format(x, y, item.width, out);
            x += item.width;
            y += item.height;
            if (item.needsSpace && i < this.items.length - 1) {
                new P(x, y).h(10).addTo(out);
                x += 10;
            }
        }
    }
}

export class RChoice extends RRNode {
    constructor(readonly normal: number, readonly items: RRNode[]) {
        super();
        if (items.length === 0) {
            throw new Error('"choice" needs a non-empty array');
        }
        const first = 0;
        const last = items.length - 1;
        this.width = Math.max(...items.map(i => i.width)) + AR * 4;
        this.height = items[normal].height;
        this.up = items[first].up;
        for (let i = first; i < normal; i++) {
            const arcs = (i === normal - 1) ? AR * 2 : AR;
            this.up += Math.max(arcs,
                items[i].height + items[i].down + VS + items[i + 1].up);
        }
        this.down = items[last].down;
        for (let i = normal + 1; i <= last; i++) {
            const arcs = (i === normal + 1) ? AR * 2 : AR;
            this.down += Math.max(arcs,
                items[i - 1].height + items[i - 1].down + VS + items[i].up);
        }
        // The default path's own height is already carried in this.height.
        this.down -= items[normal].height;
    }
    format(x: number, y: number, width: number, out: Emit): void {
        const gaps = determineGaps(width, this.width);
        new P(x, y).h(gaps[0]).addTo(out);
        new P(x + gaps[0] + this.width, y + this.height).h(gaps[1]).addTo(out);
        x += gaps[0];
        const last = this.items.length - 1;
        const innerWidth = this.width - AR * 4;
        let distanceFromY = 0;

        // Alternatives that curve ABOVE the baseline (indices < normal).
        for (let i = this.normal - 1; i >= 0; i--) {
            const item = this.items[i];
            if (i === this.normal - 1) {
                distanceFromY = Math.max(AR * 2,
                    this.items[this.normal].up + VS + item.down + item.height);
            }
            new P(x, y).arc('se').up(distanceFromY - AR * 2).arc('wn').addTo(out);
            item.format(x + AR * 2, y - distanceFromY, innerWidth, out);
            new P(x + AR * 2 + innerWidth, y - distanceFromY + item.height)
                .arc('ne').down(distanceFromY - item.height + this.height - AR * 2)
                .arc('ws').addTo(out);
            distanceFromY += Math.max(AR, item.up + VS
                + (i === 0 ? 0 : this.items[i - 1].down + this.items[i - 1].height));
        }

        // The straight-line default path.
        new P(x, y).right(AR * 2).addTo(out);
        this.items[this.normal].format(x + AR * 2, y, innerWidth, out);
        new P(x + AR * 2 + innerWidth, y + this.height).right(AR * 2).addTo(out);

        // Alternatives that curve BELOW the baseline (indices > normal).
        for (let i = this.normal + 1; i <= last; i++) {
            const item = this.items[i];
            if (i === this.normal + 1) {
                distanceFromY = Math.max(AR * 2,
                    this.height + this.items[this.normal].down + VS + item.up);
            }
            new P(x, y).arc('ne').down(distanceFromY - AR * 2).arc('ws').addTo(out);
            item.format(x + AR * 2, y + distanceFromY, innerWidth, out);
            new P(x + AR * 2 + innerWidth, y + distanceFromY + item.height)
                .arc('se').up(distanceFromY - AR * 2 + item.height - this.height)
                .arc('wn').addTo(out);
            distanceFromY += Math.max(AR, item.height + item.down + VS
                + (i === last ? 0 : this.items[i + 1].up));
        }
    }
}

/**
 * optional(x) is choice(1, [skip, x]): the bypass line runs above, the item
 * stays on the baseline — the reference's Optional with skip=false.
 */
export function optionalNode(item: RRNode): RChoice {
    return new RChoice(1, [new RSkip(), item]);
}

export class ROneOrMore extends RRNode {
    constructor(readonly item: RRNode, readonly rep: RRNode) {
        super();
        this.width = Math.max(item.width, rep.width) + AR * 2;
        this.height = item.height;
        this.up = item.up;
        this.down = Math.max(AR * 2,
            item.down + VS + rep.up + rep.height + rep.down);
        this.needsSpace = true;
    }
    format(x: number, y: number, width: number, out: Emit): void {
        const gaps = determineGaps(width, this.width);
        new P(x, y).h(gaps[0]).addTo(out);
        new P(x + gaps[0] + this.width, y + this.height).h(gaps[1]).addTo(out);
        x += gaps[0];

        // The item on the baseline.
        new P(x, y).right(AR).addTo(out);
        this.item.format(x + AR, y, this.width - AR * 2, out);
        new P(x + this.width - AR, y + this.height).right(AR).addTo(out);

        // The repeat loop back under the item.  The return path carries the
        // separator (a Skip when none was given), read right-to-left.
        const distanceFromY = Math.max(
            AR * 2, this.item.height + this.item.down + VS + this.rep.up);
        new P(x + AR, y).arc('nw').down(distanceFromY - AR * 2)
            .arc('ws').addTo(out);
        this.rep.format(x + AR, y + distanceFromY, this.width - AR * 2, out);
        new P(x + this.width - AR, y + distanceFromY + this.rep.height)
            .arc('se').up(distanceFromY - AR * 2 + this.rep.height - this.item.height)
            .arc('en').addTo(out);
    }
}

export class RGroup extends RRNode {
    readonly label?: RComment;
    readonly boxUp: number;
    constructor(readonly item: RRNode, labelText?: string) {
        super();
        this.label = labelText !== undefined
            ? new RComment(labelText) : undefined;
        this.width = Math.max(
            item.width + (item.needsSpace ? 20 : 0),
            this.label ? this.label.width : 0,
            AR * 2);
        this.height = item.height;
        this.boxUp = Math.max(item.up + VS, AR);
        this.up = this.boxUp;
        if (this.label) {
            this.up += this.label.up + this.label.height + this.label.down;
        }
        this.down = Math.max(item.down + VS, AR);
        this.needsSpace = true;
    }
    format(x: number, y: number, width: number, out: Emit): void {
        const gaps = determineGaps(width, this.width);
        new P(x, y).h(gaps[0]).addTo(out);
        new P(x + gaps[0] + this.width, y + this.height).h(gaps[1]).addTo(out);
        x += gaps[0];

        out.box(x, y - this.boxUp, this.width,
            this.boxUp + this.height + this.down, AR, 'group');
        this.item.format(x, y, this.width, out);
        if (this.label) {
            this.label.format(
                x,
                y - (this.boxUp + this.label.down + this.label.height),
                this.label.width, out);
        }
    }
}

/** The entry ornament ("simple" Start in the reference library). */
export class RStart extends RRNode {
    constructor() {
        super();
        this.width = 20;
        this.up = 10;
        this.down = 10;
    }
    format(x: number, y: number, _width: number, out: Emit): void {
        new P(x, y - 10).down(20).m(10, -20).down(20).m(-10, -10)
            .right(this.width).addTo(out);
    }
}

/** The exit ornament ("simple" End in the reference library). */
export class REnd extends RRNode {
    constructor() {
        super();
        this.width = 20;
        this.up = 10;
        this.down = 10;
    }
    format(x: number, y: number, _width: number, out: Emit): void {
        out.path(`M ${x} ${y} h 20 m -10 -10 v 20 m 10 -20 v 20`);
    }
}

// ---------------------------------------------------------------------------
// Diagram root: wraps a node in Start/End and emits the complete <svg>.
// ---------------------------------------------------------------------------
export class RDiagram {
    readonly items: RRNode[];
    width = 0;
    up = 0;
    down = 0;
    height = 0;
    constructor(node: RRNode) {
        this.items = [new RStart(), node, new REnd()];
        for (const item of this.items) {
            this.width += item.width + (item.needsSpace ? 20 : 0);
            this.up = Math.max(this.up, item.up - this.height);
            this.height += item.height;
            this.down = Math.max(this.down - item.height, item.down);
        }
    }
    toSvg(theme: RailroadTheme): { svg: string; width: number; height: number } {
        const padding = 20;
        const totalW = Math.ceil(this.width + padding * 2);
        const totalH = Math.ceil(this.up + this.height + this.down + padding * 2);
        const out = new Emit(theme);
        let x = padding;
        let y = padding + this.up;
        for (const item of this.items) {
            if (item.needsSpace) {
                new P(x, y).h(10).addTo(out);
                x += 10;
            }
            item.format(x, y, item.width, out);
            x += item.width;
            y += item.height;
            if (item.needsSpace) {
                new P(x, y).h(10).addTo(out);
                x += 10;
            }
        }
        // Styling is inline attributes rather than a <style> element on
        // purpose: <style> inside inline SVG is NOT scoped to the SVG in
        // HTML, and a saved/exported SVG must be self-contained anyway.
        // translate(.5 .5) centres the odd-width strokes on the pixel grid.
        const svg =
            `<svg xmlns="http://www.w3.org/2000/svg" class="ziya-railroad" ` +
            `role="img" width="${totalW}" height="${totalH}" ` +
            `viewBox="0 0 ${totalW} ${totalH}">` +
            `<g transform="translate(.5 .5)">${out.parts.join('')}</g></svg>`;
        return { svg, width: totalW, height: totalH };
    }
}

// ---------------------------------------------------------------------------
// Lenient JSON parsing for model-authored specs.
// ---------------------------------------------------------------------------

/** Remove a stray markdown fence around the JSON body. */
function stripFence(text: string): string {
    const m = text.trim().match(/^```[a-zA-Z0-9_-]*\s*\n([\s\S]*?)\n?```\s*$/);
    return m ? m[1] : text;
}

/**
 * JSON.parse with tolerance for the common LLM slips: trailing commas,
 * line and block comments (outside strings), smart quotes, stray fences.
 * Returns undefined — never throws — when even the cleaned text will not
 * parse, so streaming callers can use it as an is-it-complete probe.
 */
export function lenientJsonParse(text: string): any | undefined {
    if (typeof text !== 'string') return undefined;
    const t = stripFence(text)
        .replace(/[\u201C\u201D]/g, '"')
        .replace(/[\u2018\u2019]/g, "'");
    try { return JSON.parse(t); } catch { /* fall through to cleanup */ }
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
// Spec vocabulary -> layout nodes.
// ---------------------------------------------------------------------------

const VOCAB =
    'terminal, nonterminal, comment, skip, sequence, choice, optional, ' +
    'oneOrMore, zeroOrMore, group';

/** Shared budget so a pathological spec cannot explode layout work. */
interface Budget { nodes: number; }

const MAX_DEPTH = 40;
const MAX_NODES = 1500;

/**
 * Build a layout node from one JSON spec node.  Throws Error with a
 * model-teachable message on invalid input: the text names the accepted
 * vocabulary, because the caller surfaces it verbatim in the error card and
 * an LLM reads that card on its next attempt.
 */
export function buildNode(n: any, depth = 0,
                          budget: Budget = { nodes: 0 }): RRNode {
    if (depth > MAX_DEPTH) {
        throw new Error(
            `railroad spec is nested too deeply (max ${MAX_DEPTH} levels)`);
    }
    if (++budget.nodes > MAX_NODES) {
        throw new Error(`railroad spec is too large (max ${MAX_NODES} nodes)`);
    }
    if (typeof n === 'string' || typeof n === 'number') {
        return new RTerminal(String(n));
    }
    if (Array.isArray(n)) {
        if (n.length === 0) throw new Error('"sequence" needs a non-empty array');
        return new RSequence(n.map(c => buildNode(c, depth + 1, budget)));
    }
    if (!n || typeof n !== 'object') {
        throw new Error(`invalid railroad node: ${JSON.stringify(n)}`);
    }

    const one = (v: any) => buildNode(v, depth + 1, budget);
    const many = (v: any, kind: string): RRNode[] => {
        if (!Array.isArray(v) || v.length === 0) {
            throw new Error(`"${kind}" needs a non-empty array of nodes`);
        }
        return v.map(one);
    };
    const sepOf = (node: any): RRNode => {
        const sep = node.separator ?? node.repeat;
        return sep === undefined ? new RSkip() : one(sep);
    };

    if (n.terminal !== undefined) return new RTerminal(String(n.terminal));
    if (n.nonterminal !== undefined) return new RNonTerminal(String(n.nonterminal));
    if (n.ref !== undefined) return new RNonTerminal(String(n.ref));
    if (n.comment !== undefined) return new RComment(String(n.comment));
    if (n.skip === true || n.skip === 'skip') return new RSkip();
    if (n.sequence !== undefined || n.seq !== undefined) {
        return new RSequence(many(n.sequence ?? n.seq, 'sequence'));
    }
    if (n.choice !== undefined) return new RChoice(0, many(n.choice, 'choice'));
    if (n.optional !== undefined) return optionalNode(one(n.optional));
    if (n.oneOrMore !== undefined) {
        return new ROneOrMore(one(n.oneOrMore), sepOf(n));
    }
    if (n.zeroOrMore !== undefined) {
        return optionalNode(new ROneOrMore(one(n.zeroOrMore), sepOf(n)));
    }
    if (n.group !== undefined) {
        return new RGroup(one(n.group),
            n.label !== undefined ? String(n.label) : undefined);
    }
    throw new Error(
        `unrecognized railroad node (keys: ${Object.keys(n).join(', ') || 'none'}); ` +
        `expected one of: ${VOCAB}`);
}

export interface RailroadRuleSpec { name?: string; node: RRNode; }
export interface NormalizedRailroad { title?: string; rules: RailroadRuleSpec[]; }

/**
 * Accept the documented envelopes and the bare-node shorthand:
 *   {title?, diagram: <node>}                      one production
 *   {title?, rules: [{name?, diagram: <node>}]}    a grammar, stacked
 *   <node> | [<node>, ...] | "literal"             bare shorthand
 */
export function normalizeRailroadSpec(raw: any): NormalizedRailroad {
    if (raw == null) throw new Error('empty railroad spec');
    const budget: Budget = { nodes: 0 };
    if (typeof raw === 'string' || Array.isArray(raw)) {
        return { rules: [{ node: buildNode(raw, 0, budget) }] };
    }
    if (typeof raw !== 'object') {
        throw new Error('railroad spec must be JSON (object, array, or string)');
    }
    const title = typeof raw.title === 'string' ? raw.title : undefined;
    if (raw.rules !== undefined) {
        if (!Array.isArray(raw.rules) || raw.rules.length === 0) {
            throw new Error('"rules" must be a non-empty array');
        }
        return {
            title,
            rules: raw.rules.map((r: any) => {
                if (r && typeof r === 'object' && !Array.isArray(r)
                        && (r.diagram !== undefined || r.rule !== undefined)) {
                    return {
                        name: typeof r.name === 'string' ? r.name : undefined,
                        node: buildNode(r.diagram ?? r.rule, 0, budget),
                    };
                }
                return { node: buildNode(r, 0, budget) };
            }),
        };
    }
    if (raw.diagram !== undefined) {
        return {
            title,
            rules: [{
                name: typeof raw.name === 'string' ? raw.name : undefined,
                node: buildNode(raw.diagram, 0, budget),
            }],
        };
    }
    // A bare node object such as {"sequence": [...]}: drop the envelope-only
    // keys and interpret what remains.  An envelope that lost its content
    // gets an error naming what is missing rather than the vocabulary list.
    // (A filtering loop rather than rest-destructuring, so no unused-local
    // bindings are introduced.)
    const rest: Record<string, any> = {};
    for (const k of Object.keys(raw)) {
        if (k !== 'type' && k !== 'title') rest[k] = raw[k];
    }
    if (Object.keys(rest).length === 0) {
        throw new Error('railroad spec needs a "diagram" node or a "rules" array');
    }
    return { title, rules: [{ node: buildNode(rest, 0, budget) }] };
}

export interface RenderedRailroad {
    title?: string;
    rules: Array<{ name?: string; svg: string; width: number; height: number }>;
}

/**
 * The single entry point the plugin wrapper calls: definition (JSON text or
 * an already-parsed object) in, themed SVG strings out.  Throws Error with a
 * user/model-facing message on unusable input.
 */
export function renderRailroadSvg(definition: string | object,
                                  isDark: boolean): RenderedRailroad {
    let raw: any = definition;
    if (typeof definition === 'string') {
        raw = lenientJsonParse(definition);
        if (raw === undefined) {
            throw new Error(
                'definition is not valid JSON (even after tolerating comments ' +
                'and trailing commas)');
        }
    }
    const spec = normalizeRailroadSpec(raw);
    const theme = railroadTheme(isDark);
    return {
        title: spec.title,
        rules: spec.rules.map(r => {
            const d = new RDiagram(r.node).toSvg(theme);
            return { name: r.name, svg: d.svg, width: d.width, height: d.height };
        }),
    };
}
