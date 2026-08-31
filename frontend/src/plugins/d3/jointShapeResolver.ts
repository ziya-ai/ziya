/**
 * jointShapeResolver — normalize canonical JointJS graph JSON (`cells: [...]`)
 * into the plugin's internal { elements, connections } vocabulary.
 *
 * Background (graphics-stress Issue 41):
 * Every real JointJS graph serializes its cells with dotted, namespaced
 * `type` identifiers — `standard.Rectangle`, `standard.Circle`,
 * `standard.Link`, etc. — and stores element label text under
 * `attrs.label.text` (link label text under `labels[].attrs.text.text`).
 *
 * The jointPlugin previously dumped ALL cells (elements AND links) into the
 * element-creation loop, keyed the shape registry on a bare, non-namespaced
 * vocabulary (`rect`/`circle`/`link`), and read the label from
 * `text`/`label`/`id`. The result on any authentic JointJS JSON:
 *   1. `standard.Link` cells were treated as elements, coerced to generic
 *      rects → 100% of edges silently dropped (catastrophic for a graph
 *      renderer);
 *   2. `standard.Rectangle`/`standard.Circle` matched no registry key →
 *      rendered as fallback rects (shape-type loss);
 *   3. labels showed the cell `id` instead of `attrs.label.text`.
 *
 * These pure helpers fix the whole class: they SPLIT a mixed `cells` array
 * into elements vs links, MAP the `standard.*`/`custom.*` namespace onto the
 * registry vocabulary, and LIFT `attrs.label.text` / `labels[].attrs.text.text`
 * into the plain `label` field the creators already honour. Already-bare specs
 * (`type: "rect"`, top-level `label`) pass through unchanged, so this is a
 * gap-fill, not a catch-all rewrite.
 */

// JointJS built-in shape local-names that do not map 1:1 to an existing
// registry key. Names already present in the registry (rectangle, circle,
// ellipse, cylinder, ...) are handled by the generic lowercase pass below.
const JOINT_TYPE_ALIASES: Record<string, string> = {
    headeredrectangle: 'rect',
    textblock: 'rect',
    path: 'rect',
    polyline: 'rect',
    polygon: 'diamond',
    image: 'rect',
    borderedimage: 'rect',
    embeddedimage: 'rect',
    inscribedimage: 'rect',
};

/**
 * Resolve a (possibly namespaced) cell `type`/`shape` string to a shape key
 * the plugin's registry understands. Strips a `standard.`/`custom.`/... prefix
 * and lowercases. Unknown/absent types resolve to `rect` (the registry's own
 * fallback), so callers keep their `registry[type] || registry.rect` guard.
 */
export function resolveJointShapeType(rawType: any): string {
    if (typeof rawType !== 'string') return 'rect';
    const trimmed = rawType.trim();
    if (!trimmed) return 'rect';
    // Take the local name after the last dot: "standard.Rectangle" -> "Rectangle".
    const bare = trimmed.includes('.') ? trimmed.split('.').pop()! : trimmed;
    const key = bare.toLowerCase();
    if (!key) return 'rect';
    if (Object.prototype.hasOwnProperty.call(JOINT_TYPE_ALIASES, key)) {
        return JOINT_TYPE_ALIASES[key];
    }
    return key;
}

const hasEndpoint = (e: any): boolean =>
    e != null &&
    (typeof e === 'string'
        ? e.length > 0
        : typeof e === 'object' && ('id' in e || 'port' in e || 'selector' in e));

// D-142 (G-78): models routinely emit an edge's endpoints under `from`/`to`
// (also `src`/`dst`, `start`/`end`) instead of the canonical JointJS
// `source`/`target`. Left unmapped, isJointLinkCell fails to classify such a
// cell as a link (so it degrades into a stray box) and createEnhancedLink
// dereferences an undefined endpoint (`linkSpec.source.id`) and drops the edge.
// These alias lists let both the classifier and the normalizer read the
// endpoint regardless of which synonym the author used. Canonical name first
// so an explicit source/target always wins over an alias.
const SOURCE_ENDPOINT_KEYS = ['source', 'from', 'src', 'start'];
const TARGET_ENDPOINT_KEYS = ['target', 'to', 'dst', 'dest', 'end'];

/** First present (non-null) endpoint value among the given synonym keys. */
const resolveEndpoint = (cell: any, keys: string[]): any => {
    if (!cell || typeof cell !== 'object') return undefined;
    for (const k of keys) {
        if (cell[k] != null) return cell[k];
    }
    return undefined;
};

/**
 * A JointJS cell is a LINK if its type is (namespaced) `Link`/`DoubleLink`/
 * `ShadowLink`, OR it carries both a `source` and a `target` endpoint
 * referencing another cell. Elements never have both endpoints. Endpoint
 * synonyms (`from`/`to`, `src`/`dst`, `start`/`end`) are recognised too, so an
 * aliased edge is not misclassified as an element (D-142).
 */
export function isJointLinkCell(cell: any): boolean {
    if (!cell || typeof cell !== 'object') return false;
    const t = typeof cell.type === 'string' ? cell.type : '';
    if (/(^|\.)(link|doublelink|shadowlink)$/i.test(t)) return true;
    const src = resolveEndpoint(cell, SOURCE_ENDPOINT_KEYS);
    const tgt = resolveEndpoint(cell, TARGET_ENDPOINT_KEYS);
    if (hasEndpoint(src) && hasEndpoint(tgt)) return true;
    return false;
}

const coerceLabel = (v: any): string | undefined => {
    if (typeof v === 'string') return v;
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
    return undefined;
};

/**
 * Pull element label text out of the JointJS attrs tree
 * (`attrs.label.text`, `attrs.text.text`, or legacy `attrs['.label'].text`).
 * Returns undefined when none is present.
 */
export function extractJointElementLabel(cell: any): string | undefined {
    const a = (cell && typeof cell === 'object' && cell.attrs) || {};
    return (
        coerceLabel(a?.label?.text) ??
        coerceLabel(a?.text?.text) ??
        coerceLabel(a?.['.label']?.text)
    );
}

/**
 * Normalize a single element cell: resolve its shape type and lift its label
 * text out of `attrs` when no plain `text`/`label` is already present.
 * Non-mutating (returns a shallow copy).
 */
export function normalizeJointElement(cell: any): any {
    if (!cell || typeof cell !== 'object') return cell;
    const out: any = { ...cell, type: resolveJointShapeType(cell.type ?? cell.shape) };
    if (out.text == null && out.label == null) {
        const lbl = extractJointElementLabel(cell);
        if (lbl != null) out.label = lbl;
    }
    return out;
}

/**
 * Normalize a single link cell: lift the first `labels[].attrs.text.text`
 * (or `.attrs.label.text`) into the plain `label` field when absent, so the
 * link creator renders the intended edge label. Non-mutating.
 */
export function normalizeJointLink(cell: any): any {
    if (!cell || typeof cell !== 'object') return cell;
    const out: any = { ...cell };
    // D-142: alias endpoint synonyms (from/to, src/dst, start/end) onto the
    // canonical source/target the link creator dereferences, so an aliased edge
    // is not silently dropped. Endpoint values (bare id string or {id,...})
    // are copied verbatim — both forms are already honoured downstream.
    if (out.source == null) {
        const s = resolveEndpoint(cell, SOURCE_ENDPOINT_KEYS);
        if (s != null) out.source = s;
    }
    if (out.target == null) {
        const t = resolveEndpoint(cell, TARGET_ENDPOINT_KEYS);
        if (t != null) out.target = t;
    }
    if (out.label == null && Array.isArray(out.labels)) {
        for (const l of out.labels) {
            const t = coerceLabel(l?.attrs?.text?.text) ?? coerceLabel(l?.attrs?.label?.text);
            if (t != null) {
                out.label = t;
                break;
            }
        }
    }
    return out;
}

/**
 * Split a canonical JointJS `cells` array (elements + links interleaved) into
 * the plugin's { elements, connections } shape, applying shape-type resolution
 * and label lifting. `rawCells` may be an array or an id-keyed object.
 * `rawLinks` (optional, from an explicit `links`/`connections` field) are
 * appended to connections.
 */
export function normalizeJointCells(
    rawCells: any,
    rawLinks?: any
): { elements: any[]; connections: any[] } {
    const elements: any[] = [];
    const connections: any[] = [];

    const route = (cell: any) => {
        if (!cell || typeof cell !== 'object') return;
        if (isJointLinkCell(cell)) connections.push(normalizeJointLink(cell));
        else elements.push(normalizeJointElement(cell));
    };

    if (Array.isArray(rawCells)) {
        rawCells.forEach(route);
    } else if (rawCells && typeof rawCells === 'object') {
        Object.keys(rawCells).forEach(id => route({ id, ...rawCells[id] }));
    }

    // Explicit link collection (already known to be links — do not re-split).
    if (Array.isArray(rawLinks)) {
        rawLinks.forEach(l => {
            if (l && typeof l === 'object') connections.push(normalizeJointLink(l));
        });
    } else if (rawLinks && typeof rawLinks === 'object') {
        Object.keys(rawLinks).forEach(id =>
            connections.push(normalizeJointLink({ id, ...rawLinks[id] }))
        );
    }

    return { elements, connections };
}
