import type { dia, shapes } from '@joint/core';
import JSON5 from 'json5';

import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';
import { sanitizeJointGeometry } from './jointGeometrySanitizer';
import {
    sanitizeRouter,
    sanitizeConnector,
    isSelfLoop,
    selfLoopEndpointConfig,
    linkPairKey,
    computeLabelPlacement,
} from './jointLinkRouting';
import { normalizeJointCells } from './jointShapeResolver';
import { classifyColor, ensureReadableFill, namedColorToHex } from './chartTheme';

// ---------------------------------------------------------------------------
// Tolerant JSON recovery for the `definition` string (G-17 / D-139, D-140).
//
// The joint path previously gated the JSON branch on trimmed.startsWith('{')
// and called a bare JSON.parse(); a markdown fence, smart quotes, trailing
// commas, unquoted keys, single quotes, // or /* */ comments, or semicolon
// separators each defeated that and dropped control to the JSON-blind line-DSL
// (^(\w+) element regex), which found zero elements -> empty container -> 30s
// headless timeout with NO image. These pure helpers recover the six near-miss
// shapes before the parse. No DOM — unit-testable.
// ---------------------------------------------------------------------------

/** Strip a leading/trailing markdown ```json fence (D-140). */
export function stripJointFence(raw: string): string {
    let t = String(raw ?? '').trim();
    const matched = /^```[a-zA-Z0-9_-]*\s*\n?([\s\S]*?)\n?```$/.exec(t);
    if (matched) return matched[1].trim();
    // Unmatched leading/trailing fences.
    t = t.replace(/^```[a-zA-Z0-9_-]*\s*/, '').replace(/```\s*$/, '');
    return t.trim();
}

/** Normalise smart/curly quotes to ASCII (D-139 w4-05). json5 rejects U+201C etc. */
export function normalizeJointSmartQuotes(raw: string): string {
    return String(raw ?? '')
        .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
        .replace(/[\u2018\u2019\u201A\u201B]/g, "'");
}

/**
 * Replace semicolons used as value/pair separators with commas (D-139 w4-15),
 * leaving semicolons inside string literals untouched. Walks the string with a
 * tiny quote-state machine so `"a;b"` is preserved while `"x": 1; "y": 2`
 * becomes `"x": 1, "y": 2`.
 */
export function repairJsonSeparators(s: string): string {
    let out = '';
    let inStr = false;
    let quote = '';
    let esc = false;
    for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        if (inStr) {
            out += ch;
            if (esc) { esc = false; }
            else if (ch === '\\') { esc = true; }
            else if (ch === quote) { inStr = false; }
            continue;
        }
        if (ch === '"' || ch === "'") { inStr = true; quote = ch; out += ch; continue; }
        if (ch === ';') { out += ','; continue; }
        out += ch;
    }
    return out;
}

/**
 * Lenient parse of a JSON-ish joint `definition`. Order: strip fence, normalise
 * smart quotes, slice to the outermost {...} or [...] (drops leading prose /
 * trailing semicolons), then strict JSON.parse (fast path, unchanged for valid
 * input) -> JSON5.parse (trailing commas, unquoted keys, single quotes,
 * comments) -> the same after semicolon-separator repair. Returns the parsed
 * value, or `undefined` when unrecoverable (so control falls to the text DSL).
 */
export function parseJointJsonish(raw: string): any {
    const cleaned = normalizeJointSmartQuotes(stripJointFence(String(raw ?? ''))).trim();
    if (!cleaned) return undefined;
    const firstObj = cleaned.indexOf('{');
    const firstArr = cleaned.indexOf('[');
    let start = -1;
    if (firstObj === -1) start = firstArr;
    else if (firstArr === -1) start = firstObj;
    else start = Math.min(firstObj, firstArr);
    if (start === -1) return undefined;
    const closeCh = cleaned[start] === '{' ? '}' : ']';
    const end = cleaned.lastIndexOf(closeCh);
    if (end <= start) return undefined;
    const body = cleaned.slice(start, end + 1);
    try { return JSON.parse(body); } catch (_e) { /* try lenient */ }
    try { return JSON5.parse(body); } catch (_e) { /* try repair */ }
    const repaired = repairJsonSeparators(body);
    try { return JSON.parse(repaired); } catch (_e) { /* try lenient */ }
    try { return JSON5.parse(repaired); } catch (_e) { /* unrecoverable */ }
    return undefined;
}

// ---------------------------------------------------------------------------
// Cell-type guard (G-17 / D-144).
//
// @joint/core v4 dia.Graph._prepareCell throws `dia.Graph: cell type must be a
// string.` for any cell whose `type` attribute is not a non-empty string. The
// bare `new dia.Element({...})` creators (createCylinderElement,
// createElectricalElement, createDocumentElement, ...) never set `type`, so the
// base dia.Element (whose defaults carry no `type`) is rejected at addCell — the
// cell is dropped, its links dangle, and an all-custom-shape graph (e.g. every
// electrical element) ends up with zero cells -> blank canvas. shapes.standard.*
// instances already carry a type and are untouched.
// ---------------------------------------------------------------------------

/** True when `t` is a valid JointJS cell type (a non-empty string). */
export function isValidCellType(t: any): boolean {
    return typeof t === 'string' && t.length > 0;
}

/**
 * A namespaced fallback type for a typeless custom element. Unknown namespaces
 * resolve to the default dia.ElementView (which renders the element's own
 * markup), so this only needs to be a stable non-empty string.
 */
export function fallbackCellType(shapeType: any): string {
    const s = (typeof shapeType === 'string' && shapeType.trim()) ? shapeType.trim() : 'element';
    return `custom.${s}`;
}

// ---------------------------------------------------------------------------
// Scale-to-fit plan (G-17 / D-145).
//
// fitContentToPaper previously set finalWidth = max(contentWidth, containerWidth)
// and wrote a viewBox of that oversized size while the SVG rendered at container
// width, so any graph wider or taller than the capture window was cropped and
// the rest silently dropped from the PNG (no downscale existed). This plan bounds
// the emitted SVG to the capture box and reports the scale so content is framed
// via viewBox instead of clipped. Content that already fits keeps its natural
// size (scale 1) — small/medium graphs are unchanged.
// ---------------------------------------------------------------------------

export interface JointFitPlan {
    paperWidth: number;
    paperHeight: number;
    scale: number;
    scaled: boolean;
}

export function computeJointFitPlan(
    contentWidth: number,
    contentHeight: number,
    containerWidth: number,
    maxHeight: number,
): JointFitPlan {
    const cw = Math.max(1, containerWidth);
    const mh = Math.max(1, maxHeight);
    const contentW = Math.max(1, contentWidth);
    const contentH = Math.max(1, contentHeight);
    // D-404: the paper MUST carry the same aspect ratio as the content bbox that
    // the viewBox frames. The old fit branch returned paperWidth = containerWidth
    // regardless of content width, so any content narrower than the container got
    // a wider-than-tall paper while the viewBox stayed narrow: preserveAspectRatio
    // 'meet' then letterboxed with wide empty side/top bands and pushed nodes to
    // the frame edge (w1-11/w1-15). Return the content box at natural size so the
    // paper and viewBox aspect match exactly and no band/clip appears. Content that
    // already fills the container (contentW ~= cw) is byte-unchanged (paperWidth
    // rounds to the same value).
    if (contentW <= cw && contentH <= mh) {
        return { paperWidth: Math.round(contentW), paperHeight: Math.round(contentH), scale: 1, scaled: false };
    }
    // Oversized in some dimension -> scale the content box down to fit inside
    // cw x mh, preserving aspect ratio, so nothing is cropped.
    // D-405: never downscale below a legibility floor. A single-rank fan-out
    // ~10580px wide otherwise scaled to ~0.06x, collapsing the paper to a ~12px
    // strip with sub-pixel labels (visually blank). Clamp the fit scale at
    // JOINT_MIN_FIT_SCALE; beyond it the paper stays legible and is allowed to
    // exceed the container, and the downstream capture-fit (compute_capture_fit /
    // clamp_png_dimensions) does the final fit of that larger-but-legible surface.
    const rawScale = Math.min(cw / contentW, mh / contentH);
    const scale = Math.max(rawScale, JOINT_MIN_FIT_SCALE);
    return {
        paperWidth: Math.max(1, Math.round(contentW * scale)),
        paperHeight: Math.max(1, Math.round(contentH * scale)),
        scale,
        scaled: true,
    };
}

/** Minimum scale the fit pass will downscale to before it stops shrinking and
 *  lets the content exceed the capture box (D-405). Below this the labels are
 *  sub-pixel and the canvas reads as blank, which is worse than an oversized
 *  surface the downstream capture-fit can shrink with its own legibility cap. */
export const JOINT_MIN_FIT_SCALE = 0.2;

/** Max SVG height the headless capture window is trusted to hold before we
 *  downscale a very tall graph (D-145). Moderate graphs grow naturally below it. */
export const JOINT_MAX_RENDER_HEIGHT = 2000;

// ---------------------------------------------------------------------------
// Grid fallback for a DirectedGraph.layout throw at scale (G-27 / D-030 / D-111).
//
// @joint/layout-directed-graph's DirectedGraph.layout throws
// `TypeError: Cannot read properties of undefined (reading 'x')` out of
// DirectedGraph.fromGraphLib for very large graphs (empirically fine at ~80
// nodes, throws by ~131 — joint-w2-02) even with NO malformed cells. The render
// loop caught the throw but did nothing afterwards, so every auto-layout element
// stayed at its default {x:0,y:0} and the whole graph collapsed into one
// illegible pile of overlapping nodes. This lays the elements out in a
// deterministic near-square reading-order grid (columns = ceil(sqrt(n))) whose
// uniform cell pitch is sized to the LARGEST node in each axis, so no two boxes
// overlap regardless of size variance. Pure + deterministic (identical light and
// dark geometry) so it is unit-testable, and it only runs on the degraded path —
// a successful layout is untouched.
// ---------------------------------------------------------------------------
export interface JointGridCell { id: string; width: number; height: number; }
export interface JointGridPlacement { id: string; x: number; y: number; }

export function computeGridFallbackPositions(
    cells: JointGridCell[],
    gap: number = 40,
): JointGridPlacement[] {
    if (!Array.isArray(cells) || cells.length === 0) return [];
    const n = cells.length;
    const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
    // Uniform cell pitch sized to the largest node in each axis (+ gap) so a
    // mixed-size graph never overlaps a large neighbour.
    let maxW = 0, maxH = 0;
    for (const c of cells) {
        const w = (c && typeof c.width === 'number' && c.width > 0) ? c.width : 120;
        const h = (c && typeof c.height === 'number' && c.height > 0) ? c.height : 60;
        if (w > maxW) maxW = w;
        if (h > maxH) maxH = h;
    }
    const g = (typeof gap === 'number' && gap >= 0) ? gap : 40;
    const pitchX = maxW + g;
    const pitchY = maxH + g;
    const out: JointGridPlacement[] = [];
    for (let i = 0; i < n; i++) {
        const col = i % cols;
        const row = Math.floor(i / cols);
        out.push({ id: cells[i].id, x: col * pitchX, y: row * pitchY });
    }
    return out;
}

export interface JointSpec {
    type: 'joint' | 'jointjs' | 'diagram';
    isStreaming?: boolean;
    forceRender?: boolean;
    definition?: string;
    elements?: JointElement[];
    connections?: JointLink[];
    layout?: string | {
        type: 'hierarchical' | 'force' | 'grid' | 'manual';
        options?: any;
    };
    theme?: 'light' | 'dark' | 'auto';
    width?: number;
    height?: number;
    shapeLibrary?: 'basic' | 'electrical' | 'network' | 'uml' | 'custom';
    interactive?: boolean;
    autoLayout?: boolean;
    grid?: boolean;
    snapToGrid?: boolean;
}

// Add missing JointPluginOptions interface
export interface JointPluginOptions {
    theme?: 'light' | 'dark';
    width?: number;
    height?: number;
    gridSize?: number;
    showGrid?: boolean;
    interactive?: boolean;
    onElementSelect?: (id: string, element: any) => void;
    onLinkSelect?: (id: string, link: any) => void;
    onElementEdit?: (id: string, element: any) => void;
    onElementMove?: (id: string, position: { x: number; y: number }) => void;
    onElementResize?: (id: string, size: { width: number; height: number }) => void;
    onLinkChange?: (id: string, link: any) => void;
    onCanvasClick?: () => void;
}

// Add D3Plugin interface if not already defined
export interface D3Plugin {
    name: string;
    priority: number;
    initialize: (container: HTMLElement, options?: any) => JointInstance;
}

export interface JointInstance {
    graph: any; // dia.Graph - using any to avoid eager import
    paper: any; // dia.Paper
    theme: 'light' | 'dark';
    addElement: (elementSpec: JointElement) => any; // dia.Element
    addLink: (linkSpec: JointLink) => any; // dia.Link
    updateElement: (id: string, updates: Partial<JointElement>) => void;
    updateLink: (id: string, updates: Partial<JointLink>) => void;
    removeElement: (id: string) => void;
    removeLink: (id: string) => void;
    clear: () => void;
    fitToContent: () => void;
    zoomIn: () => void;
    zoomOut: () => void;
    resetZoom: () => void;
    exportSVG: () => string;
    exportJSON: () => any;
    importJSON: (data: any) => void;
    getElements: () => any[]; // dia.Element[]
    getLinks: () => any[]; // dia.Link[]
    getElementById: (id: string) => any | null;
    getLinkById: (id: string) => any | null;
    setTheme: (theme: 'light' | 'dark') => void;
    enableInteraction: () => void;
    disableInteraction: () => void;
    highlightElement: (id: string, highlight?: boolean) => void;
    selectElement: (id: string) => void;
    getElementAt: (x: number, y: number) => any | null;
    addPort: (elementId: string, portSpec: Port) => void;
    removePort: (elementId: string, portId: string) => void;
    getElementPorts: (elementId: string) => any[];
}

export interface JointElement {
    id: string;
    shape?: string; // circle, rect, ellipse, diamond, hexagon, etc.
    category?: string; // For grouping and styling
    elementType?: string; // For specialized elements (switch, router, resistor, etc.)
    type?: string; // Shape type (rect, circle, etc.)
    position?: { x: number; y: number } | [number, number];
    size?: { width: number; height: number };
    attrs?: any;
    text?: string;
    label?: string;
    ports?: Port[];
    icon?: string; // For network/circuit elements
    value?: string; // For electrical elements
    group?: string;
}

interface JointLink {
    id: string;
    source: string | { id: string; port?: string; anchor?: { name: string }; connectionPoint?: { name: string } };
    target: string | { id: string; port?: string; anchor?: { name: string }; connectionPoint?: { name: string } };
    label?: string;
    labels?: any[];
    vertices?: { x: number; y: number }[];
    router?: 'orthogonal' | 'manhattan' | 'metro' | 'normal';
    connector?: 'rounded' | 'smooth' | 'jumpover' | 'normal';
    attrs?: any;
}

export interface Port {
    id: string;
    position?: string;
    type?: 'input' | 'output' | 'inout';
    label?: string;
    attrs?: any;
}

// Type guard to check if a spec is for Joint.js
const isJointSpec = (spec: any): spec is JointSpec => {
    return (
        typeof spec === 'object' &&
        spec !== null &&
        (spec.type === 'joint' || spec.type === 'jointjs' || spec.type === 'diagram') &&
        (typeof spec.definition === 'string' ||
            (spec.elements && typeof spec.elements === 'object') ||
            (spec.cells && Array.isArray(spec.cells)))
    );
};

// Enhanced shape registry with electrical and network components
const createShapeRegistry = () => {
    return {
        // Default fallback
        'default': (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),

        // Enhanced basic shapes with better styling
        rect: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),
        square: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement({ ...spec, size: { width: 80, height: 80 } }, theme),
        circle: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement(spec, theme),
        ellipse: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedEllipseElement(spec, theme),
        diamond: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedDiamondElement(spec, theme),
        hexagon: (spec: JointElement, theme: 'light' | 'dark') => createHexagonElement(spec, theme),
        cylinder: (spec: JointElement, theme: 'light' | 'dark') => createCylinderElement(spec, theme),
        actor: (spec: JointElement, theme: 'light' | 'dark') => createActorElement(spec, theme),

        // Process/workflow shapes
        process: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement({ ...spec, size: spec.size || { width: 140, height: 60 } }, theme),
        decision: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedDiamondElement(spec, theme),
        start: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement({ ...spec, size: spec.size || { width: 80, height: 80 } }, theme),
        end: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement({ ...spec, size: spec.size || { width: 80, height: 80 } }, theme),

        // Additional common shapes
        node: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement(spec, theme),
        box: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),
        oval: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedEllipseElement(spec, theme),
        rhombus: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedDiamondElement(spec, theme),

        // Aliases for common names
        rectangle: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),

        // Database shapes
        database: (spec: JointElement, theme: 'light' | 'dark') => createCylinderElement(spec, theme),
        storage: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),

        // Network components
        message: (spec: JointElement, theme: 'light' | 'dark') => createMessageElement(spec, theme),
        document: (spec: JointElement, theme: 'light' | 'dark') => createDocumentElement(spec, theme),

        // System shapes
        component: (spec: JointElement, theme: 'light' | 'dark') => createComponentElement(spec, theme),
        module: (spec: JointElement, theme: 'light' | 'dark') => createModuleElement(spec, theme),

        // UML shapes  
        class: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedUMLElement(spec, 'class', theme),
        interface: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedUMLElement(spec, 'interface', theme),
        package: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedUMLElement(spec, 'package', theme),
        note: (spec: JointElement, theme: 'light' | 'dark') => createNoteElement(spec, theme),

        // Flowchart shapes
        subprocess: (spec: JointElement, theme: 'light' | 'dark') => createSubprocessElement(spec, theme),
        manual: (spec: JointElement, theme: 'light' | 'dark') => createManualElement(spec, theme),
        data: (spec: JointElement, theme: 'light' | 'dark') => createDataElement(spec, theme),

        // Network components
        router: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'router', theme),
        switch: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'switch', theme),
        server: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'server', theme),
        firewall: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'firewall', theme),
        cloud: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'cloud', theme),

        // Electrical components
        resistor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'resistor', theme),
        capacitor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'capacitor', theme),
        inductor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'inductor', theme),
        battery: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'battery', theme),
        ground: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'ground', theme),
        voltage_source: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'voltage_source', theme),
        current_source: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'current_source', theme),
        diode: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'diode', theme),
        transistor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'transistor', theme),

    };
};

// Create diamond-shaped element
const createEnhancedRectElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#4c566a' : '#ffffff',
                stroke: theme === 'dark' ? '#88c0d0' : '#2c3e50',
                strokeWidth: 2,
                rx: 8,
                ry: 8,
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 4px rgba(0,0,0,0.5))' : 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: fitJointLabelForNode(text, (size as any)?.width, 14, (size as any)?.height),
                fill: readableJointLabelFill(theme === 'dark' ? '#4c566a' : '#ffffff'),
                fontSize: jointLabelFontSize(14, (size as any)?.height) || 14,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedCircleElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Circle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 3,
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 6px rgba(0,0,0,0.4))' : 'drop-shadow(2px 2px 6px rgba(0,0,0,0.2))'
            },
            label: {
                text: fitJointLabelForNode(text, (size as any)?.width, 13, (size as any)?.height),
                fill: readableJointLabelFill(theme === 'dark' ? '#5e81ac' : '#3498db'),
                fontSize: jointLabelFontSize(13, (size as any)?.height) || 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedEllipseElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 140, height: 70 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Ellipse({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#bf616a' : '#e74c3c',
                stroke: theme === 'dark' ? '#d08770' : '#c0392b',
                strokeWidth: 2,
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 4px rgba(0,0,0,0.5))' : 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: fitJointLabelForNode(text, (size as any)?.width, 13, (size as any)?.height),
                fill: readableJointLabelFill(theme === 'dark' ? '#bf616a' : '#e74c3c'),
                fontSize: jointLabelFontSize(13, (size as any)?.height) || 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedDiamondElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Polygon({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#ebcb8b' : '#f39c12',
                stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                strokeWidth: 2,
                refPoints: '0,10 10,0 20,10 10,20',
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 4px rgba(0,0,0,0.5))' : 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: fitJointLabelForNode(text, (size as any)?.width, 12, (size as any)?.height),
                fill: readableJointLabelFill(theme === 'dark' ? '#ebcb8b' : '#f39c12'),
                fontSize: jointLabelFontSize(12, (size as any)?.height) || 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createHexagonElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 86 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Polygon({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#a3be8c' : '#27ae60',
                stroke: theme === 'dark' ? '#8fbcbb' : '#229954',
                strokeWidth: 2,
                refPoints: '15,0 25,0 30,8.66 25,17.32 15,17.32 10,8.66',
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: fitJointLabelForNode(text, (size as any)?.width, 12, (size as any)?.height),
                fill: readableJointLabelFill(theme === 'dark' ? '#a3be8c' : '#27ae60'),
                fontSize: jointLabelFontSize(12, (size as any)?.height) || 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

export const createCylinderElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 100 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'ellipse', selector: 'top' },
            { tagName: 'rect', selector: 'body' },
            { tagName: 'ellipse', selector: 'bottom' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            top: {
                cx: size.width / 2,
                cy: 8,
                rx: size.width / 2 - 2,
                ry: 8,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2
            },
            body: {
                x: 2,
                y: 8,
                width: size.width - 4,
                height: size.height - 16,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2
            },
            bottom: {
                cx: size.width / 2,
                cy: size.height - 8,
                rx: size.width / 2 - 2,
                ry: 8,
                fill: theme === 'dark' ? '#4c566a' : '#2c3e50',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2
            },
            label: {
                text: text,
                // D-415: derive the label colour from the RESOLVED cylinder body
                // fill (as the rect/circle/diamond creators do) instead of a
                // hardcoded near-white. The hardcoded #ffffff/#eceff4 sat on the
                // body #3498db (light, 3.15:1) / #5e81ac (dark, 3.50:1), below the
                // 4.5 text floor in BOTH themes; readableJointLabelFill picks the
                // higher-contrast tone so the label clears the floor on either body.
                fill: readableJointLabelFill(theme === 'dark' ? '#5e81ac' : '#3498db'),
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2
            }
        }
    });
};

const createDiamondElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    const commonAttrs = {
        body: {
            fill: theme === 'dark' ? '#2f3349' : '#ffffff',
            stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
            strokeWidth: 2
        },
        label: {
            text: text,
            fill: theme === 'dark' ? '#ffffff' : '#000000',
            fontSize: 12,
            fontFamily: 'Arial, sans-serif',
            textAnchor: 'middle',
            textVerticalAnchor: 'middle'
        }
    };

    // Create custom diamond shape using polygon
    const element = new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [{
            tagName: 'polygon',
            selector: 'body'
        }, {
            tagName: 'text',
            selector: 'label'
        }],
        attrs: {
            body: {
                ...commonAttrs.body,
                points: `${size.width / 2},0 ${size.width},${size.height / 2} ${size.width / 2},${size.height} 0,${size.height / 2}`
            },
            label: commonAttrs.label
        }
    });

    return element;
};

// Create network element with ports and specialized styling
const createNetworkElement = (elementSpec: JointElement, elementType: string, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || getDefaultSizeForNetworkElement(elementType);
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    const networkAttrs = getNetworkElementAttrs(elementType, theme);
    const defaultPorts = getDefaultPortsForNetworkElement(elementType);

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    // D-151: pick a distinct shape per device type where cheap (cloud -> ellipse);
    // getNetworkElementAttrs already colour-codes the fill/stroke per type. Define
    // the four side port GROUPS up front so the default/custom ports actually
    // render and links can anchor to them (D-153).
    const netStyle = networkElementStyle(elementType, theme);
    const ShapeCtor = netStyle.shape === 'ellipse' ? shapes.standard.Ellipse : shapes.standard.Rectangle;
    const element = new ShapeCtor({
        id: elementSpec.id,
        position,
        size,
        ports: { groups: standardJointPortGroups(theme) },
        attrs: {
            body: networkAttrs.body,
            label: {
                ...networkAttrs.label,
                text: text
            }
        }
    });

    // Add default ports
    defaultPorts.forEach(portSpec => {
        element.addPort(createPortFromSpec(portSpec, theme));
    });

    // Add custom ports if specified
    if (elementSpec.ports) {
        elementSpec.ports.forEach(portSpec => {
            element.addPort(createPortFromSpec(portSpec, theme));
        });
    }

    return element;
};

// Helper function to parse UML content from text
const parseUMLContent = (text: string) => {
    const lines = text.split('\n').map(line => line.trim()).filter(line => line);
    let name = 'Class';
    let attributes: string[] = [];
    let methods: string[] = [];

    let currentSection = 'name';

    for (const line of lines) {
        if (line === '---' || line === '===') {
            currentSection = currentSection === 'name' ? 'attributes' : 'methods';
            continue;
        }

        if (currentSection === 'name') {
            name = line;
        } else if (currentSection === 'attributes') {
            if (line.startsWith('+') || line.startsWith('-') || line.startsWith('#') || line.startsWith('~')) {
                attributes.push(line);
            } else {
                attributes.push(`+ ${line}`);
            }
        } else if (currentSection === 'methods') {
            if (line.startsWith('+') || line.startsWith('-') || line.startsWith('#') || line.startsWith('~')) {
                methods.push(line);
            } else {
                methods.push(`+ ${line}()`);
            }
        }
    }

    return {
        name,
        attributes,
        methods
    };
};

const createDocumentElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 0 0 L ${size.width - 15} 0 L ${size.width} 15 L ${size.width} ${size.height} L 0 ${size.height} Z M ${size.width - 15} 0 L ${size.width - 15} 15 L ${size.width} 15`,
                fill: theme === 'dark' ? '#d08770' : '#f39c12',
                stroke: theme === 'dark' ? '#ebcb8b' : '#e67e22',
                strokeWidth: 2,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                fontSize: 11,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

const createComponentElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'rect', selector: 'tab1' },
            { tagName: 'rect', selector: 'tab2' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                x: 0, y: 10, width: size.width, height: size.height - 10,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2,
                rx: 5,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            tab1: {
                x: 10, y: 0, width: 20, height: 15,
                fill: theme === 'dark' ? '#81a1c1' : '#5dade2',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 1,
                rx: 3
            },
            tab2: {
                x: 35, y: 0, width: 20, height: 15,
                fill: theme === 'dark' ? '#81a1c1' : '#5dade2',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 1,
                rx: 3
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

const createStartEndElement = (elementSpec: JointElement, theme: 'light' | 'dark', type: 'start' | 'end') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 40 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;
    const color = type === 'start' ?
        (theme === 'dark' ? '#a3be8c' : '#27ae60') :
        (theme === 'dark' ? '#bf616a' : '#e74c3c');

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Ellipse({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: color,
                stroke: theme === 'dark' ? '#eceff4' : '#2c3e50',
                strokeWidth: 3,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.4))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createProcessElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 140, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#81a1c1' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2,
                rx: 10,
                ry: 10,
                filter: 'drop-shadow(3px 3px 6px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

// Create electrical element with specialized symbols
const createElectricalElement = (elementSpec: JointElement, elementType: string, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || getDefaultSizeForElectricalElement(elementType);
    const value = elementSpec.value || getDefaultValueForElement(elementType);
    const label = elementSpec.text || elementSpec.label || elementSpec.id + (value ? ` (${value})` : '');

    const electricalAttrs = getEnhancedElectricalAttrs(elementType, theme, size, label);
    const markup = getEnhancedElectricalMarkup(elementType);
    const defaultPorts = getDefaultPortsForElectricalElement(elementType);
    
    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    const element = new dia.Element({
        id: elementSpec.id,
        position,
        size,
        // D-153: define the side port groups so createPortFromSpec's group-keyed
        // ports have a position layout (else getPortCenter throws and links drop).
        ports: { groups: standardJointPortGroups(theme) },
        markup: markup,
        attrs: electricalAttrs
    });

    // Add default ports
    defaultPorts.forEach(portSpec => {
        element.addPort(createPortFromSpec(portSpec, theme));
    });

    return element;
};

// Enhanced electrical element markup with proper symbols
const getEnhancedElectricalMarkup = (elementType: string) => {
    const markups: { [key: string]: any[] } = {
        resistor: [
            { tagName: 'path', selector: 'symbol' },
            { tagName: 'text', selector: 'label' }
        ],
        capacitor: [
            { tagName: 'line', selector: 'plate1' },
            { tagName: 'line', selector: 'plate2' },
            { tagName: 'text', selector: 'label' }
        ],
        inductor: [
            { tagName: 'path', selector: 'coil' },
            { tagName: 'text', selector: 'label' }
        ],
        battery: [
            { tagName: 'line', selector: 'positive' },
            { tagName: 'line', selector: 'negative' },
            { tagName: 'text', selector: 'polarityPos' },
            { tagName: 'text', selector: 'polarityNeg' },
            { tagName: 'text', selector: 'label' }
        ],
        ground: [
            { tagName: 'path', selector: 'symbol' },
            { tagName: 'text', selector: 'label' }
        ],
        diode: [
            { tagName: 'path', selector: 'triangle' },
            { tagName: 'line', selector: 'cathode' },
            { tagName: 'text', selector: 'label' }
        ],
        voltage_source: [
            { tagName: 'circle', selector: 'body' },
            { tagName: 'text', selector: 'polarityPos' },
            { tagName: 'text', selector: 'polarityNeg' },
            { tagName: 'text', selector: 'label' }
        ],
        current_source: [
            { tagName: 'circle', selector: 'body' },
            { tagName: 'path', selector: 'arrow' },
            { tagName: 'text', selector: 'label' }
        ],
        transistor: [
            { tagName: 'circle', selector: 'body' },
            { tagName: 'line', selector: 'baseLead' },
            { tagName: 'line', selector: 'base' },
            { tagName: 'line', selector: 'collector' },
            { tagName: 'line', selector: 'emitter' },
            { tagName: 'path', selector: 'arrow' },
            { tagName: 'text', selector: 'label' }
        ]
    };

    return markups[elementType] || [
        { tagName: 'rect', selector: 'body' },
        { tagName: 'text', selector: 'label' }
    ];
};

// Enhanced electrical element attributes with proper symbols
const getEnhancedElectricalAttrs = (elementType: string, theme: 'light' | 'dark', size: { width: number; height: number }, label: string) => {
    const strokeColor = theme === 'dark' ? '#ffffff' : '#000000';
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';

    const commonLabel = {
        text: label,
        fill: textColor,
        fontSize: 11,
        fontFamily: 'Arial, sans-serif',
        textAnchor: 'middle',
        textVerticalAnchor: 'top',
        x: size.width / 2,
        y: size.height + 5
    };

    const attrs: { [key: string]: any } = {
        resistor: {
            symbol: {
                d: `M 0,${size.height / 2} L ${size.width * 0.2},${size.height / 2} L ${size.width * 0.25},${size.height * 0.2} L ${size.width * 0.35},${size.height * 0.8} L ${size.width * 0.45},${size.height * 0.2} L ${size.width * 0.55},${size.height * 0.8} L ${size.width * 0.65},${size.height * 0.2} L ${size.width * 0.75},${size.height * 0.8} L ${size.width * 0.8},${size.height / 2} L ${size.width},${size.height / 2}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        capacitor: {
            plate1: {
                x1: size.width * 0.45,
                y1: size.height * 0.2,
                x2: size.width * 0.45,
                y2: size.height * 0.8,
                stroke: strokeColor,
                strokeWidth: 3
            },
            plate2: {
                x1: size.width * 0.55,
                y1: size.height * 0.2,
                x2: size.width * 0.55,
                y2: size.height * 0.8,
                stroke: strokeColor,
                strokeWidth: 3
            },
            label: commonLabel
        },
        battery: {
            positive: {
                x1: size.width * 0.4,
                y1: size.height * 0.2,
                x2: size.width * 0.4,
                y2: size.height * 0.8,
                stroke: strokeColor,
                strokeWidth: 4
            },
            negative: {
                x1: size.width * 0.6,
                y1: size.height * 0.3,
                x2: size.width * 0.6,
                y2: size.height * 0.7,
                stroke: strokeColor,
                strokeWidth: 2
            },
            polarityPos: {
                text: '+',
                fill: textColor,
                fontSize: 14,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width * 0.4,
                y: size.height * 0.1
            },
            polarityNeg: {
                text: '−',
                fill: textColor,
                fontSize: 14,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width * 0.6,
                y: size.height * 0.1
            },
            label: commonLabel
        },
        ground: {
            symbol: {
                d: `M ${size.width / 2},0 L ${size.width / 2},${size.height * 0.6} M ${size.width * 0.2},${size.height * 0.6} L ${size.width * 0.8},${size.height * 0.6} M ${size.width * 0.3},${size.height * 0.75} L ${size.width * 0.7},${size.height * 0.75} M ${size.width * 0.4},${size.height * 0.9} L ${size.width * 0.6},${size.height * 0.9}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        diode: {
            triangle: {
                d: `M ${size.width * 0.3},${size.height * 0.3} L ${size.width * 0.7},${size.height / 2} L ${size.width * 0.3},${size.height * 0.7} Z`,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 2
            },
            cathode: {
                x1: size.width * 0.7,
                y1: size.height * 0.3,
                x2: size.width * 0.7,
                y2: size.height * 0.7,
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        inductor: {
            coil: {
                d: `M 0,${size.height / 2} L ${size.width * 0.15},${size.height / 2} ` +
                   `Q ${size.width * 0.2},${size.height * 0.1} ${size.width * 0.25},${size.height / 2} ` +
                   `Q ${size.width * 0.3},${size.height * 0.9} ${size.width * 0.35},${size.height / 2} ` +
                   `Q ${size.width * 0.4},${size.height * 0.1} ${size.width * 0.45},${size.height / 2} ` +
                   `Q ${size.width * 0.5},${size.height * 0.9} ${size.width * 0.55},${size.height / 2} ` +
                   `Q ${size.width * 0.6},${size.height * 0.1} ${size.width * 0.65},${size.height / 2} ` +
                   `Q ${size.width * 0.7},${size.height * 0.9} ${size.width * 0.75},${size.height / 2} ` +
                   `L ${size.width},${size.height / 2}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        voltage_source: {
            body: {
                cx: size.width / 2,
                cy: size.height / 2,
                r: Math.min(size.width, size.height) / 2 - 2,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 2
            },
            polarityPos: {
                text: '+',
                fill: textColor,
                fontSize: 16,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width / 2 - size.width * 0.15,
                y: size.height / 2 + 5
            },
            polarityNeg: {
                text: '−',
                fill: textColor,
                fontSize: 16,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width / 2 + size.width * 0.15,
                y: size.height / 2 + 5
            },
            label: commonLabel
        },
        current_source: {
            body: {
                cx: size.width / 2,
                cy: size.height / 2,
                r: Math.min(size.width, size.height) / 2 - 2,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 2
            },
            arrow: {
                d: `M ${size.width / 2},${size.height * 0.3} L ${size.width / 2},${size.height * 0.7} M ${size.width / 2},${size.height * 0.7} L ${size.width * 0.4},${size.height * 0.6} M ${size.width / 2},${size.height * 0.7} L ${size.width * 0.6},${size.height * 0.6}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        transistor: {
            body: {
                cx: size.width / 2,
                cy: size.height / 2,
                r: Math.min(size.width, size.height) / 2.5,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 1.5
            },
            baseLead: {
                x1: 0,
                y1: size.height / 2,
                x2: size.width * 0.35,
                y2: size.height / 2,
                stroke: strokeColor,
                strokeWidth: 2
            },
            base: {
                x1: size.width * 0.35,
                y1: size.height * 0.3,
                x2: size.width * 0.35,
                y2: size.height * 0.7,
                stroke: strokeColor,
                strokeWidth: 3
            },
            collector: {
                x1: size.width * 0.35,
                y1: size.height * 0.35,
                x2: size.width,
                y2: size.height * 0.15,
                stroke: strokeColor,
                strokeWidth: 2
            },
            emitter: {
                x1: size.width * 0.35,
                y1: size.height * 0.65,
                x2: size.width,
                y2: size.height * 0.85,
                stroke: strokeColor,
                strokeWidth: 2
            },
            arrow: {
                d: `M ${size.width * 0.55},${size.height * 0.72} L ${size.width * 0.65},${size.height * 0.8} L ${size.width * 0.6},${size.height * 0.68} Z`,
                fill: strokeColor,
                stroke: strokeColor,
                strokeWidth: 1
            },
            label: commonLabel
        }
    };

    return attrs[elementType] || {
        body: {
            fill: 'transparent',
            stroke: strokeColor,
            strokeWidth: 2,
            width: size.width,
            height: size.height
        },
        label: commonLabel
    };
};

// Create UML element with proper compartments
const createUMLElement = (elementSpec: JointElement, elementType: string, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 160, height: 120 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Parse UML content (methods, properties)
    const umlContent = parseUMLContent(text);

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    // Create UML class using standard rectangle with custom markup
    const element = new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'rect', selector: 'header' },
            { tagName: 'text', selector: 'headerText' },
            { tagName: 'rect', selector: 'attributes' },
            { tagName: 'text', selector: 'attributesText' },
            { tagName: 'rect', selector: 'methods' },
            { tagName: 'text', selector: 'methodsText' }
        ],
        attrs: {
            body: {
                fill: theme === 'dark' ? '#2f3349' : '#ffffff',
                stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
                strokeWidth: 2,
                width: size.width,
                height: size.height
            },
            header: {
                fill: theme === 'dark' ? '#3b4252' : '#f0f0f0',
                stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
                width: size.width,
                height: size.height / 3,
                y: 0
            },
            headerText: {
                text: umlContent.name,
                fill: theme === 'dark' ? '#ffffff' : '#000000',
                fontSize: 14,
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 6
            }
        }
    });

    return element;
};
// Parse simplified Joint.js syntax
const parseJointDefinition = (definition: string): { elements: JointElement[]; connections: JointLink[] } => {
    const lines = definition.split('\n').map(line => line.trim()).filter(line => line && !line.startsWith('//') && !line.startsWith('#') && !line.startsWith('```') && !line.startsWith('type:') && !line.startsWith('definition:') && line !== '|');
    const elements: JointElement[] = [];
    const links: JointLink[] = [];
    let currentSection = 'elements';

    for (const line of lines) {
        if (line.toLowerCase().includes('elements:')) {
            currentSection = 'elements';
            continue;
        }
        if (line.toLowerCase().includes('links:') || line.toLowerCase().includes('connections:')) {
            currentSection = 'links';
            continue;
        }

        if (currentSection === 'elements') {
            // Parse element: id [type] "label" @(x,y) size(w,h)
            // Enhanced regex to handle simpler formats: "A: Label" or just "A"
            const elementMatch = line.match(/^(\w+)(?:\s*\[(\w+)\])?(?:\s*"([^"]*)")?(?:\s*@\((\d+),\s*(\d+)\))?(?:\s*size\((\d+),\s*(\d+)\))?/) ||
                line.match(/^(\w+):\s*"?([^"]*)"?$/) ||
                line.match(/^(\w+)$/);

            if (elementMatch) {
                const [, id, type, label, x, y, w, h] = elementMatch;
                elements.push({
                    id,
                    type: type || 'rect',
                    position: x && y ?
                        [parseInt(x), parseInt(y)] :
                        [
                            (elements.length % 4) * 180 + 80,
                            Math.floor(elements.length / 4) * 120 + 60
                        ],
                    size: w && h ?
                        { width: parseInt(w), height: parseInt(h) } :
                        { width: 120, height: 80 },
                    text: label || id
                });
            } else if (line.includes(':') && !line.includes('->')) {
                // Simple format: id: "label"
                const simpleMatch = line.match(/^(\w+):\s*"?([^"]*)"?$/);
                if (simpleMatch) {
                    const [, id, label] = simpleMatch;
                    elements.push({
                        id,
                        type: 'rect',
                        position: [
                            (elements.length % 4) * 180 + 80,
                            Math.floor(elements.length / 4) * 120 + 60
                        ],
                        size: { width: 120, height: 80 },
                        text: label || id
                    });
                }
            }
        } else if (currentSection === 'links') {
            // Parse link: source -> target "label"
            const linkMatch = line.match(/^(\w+)\s*->\s*(\w+)(?:\s*"([^"]*)")?/);
            if (linkMatch) {
                const [, source, target, label] = linkMatch;
                links.push({
                    id: `${source}-${target}`,
                    source,
                    target,
                    label: label
                });
            }
        }
    }

    // NOTE: Deliberately NO "default test elements" fallback here.
    // Previously, a definition that produced zero parsed elements was silently
    // replaced with a hardcoded "Element A -> Element B (connection)" placeholder.
    // That masked real parse failures (e.g. a JSON spec mis-routed into this
    // line-DSL parser) as a plausible-looking 2-node diagram — the worst kind of
    // silent data loss. We now return the (possibly empty) result and let the
    // caller throw "No elements found in specification", surfacing the failure.
    return { elements, connections: links };
};

// Create Joint.js elements from specification
const createElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.id;

    // Add validation
    if (!elementSpec.id) {
        console.error('Element missing required id:', elementSpec);
        throw new Error('Element must have an id');
    }

    console.log(`Creating element ${elementSpec.id}:`, { position, size, text, type: elementSpec.type });

    const commonAttrs = {
        body: {
            fill: theme === 'dark' ? '#2f3349' : '#ffffff',
            stroke: theme === 'dark' ? '#4cc9f0' : '#2c3e50',
            strokeWidth: 3,
            rx: 8,
            ry: 8,
            filter: theme === 'dark' ? 'drop-shadow(2px 2px 6px rgba(0,0,0,0.4))' : 'drop-shadow(2px 2px 6px rgba(0,0,0,0.2))'
        },
        label: {
            text: text,
            fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
            fontSize: 13,
            fontFamily: 'Arial, sans-serif',
            fontWeight: 'bold',
            textAnchor: 'middle',
        textVerticalAnchor: 'middle'
        }
    };
    
    // Access shapes and dia from the global scope set by render()
    const { shapes, dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes || !dia) throw new Error('Joint.js not initialized');

    let element: dia.Element;
    switch (elementSpec.type || 'rect') {
        case 'circle':
            element = new shapes.standard.Circle({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                        stroke: theme === 'dark' ? '#88c0d0' : '#2980b9'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#eceff4' : '#ffffff'
                    }
                }
            });
            break;
        case 'ellipse':
            element = new shapes.standard.Ellipse({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#bf616a' : '#e74c3c',
                        stroke: theme === 'dark' ? '#d08770' : '#c0392b'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#eceff4' : '#ffffff'
                    }
                }
            });
            break;
        case 'cylinder':
            // Use Cylinder shape if available, fallback to Rectangle
            element = new shapes.standard.Ellipse({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#a3be8c' : '#2ecc71',
                        stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                        fontSize: 11
                    }
                }
            });
            break;
        case 'diamond':
            // Create proper diamond using Polygon
            element = new shapes.standard.Polygon({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#ebcb8b' : '#f39c12',
                        stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                        refPoints: '0,10 10,0 20,10 10,20'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                        fontSize: 12
                    }
                }
            });
            break;
        default: // 'rect' or any other type
            element = new shapes.standard.Rectangle({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#4c566a' : '#ffffff'
                    },
                    label: commonAttrs.label
                }
            });
    }

    console.log(`Created element:`, element);
    return element;
};

// ---------------------------------------------------------------------------
// D-143 (G-47): coerce a string boolean to a real boolean.
//
// Models routinely emit JSON-encoded booleans as strings ("autoLayout":"false",
// "grid":"0"). A non-empty string is truthy, so `spec.autoLayout !== false` was
// TRUE for the string "false" and auto-layout ran anyway — discarding the
// author's declared x/y positions and collapsing an intended horizontal row into
// a DirectedGraph column. This coerces the recognised string boolean forms
// ("true"/"false"/"1"/"0", case/space-insensitive) to their boolean value and
// leaves real booleans, undefined, and every other value untouched (so a genuine
// object-valued option or an unset field is unaffected).
export function coerceJointBoolean(value: any): any {
    if (typeof value !== 'string') return value;
    const v = value.trim().toLowerCase();
    if (v === 'true' || v === '1') return true;
    if (v === 'false' || v === '0') return false;
    return value;
}

// Enhanced link creation with better routing and styling
const createEnhancedLink = (
    linkSpec: JointLink,
    theme: 'light' | 'dark',
    siblingInfo?: { index: number; count: number }
) => {
    // Configure source/target with proper anchor and connection points
    const sourceConfig = typeof linkSpec.source === 'string'
        ? { id: linkSpec.source, anchor: { name: 'modelCenter' }, connectionPoint: { name: 'boundary' } }
        : {
            ...linkSpec.source,
            anchor: linkSpec.source.anchor || { name: 'modelCenter' },
            connectionPoint: linkSpec.source.connectionPoint || { name: 'boundary' }
        };

    const targetConfig = typeof linkSpec.target === 'string'
        ? { id: linkSpec.target, anchor: { name: 'modelCenter' }, connectionPoint: { name: 'boundary' } }
        : {
            ...linkSpec.target,
            anchor: linkSpec.target.anchor || { name: 'modelCenter' },
            connectionPoint: linkSpec.target.connectionPoint || { name: 'boundary' }
        };

    // D-411 (self-loop-zero-length-invisible): a link from an element to itself
    // resolves both modelCenter anchors + boundary connectionPoint to the SAME
    // node centre, collapsing the link and its label to zero length inside the
    // body (invisible). Re-anchor a self-loop to two different sides so it draws
    // as a visible arc that bows out past the node boundary.
    const selfLoop = isSelfLoop(linkSpec.source, linkSpec.target);
    let selfLoopConnector: { name: string; args?: any } | undefined;
    if (selfLoop) {
        const loop = selfLoopEndpointConfig();
        (sourceConfig as any).anchor = loop.sourceAnchor;
        (sourceConfig as any).connectionPoint = loop.connectionPoint;
        (targetConfig as any).anchor = loop.targetAnchor;
        (targetConfig as any).connectionPoint = loop.connectionPoint;
        selfLoopConnector = loop.connector;
    }

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    const link = new shapes.standard.Link({
        id: linkSpec.id,
        // D-147 (G-47): links are added to the graph AFTER elements, so their
        // insertion-order z put every edge ABOVE the node bodies — at density the
        // horizontal edge bundles were drawn straight through node labels and struck
        // them out (35 of 40 labels lost). Pinning links to a z below the elements
        // (which auto-assign z >= 1 on insertion) renders edges beneath the node
        // bodies/labels in both themes; endpoints already terminate at the node
        // boundary so no arrowhead information is lost.
        z: -1,
        source: sourceConfig,
        target: targetConfig,
        // Normalize router/connector to a KNOWN JointJS name (graphics-stress Issue 29).
        // An object-shaped `{name:"exotic-nonexistent-router"}` or any unrecognized name
        // otherwise makes findRoute() throw `unknown router: "[object Object]"` during the
        // shared link view-flush, poisoning EVERY link and the auto-layout -> blank canvas.
        router: sanitizeRouter(linkSpec.router, 'normal', { padding: 20 }),
        // D-131: a per-link `connectionStrategy` returning getBBox().center() forced
        // BOTH endpoints onto the element bbox centre. `connectionStrategy` is a
        // dia.Paper option — inert on a link MODEL at best, and at worst (any version
        // that honours it) it collapses every route onto the centre-to-centre line and
        // defeats the orthogonal/manhattan/metro router the spec asked for, so all
        // links render as straight segments regardless of `router`. Removed: the
        // modelCenter anchor + boundary connectionPoint on source/target already
        // terminate the link at the node edge, and the router now routes freely.
        // D-411: a self-loop overrides the connector to 'smooth' so the arc between
        // its two distinct side-anchors bows out visibly rather than cutting straight.
        connector: selfLoopConnector || sanitizeConnector(linkSpec.connector, 'rounded', { radius: 15 }),
        vertices: linkSpec.vertices || [],
        defaultRouter: { name: 'normal' },
        attrs: {
            line: {
                stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                strokeWidth: 3,
                strokeLinecap: 'round',
                strokeLinejoin: 'round',
                strokeDasharray: linkSpec.attrs?.line?.strokeDasharray || '0',
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))',
                targetMarker: {
                    type: 'path',
                    d: 'M 14 -7 0 0 14 7 z',
                    fill: theme === 'dark' ? '#88c0d0' : '#34495e',
                    stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                    strokeWidth: 2
                }
            },
            wrapper: {
                strokeWidth: 10,
                stroke: 'transparent'
            }
        }
    });

    // Add label if specified
    if (linkSpec.label) {
        // D-131 / D-407: lift the label perpendicular OFF the stroke (a bare
        // `position: 0.5` centres the text ON the line so it is bisected
        // lengthwise) and, when several links share a node pair, stagger the
        // labels along the link and to alternating sides so they do not pile up
        // at one midpoint (the a<->b 2-cycle overprint).
        const placement = computeLabelPlacement(siblingInfo?.index ?? 0, siblingInfo?.count ?? 1);
        link.appendLabel({
            position: { distance: placement.distance, offset: placement.offset },
            attrs: {
                rect: {
                    // D-148 (G-47): the calc(w/h/x/y) sizing terms are RELATIVE and
                    // must reference the label's text bbox — without `ref: 'text'`
                    // they resolved against a null reference, so the backing plate
                    // collapsed and was effectively never drawn, leaving labels sitting
                    // directly on the link stroke / arrowheads / each other (the 1.18
                    // light / 1.74 dark overprint). JointJS's own builtin default label
                    // rect carries `ref: 'text'` for exactly this reason.
                    ref: 'text',
                    fill: theme === 'dark' ? '#3b4252' : '#ffffff',
                    stroke: theme === 'dark' ? '#4c566a' : '#bdc3c7',
                    strokeWidth: 1,
                    rx: 6,
                    ry: 6,
                    width: 'calc(w + 16)',
                    height: 'calc(h + 8)',
                    x: 'calc(x - 8)',
                    y: 'calc(y - 4)'
                },
                text: {
                    text: linkSpec.label,
                    fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                    fontSize: 12,
                    fontFamily: 'Arial, sans-serif',
                    fontWeight: 'bold',
                    textAnchor: 'middle',
                    textVerticalAnchor: 'middle'
                }
            }
        });
    }

    return link;
};

// Override the original createLink to use the enhanced version
const createLink = (linkSpec: JointLink, theme: 'light' | 'dark') => {
    // Prepare source and target with proper anchor points for better connections
    const sourceConfig = typeof linkSpec.source === 'string'
        ? { id: linkSpec.source, anchor: { name: 'modelCenter' } }
        : { ...linkSpec.source, anchor: { name: 'modelCenter' } };

    const targetConfig = typeof linkSpec.target === 'string'
        ? { id: linkSpec.target, anchor: { name: 'modelCenter' } }
        : { ...linkSpec.target, anchor: { name: 'modelCenter' } };

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    const link = new shapes.standard.Link({
        id: linkSpec.id,
        // D-147 (G-47): links are added to the graph AFTER elements, so their
        // insertion-order z put every edge ABOVE the node bodies — at density the
        // horizontal edge bundles were drawn straight through node labels and struck
        // them out (35 of 40 labels lost). Pinning links to a z below the elements
        // (which auto-assign z >= 1 on insertion) renders edges beneath the node
        // bodies/labels in both themes; endpoints already terminate at the node
        // boundary so no arrowhead information is lost.
        z: -1,
        source: sourceConfig,
        target: targetConfig,
        // Normalize router/connector to a KNOWN JointJS name (graphics-stress Issue 29).
        router: sanitizeRouter(linkSpec.router, 'normal', { padding: 10 }),
        // D-131: a per-link `connectionStrategy` returning getBBox().center() forced
        // BOTH endpoints onto the element bbox centre. `connectionStrategy` is a
        // dia.Paper option — inert on a link MODEL at best, and at worst (any version
        // that honours it) it collapses every route onto the centre-to-centre line and
        // defeats the orthogonal/manhattan/metro router the spec asked for, so all
        // links render as straight segments regardless of `router`. Removed: the
        // modelCenter anchor + boundary connectionPoint on source/target already
        // terminate the link at the node edge, and the router now routes freely.
        connector: sanitizeConnector(linkSpec.connector, 'rounded', { radius: 15 }),
        vertices: linkSpec.vertices || [],
        attrs: {
            line: {
                stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                strokeWidth: 3,
                strokeLinecap: 'round',
                strokeLinejoin: 'round',
                strokeDasharray: linkSpec.attrs?.line?.strokeDasharray || '0',
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))',
                targetMarker: {
                    type: 'path',
                    d: 'M 14 -7 0 0 14 7 z',
                    fill: theme === 'dark' ? '#88c0d0' : '#34495e',
                    stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                    strokeWidth: 2
                }
            },
            wrapper: {
                strokeWidth: 10,
                stroke: 'transparent'
            }
        }
    });

    // Add label if specified
    if (linkSpec.label) {
        link.appendLabel({
            position: 0.5,
            attrs: {
                rect: {
                    // D-148 (G-47): the calc(w/h/x/y) sizing terms are RELATIVE and
                    // must reference the label's text bbox — without `ref: 'text'`
                    // they resolved against a null reference, so the backing plate
                    // collapsed and was effectively never drawn, leaving labels sitting
                    // directly on the link stroke / arrowheads / each other (the 1.18
                    // light / 1.74 dark overprint). JointJS's own builtin default label
                    // rect carries `ref: 'text'` for exactly this reason.
                    ref: 'text',
                    fill: theme === 'dark' ? '#3b4252' : '#ffffff',
                    stroke: theme === 'dark' ? '#4c566a' : '#bdc3c7',
                    strokeWidth: 1,
                    rx: 6,
                    ry: 6,
                    width: 'calc(w + 16)',
                    height: 'calc(h + 8)',
                    x: 'calc(x - 8)',
                    y: 'calc(y - 4)'
                },
                text: {
                    text: linkSpec.label,
                    fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                    fontSize: 12,
                    fontFamily: 'Arial, sans-serif',
                    fontWeight: 'bold',
                    textAnchor: 'middle',
                    textVerticalAnchor: 'middle'
                }
            }
        });
    }

    return link;
};

// ---------------------------------------------------------------------------
// G-26 shared helpers (pure, exported for unit tests — no DOM / @joint/core dep)
// ---------------------------------------------------------------------------

// D-156: only a real theme token may be lifted off a model-supplied definition or
// used to drive the theme ternaries. A bogus string ('nord-dark') must NOT outrank
// the caller's render theme (which previously fell every ternary to its light branch,
// painting a light slab inside a dark page).
const VALID_JOINT_THEMES = new Set(['light', 'dark', 'auto']);
export const isValidJointTheme = (t: any): boolean =>
    typeof t === 'string' && VALID_JOINT_THEMES.has(t);

// D-116/D-156: resolve the render theme that every `theme === 'dark'` ternary reads.
// Only the literal 'light'/'dark' pass through; 'auto', undefined AND any bogus token
// ('nord-dark') fall back to the caller's render theme (isDarkMode) instead of leaking
// through and flipping every ternary to its light branch under dark mode (which painted
// a near-white paper slab inside a genuinely dark page). Behaviour-identical extraction
// of the inline render-site resolution, exported so both themes can be asserted in tests.
export const resolveJointRenderTheme = (
    specTheme: any,
    isDarkMode: boolean
): 'light' | 'dark' =>
    (specTheme === 'light' || specTheme === 'dark')
        ? specTheme
        : (isDarkMode ? 'dark' : 'light');

// D-141: locate the first object (within maxDepth levels) that owns an elements/cells
// array, so a one-level-deeper wrapper ({graph:{cells:[...]}}, {data:{elements:[...]}},
// {diagram:{...}}, {spec:{...}}) is recovered instead of falling through to the
// JSON-blind line-DSL (zero elements -> empty container -> 30s hang).
export const findJointGraphContainer = (root: any, maxDepth = 3): any => {
    const seen = new Set<any>();
    const visit = (node: any, depth: number): any => {
        if (!node || typeof node !== 'object' || seen.has(node)) return null;
        seen.add(node);
        const hasEls = Array.isArray(node.elements) ||
            (node.elements && typeof node.elements === 'object' && !Array.isArray(node.elements));
        const hasCells = Array.isArray(node.cells);
        if (hasEls || hasCells) return node;
        if (depth >= maxDepth) return null;
        for (const k of Object.keys(node)) {
            const child = node[k];
            if (child && typeof child === 'object') {
                const found = visit(child, depth + 1);
                if (found) return found;
            }
        }
        return null;
    };
    return visit(root, 0);
};

// D-155: per-fill luminance-aware label colour. The node body fills are hardcoded per
// creator; a single hardcoded label colour (#ffffff/#eceff4) fails on the warm/pastel
// fills (worst pair #eceff4 on #a3be8c = 1.77:1). Pick the higher-contrast of a
// near-black / near-white candidate against the RESOLVED body fill so both themes work.
const JOINT_LABEL_DARK = '#14171c';
const JOINT_LABEL_LIGHT = '#f7f9fc';

const _jointLin = (c: number): number => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
};
const _jointRelLum = (hex: string): number => {
    const m = /^#?([0-9a-fA-F]{6})$/.exec((hex || '').trim());
    if (!m) return 0;
    const n = parseInt(m[1], 16);
    return 0.2126 * _jointLin((n >> 16) & 255) +
        0.7152 * _jointLin((n >> 8) & 255) +
        0.0722 * _jointLin(n & 255);
};
export const jointContrastRatio = (a: string, b: string): number => {
    const la = _jointRelLum(a);
    const lb = _jointRelLum(b);
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
};
/** Higher-contrast of the near-black / near-white label candidate against `bodyFill`.
 *  Falls back to the light candidate for an unparseable (non 6-digit-hex) fill. */
export const readableJointLabelFill = (bodyFill: string): string => {
    if (!/^#?[0-9a-fA-F]{6}$/.test((bodyFill || '').trim())) return JOINT_LABEL_LIGHT;
    // Pick the higher-contrast aesthetic near-tone against the ACTUAL fill.
    const near = jointContrastRatio(bodyFill, JOINT_LABEL_DARK) >=
        jointContrastRatio(bodyFill, JOINT_LABEL_LIGHT)
        ? JOINT_LABEL_DARK : JOINT_LABEL_LIGHT;
    if (jointContrastRatio(bodyFill, near) >= 4.5) return near;
    // D-138: a few default-palette fills are mid-toned enough that neither
    // softened near-tone clears the 4.5 text floor (dark-theme circle #5e81ac
    // best-label 4.46, ellipse #bf616a 4.39). Escalate to the MATCHING pure tone
    // — still chosen by the fill's own luminance (not a blind constant swap),
    // just the extreme rather than the softened variant — which does clear it in
    // BOTH themes (worst-case best-label ratio rises 4.39 -> 5.13). Fills that
    // already pass keep their softer near-tone, so ordinary output is unchanged.
    return near === JOINT_LABEL_DARK ? '#000000' : '#ffffff';
};

// D-146: no textWrap/ellipsis exists, so a long label overruns the node/canvas and is
// truncated mid-word at the raster edge (and an undersized node has its own stroke
// bisect the glyphs). Headless has no text metrics, so estimate glyph advance (~0.6em
// for the bold sans stack) and ellipsis-truncate to the node width.
export const JOINT_LABEL_ELLIPSIS = '\u2026';
export const fitJointLabel = (text: any, nodeWidth: number, fontSize: number, nodeHeight?: number): string => {
    const s = (text === undefined || text === null) ? '' : String(text);
    if (!s) return s;
    const w = (typeof nodeWidth === 'number' && nodeWidth > 0) ? nodeWidth : 120;
    const fs = (typeof fontSize === 'number' && fontSize > 0) ? fontSize : 13;
    // D-130: reconcile the label against the node HEIGHT as well as its width. A
    // width-only fit still leaves a label whose single line of glyphs is TALLER than
    // an undersized node (joint-w2-13: 14px bold label in a 10px-tall node), so the
    // node's own top/bottom stroke bisects the glyphs at contrast ratio 1.00. When
    // the node cannot vertically contain even one line at this font size (needs the
    // glyph box ~fontSize plus a little inset inside the ~2px stroke), drop the label
    // entirely rather than paint a struck-through smear — the node still renders as a
    // clean marker. Nodes tall enough to hold the line keep their (width-fitted) text,
    // and callers that pass no height (legacy 3-arg calls) are unaffected.
    if (typeof nodeHeight === 'number' && nodeHeight > 0 && nodeHeight < fs + 2) return '';
    const padding = 12;                       // ~6px inset each side
    const avgChar = fs * 0.6;                 // mean glyph advance for the bold sans stack
    const cap = Math.max(3, Math.floor((w - padding) / avgChar));
    if (s.length <= cap) return s;
    if (cap <= 1) return JOINT_LABEL_ELLIPSIS;
    return s.slice(0, cap - 1).replace(/\s+$/, '') + JOINT_LABEL_ELLIPSIS;
};

// D-130 (regression): the earlier vertical-fit reconciliation DROPPED a label
// whenever the node was too short to hold one line at the base font (a 14px label
// in a 10px node -> the node's own stroke bisects the glyphs at ratio 1.00).
// Dropping left the tiny-node case (joint-w2-13: 14x10 pills) with EVERY label
// gone, which the render judge scores as a failure ("labels trimmed to zero, no
// minimum, no fallback"): an empty node conveys nothing. The correct
// reconciliation is to SHRINK the font to the node rather than delete the text —
// a legible smaller glyph that sits inside the stroke instead of being struck
// through by it. Only when the node is so short that even the legibility floor
// cannot fit (below ~JOINT_MIN_LABEL_FONT+3px) do we fall back to dropping.
// Nodes tall enough for the base font are byte-unchanged (they return the base
// size), so w2-05/w2-06 and every normal-height node are unaffected.
export const JOINT_MIN_LABEL_FONT = 6;
export const jointLabelFontSize = (baseFontSize: number, nodeHeight?: number): number => {
    const base = (typeof baseFontSize === 'number' && baseFontSize > 0) ? baseFontSize : 13;
    if (typeof nodeHeight !== 'number' || nodeHeight <= 0) return base;
    // Tall enough for the base font (glyph box ~fontSize plus ~2px inset inside
    // the body stroke): unchanged.
    if (nodeHeight >= base + 2) return base;
    // Too short for the base font: shrink so one line fits inside the ~2px stroke
    // (leave ~3px total inset). Below the legibility floor there is no room for a
    // readable glyph, so signal a drop with 0 rather than paint a sub-pixel smear.
    const shrunk = Math.floor(nodeHeight - 3);
    return shrunk >= JOINT_MIN_LABEL_FONT ? shrunk : 0;
};

// D-130: width-fit the label at the font size the node HEIGHT can actually hold
// (jointLabelFontSize), and only return empty when the node is too short for even
// the shrunk floor. Used by the element creators so tiny nodes keep a legible,
// smaller, non-bisected label instead of being blanked. Pure/DOM-free.
export const fitJointLabelForNode = (
    text: any, nodeWidth: number, baseFontSize: number, nodeHeight?: number,
): string => {
    const f = jointLabelFontSize(baseFontSize, nodeHeight);
    return f > 0 ? fitJointLabel(text, nodeWidth, f) : '';
};

// ---------------------------------------------------------------------------
// G-48 — joint element theming + network/port fixes.
//   D-150 nested-container labels occluded / dark flat-slab fill
//   D-151 network shapes flattened to a plain rect + ports never drawn
//   D-152 author-supplied element `attrs` (fill/stroke/label) silently dropped
//   D-153 string port position -> layoutCallback throw -> links dropped
// Pure helpers (no live element) so they are unit-testable.
// ---------------------------------------------------------------------------

/** Effective page surface used for label-contrast reasoning when a node fill is
 *  transparent (so the label sits on the page, not on a node fill). */
export const jointPageBackground = (theme: 'light' | 'dark'): string =>
    theme === 'dark' ? '#1e1e1e' : '#ffffff';

/** Expand #rgb -> #rrggbb (lowercased); pass 6-digit through; null if not hex. */
const expandJointHex = (s: string): string | null => {
    const m = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec((s || '').trim());
    if (!m) return null;
    let h = m[1].toLowerCase();
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    return '#' + h;
};

/**
 * Normalise ONE author colour string for a joint body/stroke attribute.
 *  - literal 'transparent'/'none' (or zero-alpha rgba) -> { value:'none', hex:null, absent:true }
 *  - #hex / #rgb -> { value:#rrggbb, hex:#rrggbb }
 *  - rgb()/rgba() (alpha>0) -> { value:#rrggbb, hex:#rrggbb } (alpha dropped)
 *  - known CSS name -> { value:#rrggbb, hex:#rrggbb }; unknown name -> { value:name, hex:null }
 *  - unresolvable token (var()/$x/--x) or non-string -> { value:null } (leave the creator default)
 */
export function normalizeJointColor(raw: any): { value: string | null; hex: string | null; absent?: boolean } {
    if (typeof raw !== 'string') return { value: null, hex: null };
    const lower = raw.trim().toLowerCase();
    if (lower === 'transparent' || lower === 'none') return { value: 'none', hex: null, absent: true };
    const cls = classifyColor(raw);
    if (!cls) {
        if (/^rgba?\(/.test(lower)) return { value: 'none', hex: null, absent: true }; // zero-alpha rgba
        return { value: null, hex: null }; // unresolvable token -> leave default
    }
    if (cls.hex) {
        const h = expandJointHex(cls.hex) || cls.hex;
        return { value: h, hex: h };
    }
    const nm = namedColorToHex(cls.named!);
    return nm ? { value: nm, hex: nm } : { value: cls.named!, hex: null };
}

/** True when an element is a container (owns a non-empty `embeds` list). */
export function isJointContainer(spec: any): boolean {
    return !!spec && Array.isArray(spec.embeds) && spec.embeds.length > 0;
}

/** Nesting depth of `id` following the `parent` chain within `specById`. */
export function jointElementDepth(id: any, specById: Map<string, any>): number {
    let depth = 0, cur = specById.get(String(id)), guard = 0;
    while (cur && cur.parent != null && guard++ < 40) {
        const p = specById.get(String(cur.parent));
        if (!p || p === cur) break;
        depth++; cur = p;
    }
    return depth;
}

// Dark per-depth container ramp: outer darkest -> inner lightest, so five nested
// containers that all shared #4c566a (contrast 1.00 in dark) now read as distinct
// bands. Light keeps its crisp boundary strokes, so it is left untouched.
const JOINT_DARK_CONTAINER_RAMP = ['#2e3440', '#3b4252', '#434c5e', '#4c566a', '#5a657c', '#68758f'];

/** Container body fill for `depth`. Dark: a per-depth ramp entry. Light: null. */
export function jointContainerFill(depth: number, theme: 'light' | 'dark'): string | null {
    if (theme !== 'dark') return null;
    const i = Math.max(0, Math.min(depth, JOINT_DARK_CONTAINER_RAMP.length - 1));
    return JOINT_DARK_CONTAINER_RAMP[i];
}

/**
 * { body?, label? } attribute patch for a freshly-created joint element:
 * author `attrs` override (D-152) + container styling (D-150). Pure.
 *  - author body fill/stroke colour-form normalised; other body keys pass through.
 *  - author label fill honoured but CLAMPED to >=4.5 contrast on the resolved
 *    fill (author colours resolve AND stay legible in BOTH themes — the coupling
 *    the group warns about).
 *  - a container (embeds) gets a top-anchored title (children can't occlude it)
 *    and, in dark, a per-depth ramp fill.
 * Returns null when nothing changes (byte-identical output).
 */
export function computeJointElementStyle(
    spec: any,
    opts: { theme: 'light' | 'dark'; defaultBodyFill: string; pageBg: string; depth: number; isContainer: boolean }
): { body?: Record<string, any>; label?: Record<string, any> } | null {
    const body: Record<string, any> = {};
    const label: Record<string, any> = {};

    let effectiveFill = expandJointHex(opts.defaultBodyFill) || opts.defaultBodyFill;
    let bodyFillChanged = false;
    let fillAbsent = false;

    // (1) Container dark fill ramp (author attrs may override below).
    if (opts.isContainer) {
        const cf = jointContainerFill(opts.depth, opts.theme);
        if (cf) { body.fill = cf; effectiveFill = cf; bodyFillChanged = true; }
    }

    // (2) Author attrs override (D-152).
    const a = spec && typeof spec === 'object' ? spec.attrs : undefined;
    if (a && typeof a === 'object') {
        if (a.body && typeof a.body === 'object') {
            for (const k of Object.keys(a.body)) {
                if (k === 'fill') {
                    const n = normalizeJointColor(a.body.fill);
                    if (n.value !== null) {
                        body.fill = n.value; bodyFillChanged = true;
                        effectiveFill = n.absent ? opts.pageBg : (n.hex || effectiveFill);
                        fillAbsent = !!n.absent;
                    }
                } else if (k === 'stroke') {
                    const n = normalizeJointColor(a.body.stroke);
                    if (n.value !== null) body.stroke = n.value;
                } else {
                    body[k] = a.body[k];
                }
            }
            // When the fill is transparent the STROKE is the node's only visible
            // boundary, so it must clear the 3:1 graphical-contrast floor against
            // the page it sits on. An author stroke legible on one theme's page
            // (black on white = 21:1) otherwise vanishes on the other (black on
            // the dark page #1e1e1e = 1.26:1), taking the whole node with it —
            // D-132/joint-w4-13 "mid" (fill:transparent, stroke:black) rendered as
            // an invisible box in dark while light was fine. Reconcile toward the
            // page's opposite (dark stroke -> #999999 = 5.85:1 on #1e1e1e); light
            // stays black (already above floor, passed through untouched). Only
            // fires in the transparent-fill case, so opaque-fill nodes (w1-13 dark
            // strokes #7f0000/#0d47a1 at 1.5-1.9:1) keep their author stroke — the
            // fill itself makes those nodes visible.
            if (fillAbsent && typeof body.stroke === 'string' && body.stroke !== 'none') {
                body.stroke = ensureReadableFill(body.stroke, opts.pageBg, body.stroke, 3);
            }
        }

        // (2b) D-417: an OPAQUE author fill whose luminance is within a hair of the
        // page dissolves the whole node into the paper — the fill can no longer make
        // the node visible, so (exactly like the transparent-fill case above) the
        // STROKE becomes the node's only boundary and must clear the 3:1 graphical
        // floor against the page. joint-w3-09 near-white fills (#fafafa/#ffffff/#f5f5f5
        // on the light page = 1.00-1.09:1) with #ddd/#eee/#ccc hairlines (1.16-1.61:1),
        // and joint-w3-10 near-black fills (#1a1a1a/#000/#0d1117 on the dark page
        // #1e1e1e = 1.04-1.26:1) with #222-#374151 strokes (1.05-1.62:1) both rendered
        // as invisible boxes. Reconcile the stroke toward the page's opposite: w3-09
        // #ddd->#858585=3.69, #ccc->#7a7a7a=4.29; w3-10 #333->#858585=4.52,
        // #374151->#878d97=4.99 (all vs their page). Fills that already stand off the
        // page (contrast > 1.35) are untouched, so ordinary opaque nodes — and the
        // w1-13 dark-stroke-on-vivid-fill nodes whose fill itself is visible — keep
        // their author stroke verbatim.
        if (!fillAbsent) {
            const surfHex = expandJointHex(effectiveFill)
                || (/^#[0-9a-f]{6}$/i.test(effectiveFill) ? effectiveFill : null);
            if (surfHex && jointContrastRatio(surfHex, opts.pageBg) < 1.35) {
                const authorStroke = (typeof body.stroke === 'string' && body.stroke !== 'none')
                    ? body.stroke : '#808080';
                body.stroke = ensureReadableFill(authorStroke, opts.pageBg, authorStroke, 3);
                if (body.strokeWidth === undefined) body.strokeWidth = 1;
            }
        }
        if (a.label && typeof a.label === 'object') {
            for (const k of Object.keys(a.label)) {
                if (k === 'fill') {
                    const n = normalizeJointColor(a.label.fill);
                    if (n.hex) {
                        const surf = expandJointHex(effectiveFill) || effectiveFill;
                        label.fill = (jointContrastRatio(n.hex, surf) >= 4.5)
                            ? n.hex
                            : ensureReadableFill(n.hex, surf, readableJointLabelFill(surf), 4.5);
                    } else if (n.value !== null && !n.absent) {
                        label.fill = n.value; // unknown named colour: honour verbatim
                    }
                } else if (k !== 'text') {
                    label[k] = a.label[k];
                }
            }
        }
    }

    // (3) Body fill changed but no author label fill -> recompute a readable label.
    if (bodyFillChanged && label.fill === undefined) {
        const surf = expandJointHex(effectiveFill) || effectiveFill;
        if (/^#[0-9a-f]{6}$/i.test(surf)) label.fill = readableJointLabelFill(surf);
    }

    // (4) Container: top-anchor the title so later-drawn children can't cover it.
    if (opts.isContainer) {
        label.textVerticalAnchor = 'top';
        label.refY = 0.08;
    }

    const out: { body?: Record<string, any>; label?: Record<string, any> } = {};
    if (Object.keys(body).length) out.body = body;
    if (Object.keys(label).length) out.label = label;
    return (out.body || out.label) ? out : null;
}

// ── network shape semantics (D-151) ──────────────────────────────────────────
// Every network device previously rendered as an identical rounded rect. Give
// each a distinct fill/stroke (colour-coded, contrast-checked labels) and, where
// cheap, a distinct shape (cloud -> ellipse). Full per-vendor iconography is out
// of scope for a targeted fix.
export interface JointNetworkStyle { fill: string; stroke: string; shape: 'rect' | 'ellipse'; }
const JOINT_NETWORK_STYLES: Record<string, { light: JointNetworkStyle; dark: JointNetworkStyle }> = {
    router:   { light: { fill: '#e8f0fe', stroke: '#1a56c4', shape: 'rect' },    dark: { fill: '#2b4a63', stroke: '#4cc9f0', shape: 'rect' } },
    switch:   { light: { fill: '#e6f4ea', stroke: '#137333', shape: 'rect' },    dark: { fill: '#2f4a34', stroke: '#81c995', shape: 'rect' } },
    server:   { light: { fill: '#fef7e0', stroke: '#a15c00', shape: 'rect' },    dark: { fill: '#4a3f2a', stroke: '#fdd663', shape: 'rect' } },
    firewall: { light: { fill: '#fce8e6', stroke: '#c5221f', shape: 'rect' },    dark: { fill: '#5a2b2b', stroke: '#f28b82', shape: 'rect' } },
    cloud:    { light: { fill: '#e8eaed', stroke: '#5f6368', shape: 'ellipse' }, dark: { fill: '#3c4043', stroke: '#9aa0a6', shape: 'ellipse' } },
};

/** Per-type network device style; falls back to the router style for unknowns. */
export function networkElementStyle(elementType: string, theme: 'light' | 'dark'): JointNetworkStyle {
    const e = JOINT_NETWORK_STYLES[elementType] || JOINT_NETWORK_STYLES.router;
    return theme === 'dark' ? e.dark : e.light;
}

// ── port groups (D-151 ports never drawn, D-153 layoutCallback throw) ─────────
// createPortFromSpec set `group: portSpec.type` (e.g. 'input') plus an `args`
// position, but the element defined NO matching port group, so JointJS had no
// position layout: ports were not drawn and getPortCenter threw
// `(0,i.layoutCallback) is not a function` when a link anchored to a port,
// dropping every link. Define the four side groups (each with a built-in
// position layout) on any element that carries ports, and map a port's string
// position to its side group.

/** Map a port's declared `position` string to a side group name. */
export function jointPortSide(position: any): 'top' | 'bottom' | 'left' | 'right' {
    const p = typeof position === 'string' ? position.trim().toLowerCase() : '';
    return (p === 'top' || p === 'bottom' || p === 'left' || p === 'right') ? p : 'top';
}

/** Side port-group definitions (with built-in position layouts) for an element. */
export function standardJointPortGroups(theme: 'light' | 'dark') {
    const portBody = {
        fill: theme === 'dark' ? '#4cc9f0' : '#333333',
        stroke: theme === 'dark' ? '#ffffff' : '#000000',
        strokeWidth: 1, r: 4, magnet: true,
    };
    const grp = (position: 'top' | 'bottom' | 'left' | 'right') => ({
        position,
        markup: [{ tagName: 'circle', selector: 'portBody' }],
        attrs: { portBody },
    });
    return { top: grp('top'), bottom: grp('bottom'), left: grp('left'), right: grp('right') };
}

export const jointPlugin: D3RenderPlugin = {
    name: 'joint-renderer',
    priority: 6, // Higher than basic charts, lower than mermaid/graphviz
    sizingConfig: {
        sizingStrategy: 'auto-expand',
        needsDynamicHeight: true,
        needsOverflowVisible: true,
        observeResize: false,
        minWidth: undefined,
        minHeight: 400,
        containerStyles: {
            width: '100%',
            height: 'auto',
            maxHeight: 'none',
            overflow: 'visible'
        }
    },

    canHandle: (spec: any): boolean => {
        return isJointSpec(spec);
    },

    // Helper to check if a joint definition is complete
    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check if we have at least one element definition
        const lines = definition.trim().split('\n');
        return lines.length >= 2 && lines.some(line =>
            line.includes('elements:') || line.match(/\w+\s*(?:\[\w+\])?/)
        );
    },

    render: async (container: HTMLElement, d3: any, spec: JointSpec, isDarkMode: boolean): Promise<void> => {
        console.log('Joint.js plugin render called with spec:', spec);

        // Lazy load Joint.js libraries
        const [jointCore, jointLayout] = await Promise.all([
            import('@joint/core'),
            import('@joint/layout-directed-graph')
        ]);
        const { dia, shapes, anchors, connectionPoints, routers, connectors } = jointCore;
        const { DirectedGraph } = jointLayout;

        // Make runtime dependencies available to helper functions
        (globalThis as any).__jointRuntimeDeps = {
            dia, shapes, anchors, connectionPoints, routers, connectors
        };

        try {
            // Clear container and any existing Joint.js instances
            const existingPaper = (container as any)._jointPaper;
            if (existingPaper) {
                existingPaper.remove();
                delete (container as any)._jointPaper;
            }
            container.innerHTML = '';

            // Ensure container uses full width from parent
            container.style.width = '100%';
            container.style.maxWidth = '100%';

            // Show loading spinner

            // Show loading spinner
            const loadingSpinner = document.createElement('div');
            loadingSpinner.className = 'joint-loading-spinner';
            loadingSpinner.style.cssText = `
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 2em;
                min-height: 200px;
                width: 100%;
            `;
            loadingSpinner.innerHTML = `
                <div style="
                    border: 4px solid rgba(0, 0, 0, 0.1);
                    border-top: 4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: joint-spin 1s linear infinite;
                    margin-bottom: 15px;
                "></div>
                <div style="font-family: system-ui, -apple-system, sans-serif; color: ${isDarkMode ? '#eceff4' : '#333333'};">
                    Rendering diagram...
                </div>
            `;
            container.appendChild(loadingSpinner);

            // Add spinner animation
            if (!document.querySelector('#joint-spinner-keyframes')) {
                const keyframes = document.createElement('style');
                keyframes.id = 'joint-spinner-keyframes';
                keyframes.textContent = `
                    @keyframes joint-spin {
                        0% { transform: rotate(0deg); }
                        100% { transform: rotate(360deg); }
                    }
                `;
                document.head.appendChild(keyframes);
            }

            // If we're streaming and the definition is incomplete, show a waiting message
            if (spec.isStreaming && !spec.forceRender) {
                const definition = spec.definition || '';
                const isComplete = jointPlugin.isDefinitionComplete!(definition);

                if (!isComplete) {
                    container.innerHTML = `
                        <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
                            <p>Waiting for complete Joint.js diagram definition...</p>
                            <button onclick="this.parentElement.style.display='none'; this.dispatchEvent(new CustomEvent('forceRender', { bubbles: true }))" 
                                style="background-color: #4361ee; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-top: 10px;">
                                🔄 Force Render
                            </button>
                        </div>
                    `;
                    return;
                }
            }

            // Parse the specification.
            //
            // IMPORTANT: the render tool wrapper (app/mcp/tools/diagram_render.py)
            // always packs the caller's payload into `spec.definition` as a STRING.
            // For a structured JSON joint spec that means the real elements/connections
            // arrive as a JSON blob inside `definition`, NOT as `spec.elements`.
            // Historically this routed straight into the line-oriented
            // parseJointDefinition() mini-DSL, which cannot parse JSON, produced zero
            // elements, and silently substituted a hardcoded "Element A/Element B"
            // placeholder — total, invisible data loss.
            //
            // Fix: prefer STRUCTURED input whenever it is available. If `definition`
            // is (or contains) JSON with an `elements` field, parse it and merge those
            // structured fields into the spec so the object branch handles them. Only
            // fall back to the line-DSL for genuinely non-JSON textual definitions.
            let elements: JointElement[], connections: JointLink[];

            // Normalize `elements`/`cells` (array OR id-keyed object) + `connections`
            // into the array form the element/link creation loops expect.
            //
            // Delegates to normalizeJointCells (jointShapeResolver), which is the
            // single place that (a) SPLITS a canonical JointJS `cells` array — where
            // elements and `standard.Link` cells are interleaved — into elements vs
            // links so links are never coerced into fallback rects (graphics-stress
            // Issue 41), (b) resolves namespaced `standard.*`/`custom.*` shape types
            // onto the registry vocabulary, and (c) lifts `attrs.label.text` /
            // `labels[].attrs.text.text` into the plain `label` field the creators
            // honour. Already-bare specs pass through unchanged.
            const normalizeStructured = (
                rawElements: any,
                rawConnections: any
            ): { elements: JointElement[]; connections: JointLink[] } => {
                const { elements: els, connections: conns } =
                    normalizeJointCells(rawElements, rawConnections);
                return { elements: els as JointElement[], connections: conns as JointLink[] };
            };

            // Try to recover structured input from a JSON `definition` string.
            let structuredFromDefinition: { elements: JointElement[]; connections: JointLink[] } | null = null;
            if (spec.definition) {
                const rawDef = extractDefinitionFromYAML(spec.definition, 'joint');
                // Strip a markdown ```json fence (D-140) and normalise smart quotes
                // (D-139 w4-05) BEFORE the shape gate, then parse leniently: strict
                // JSON -> JSON5 -> semicolon-separator repair (D-139). Trailing commas,
                // unquoted keys, single quotes, // and /* */ comments and semicolon
                // separators now recover instead of throwing and dropping to the
                // JSON-blind line-DSL (zero elements -> empty container -> 30s hang).
                const parsedJson = parseJointJsonish(rawDef || '');
                if (parsedJson !== undefined && parsedJson !== null) {
                    try {
                        const top = Array.isArray(parsedJson) ? { elements: parsedJson } : parsedJson;
                        // D-141: descend up to 3 levels for a wrapped graph
                        // ({graph:{cells:[...]}}, {data:{elements:[...]}}, {diagram:...},
                        // {spec:...}); the old guard only inspected depth 1, so a wrapped
                        // spec stayed null and fell to the zero-element DSL -> 30s hang.
                        const obj = findJointGraphContainer(top, 3) || top;
                        if (obj && (obj.elements || obj.cells)) {
                            // Lift structural hints (autoLayout/grid/layout/theme) that would
                            // otherwise be lost when JSON-encoded, from the graph container and
                            // its outer wrapper (container wins). D-156: only a valid theme
                            // token may be lifted — a bogus string must not outrank the render
                            // theme and flip every ternary to its light branch.
                            for (const src of [obj, top]) {
                                if (!src || typeof src !== 'object') continue;
                                if (src.autoLayout !== undefined && spec.autoLayout === undefined) spec.autoLayout = src.autoLayout;
                                if (src.grid !== undefined && spec.grid === undefined) spec.grid = src.grid;
                                if (src.layout !== undefined && spec.layout === undefined) spec.layout = src.layout;
                                if (src.theme !== undefined && spec.theme === undefined && isValidJointTheme(src.theme)) spec.theme = src.theme;
                            }
                            structuredFromDefinition = normalizeStructured(
                                obj.elements || obj.cells,
                                obj.connections || obj.links
                            );
                        }
                    } catch (e) {
                        // Parsed but not the expected shape — fall through to the DSL.
                        console.warn('joint: structured JSON normalise failed; using text parser', e);
                    }
                }
            }

            if (structuredFromDefinition && structuredFromDefinition.elements.length > 0) {
                elements = structuredFromDefinition.elements;
                connections = structuredFromDefinition.connections;
                console.log('Parsed from structured JSON definition:', {
                    elements: elements.length,
                    connections: connections.length
                });
            } else if (spec.elements) {
                // Structured object/array passed directly on the spec.
                const norm = normalizeStructured(spec.elements, (spec as any).connections);
                elements = norm.elements;
                connections = norm.connections;
                console.log('Parsed from structured spec.elements:', {
                    elements: elements.length,
                    elementIds: elements.map(e => e.id),
                    connections: connections.length
                });
            } else if ((spec as any).cells) {
                // Canonical JointJS graph passed directly on the spec as `cells`
                // (elements + standard.Link cells interleaved). Split them so links
                // are not coerced into fallback rects (graphics-stress Issue 41).
                const norm = normalizeStructured((spec as any).cells, (spec as any).links || (spec as any).connections);
                elements = norm.elements;
                connections = norm.connections;
                console.log('Parsed from structured spec.cells:', {
                    elements: elements.length,
                    connections: connections.length
                });
            } else if (spec.definition) {
                // Genuinely non-JSON textual definition: use the line-oriented mini-DSL.
                const definition = extractDefinitionFromYAML(spec.definition, 'joint');
                const parsed = parseJointDefinition(definition);
                elements = parsed.elements;
                connections = parsed.connections;
                console.log('Parsed from text definition (DSL):', {
                    elements: elements.length,
                    connections: connections.length
                });
            } else {
                throw new Error('No elements or definition provided');
            }

            console.log('Parsed elements:', elements);
            console.log('Parsed connections:', connections);

            if (elements.length === 0) {
                throw new Error('No elements found in specification');
            }

            // Sanitize element positions/sizes and link waypoints BEFORE creating cells.
            // A single element at an extreme position (x=1e8) or size (1e7x1e7), or a link
            // vertex at ±1e9, otherwise blows the graph bounding box to tens-of-millions of
            // px: the fit-to-content pass sets a ~1e7 SVG viewBox and enters a runaway
            // resize loop, so the headless screenshot never stabilizes and NO image is
            // produced (graphics-stress Issue 16). Robust median/MAD outlier clamping pulls
            // only true outliers back while leaving evenly-spread legitimate diagrams
            // untouched. Mirrors sanitizeDrawioCoordinates (drawio Issue 8).
            try {
                const sanitized = sanitizeJointGeometry(elements as any[], connections as any[]);
                elements = sanitized.elements as typeof elements;
                connections = sanitized.connections as typeof connections;
            } catch (sanitizeErr) {
                console.warn('joint: geometry sanitize failed, using raw geometry', sanitizeErr);
            }

            // D-156: resolve to a real theme. 'light'/'dark' pass through; 'auto',
            // undefined AND any bogus token ('nord-dark') fall back to the caller's
            // render theme instead of leaking through and flipping every
            // `theme === 'dark'` ternary to its light branch under dark mode.
            const theme: 'light' | 'dark' = resolveJointRenderTheme(spec.theme, isDarkMode);

            // Calculate container dimensions - walk up to find a rendered parent with actual dimensions
            const parentContainer = container.parentElement;
            let parentRect = parentContainer?.getBoundingClientRect();

            // If parent has no width (not laid out), walk up further
            let searchParent = parentContainer;
            while (searchParent && parentRect && parentRect.width === 0) {
                searchParent = searchParent.parentElement;
                parentRect = searchParent?.getBoundingClientRect();
            }

            // Use parent width if available, otherwise use viewport-relative default
            const availableWidth = (parentRect && parentRect.width > 0) ? parentRect.width : window.innerWidth * 0.8;
            const availableHeight = (parentRect && parentRect.height > 0) ? parentRect.height : 400;

            console.log('Joint.js sizing:', {
                container: container.getBoundingClientRect(),
                parentRect,
                availableWidth,
                availableHeight,
                windowWidth: window.innerWidth
            });

            const width = spec.width || Math.max(availableWidth - 40, 400);
            const height = spec.height || Math.max(availableHeight - 40, 300);

            // D-143 (G-47): coerce string-encoded booleans BEFORE the option checks
            // below (grid at paper construction, interactive, autoLayout after link
            // creation). A model-emitted "autoLayout":"false" is a truthy string, so
            // without this the `spec.autoLayout !== false` guard fired and auto-layout
            // overwrote the author's manual x/y positions. Only recognised string
            // boolean forms are converted; real booleans / undefined pass through.
            spec.autoLayout = coerceJointBoolean(spec.autoLayout);
            spec.grid = coerceJointBoolean(spec.grid);
            (spec as any).interactive = coerceJointBoolean((spec as any).interactive);

            // Create Joint.js graph and paper
            const graph = new dia.Graph({}, {
                cellNamespace: shapes
            });

            console.log('Creating Joint.js paper with dimensions:', { width, height });

            const paper = new dia.Paper({
                el: container,
                width,
                height,
                gridSize: spec.grid !== false ? 10 : 1,
                drawGrid: spec.grid !== false,
                model: graph,
                cellViewNamespace: shapes,
                anchorNamespace: anchors,
                connectionPointNamespace: connectionPoints,
                routerNamespace: routers,
                connectorNamespace: connectors,
                // Every element factory sets attrs.body.magnet = true, which makes the
                // shape body a valid link-drag source. Combined with a blanket
                // `interactive: true`, a plain pointerdown on any shape ran
                // dragMagnetStart -> dragLinkStart -> addLinkFromMagnet -> addTo(graph),
                // and the synchronous view flush that follows could throw
                // "LinkView: invalid target cell." from LinkView.checkEndModel — an
                // uncaught error outside React, escaping to the root error boundary.
                //
                // These diagrams are read-only renderings inside a chat message, so
                // authoring new links by dragging is not a wanted capability. Disabling
                // just that feature removes the crashing path while leaving element
                // dragging, clicks, and context menus intact. `labelMove: false` matches
                // JointJS's own default, which the object form would otherwise discard.
                interactive: spec.interactive === false
                    ? false
                    : { addLinkFromMagnet: false, labelMove: false },
                snapLinks: { radius: 30 },
                linkPinning: false,
                defaultAnchor: { name: 'modelCenter' },
                defaultConnectionPoint: { name: 'boundary' },
                defaultRouter: { name: 'normal' },
                defaultLink: () => new shapes.standard.Link(),
                defaultConnector: { name: 'rounded', args: { radius: 15 } },
                background: {
                    color: theme === 'dark' ? '#1f1f1f' : '#ffffff'
                },
            });

            // Store paper reference for cleanup
            (container as any)._jointPaper = paper;

            // Add ResizeObserver to handle container width changes
            const resizeObserver = new ResizeObserver((entries) => {
                for (const entry of entries) {
                    const { width: newWidth } = entry.contentRect;

                    // Only update width - height is controlled by content
                    const currentDimensions = paper.getComputedSize();
                    if (newWidth > 0 && newWidth !== currentDimensions.width && Math.abs(currentDimensions.width - newWidth) > 5) {

                        console.log('Container width changed, updating paper width:', {
                            from: currentDimensions.width,
                            to: newWidth
                        });

                        // Get content bounds to maintain proper height
                        const bbox = graph.getBBox();
                        if (bbox) {
                            const padding = 40;
                            const contentHeight = bbox.height + padding * 2;

                            // Update paper width, maintain content-based height
                            paper.setDimensions(newWidth, Math.max(contentHeight, 300));

                            // Update container and parent heights to match
                            container.style.height = `${Math.max(contentHeight, 300)}px`;
                            container.style.minHeight = `${Math.max(contentHeight, 300)}px`;

                            // Also update parent d3-container if it exists
                            const parentContainer = container.parentElement;
                            if (parentContainer?.classList.contains('d3-container')) {
                                parentContainer.style.height = 'auto';
                                parentContainer.style.minHeight = `${Math.max(contentHeight, 300)}px`;
                            }

                            // Reposition content to center
                            paper.translate(padding - bbox.x, padding - bbox.y);

                            // Update SVG viewBox
                            const svg = container.querySelector('svg');
                            if (svg) {
                                svg.setAttribute('viewBox', `0 0 ${newWidth} ${Math.max(contentHeight, 300)}`);
                            }
                        }
                    }
                }
            });

            resizeObserver.observe(container);

            // Store observer for cleanup
            (container as any)._resizeObserver = resizeObserver;

            // Force the paper container to use full width
            const paperEl = container.querySelector('.joint-paper') as HTMLElement;
            if (paperEl) {
                paperEl.style.width = '100%';
                paperEl.style.height = 'auto';
                paperEl.style.minHeight = 'unset';
                paperEl.style.maxHeight = 'none';
                console.log('Forced paper element to full width');
            }

            // Remove loading spinner
            if (loadingSpinner && loadingSpinner.parentNode === container) {
                container.removeChild(loadingSpinner);
            }

            // Create and add elements
            const jointElements: dia.Element[] = [];
            let elementIndex = 0;
            const gridCols = Math.min(Math.ceil(Math.sqrt(elements.length)), 4); // Cap at 4 columns
            const elementSpacing = Math.min((width - 100) / gridCols, 150); // Leave margins
            const totalGridWidth = (gridCols - 1) * elementSpacing;
            const startX = Math.max(60, (width - totalGridWidth) / 2);
            const startY = 80;

            // Create shape registry for enhanced element creation
            const shapeRegistry = createShapeRegistry();

            // G-48: index specs by id + resolve the page surface, so the per-element
            // post-create hook below can honour author `attrs` (D-152) and style
            // nested containers by depth (D-150).
            const jointSpecById = new Map<string, any>();
            elements.forEach((e: any) => { if (e && e.id != null) jointSpecById.set(String(e.id), e); });
            const jointPageBg = jointPageBackground(theme);

            console.log('🔧 JOINT-DEBUG: Starting element creation');
            console.log('🔧 JOINT-DEBUG: Shape registry keys:', Object.keys(shapeRegistry));

            elements.forEach(elementSpec => {
                try {
                    // Use shape registry for enhanced shapes
                    const shapeType = elementSpec.type || elementSpec.shape || 'rect';

                    console.log(`🔧 JOINT-DEBUG: Processing element ${elementSpec.id}:`, {
                        type: elementSpec.type,
                        shape: elementSpec.shape,
                        shapeType: shapeType,
                        hasCreator: !!shapeRegistry[shapeType]
                    });

                    const shapeCreator = shapeRegistry[shapeType];
                    if (!shapeCreator) {
                        console.warn(`🔧 JOINT-DEBUG: No shape creator found for "${shapeType}", using rect as fallback`);
                        const fallbackCreator = shapeRegistry['rect'];
                        if (!fallbackCreator) {
                            throw new Error(`No shape creator for "${shapeType}" and no rect fallback available`);
                        }
                    }

                    const actualCreator = shapeRegistry[shapeType] || shapeRegistry['rect'];

                    // Ensure element has required properties
                    let defaultPosition = elementSpec.position;

                    // Only auto-position if position is completely missing or clearly invalid
                    const needsAutoPosition = !defaultPosition ||
                        (Array.isArray(defaultPosition) && (defaultPosition[0] < 0 || defaultPosition[1] < 0)) ||
                        (typeof defaultPosition === 'object' && !Array.isArray(defaultPosition) &&
                            ('x' in defaultPosition && 'y' in defaultPosition && (defaultPosition.x < 0 || defaultPosition.y < 0)));

                    if (needsAutoPosition) {
                        // Use grid layout for better default positioning
                        const col = elementIndex % gridCols;
                        const row = Math.floor(elementIndex / gridCols);
                        defaultPosition = [startX + col * elementSpacing, startY + row * 120];
                        console.log(`🔧 JOINT-DEBUG: Auto-positioning element ${elementSpec.id} at grid (${col}, ${row}) -> (${defaultPosition[0]}, ${defaultPosition[1]})`);
                    }

                    const elementWithDefaults = {
                        ...elementSpec,
                        position: defaultPosition,
                        size: elementSpec.size || { width: 120, height: 80 }
                    };

                    const element = actualCreator(elementWithDefaults, theme);
                    if (element) {
                        // D-144: @joint/core v4 rejects a cell whose `type` is not a
                        // non-empty string ('dia.Graph: cell type must be a string').
                        // The bare `new dia.Element({...})` custom creators never set
                        // one, so guarantee a namespaced fallback type (resolves to the
                        // default ElementView, which renders the element's own markup)
                        // BEFORE addCell so the cell — and every link touching it — is
                        // not dropped. standard.* shapes already carry a type: untouched.
                        try {
                            const existingType = (element as any)?.attributes?.type;
                            if (!isValidCellType(existingType)) {
                                (element as any).set('type', fallbackCellType(shapeType));
                            }
                        } catch (typeErr) {
                            console.warn(`joint: could not assign fallback type for ${elementSpec.id}`, typeErr);
                        }
                        // G-48: honour author `attrs` (D-152) and style nested
                        // containers (D-150). Reads the element's own creator fill as
                        // the surface for label-contrast reasoning, then applies the
                        // merged { body?, label? } patch. No-op (byte-identical) when
                        // the element is neither a container nor carries author attrs.
                        try {
                            const curFill = (typeof (element as any).attr === 'function'
                                ? (element as any).attr('body/fill') : undefined) || '#ffffff';
                            const stylePatch = computeJointElementStyle(elementSpec, {
                                theme,
                                defaultBodyFill: String(curFill),
                                pageBg: jointPageBg,
                                depth: jointElementDepth(elementSpec.id, jointSpecById),
                                isContainer: isJointContainer(elementSpec),
                            });
                            if (stylePatch) (element as any).attr(stylePatch);
                        } catch (styleErr) {
                            console.warn(`joint: could not apply author/container style for ${elementSpec.id}`, styleErr);
                        }
                        graph.addCell(element);
                        jointElements.push(element);
                        console.log(`🔧 JOINT-DEBUG: ✓ Created element ${elementSpec.id}`);
                    }
                } catch (error) {
                    console.warn(`Failed to create element ${elementSpec.id}:`, error);
                } finally {
                    elementIndex++;
                }
            });

            console.log(`Created ${jointElements.length} elements out of ${elements.length} specified`);

            if (jointElements.length === 0) {
                throw new Error('No elements were successfully created');
            }

            // Create and add links
            const jointLinks: dia.Link[] = [];
            // D-131 / D-407: pre-compute, per unordered node pair, how many links
            // connect it and each link's position within that bucket, so labels on
            // parallel/antiparallel links (e.g. a<->b) can be staggered apart rather
            // than piled at one midpoint.
            const jointPairCount = new Map<string, number>();
            const jointPairSeen = new Map<string, number>();
            connections.forEach(ls => {
                const key = linkPairKey(ls.source, ls.target);
                jointPairCount.set(key, (jointPairCount.get(key) || 0) + 1);
            });
            connections.forEach(linkSpec => {
                try {
                    // Validate that source and target elements exist
                    const sourceId = typeof linkSpec.source === 'string' ? linkSpec.source : linkSpec.source.id;
                    const targetId = typeof linkSpec.target === 'string' ? linkSpec.target : linkSpec.target.id;

                    const sourceExists = jointElements.some(el => el.id === sourceId);
                    const targetExists = jointElements.some(el => el.id === targetId);

                    if (!sourceExists) {
                        console.warn(`Link source "${sourceId}" not found in created elements`);
                        return;
                    }
                    if (!targetExists) {
                        console.warn(`Link target "${targetId}" not found in created elements`);
                        return;
                    }

                    const pairKey = linkPairKey(linkSpec.source, linkSpec.target);
                    const pairIdx = jointPairSeen.get(pairKey) || 0;
                    jointPairSeen.set(pairKey, pairIdx + 1);
                    const link = createEnhancedLink(linkSpec, theme, {
                        index: pairIdx,
                        count: jointPairCount.get(pairKey) || 1,
                    });
                    if (link) {
                        jointLinks.push(link);
                        graph.addCell(link);
                        console.log(`Created link: ${linkSpec.id}`, link);
                    }
                } catch (error) {
                    console.warn(`Failed to create link ${linkSpec.id}:`, error);
                }
            });

            console.log(`Created ${jointLinks.length} links out of ${connections.length} specified`);

            // Apply auto-layout if enabled
            if (spec.autoLayout !== false && jointElements.length > 1) {
                console.log('Applying DirectedGraph layout to Joint.js diagram');

                try {
                    DirectedGraph.layout(graph, {
                        nodeSep: 50,
                        edgeSep: 80,
                        rankSep: 100,
                        marginX: 30,
                        marginY: 30,
                        rankDir: 'TB', // Top to bottom
                        resizeClusters: true,
                        clusterPadding: { top: 40, left: 10, right: 10, bottom: 10 }
                    });
                    console.log('DirectedGraph layout applied successfully');
                } catch (layoutError) {
                    console.warn('Auto-layout failed, using manual positioning:', layoutError);
                    // D-030 / D-111: DirectedGraph.layout throws at scale (~131 nodes)
                    // leaving every auto-layout element at its default {0,0} — one
                    // illegible pile. Reposition them in a deterministic non-overlapping
                    // reading-order grid so the graph degrades to a legible matrix. Only
                    // runs on this degraded path; a successful layout is never touched.
                    try {
                        const cells: JointGridCell[] = jointElements.map(el => {
                            const s = (typeof (el as any).size === 'function')
                                ? (el as any).size() : undefined;
                            return {
                                id: String((el as any).id),
                                width: (s && s.width) || 120,
                                height: (s && s.height) || 60,
                            };
                        });
                        const placements = computeGridFallbackPositions(cells);
                        const byId = new Map(placements.map(p => [p.id, p]));
                        jointElements.forEach(el => {
                            const p = byId.get(String((el as any).id));
                            if (p && typeof (el as any).position === 'function') {
                                (el as any).position(p.x, p.y);
                            }
                        });
                        console.log('joint: applied grid fallback positions to', placements.length, 'elements');
                    } catch (gridErr) {
                        console.warn('joint: grid fallback positioning failed', gridErr);
                    }
                }
            }

            // Fit content to paper after layout - ensure all content is visible
            const fitContentToPaper = () => {
                // Get the actual content bounds
                const bbox = graph.getBBox();
                console.log('Graph bounding box:', bbox);

                if (bbox && bbox.width > 0 && bbox.height > 0) {
                    const padding = 40;
                    const contentWidth = bbox.width + padding * 2;
                    const contentHeight = bbox.height + padding * 2;

                    // Get current container size for responsive width
                    const containerRect = container.getBoundingClientRect();
                    const containerWidth = containerRect.width > 0 ? containerRect.width : width;

                    // D-145: never emit an SVG bigger than the capture box. The old
                    // path set finalWidth = max(content, container) and wrote that
                    // oversized value into the viewBox while the SVG rendered at
                    // container width, so any graph larger than one capture window was
                    // CROPPED and the rest silently dropped from the PNG (no downscale
                    // existed). Bound the emitted paper/SVG to (containerWidth x
                    // JOINT_MAX_RENDER_HEIGHT) and frame the FULL content extent via the
                    // viewBox, so an oversized graph is scaled down to fit instead of
                    // clipped. Content that already fits keeps its natural size.
                    const maxHeight = Math.max(JOINT_MAX_RENDER_HEIGHT, spec.height || 0);
                    const plan = computeJointFitPlan(contentWidth, contentHeight, containerWidth, maxHeight);
                    const finalWidth = plan.paperWidth;
                    const finalHeight = plan.paperHeight;

                    paper.setDimensions(finalWidth, finalHeight);

                    // D-404: size the container WIDTH to the paper too. The paper now
                    // carries the content bbox aspect (computeJointFitPlan), so pinning
                    // both container dimensions to the paper makes the SVG box and the
                    // viewBox aspect-identical: preserveAspectRatio 'meet' then fills the
                    // box exactly with no letterbox band and no edge clip. Width is
                    // allowed to exceed the original container for a floored oversized
                    // graph (D-405); the downstream capture-fit shrinks that surface.
                    container.style.width = `${finalWidth}px`;
                    container.style.maxWidth = 'none';

                    // Update container height to match paper
                    container.style.height = `${finalHeight}px`;
                    container.style.minHeight = `${finalHeight}px`;
                    container.style.maxHeight = 'none';

                    // Propagate height to parent containers
                    const parentContainer = container.parentElement;
                    if (parentContainer?.classList.contains('d3-container')) {
                        parentContainer.style.height = `${finalHeight}px`;
                        parentContainer.style.minHeight = `${finalHeight}px`;
                        parentContainer.style.maxHeight = 'none';
                    }

                    // Update grandparent if it exists (outer wrapper)
                    const grandparentContainer = parentContainer?.parentElement;
                    if (grandparentContainer?.classList.contains('d3-container')) {
                        grandparentContainer.style.height = `${finalHeight}px`;
                        grandparentContainer.style.minHeight = `${finalHeight}px`;
                        grandparentContainer.style.maxHeight = 'none';
                    }

                    // Also update max-height to allow growth
                    container.style.maxHeight = 'none';

                    // Keep the paper at the identity transform: the viewBox (set below)
                    // maps the full content extent into the bounded viewport, so no
                    // paper.translate is needed and the earlier oversize-crop is gone.
                    paper.translate(0, 0);
                    console.log('Paper dimensions updated to fit content', plan);

                    // Force SVG to scale properly. The viewBox frames the whole content
                    // bbox (in paper coordinates) so preserveAspectRatio 'meet' scales
                    // ALL content to fit the bounded SVG — nothing is cropped.
                    const svg = container.querySelector('svg');
                    if (svg) {
                        svg.style.width = '100%';
                        svg.style.height = '100%';
                        svg.style.maxWidth = '100%';
                        svg.style.maxHeight = 'none'; // Allow vertical growth
                        const vbX = bbox.x - padding;
                        const vbY = bbox.y - padding;
                        svg.setAttribute('viewBox', `${vbX} ${vbY} ${contentWidth} ${contentHeight}`);
                        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                        console.log('SVG viewBox set to content extent:', vbX, vbY, contentWidth, contentHeight);
                    }
                }
            };

            // Fit content after layout completes
            setTimeout(fitContentToPaper, 300);

            // Add interaction handlers
            paper.on('element:pointerclick', (elementView: dia.ElementView) => {
                console.log('Element clicked:', elementView.model.id);
                // Add selection highlighting
                graph.getElements().forEach(el => {
                    const view = paper.findViewByModel(el);
                    if (view) view.unhighlight();
                });
                elementView.highlight();
            });

            paper.on('link:pointerclick', (linkView: dia.LinkView) => {
                console.log('Link clicked:', linkView.model.id);
            });

            // Add context menu for right-click
            paper.on('element:contextmenu', (elementView: dia.ElementView, evt: dia.Event) => {
                evt.preventDefault();
                console.log('Element right-clicked:', elementView.model.id);
                // Could add context menu here
            });

            // Add action buttons
            const actionsContainer = document.createElement('div');
            actionsContainer.className = 'diagram-actions';
            actionsContainer.style.cssText = `
                position: absolute;
                top: 10px;
                right: 10px;
                z-index: 1000;
                display: flex;
                gap: 5px;
            `;

            // Fit to content button
            const fitButton = document.createElement('button');
            fitButton.innerHTML = '🔍 Fit';
            fitButton.className = 'diagram-action-button';
            fitButton.style.cssText = `
                background-color: #4361ee;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                cursor: pointer;
                font-size: 12px;
                margin: 2px;
            `;
            fitButton.onclick = () => {
                paper.scaleContentToFit({ padding: 20, minScale: 0.5, maxScale: 1.5 });

                // Also update container height after fitting
                setTimeout(() => {
                    const bbox = graph.getBBox();
                    if (bbox) {
                        container.style.height = `${bbox.height + 80}px`;
                    }
                }, 100);
            };
            actionsContainer.appendChild(fitButton);

            // Make container relative for absolute positioning  
            container.style.position = 'relative';
            container.appendChild(actionsContainer);

            console.log('Joint.js diagram rendered successfully');

        } catch (error) {
            console.error('Joint.js rendering error:', error);

            // Remove loading spinner if it exists
            const spinner = container.querySelector('.joint-loading-spinner') as HTMLElement;
            if (spinner && spinner.parentNode === container) {
                container.removeChild(spinner);
            }

            // D-409: tag the error card with the cross-plugin `data-diagram-error`
            // contract marker. The headless capture harness (DiagramRenderPage)
            // decides a render finished by polling for an svg/canvas/img OR an
            // element carrying this marker. The joint error card is a styled <div>
            // with none of those, so without the marker a spec that threw (e.g. an
            // EMPTY elements array -> 'No elements found in specification') produced
            // no readiness signal and the harness spun to its 30s cap, returning a
            // blank 'Render timeout' with svg:0 instead of the real message.
            const jointErrMsg = error instanceof Error ? error.message : 'Unknown error';
            const jointErrAttr = jointErrMsg.replace(/"/g, '&quot;');
            container.innerHTML = `
                <div class="joint-error" data-diagram-error="${jointErrAttr}" style="
                    padding: 16px;
                    margin: 16px 0;
                    border-radius: 6px;
                    background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
                    border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
                    color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                ">
                    <strong>Joint.js Rendering Error:</strong>
                    <p>${error instanceof Error ? error.message : 'Unknown error'}</p>
                    <details>
                        <summary>Show Definition</summary>
                    <pre><code>${spec.definition || JSON.stringify(spec, null, 2)}</code></pre>
                </details>
            </div>
            `;
        }
    }
};

// Helper functions for network elements
const getDefaultSizeForNetworkElement = (elementType: string) => {
    const sizes = {
        router: { width: 80, height: 60 },
        switch: { width: 100, height: 40 },
        server: { width: 60, height: 80 },
        firewall: { width: 80, height: 80 },
        cloud: { width: 120, height: 80 }
    };
    return sizes[elementType as keyof typeof sizes] || { width: 80, height: 60 };
};

export const getNetworkElementAttrs = (elementType: string, theme: 'light' | 'dark') => {
    // D-151: colour-code the body per device type so five distinct device types
    // are no longer identical rounded rects. The label colour is derived from the
    // resolved fill (readableJointLabelFill) so it clears the text floor in BOTH
    // themes regardless of which per-type fill was chosen.
    const s = networkElementStyle(elementType, theme);
    // D-406: rx/ry are the CORNER radii of a rounded rect, but for a shape whose
    // body is an <ellipse> (cloud -> shapes.standard.Ellipse since D-151) they ARE
    // the ellipse radii. Emitting rx:15/ry:15 collapsed the 120x80 cloud ellipse to
    // a ~30px circle and struck the label through. Only emit corner radii on rect
    // bodies; let an ellipse body take its radii from the element width/height.
    const body: Record<string, any> = {
        fill: s.fill,
        stroke: s.stroke,
        strokeWidth: 2,
    };
    if (s.shape !== 'ellipse') {
        body.rx = 5;
        body.ry = 5;
    }
    return {
        body,
        label: {
            fill: readableJointLabelFill(s.fill),
            fontSize: 11,
            fontFamily: 'Arial, sans-serif',
            textAnchor: 'middle',
            textVerticalAnchor: 'middle'
        }
    };
};

const getDefaultPortsForNetworkElement = (elementType: string) => {
    const portConfigs = {
        router: [
            { id: 'wan', position: 'top', type: 'input' as const },
            { id: 'lan1', position: 'bottom', type: 'output' as const },
            { id: 'lan2', position: 'left', type: 'output' as const },
            { id: 'lan3', position: 'right', type: 'output' as const }
        ],
        switch: [
            { id: 'port1', position: 'left', type: 'inout' as const },
            { id: 'port2', position: 'left', type: 'inout' as const },
            { id: 'port3', position: 'right', type: 'inout' as const },
            { id: 'port4', position: 'right', type: 'inout' as const }
        ],
        server: [
            { id: 'network', position: 'top', type: 'input' as const },
            { id: 'storage', position: 'bottom', type: 'output' as const }
        ],
        firewall: [
            { id: 'external', position: 'left', type: 'input' as const },
            { id: 'internal', position: 'right', type: 'output' as const }
        ],
        cloud: [
            { id: 'connection', position: 'bottom', type: 'inout' as const }
        ]
    };

    return portConfigs[elementType as keyof typeof portConfigs] || [];
};

// Helper functions for electrical elements
const getDefaultSizeForElectricalElement = (elementType: string): { width: number; height: number } => {
    const sizes = {
        resistor: { width: 60, height: 20 },
        capacitor: { width: 40, height: 40 },
        inductor: { width: 60, height: 30 },
        battery: { width: 40, height: 60 },
        ground: { width: 40, height: 30 },
        voltage_source: { width: 40, height: 40 },
        current_source: { width: 40, height: 40 },
        diode: { width: 30, height: 30 },
        transistor: { width: 50, height: 40 }
    };
    return sizes[elementType as keyof typeof sizes] || { width: 40, height: 40 };
};

const getDefaultValueForElement = (elementType: string): string => {
    const defaultValues = {
        resistor: '1kΩ',
        capacitor: '100µF',
        inductor: '1mH',
        battery: '9V',
        voltage_source: '5V',
        current_source: '1A'
    };
    return defaultValues[elementType as keyof typeof defaultValues] || '';
};

const getElectricalElementAttrs = (elementType: string, theme: 'light' | 'dark') => {
    const baseColor = theme === 'dark' ? '#ffffff' : '#000000';
    const fillColor = theme === 'dark' ? 'transparent' : 'transparent';

    const commonAttrs = {
        body: {
            fill: fillColor,
            stroke: baseColor,
            strokeWidth: 2
        }
    };

    return commonAttrs;
};

const getElectricalElementMarkup = (elementType: string) => {
    // Simple markup for now - can be enhanced with proper electrical symbols
    const markups = {
        resistor: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        capacitor: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'line', selector: 'plate1' },
            { tagName: 'line', selector: 'plate2' },
            { tagName: 'text', selector: 'label' }
        ],
        battery: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'line', selector: 'positive' },
            { tagName: 'line', selector: 'negative' },
            { tagName: 'text', selector: 'label' }
        ]
    };

    return markups[elementType as keyof typeof markups] || [
        { tagName: 'rect', selector: 'body' },
        { tagName: 'text', selector: 'label' }
    ];
};

const getDefaultPortsForElectricalElement = (elementType: string) => {
    const portConfigs = {
        resistor: [
            { id: 'terminal1', position: 'left', type: 'inout' as const },
            { id: 'terminal2', position: 'right', type: 'inout' as const }
        ],
        capacitor: [
            { id: 'positive', position: 'top', type: 'inout' as const },
            { id: 'negative', position: 'bottom', type: 'inout' as const }
        ],
        battery: [
            { id: 'positive', position: 'top', type: 'output' as const },
            { id: 'negative', position: 'bottom', type: 'input' as const }
        ],
        diode: [
            { id: 'anode', position: 'left', type: 'input' as const },
            { id: 'cathode', position: 'right', type: 'output' as const }
        ],
        transistor: [
            { id: 'base', position: 'left', type: 'input' as const },
            { id: 'collector', position: 'top', type: 'output' as const },
            { id: 'emitter', position: 'bottom', type: 'output' as const }
        ]
    };

    return portConfigs[elementType as keyof typeof portConfigs] || [
        { id: 'port1', position: 'left', type: 'inout' },
        { id: 'port2', position: 'right', type: 'inout' }
    ];
};

// Convert our Port interface to Joint.js port format
const createJointPort = (portSpec: Port, theme: 'light' | 'dark') => {
    const portPosition = getPortPosition(portSpec.position || 'top');

    return {
        id: portSpec.id,
        group: portSpec.type || 'default',
        args: portPosition,
        markup: [{
            tagName: 'circle',
            selector: 'portBody'
        }],
        attrs: {
            portBody: {
                fill: theme === 'dark' ? '#4cc9f0' : '#333333',
                stroke: theme === 'dark' ? '#ffffff' : '#000000',
                strokeWidth: 1,
                r: 4,
                magnet: true
            }
        },
        label: {
            position: { name: 'outside' },
            markup: [{ tagName: 'text', selector: 'label' }],
            attrs: {
                label: {
                    text: portSpec.label || '',
                    fill: theme === 'dark' ? '#ffffff' : '#000000',
                    fontSize: 10,
                    textAnchor: 'middle'
                }
            }
        }
    };
};

const createPortFromSpec = (portSpec: Port, theme: 'light' | 'dark') => {
    // D-153: the port's `group` MUST reference a group the element actually
    // defines (standardJointPortGroups adds top/bottom/left/right, each with a
    // built-in position layout). Keying `group` on portSpec.type (e.g. 'input')
    // left the port group-less -> no layout -> getPortCenter threw
    // `layoutCallback is not a function` and every link to the port was dropped.
    // Map the declared string position to its side group instead.
    const port: any = {
        id: portSpec.id,
        group: jointPortSide(portSpec.position),
        markup: [{
            tagName: 'circle',
            selector: 'portBody'
        }],
        attrs: {
            portBody: {
                fill: theme === 'dark' ? '#4cc9f0' : '#333333',
                stroke: theme === 'dark' ? '#ffffff' : '#000000',
                strokeWidth: 1,
                r: 4,
                magnet: true
            }
        }
    };
    if (portSpec.label) {
        port.label = {
            position: { name: 'outside' },
            markup: [{ tagName: 'text', selector: 'label' }],
            attrs: {
                label: {
                    text: portSpec.label,
                    fill: theme === 'dark' ? '#ffffff' : '#000000',
                    fontSize: 10,
                    textAnchor: 'middle'
                }
            }
        };
    }
    return port;
};

const getPortPosition = (position: string) => {
    const positions = {
        top: { x: '50%', y: '0%' },
        bottom: { x: '50%', y: '100%' },
        left: { x: '0%', y: '50%' },
        right: { x: '100%', y: '50%' }
    };
    return positions[position as keyof typeof positions] || { x: '50%', y: '50%' };
};



const createDatabaseElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 100 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'ellipse', selector: 'top' },
            { tagName: 'rect', selector: 'body' },
            { tagName: 'ellipse', selector: 'bottom' },
            { tagName: 'ellipse', selector: 'bottomShadow' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            top: {
                cx: size.width / 2, cy: 10, rx: size.width / 2 - 2, ry: 10,
                fill: theme === 'dark' ? '#a3be8c' : '#2ecc71',
                stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                strokeWidth: 2
            },
            body: {
                x: 2, y: 10, width: size.width - 4, height: size.height - 20,
                fill: theme === 'dark' ? '#a3be8c' : '#2ecc71',
                stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                strokeWidth: 2
            },
            bottom: {
                cx: size.width / 2, cy: size.height - 10, rx: size.width / 2 - 2, ry: 10,
                fill: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                strokeWidth: 2
            },
            bottomShadow: {
                cx: size.width / 2, cy: size.height - 8, rx: size.width / 2 - 4, ry: 6,
                fill: theme === 'dark' ? '#4c566a' : '#229954',
                opacity: 0.7
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                fontSize: 11,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2
            }
        }
    });
};

const createStorageElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#b48ead' : '#9b59b6',
                stroke: theme === 'dark' ? '#d08770' : '#8e44ad',
                strokeWidth: 2,
                rx: 10,
                ry: 10,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createMessageElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'path', selector: 'flap' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                x: 0, y: 0, width: size.width, height: size.height,
                fill: theme === 'dark' ? '#ebcb8b' : '#f39c12',
                stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                strokeWidth: 2,
                rx: 5,
                ry: 5
            },
            flap: {
                d: `M 0,0 L ${size.width / 2},${size.height / 3} L ${size.width},0`,
                fill: 'none',
                stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

const createModuleElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#81a1c1' : '#2980b9',
                strokeWidth: 3,
                strokeDasharray: '10,5',
                rx: 8,
                ry: 8,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedUMLElement = (elementSpec: JointElement, umlType: 'class' | 'interface' | 'package', theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 160, height: 120 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    const colors = {
        class: { fill: theme === 'dark' ? '#4c566a' : '#ffffff', stroke: theme === 'dark' ? '#88c0d0' : '#2c3e50' },
        interface: { fill: theme === 'dark' ? '#5e81ac' : '#e8f4fd', stroke: theme === 'dark' ? '#81a1c1' : '#3498db' },
        package: { fill: theme === 'dark' ? '#a3be8c' : '#e8f5e8', stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60' }
    };

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: colors[umlType].fill,
                stroke: colors[umlType].stroke,
                strokeWidth: 2,
                rx: 5,
                ry: 5,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: umlType === 'interface' ? `<<interface>>\n${text}` : text,
                // D-416: the label sits ON the compartment fill, so its colour must be
                // resolved from that RESOLVED fill — not a blind theme constant. The old
                // dark #eceff4 landed on the pastel package fill #a3be8c at 1.77:1
                // (washed out) and on the interface fill #5e81ac at 3.50:1. Resolve from
                // the actual fill so both themes clear the 4.5 text floor: dark package
                // #a3be8c -> #14171c = 8.81, interface #5e81ac -> #000000 = 5.21, class
                // #4c566a -> #f7f9fc = 6.99; light fills (#ffffff/#e8f4fd/#e8f5e8) ->
                // #14171c = 15.96-17.96.
                fill: readableJointLabelFill(colors[umlType].fill),
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'top',
                y: 15
            }
        }
    });
};


const createNoteElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 0,0 L ${size.width - 15},0 L ${size.width},15 L ${size.width},${size.height} L 0,${size.height} Z M ${size.width - 15},0 L ${size.width - 15},15 L ${size.width},15`,
                fill: theme === 'dark' ? '#ebcb8b' : '#fff3cd',
                stroke: theme === 'dark' ? '#d08770' : '#ffc107',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#856404',
                fontSize: 11,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'normal',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2 - 7,
                y: size.height / 2
            }
        }
    });
};


const createDataElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 15,0 L ${size.width},0 L ${size.width - 15},${size.height} L 0,${size.height} Z`,
                fill: theme === 'dark' ? '#b48ead' : '#9b59b6',
                stroke: theme === 'dark' ? '#d08770' : '#8e44ad',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2
            }
        }
    });
};

const createSubprocessElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'rect', selector: 'plus1' },
            { tagName: 'rect', selector: 'plus2' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                x: 0, y: 0, width: size.width, height: size.height,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#81a1c1' : '#2980b9',
                strokeWidth: 2,
                rx: 5,
                ry: 5
            },
            plus1: {
                x: size.width / 2 - 8, y: size.height / 2 - 2,
                width: 16, height: 4,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff'
            },
            plus2: {
                x: size.width / 2 - 2, y: size.height / 2 - 8,
                width: 4, height: 16,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'top',
                x: size.width / 2,
                y: 10
            }
        }
    });
};

const createManualElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 0,15 Q 30,0 60,15 Q 90,0 120,15 L 120,80 L 0,80 Z`,
                fill: theme === 'dark' ? '#d08770' : '#e67e22',
                stroke: theme === 'dark' ? '#bf616a' : '#d35400',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

// Add missing shape creation functions that are referenced in the existing code
const createActorElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 60, height: 100 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'circle', selector: 'head' },
            { tagName: 'line', selector: 'body' },
            { tagName: 'line', selector: 'leftArm' },
            { tagName: 'line', selector: 'rightArm' },
            { tagName: 'line', selector: 'leftLeg' },
            { tagName: 'line', selector: 'rightLeg' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            head: {
                cx: size.width / 2, cy: 15, r: 10,
                fill: theme === 'dark' ? '#d08770' : '#f39c12',
                stroke: theme === 'dark' ? '#bf616a' : '#e67e22',
                strokeWidth: 2
            },
            body: { x1: size.width / 2, y1: 25, x2: size.width / 2, y2: 60, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            leftArm: { x1: size.width / 2, y1: 35, x2: size.width / 2 - 15, y2: 50, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            rightArm: { x1: size.width / 2, y1: 35, x2: size.width / 2 + 15, y2: 50, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            leftLeg: { x1: size.width / 2, y1: 60, x2: size.width / 2 - 15, y2: 85, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            rightLeg: { x1: size.width / 2, y1: 60, x2: size.width / 2 + 15, y2: 85, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                fontSize: 10,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width / 2,
                y: size.height - 5
            }
        }
    });
};

const createLogicGate = (elementSpec: JointElement, gateType: string, theme: 'light' | 'dark') => {
    // Fallback to enhanced rectangle for logic gates
    return createEnhancedRectElement({
        ...elementSpec,
        text: `${gateType.toUpperCase()} Gate`
    }, theme);
};

const createCustomElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    // Fallback to enhanced rectangle for custom elements
    return createEnhancedRectElement(elementSpec, theme);
};
