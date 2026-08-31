/**
 * Flame graph (performance profile) support utilities.
 *
 * Pure functions only -- no DOM and no d3-flame-graph import -- so stack
 * folding, spec validation and scoped-CSS generation are unit-testable in
 * Node without the rendering dependency.  The plugin wrapper
 * (plugins/d3/flamegraphPlugin.ts) owns the library and the mounting, the
 * same split the packet, railroad and wavedrom plugins use.
 */
import JSON5 from 'json5';

/** A frame.  The library reads either the long or the terse key spelling. */
export interface FlameNode {
    name?: string;
    n?: string;
    value?: number;
    v?: number;
    children?: FlameNode[];
    c?: FlameNode[];
    [key: string]: any;
}

export interface ParsedFlamegraph {
    root: FlameNode;
    format: 'json' | 'collapsed';
}

/** Guard against a pathological tree recursing without bound. */
const MAX_DEPTH = 256;

/** Remove a stray markdown fence around the body. */
function stripFence(text: string): string {
    const m = text.trim().match(/^```[a-zA-Z0-9_-]*\s*\n([\s\S]*?)\n?```\s*$/);
    return m ? m[1] : text;
}

/**
 * Whether the text is collapsed-stack output rather than a JSON tree.
 *
 * The JSON case is decided by the FIRST character, matching every other
 * spec format here: a profile whose frame names contain braces would
 * otherwise be ambiguous.
 */
export function looksLikeCollapsedStacks(text: string): boolean {
    if (typeof text !== 'string') return false;
    const t = stripFence(text).trim();
    if (!t) return false;
    if (t.startsWith('{') || t.startsWith('[')) return false;
    for (const raw of t.split(/\r?\n/)) {
        const line = raw.trim();
        if (!line || line.startsWith('#')) continue;
        // A frame path (semicolons optional -- a one-frame profile has
        // none) followed by a numeric sample count.
        if (/^.*\S[ \t]+[0-9]*\.?[0-9]+$/.test(line)) return true;
    }
    return false;
}

/**
 * Fold `frame;frame;frame count` lines into a tree.
 *
 * Every ancestor accumulates the count, so each node's `value` is the
 * INCLUSIVE total for its subtree.  That is the contract d3-flame-graph
 * expects: it runs with compoundValue = !selfValue (default true) and
 * derives self time by subtracting children.  Emitting self-only values
 * instead yields parents narrower than their children -- visually broken,
 * with no error raised anywhere.
 */
export function foldCollapsedStacks(text: string): FlameNode {
    const lines = stripFence(text).split(/\r?\n/);
    const root: FlameNode = { name: 'all', value: 0, children: [] };
    // Per-node name -> child index, so a repeated stack merges into the
    // existing frame instead of appending a duplicate sibling.
    const index = new WeakMap<FlameNode, Map<string, FlameNode>>();
    let samples = 0;

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line || line.startsWith('#')) continue;

        // Split on the LAST whitespace run: real frames carry argument
        // lists and file positions with spaces (`Main.run(int, int)`,
        // `parse (app.py:42)`), so splitting on the first space would
        // truncate the frame and mis-key the tree.
        const m = line.match(/^(.*\S)[ \t]+(\S+)$/);
        if (!m) {
            throw new Error(
                `flame graph: line ${i + 1} has no trailing sample count `
                + `(expected \`frame;frame count\`): "${line}"`);
        }
        const count = Number(m[2]);
        if (!Number.isFinite(count)) {
            throw new Error(
                `flame graph: line ${i + 1} has a non-numeric sample `
                + `count: "${line}"`);
        }
        const frames = m[1].split(';')
            .map(f => f.trim())
            .filter(f => f.length > 0);
        if (frames.length === 0) continue;

        samples++;
        root.value = (root.value || 0) + count;
        let node = root;
        for (const frame of frames) {
            let map = index.get(node);
            if (!map) { map = new Map(); index.set(node, map); }
            let child = map.get(frame);
            if (!child) {
                child = { name: frame, value: 0, children: [] };
                map.set(frame, child);
                node.children!.push(child);
            }
            child.value = (child.value || 0) + count;
            node = child;
        }
    }

    if (samples === 0) {
        throw new Error(
            'flame graph: no samples found -- expected collapsed-stack '
            + 'lines like `main;parse;lex 42`');
    }
    return root;
}

/**
 * Validate a frame tree.  Returns a model-teachable message naming the
 * path to the defect, or null when renderable.  A path rather than a bare
 * "invalid node" because an LLM reads the error card on its next attempt
 * and cannot act on an unlocated failure.
 */
export function validateFlamegraphNode(raw: any, path: string[] = [],
                                       depth = 0): string | null {
    if (depth > MAX_DEPTH) {
        return `flame graph: tree is nested too deeply (max ${MAX_DEPTH} frames)`;
    }
    const where = path.length
        ? `frame ${path.map(p => `"${p}"`).join(' > ')}`
        : 'the root frame';
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
        return `flame graph: ${where} must be an object with "name" and "value"`;
    }
    const name = raw.name ?? raw.n;
    if (typeof name !== 'string' || name.length === 0) {
        return `flame graph: ${where} needs a "name" (string)`;
    }
    // ?? rather than || so a legitimate 0-sample frame is not rejected.
    const value = raw.value ?? raw.v;
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return `flame graph: frame "${name}" needs a numeric "value" `
            + '(its total, including children)';
    }
    const children = raw.children ?? raw.c;
    if (children !== undefined) {
        if (!Array.isArray(children)) {
            return `flame graph: frame "${name}" has "children" that is `
                + 'not an array';
        }
        for (let i = 0; i < children.length; i++) {
            const msg = validateFlamegraphNode(
                children[i], [...path, name], depth + 1);
            if (msg) return msg;
        }
    }
    return null;
}

/**
 * Accept either input form and report which was used.  Does NOT validate --
 * callers run validateFlamegraphNode so a shape problem is reported
 * separately from a parse problem.
 */
export function parseFlamegraphInput(input: string | object): ParsedFlamegraph {
    if (input && typeof input === 'object') {
        return { root: input as FlameNode, format: 'json' };
    }
    if (typeof input !== 'string') {
        throw new Error('flame graph: definition must be text or an object');
    }
    const text = stripFence(input);
    if (looksLikeCollapsedStacks(text)) {
        return { root: foldCollapsedStacks(text), format: 'collapsed' };
    }
    try {
        // JSON5 so the model's habitual unquoted keys / single quotes /
        // trailing commas parse, consistent with the wavedrom path.
        return { root: JSON5.parse(text), format: 'json' };
    } catch {
        throw new Error(
            'flame graph: definition is not valid JSON, and does not look '
            + 'like collapsed stacks (`frame;frame count` per line)');
    }
}

/**
 * Theme CSS scoped to one diagram's root id.
 *
 * The library ships a stylesheet with DOCUMENT-GLOBAL selectors
 * (`.d3-flame-graph rect`, `.d3-flame-graph-label`) and hardcodes the
 * label ink to #000, which is invisible on the dark chat background.
 * Rather than import that stylesheet, the plugin injects this into the
 * diagram's own <svg>: a <style> element inside inline SVG is NOT scoped
 * to that SVG in HTML, so every selector is prefixed with the diagram's
 * id.  Keeping it inside the SVG also means a serialized export carries
 * its own styling -- frame labels are foreignObject <div>s and would
 * otherwise export bare.
 */
export function flamegraphCss(scopeId: string, isDark: boolean): string {
    const ink = isDark ? '#e6edf3' : '#141414';
    const frameStroke = isDark ? '#0d1117' : '#eeeeee';
    const hoverStroke = isDark ? '#c9d1d9' : '#474747';
    const titleInk = isDark ? '#9198a1' : '#808080';
    const s = `#${scopeId}`;
    return [
        `${s} rect { stroke: ${frameStroke}; stroke-width: 0.5; fill-opacity: 0.9; }`,
        `${s} rect:hover { stroke: ${hoverStroke}; stroke-width: 1; cursor: pointer; }`,
        `${s} .d3-flame-graph-label { white-space: nowrap; text-overflow: ellipsis;`
            + ` overflow: hidden; font-size: 12px; font-family: monospace;`
            + ` margin-left: 4px; margin-right: 4px; line-height: 1.5; padding: 0;`
            + ` font-weight: 400; color: ${ink}; text-align: left; }`,
        `${s} .fade { opacity: 0.55 !important; }`,
        `${s} .title { font-size: 15px; font-family: monospace; fill: ${titleInk}; }`,
    ].join('\n');
}
