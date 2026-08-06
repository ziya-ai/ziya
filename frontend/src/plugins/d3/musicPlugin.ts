/**
 * D3 render plugin for annotated music notation (full-chrome, Tier 2).
 *
 * Recognises specs with `type: 'music'`.  Renders staves, notes,
 * annotations, chord symbols, and harp pedal diagrams via VexFlow.
 * See utils/d3Plugins/musicPlugin.ts for the shared rendering core.
 */
import { D3RenderPlugin } from '../../types/d3';
import { isMusicSpec, resolveMusicSpec, renderMusicSpec, type MusicSpec } from '../../utils/d3Plugins/musicPlugin';
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
  // resolveMusicSpec recovers the music spec from the `render_diagram`
  // wrapper ({type:'music', definition:'<json>'}) -- the definition body
  // carries no `type`, so the spec must be lifted out and stamped before the
  // isMusicSpec gate below accepts it. A structured spec passes through
  // unchanged. Report malformed JSON explicitly rather than falling through
  // to a misleading "requires a notes array" error.
  if (typeof rawSpec?.definition === 'string'
      && rawSpec.definition.trim() !== ''
      && rawSpec.definition.trimStart()[0] === '{'
      && !isMusicSpec(rawSpec)) {
    try { JSON.parse(rawSpec.definition); }
    catch { renderError(container, 'Invalid JSON in definition', rawSpec, isDarkMode); return; }
  }
  const spec: MusicSpec = resolveMusicSpec(rawSpec) as MusicSpec;

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
    // Recover the music spec from the {type,definition} wrapper first: the
    // definition body carries no `type`, so a bare isMusicSpec(JSON.parse(...))
    // fails the type gate and the plugin is never selected -> 30s timeout.
    // resolveMusicSpec lifts the parsed body and stamps type:'music' ONLY when
    // it actually carries music content, so non-music specs are not hijacked.
    return isMusicSpec(resolveMusicSpec(spec));
  },
  isDefinitionComplete: (definition: string): boolean => {
    // Mirror canHandle: the definition body carries no `type`, so stamp it via
    // resolveMusicSpec before the isMusicSpec gate.
    return isMusicSpec(resolveMusicSpec({ type: 'music', definition }));
  },
  render,
};
