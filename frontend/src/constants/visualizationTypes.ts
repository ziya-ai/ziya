import { LATEX_PROFILE_KEYS } from './latexProfiles';

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
 export const VISUALIZATION_TYPES: readonly string[] = [
  'graphviz',
  'mermaid',
  'vega-lite',
  'd3',
  'joint',
   // Every LaTeX profile, not just circuitikz.  Listing one by hand meant a
   // rendered chemfig/tikz/tikz-cd diagram fell through to the 'd3' default in
   // visualizationCapture.ts, because D3Renderer names its container after the
   // PROFILE (``${plugin.name}-container``), not after 'circuitikz'.
   ...LATEX_PROFILE_KEYS,
  'packet',
  'drawio',
  'designinspector',
 ];

 export type VisualizationType = string;

/**
 * Check whether a (normalised) code-fence language name is a
 * visualisation type that gets routed to D3Renderer.
 */
export function isVisualizationType(lang: string): boolean {
  return (VISUALIZATION_TYPES as readonly string[]).includes(lang);
}
