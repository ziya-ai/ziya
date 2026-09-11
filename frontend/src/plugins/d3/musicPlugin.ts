/**
 * D3 render plugin for annotated music notation (full-chrome, Tier 2).
 *
 * Recognises specs with `type: 'music'`.  Renders staves, notes,
 * annotations, chord symbols, and harp pedal diagrams via VexFlow.
 * See utils/d3Plugins/musicPlugin.ts for the shared rendering core.
 */
import { D3RenderPlugin } from '../../types/d3';
import {
  isMusicSpec, resolveMusicSpec, renderMusicSpec, degenerateMusicBody, type MusicSpec,
} from '../../utils/d3Plugins/musicPlugin';
import { escapeXml } from '../../utils/d3Plugins/packetPlugin';

/**
 * Draw a titled, empty staff for a well-formed but ZERO-CONTENT music spec
 * (D-145, music-w3-09: a title with empty `measures`/`notes`/`staves`).  Such
 * a spec is not renderable by the VexFlow core, but leaving it UNCLAIMED made
 * the orchestrator retry to its ~30s no-plugin timeout with zero output -- a
 * total loss.  A bare titled staff converts that hang into an immediate, clean
 * render in both themes.  Pure DOM (no VexFlow) so it cannot itself hang.
 *
 * Colours resolve from the theme the renderer was given, not a constant: the
 * staff line and text are chosen to clear WCAG on each background
 * (light #6b7280 line 4.83:1 / #333333 text 12.63:1 on #ffffff; dark #8b949e
 * line 5.42:1 / #e0e0e0 text 12.63:1 on #1e1e1e -- both clear the 3:1 boundary
 * and 4.5:1 text floors on their own theme's background).
 */
function renderEmptyMusicStaff(container: HTMLElement, body: any, isDarkMode: boolean): void {
  const title = typeof body?.title === 'string' ? body.title.trim() : '';
  const textFill = isDarkMode ? '#e0e0e0' : '#333333';
  const lineStroke = isDarkMode ? '#8b949e' : '#6b7280';
  const width = 480;
  const height = 132;
  const left = 40;
  const right = width - 40;
  const staffTop = 56;
  const lineGap = 8;
  const lines = [0, 1, 2, 3, 4]
    .map((i) => `<line x1="${left}" y1="${staffTop + i * lineGap}" x2="${right}" `
      + `y2="${staffTop + i * lineGap}" stroke="${lineStroke}" stroke-width="1" />`)
    .join('');
  const titleText = title
    ? `<text x="${width / 2}" y="30" text-anchor="middle" font-family="serif" `
      + `font-size="16" fill="${textFill}">${escapeXml(title)}</text>`
    : '';
  container.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
         viewBox="0 0 ${width} ${height}" role="img"
         aria-label="empty music score${title ? `: ${escapeXml(title)}` : ''}">
      ${titleText}
      ${lines}
      <text x="${width / 2}" y="${staffTop + 4 * lineGap + 28}" text-anchor="middle"
            font-family="sans-serif" font-size="12" fill="${textFill}">(empty score)</text>
    </svg>
  `;
}

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

  // Tag the card so the headless harness (DiagramRenderPage) fails fast with
  // this message instead of polling for an svg/canvas/img the card never
  // contains and reporting a generic "svg:0" timeout. setAttribute, not
  // template interpolation: the message can contain quotes.
  const card = container.firstElementChild;
  if (card) card.setAttribute('data-diagram-error', message);
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
    // D-145: a well-formed but ZERO-CONTENT music spec (title + empty
    // measures/notes/staves) is not renderable by the VexFlow core, but the
    // plugin claims it (see canHandle) so it does not fall through to the
    // orchestrator's ~30s no-plugin timeout.  Draw a titled blank staff.
    const emptyBody = degenerateMusicBody(rawSpec);
    if (emptyBody) {
      renderEmptyMusicStaff(container, emptyBody, isDarkMode);
      return;
    }
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
    // D-145: also claim a well-formed but zero-content music-shaped wrapper
    // (empty notes/measures/staves) so render() can draw a titled blank staff
    // instead of leaving it unclaimed -> ~30s no-plugin timeout.  A non-music
    // body (no music structural keys) is not matched, so this never hijacks.
    return isMusicSpec(resolveMusicSpec(spec)) || degenerateMusicBody(spec) !== null;
  },
  isDefinitionComplete: (definition: string): boolean => {
    // Mirror canHandle: the definition body carries no `type`, so stamp it via
    // resolveMusicSpec before the isMusicSpec gate.
    return isMusicSpec(resolveMusicSpec({ type: 'music', definition }));
  },
  render,
};
