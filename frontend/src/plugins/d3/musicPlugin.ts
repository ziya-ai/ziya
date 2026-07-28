/**
 * D3 render plugin for annotated music notation (full-chrome, Tier 2).
 *
 * Recognises specs with `type: 'music'`.  Renders staves, notes,
 * annotations, chord symbols, and harp pedal diagrams via VexFlow.
 * See utils/d3Plugins/musicPlugin.ts for the shared rendering core.
 */
import { D3RenderPlugin } from '../../types/d3';
import { isMusicSpec, renderMusicSpec, type MusicSpec } from '../../utils/d3Plugins/musicPlugin';
import { escapeXml } from '../../utils/d3Plugins/packetPlugin';

function renderError(container: HTMLElement, message: string, rawSpec: any, isDarkMode: boolean): void {
  const specStr = typeof rawSpec === 'string' ? rawSpec
    : typeof rawSpec?.definition === 'string' ? rawSpec.definition
    : JSON.stringify(rawSpec, null, 2);
  const escaped = escapeXml(specStr || '(empty)');

  container.innerHTML = `
    <div style="
      padding: 16px;
      margin: 8px;
      background: ${isDarkMode ? '#2a1215' : '#fff1f0'};
      border: 1px solid ${isDarkMode ? '#5c2223' : '#ffa39e'};
      border-radius: 4px;
      color: ${isDarkMode ? '#ff4d4f' : '#cf1322'};
      font-family: monospace;
      font-size: 14px;
      line-height: 1.5;
    ">
      <strong>Music notation error:</strong> ${escapeXml(message)}
      <details style="margin-top: 8px; cursor: pointer;">
        <summary style="font-weight: bold;">Show Definition</summary>
        <pre style="
          max-height: 400px;
          overflow: auto;
          background: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
          padding: 12px;
          border-radius: 4px;
          margin: 8px 0 0 0;
          word-break: break-word;
          white-space: pre-wrap;
          color: ${isDarkMode ? '#e0e0e0' : '#24292e'};
        "><code>${escaped}</code></pre>
      </details>
    </div>
  `;
}

async function render(container: HTMLElement, d3: any, rawSpec: any, isDarkMode: boolean): Promise<void> {
  let spec: MusicSpec;
  if (typeof rawSpec.definition === 'string') {
    try { spec = JSON.parse(rawSpec.definition); }
    catch { renderError(container, 'Invalid JSON in definition', rawSpec, isDarkMode); return; }
  } else {
    spec = rawSpec as MusicSpec;
  }

  // Reuse isMusicSpec rather than re-checking `notes` here: a grand staff has
  // no top-level `notes` (they live in staves[].notes), so a local check
  // duplicating that assumption rejects valid multi-staff specs even once
  // canHandle has admitted them.
  if (!isMusicSpec(spec)) {
    renderError(
      container,
      'Requires a "notes" array with at least one note, or a "staves" list whose staves have notes',
      rawSpec, isDarkMode,
    );
    return;
  }

  try {
    await renderMusicSpec(container, spec, isDarkMode, d3);
  } catch (err) {
    renderError(container, err instanceof Error ? err.message : String(err), rawSpec, isDarkMode);
  }
}

export const musicPlugin: D3RenderPlugin = {
  name: 'music-renderer',
  priority: 6,
  sizingConfig: {
    sizingStrategy: 'content-driven',
    needsDynamicHeight: true,
    needsOverflowVisible: true,
    observeResize: false,
    containerStyles: {
      width: '100%',
      height: 'auto',
      minHeight: 'unset',
      overflow: 'visible',
    },
  },
  canHandle: (spec: any): boolean => {
    if (isMusicSpec(spec)) return true;
    if (typeof spec?.definition === 'string') {
      try { return isMusicSpec(JSON.parse(spec.definition)); }
      catch { return false; }
    }
    return false;
  },
  isDefinitionComplete: (definition: string): boolean => {
    try {
      const parsed = JSON.parse(definition);
      return isMusicSpec(parsed);
    } catch { return false; }
  },
  render,
};
