/**
 * PDF Export Utility
 *
 * Captures the post-rendered conversation DOM (with all formatting,
 * syntax highlighting, rendered diagrams, embedded images, etc.)
 * and produces a PDF via the browser's native print engine.
 *
 * The header/footer metadata (version, model, provider, Ziya URL)
 * matches what the markdown and HTML exports produce, fetched live
 * from /api/info.
 */

export interface PdfExportOptions {
  /** Title shown in the PDF header. */
  title?: string;
  /** Include a metadata footer with timestamp / model info. */
  includeFooter?: boolean;
  /** Callback fired with progress percentage (0-100) and status text. */
  onProgress?: (pct: number, status: string) => void;
  /** Keep only the last N human→assistant rounds. null = all. */
  roundLimit?: number | null;
  /** Include human messages. When false, strips user prompts. */
  includeHuman?: boolean;
  /** Include collapsed sections (<details> blocks). When false, strips them. */
  includeCollapsed?: boolean;
}

interface ExportMetadata {
  version: string;
  model: string;
  provider: string;
  edition: string;
  ziyaUrl: string;
}

/** Fetch system info so the PDF header/footer matches the markdown export. */
async function fetchExportMetadata(): Promise<ExportMetadata> {
  try {
    const resp = await fetch('/api/info');
    if (!resp.ok) throw new Error('info endpoint unavailable');
    const info = await resp.json();
    return {
      version: info.version?.ziya_version ?? 'unknown',
      edition: info.version?.edition ?? 'Ziya',
      model: info.model?.model ?? 'unknown',
      provider: info.model?.endpoint ?? 'unknown',
      ziyaUrl: 'https://github.com/ziya-ai/ziya',
    };
  } catch {
    return {
      version: 'unknown',
      model: 'unknown',
      provider: 'unknown',
      edition: 'Ziya',
      ziyaUrl: 'https://github.com/ziya-ai/ziya',
    };
  }
}

/**
 * Collect <link rel="stylesheet"> and <style> blocks from the host
 * document so the iframe clone inherits the same visual appearance.
 */
function gatherDocumentStyles(): string {
  const parts: string[] = [];

  document.querySelectorAll('style').forEach((el) => {
    parts.push(el.outerHTML);
  });

  document.querySelectorAll('link[rel="stylesheet"]').forEach((link) => {
    const href = (link as HTMLLinkElement).href;
    if (href) {
      parts.push(`<style>@import url("${href}");</style>`);
    }
  });

  return parts.join('\n');
}

/**
 * Inline computed SVG styles so Mermaid/Graphviz diagrams render
 * correctly in the print iframe (which may not process external
 * stylesheets in time).
 *
 * Must be called with the ORIGINAL container (still in the live DOM)
 * and the CLONED container. getComputedStyle only works on elements
 * attached to the document.
 */
 async function inlineSvgStyles(original: HTMLElement, clone: HTMLElement): Promise<void> {
  const origSvgs = original.querySelectorAll('svg');
  const cloneSvgs = clone.querySelectorAll('svg');

   const rasterizations: Promise<void>[] = [];

   origSvgs.forEach((origSvg, svgIdx) => {
    const cloneSvg = cloneSvgs[svgIdx];
    if (!cloneSvg) return;

     // Rasterize each SVG to a PNG <img>. This solves:
     //  1. Colors — captured pixel-perfect from the live DOM
     //  2. Scaling — <img> correctly respects max-height + object-fit: contain
     //     (inline <svg> does NOT — it clips instead of scaling)
     const promise = rasterizeSvg(origSvg as SVGSVGElement, cloneSvg as SVGSVGElement);
     rasterizations.push(promise);
  });

   await Promise.all(rasterizations);
}

 /** Rasterize a single live-DOM SVG into a PNG <img> and replace the clone. */
 async function rasterizeSvg(origSvg: SVGSVGElement, cloneSvg: SVGSVGElement): Promise<void> {
   try {
     const bbox = origSvg.getBoundingClientRect();
     if (bbox.width < 1 || bbox.height < 1) return;

     // Inline computed styles on a fresh copy of the original SVG so the
     // serialized version carries all visual information.
     const tempClone = origSvg.cloneNode(true) as SVGSVGElement;
     const origEls = origSvg.querySelectorAll('*');
     const tempEls = tempClone.querySelectorAll('*');
     origEls.forEach((origEl, i) => {
       const tmpEl = tempEls[i] as SVGElement | undefined;
       if (!tmpEl) return;
       const computed = window.getComputedStyle(origEl);
       const props = ['fill', 'stroke', 'stroke-width', 'font-family',
         'font-size', 'font-weight', 'opacity', 'color', 'display',
         'visibility', 'stop-color', 'flood-color', 'lighting-color',
         'text-decoration', 'dominant-baseline', 'text-anchor',
         'background', 'background-color'];
       props.forEach((p) => {
         const v = computed.getPropertyValue(p);
         if (v && v !== 'normal' && v !== '') tmpEl.style.setProperty(p, v);
       });
     });

     tempClone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
     tempClone.setAttribute('width', String(bbox.width));
     tempClone.setAttribute('height', String(bbox.height));

     const svgString = new XMLSerializer().serializeToString(tempClone);
     const svgBlob = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' });
     const url = URL.createObjectURL(svgBlob);

     const pngDataUri = await new Promise<string>((resolve, reject) => {
       const img = new Image();
       img.onload = () => {
         try {
           const scale = 2; // retina / print quality
           const canvas = document.createElement('canvas');
           canvas.width = bbox.width * scale;
           canvas.height = bbox.height * scale;
           const ctx = canvas.getContext('2d')!;
           ctx.scale(scale, scale);
           ctx.drawImage(img, 0, 0, bbox.width, bbox.height);
           URL.revokeObjectURL(url);
           resolve(canvas.toDataURL('image/png'));
         } catch (_e) {
           // Tainted canvas (SVG has external resource refs) —
           // fall back to the style-inlined SVG as a data URI
           URL.revokeObjectURL(url);
           const b64 = btoa(unescape(encodeURIComponent(svgString)));
           resolve(`data:image/svg+xml;base64,${b64}`);
         }
       };
       img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('SVG load failed')); };
       img.src = url;
     });

     const imgEl = document.createElement('img');
     imgEl.src = pngDataUri;
     imgEl.style.cssText = 'max-width:100%;max-height:580px;object-fit:contain;display:block;';
     imgEl.width = bbox.width;
     imgEl.height = bbox.height;

     cloneSvg.parentNode?.replaceChild(imgEl, cloneSvg);
   } catch (e) {
     console.warn('SVG rasterization failed, keeping original:', e);
   }
 }

/**
 * Convert <canvas> elements to inline <img> so they survive the
 * clone into the print iframe.
 */
function convertCanvasElements(container: HTMLElement): void {
  container.querySelectorAll('canvas').forEach((canvas) => {
    try {
      const dataUrl = (canvas as HTMLCanvasElement).toDataURL('image/png');
      const img = document.createElement('img');
      img.src = dataUrl;
      img.style.maxWidth = '100%';
      img.width = canvas.width;
      img.height = canvas.height;
      canvas.replaceWith(img);
    } catch (_e) {
      // Tainted canvas — skip silently
    }
  });
}

/** Print-optimized CSS injected into the iframe. */
const PRINT_CSS = `
  @page {
    size: A4;
    margin: 12mm 12mm;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6;
    color: #24292e;
    background: #fff;
    padding: 0;
    margin: 0;
  }
  .pdf-header {
    text-align: center;
    padding-bottom: 12px;
    margin-bottom: 20px;
    border-bottom: 2px solid #e1e4e8;
  }
  .pdf-header h1 { font-size: 20px; margin: 0 0 4px; }
  .pdf-header p  { font-size: 11px; color: #57606a; margin: 2px 0; }
  .pdf-header code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 10px; }
  .pdf-footer {
    margin-top: 30px;
    padding-top: 10px;
    font-size: 10px;
    color: #57606a;
    text-align: center;
  }
  .pdf-footer p { margin: 2px 0; }
  .pdf-footer code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 10px; }
  .pdf-footer a { color: #0969da; }

  /* Force light-mode rendering */
  * { color-scheme: light !important; }

  /* Keep code blocks from overflowing pages */
  pre {
    white-space: pre-wrap !important;
    word-break: break-word !important;
    max-height: none;
  }

  /* Constrain images/diagrams to fit within a single page
     leaving room (~80px) for a preceding heading */
  img { max-width: 100% !important; max-height: 580px !important; object-fit: contain; }
  .d3-container, .visualization, .vega-lite-container,
  [class*="renderer-container"] {
    max-height: 600px;
    overflow: hidden;
  }

  /* Keep heading with following content */
  h1, h2, h3, h4, h5, h6 { break-after: avoid; }

  /* Hide interactive controls that don't belong in a PDF */
  button, .ant-btn, .copy-button, .toolbar,
  .scroll-indicator, .stop-stream, .retry-section,
  [data-no-print], .code-block-actions {
    display: none !important;
  }

  /* Remove all viewport/scroll constraints so the full conversation
     flows naturally across pages instead of being clipped to one screen. */
  .chat-container,
  .conversation-messages-container,
  [class*="container"] {
    height: auto !important;
    max-height: none !important;
    overflow: visible !important;
    contain: none !important;
  }
`;

/**
 * Export the currently rendered conversation to PDF.
 *
 * Resolves when the print dialog has been opened (the user may still
 * cancel). Rejects on error.
 */
export async function exportConversationAsPdf(
  options: PdfExportOptions = {}
): Promise<void> {
  const {
    title = 'Ziya Conversation Export',
    includeFooter = true,
    onProgress,
    roundLimit = null,
    includeHuman = true,
    includeCollapsed = true,
  } = options;

  onProgress?.(5, 'Fetching export metadata…');
  const meta = await fetchExportMetadata();

  onProgress?.(15, 'Locating conversation content…');

  const source = document.querySelector('.conversation-messages-container');
  if (!source) {
    throw new Error(
      'Could not find the conversation container (.conversation-messages-container). ' +
      'Make sure a conversation is open before exporting.'
    );
  }

  // If the app is in dark mode, temporarily flip to light so the
  // cloned DOM and rasterized SVGs capture light-theme colors.
  // Mermaid bakes theme colors into SVG <style> blocks at render
  // time, so we need the live DOM to actually be in light mode.
  const wasDarkMode = document.body.classList.contains('dark');
  if (wasDarkMode) {
    onProgress?.(18, 'Switching to light mode for export…');
    document.body.classList.remove('dark');
    // Wait for CSS to recompute and re-renders to settle
    await new Promise(r => setTimeout(r, 600));
  }

  try {
    onProgress?.(25, 'Cloning rendered content…');
    const clone = source.cloneNode(true) as HTMLElement;

    onProgress?.(35, 'Inlining diagram styles…');
    await inlineSvgStyles(source as HTMLElement, clone);
    convertCanvasElements(clone);

    // Strip interactive elements
    clone.querySelectorAll(
      'button, .ant-btn, .copy-button, .toolbar, .scroll-indicator, ' +
      '.stop-stream, .retry-section, [data-no-print], .code-block-actions'
    ).forEach((el) => el.remove());

    // ── Apply content filters to the cloned DOM ─────────────────────
    const allMessages = Array.from(clone.querySelectorAll('.message'));

    // Round limit: keep only the last N human→assistant exchanges
    if (roundLimit !== null && roundLimit > 0 && allMessages.length > 0) {
      const humanMsgEls = allMessages.filter(el => el.classList.contains('human'));
      const keepFrom = humanMsgEls[Math.max(0, humanMsgEls.length - roundLimit)];
      if (keepFrom) {
        let removing = true;
        for (const msg of allMessages) {
          if (msg === keepFrom) removing = false;
          if (removing) msg.remove();
        }
      }
    }

    // Exclude human messages
    if (!includeHuman) {
      clone.querySelectorAll('.message.human').forEach(el => el.remove());
    }

    // Strip collapsed sections
    if (!includeCollapsed) {
      clone.querySelectorAll('details').forEach(el => el.remove());
      clone.querySelectorAll('pre').forEach(pre => {
        const code = pre.querySelector('code');
        if (code?.className?.includes('thinking')) {
          pre.remove();
        }
      });
    }

    onProgress?.(50, 'Building print document…');

    const existingStyles = gatherDocumentStyles();
    const now = new Date();
    const dateStr = now.toLocaleString();
    const messageCount = source.querySelectorAll('[class*="message"]').length || '—';

    const headerHtml = `
      <div class="pdf-header">
        <h1>🎯 ${title}</h1>
        <p><strong>Exported:</strong> ${dateStr}</p>
        <p><strong>Model:</strong> <code>${meta.model}</code> &nbsp;|&nbsp;
           <strong>Provider:</strong> <code>${meta.provider}</code> &nbsp;|&nbsp;
           <strong>Messages:</strong> ${messageCount}</p>
      </div>`;

    const footerHtml = includeFooter
      ? `<div class="pdf-footer">
           <hr style="border:none;border-top:2px solid #e1e4e8;margin-bottom:12px;" />
           <p><strong>📋 Export Metadata</strong></p>
           <p><strong>Generated by:</strong> ${meta.edition} v${meta.version}</p>
           <p><strong>Model:</strong> <code>${meta.model}</code> &nbsp;|&nbsp;
              <strong>Provider:</strong> <code>${meta.provider}</code></p>
           <p><strong>Exported:</strong> ${dateStr}</p>
           <p><strong>Learn more about Ziya:</strong> <a href="${meta.ziyaUrl}">${meta.ziyaUrl}</a></p>
           <p><em>This conversation was exported from Ziya — an AI client and orchestration harness for software engineering, system architecture, operations, and technical visualization.</em></p>
         </div>`
      : '';

    const iframeHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Ziya Session Transcript</title>
  ${existingStyles}
</head>
<body>
  ${headerHtml}
  ${clone.outerHTML}
  ${footerHtml}
  <style>${PRINT_CSS}</style>
</body>
</html>`;

    onProgress?.(70, 'Preparing PDF…');

    const iframe = document.createElement('iframe');
    iframe.style.cssText = 'position:fixed;top:-10000px;left:-10000px;width:210mm;height:1px;border:none;';
    document.body.appendChild(iframe);

    const iframeDoc = iframe.contentDocument || iframe.contentWindow?.document;
    if (!iframeDoc) {
      document.body.removeChild(iframe);
      throw new Error('Failed to create print iframe');
    }

    iframeDoc.open();
    iframeDoc.write(iframeHtml);
    iframeDoc.close();

    // Let the iframe load stylesheets and render SVGs
    await new Promise<void>((resolve) => {
      iframe.onload = () => resolve();
      setTimeout(resolve, 1500);
    });

    // Force-override every element's height/overflow/contain via JS.
    const allEls = iframeDoc.querySelectorAll('*');
    allEls.forEach((el) => {
      const s = (el as HTMLElement).style;
      const computed = iframeDoc.defaultView?.getComputedStyle(el);
      if (!computed) return;

      if ((el as HTMLElement).closest?.('svg')) return;
      if (el.tagName === 'svg' || el.tagName === 'SVG') return;
      if (el.tagName === 'IMG') return;

      const pos = computed.position;
      if (pos === 'fixed' || pos === 'sticky') {
        s.setProperty('position', 'relative', 'important');
      }
      s.setProperty('contain', 'none', 'important');
      s.setProperty('overflow', 'visible', 'important');

      const hasSvgChild = (el as HTMLElement).querySelector?.('svg');
      if (!hasSvgChild) {
        s.setProperty('height', 'auto', 'important');
        s.setProperty('max-height', 'none', 'important');
      }
    });

    const docHeight = iframeDoc.body.scrollHeight || 10000;
    iframe.style.height = docHeight + 'px';

    onProgress?.(90, 'Opening print dialog…');

    try {
      iframe.contentWindow?.focus();
      iframe.contentWindow?.print();
    } finally {
      setTimeout(() => {
        document.body.removeChild(iframe);
      }, 3000);
    }

    onProgress?.(100, 'PDF dialog opened!');
  } finally {
    // Restore dark mode if it was active before export
    if (wasDarkMode) {
      document.body.classList.add('dark');
    }
  }
}
