/**
 * STUB — CircuiTikZ parser utilities.
 *
 * This module exists only to satisfy the TS5 migration build: the real
 * parser was never implemented (see MarkdownRenderer.tsx's 'circuitikz'
 * case, which renders circuit diagrams as a plain LaTeX code block with
 * an explicit comment noting no visual renderer is registered).
 *
 * The exported functions here are minimal, type-correct stubs — they do
 * NOT parse real TikZ/CircuiTikZ syntax. circuitikzPlugin.test.ts's
 * assertions on parsed circuit structure will not pass against this stub.
 * Tracked separately as a follow-up: implement the real parser (element
 * classification, relative ++ coordinates, named \coordinate resolution,
 * ground detection, etc.) per the test file's documented expectations.
 */

export type CircuitElementKind =
    | 'resistor'
    | 'capacitor'
    | 'inductor'
    | 'diode'
    | 'switch'
    | 'voltage-source'
    | 'wire';

export interface CircuitPoint {
    x: number;
    y: number;
}

export interface CircuitElement {
    kind: CircuitElementKind;
    from: CircuitPoint;
    to: CircuitPoint;
    label?: string;
}

export interface CircuitGround {
    at: CircuitPoint;
}

export interface CircuitLabel {
    text: string;
    at: CircuitPoint;
}

export interface ParsedCircuit {
    elements: CircuitElement[];
    grounds: CircuitGround[];
    labels: CircuitLabel[];
}

export interface CircuitBounds {
    minX: number;
    minY: number;
    maxX: number;
    maxY: number;
}

/**
 * STUB: does not actually parse TikZ syntax. Always returns an empty
 * result. Never throws, matching the real parser's documented contract
 * of tolerating malformed/partial input.
 */
export function parseCircuit(_definition: string): ParsedCircuit {
    return { elements: [], grounds: [], labels: [] };
}

/**
 * STUB: rough heuristic only (balanced parens + presence of a terminated
 * \draw statement). Not a faithful reimplementation of the real
 * completeness contract described in the test file.
 */
export function isCircuitDefinitionComplete(definition: string): boolean {
    const trimmed = definition.trim();
    if (!trimmed) return false;

    let depth = 0;
    for (const ch of trimmed) {
        if (ch === '(') depth++;
        else if (ch === ')') depth--;
        if (depth < 0) return false;
    }
    if (depth !== 0) return false;

    return /\\draw\b[^;]*;/.test(trimmed);
}

/**
 * STUB: computes a bounding box over whatever elements/grounds/labels
 * are present; returns the documented default box when the circuit is
 * empty. This part is real logic (not parsing), so it's implemented
 * properly rather than stubbed.
 */
export function bounds(parsed: ParsedCircuit): CircuitBounds {
    const points: CircuitPoint[] = [
        ...parsed.elements.flatMap(el => [el.from, el.to]),
        ...parsed.grounds.map(g => g.at),
        ...parsed.labels.map(l => l.at),
    ];

    if (points.length === 0) {
        return { minX: 0, minY: 0, maxX: 1, maxY: 1 };
    }

    return {
        minX: Math.min(...points.map(p => p.x)),
        minY: Math.min(...points.map(p => p.y)),
        maxX: Math.max(...points.map(p => p.x)),
        maxY: Math.max(...points.map(p => p.y)),
    };
}
