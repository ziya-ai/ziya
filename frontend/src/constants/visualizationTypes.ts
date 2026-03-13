/**
 * Canonical list of code-fence language names that are visualizations.
 *
 * This is the single source of truth on the frontend. Consumers:
 *   - MarkdownRenderer.tsx  (classifies code blocks for D3Renderer)
 *   - visualizationCapture.ts  (captures rendered diagrams for export)
 *   - conversation_exporter.py  (Python mirror — keep in sync manually)
 *
 * Aliases (e.g. 'dot' → 'graphviz', 'bytefield' → 'packet') are
 * resolved upstream in MarkdownRenderer before hitting this list.
 */
export const VISUALIZATION_TYPES = [
  'graphviz',
  'mermaid',
  'vega-lite',
  'd3',
  'joint',
  'circuitikz',
  'packet',
  'drawio',
  'designinspector',
] as const;

export type VisualizationType = (typeof VISUALIZATION_TYPES)[number];

/**
 * Check whether a (normalised) code-fence language name is a
 * visualisation type that gets routed to D3Renderer.
 */
export function isVisualizationType(lang: string): boolean {
  return (VISUALIZATION_TYPES as readonly string[]).includes(lang);
}
