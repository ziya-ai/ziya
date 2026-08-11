/**
 * D3 render plugin for server-rendered LaTeX diagrams (circuitikz, tikz,
 * chemfig, tikz-cd).
 *
 * This is the first plugin in the registry that depends on the backend.  Every
 * other plugin draws client-side from a library bundled into the app, so the
 * lifecycle here is genuinely different and worth stating:
 *
 *   1. Rendering is ASYNCHRONOUS and takes 0.5-3s.  A spinner is mandatory,
 *      and an in-flight request must be abortable, because a theme toggle or
 *      a re-render can supersede it.
 *   2. Rendering can be UNAVAILABLE rather than merely failing.  A missing TeX
 *      package is not the diagram's fault, so that case gets an actionable
 *      install notice instead of an error, and the LaTeX source is always kept
 *      visible so nothing is lost.
 *   3. The rendered SVG is CACHED in-module keyed by (type, definition,
 *      format).  A dark-mode toggle must recolour the existing SVG, not
 *      re-compile it server-side.
 *
 * Output is SVG when dvisvgm is installed (text stays selectable and can be
 * recoloured for dark mode -- see applyLatexDarkTheme) and PNG otherwise, in
 * which case dark mode gets a CSS filter because raster pixels cannot be
 * selectively recoloured.
 */
import { D3RenderPlugin } from '../../types/d3';
import { escapeHtml } from '../../utils/htmlSanitize';
import { applyLatexDarkTheme, sizeLatexSvg } from '../../utils/latexSvgTheme';
import { LATEX_PROFILE_KEYS } from '../../constants/latexProfiles';

/**
 * Diagram types served by the backend LaTeX profiles, derived from the shared
 * registry so a new profile cannot be supported everywhere except here.
 */
const LATEX_TYPES = new Set(LATEX_PROFILE_KEYS);

interface LatexSpec {
    type: string;
    definition: string;
    isStreaming?: boolean;
    isMarkdownBlockClosed?: boolean;
    forceRender?: boolean;
}

interface RenderedArtifact {
    fmt: 'svg' | 'png';
    /** SVG markup, or a data URL for PNG. */
    payload: string;
}

/**
 * Successful renders, keyed by content.  The server is content-addressed too,
 * but caching here also avoids a network round-trip on every theme toggle.
 */
const artifactCache = new Map<string, RenderedArtifact>();

const cacheKey = (type: string, definition: string) => `${type}\u0000${definition}`;

function spinner(container: HTMLElement, isDarkMode: boolean, label: string): void {
    container.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                    padding:2em;width:100%;min-height:120px;">
            <div style="border:4px solid ${isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)'};
                        border-top:4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
                        border-radius:50%;width:36px;height:36px;
                        animation:ziya-latex-spin 1s linear infinite;margin-bottom:12px;"></div>
            <div style="font-family:system-ui,-apple-system,sans-serif;font-size:13px;
                        color:${isDarkMode ? '#eceff4' : '#333333'};">${escapeHtml(label)}</div>
        </div>
        <style>@keyframes ziya-latex-spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}</style>
    `;
}

/** Collapsible view of the LaTeX source, appended to every failure state. */
function sourceDetails(definition: string, isDarkMode: boolean): string {
    return `
        <details style="margin-top:10px;cursor:pointer;">
            <summary style="font-weight:600;font-size:12px;">Show LaTeX source</summary>
            <pre style="max-height:340px;overflow:auto;margin:8px 0 0 0;padding:10px;
                        border-radius:4px;white-space:pre-wrap;word-break:break-word;
                        font-family:Monaco,Menlo,'Ubuntu Mono',monospace;font-size:12px;
                        background:${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                        color:${isDarkMode ? '#e0e0e0' : '#24292e'};"><code>${escapeHtml(definition)}</code></pre>
        </details>`;
}

/**
 * The not-installed affordance.
 *
 * Deliberately styled as a notice rather than an error: the diagram is valid,
 * the machine simply lacks a package.  The install command is shown inline and
 * the source is preserved so the content is never lost.
 */
function installNotice(
    container: HTMLElement,
    detail: { message?: string; install_command?: string; missing_packages?: string[] },
    definition: string,
    isDarkMode: boolean,
): void {
    const cmd = detail.install_command || 'tlmgr install circuitikz standalone siunitx dvisvgm';
    const missing = (detail.missing_packages || []).map(escapeHtml).join(', ');

    container.innerHTML = `
        <div style="padding:14px;margin:8px 0;border-radius:6px;font-size:13px;line-height:1.55;
                    font-family:system-ui,-apple-system,sans-serif;
                    background:${isDarkMode ? '#2b2111' : '#fffbe6'};
                    border:1px solid ${isDarkMode ? '#594214' : '#ffe58f'};
                    color:${isDarkMode ? '#f0e6d2' : '#614700'};">
            <div style="font-weight:600;margin-bottom:6px;">
                ⚠️ LaTeX renderer not installed
            </div>
            <div>${escapeHtml(detail.message || 'A local TeX installation is required to render this diagram.')}</div>
            ${missing ? `<div style="margin-top:6px;font-size:12px;opacity:0.85;">Missing: ${missing}</div>` : ''}
            <details style="margin-top:10px;cursor:pointer;">
                <summary style="font-weight:600;">How to install</summary>
                <div style="margin-top:8px;">
                    <div style="font-size:12px;margin-bottom:6px;">
                        Install a TeX distribution (BasicTeX or TeX Live), then run:
                    </div>
                    <pre style="margin:0;padding:10px;border-radius:4px;overflow:auto;
                                font-family:Monaco,Menlo,'Ubuntu Mono',monospace;font-size:12px;
                                background:${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                                color:${isDarkMode ? '#e0e0e0' : '#24292e'};"><code>sudo ${escapeHtml(cmd)}</code></pre>
                    <div style="font-size:12px;margin-top:8px;opacity:0.85;">
                        <code>dvisvgm</code> enables SVG output, which keeps diagram text
                        selectable and recolourable for dark mode.  Without it, diagrams
                        render as PNG.
                    </div>
                </div>
            </details>
            ${sourceDetails(definition, isDarkMode)}
        </div>`;
}

/** Compile / rejection / transport failures. */
function errorBox(
    container: HTMLElement,
    heading: string,
    message: string,
    logExcerpt: string,
    definition: string,
    isDarkMode: boolean,
): void {
    container.innerHTML = `
        <div style="padding:14px;margin:8px 0;border-radius:6px;font-size:13px;line-height:1.55;
                    font-family:system-ui,-apple-system,sans-serif;
                    background:${isDarkMode ? '#2a1215' : '#fff1f0'};
                    border:1px solid ${isDarkMode ? '#5c2223' : '#ffa39e'};
                    color:${isDarkMode ? '#ff7875' : '#cf1322'};">
            <div style="font-weight:600;margin-bottom:6px;">${escapeHtml(heading)}</div>
            <div style="font-family:Monaco,Menlo,monospace;font-size:12px;white-space:pre-wrap;
                        word-break:break-word;">${escapeHtml(message)}</div>
            ${logExcerpt ? `
            <details style="margin-top:10px;cursor:pointer;">
                <summary style="font-weight:600;font-size:12px;">Show TeX log</summary>
                <pre style="max-height:280px;overflow:auto;margin:8px 0 0 0;padding:10px;
                            border-radius:4px;white-space:pre-wrap;font-size:11px;
                            font-family:Monaco,Menlo,monospace;
                            background:${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                            color:${isDarkMode ? '#e0e0e0' : '#24292e'};"><code>${escapeHtml(logExcerpt)}</code></pre>
            </details>` : ''}
            ${sourceDetails(definition, isDarkMode)}
        </div>`;
}

/** Mount an artifact, applying theme treatment appropriate to its format. */
function mount(container: HTMLElement, artifact: RenderedArtifact, isDarkMode: boolean): void {
    const wrapper = document.createElement('div');
    wrapper.className = 'latex-diagram-wrapper';
    wrapper.style.cssText =
        'width:100%;max-width:100%;overflow:auto;padding:0.75em;display:flex;justify-content:center;';

    if (artifact.fmt === 'svg') {
        wrapper.innerHTML = artifact.payload;
        const svg = wrapper.querySelector('svg');
        if (svg) {
            // Size to the diagram's own dimensions, capped at the container.
            // Dropping width/height outright (as this did) leaves only the
            // viewBox, which defaults to width:100% -- stretching a 70px
            // benzene ring across an 820px column, a ~12x upscale.
            sizeLatexSvg(svg as SVGElement);
            // TeX draws in black, which measures 1.27:1 on the #1f1f1f
            // diagram background -- invisible.  This must NOT go through
            // enhanceSVGVisibility: that helper reads each element's OWN
            // fill/stroke, and dvisvgm puts the colour on ancestor <g>
            // elements instead, so it silently misses the ink while
            // clobbering TeX's hairline stroke widths.  See latexSvgTheme.
            applyLatexDarkTheme(svg as SVGElement, isDarkMode);
        }
    } else {
        const img = document.createElement('img');
        img.src = artifact.payload;
        img.alt = 'LaTeX diagram';
        img.style.cssText = 'max-width:100%;height:auto;';
        // A raster cannot be recoloured selectively; inversion is the only
        // option that keeps black-on-white line art legible in dark mode.
        if (isDarkMode) img.style.filter = 'invert(0.92) hue-rotate(180deg)';
        wrapper.appendChild(img);
    }

    container.innerHTML = '';
    container.appendChild(wrapper);
}

async function render(
    container: HTMLElement,
    _d3: any,
    rawSpec: LatexSpec,
    isDarkMode: boolean,
): Promise<void | (() => void)> {
    const type = String(rawSpec?.type || '').toLowerCase();
    const definition = typeof rawSpec?.definition === 'string' ? rawSpec.definition : '';

    // Streaming: a partial document is almost never valid LaTeX, and firing a
    // 1-3s compile per chunk would hammer the backend.  Wait for the fence to
    // close, and preserve any completed render rather than clearing it.
    if (!rawSpec.isMarkdownBlockClosed && !rawSpec.forceRender) {
        if (container.querySelector('svg') || container.querySelector('img')) return;
        container.innerHTML = '';
        return;
    }

    if (!definition.trim()) {
        container.innerHTML = '';
        return;
    }

    // Cache hit: re-mount so a theme change recolours without a round-trip.
    const key = cacheKey(type, definition);
    const cached = artifactCache.get(key);
    if (cached) {
        mount(container, cached, isDarkMode);
        return;
    }

    const controller = new AbortController();
    spinner(container, isDarkMode, 'Compiling LaTeX…');

    try {
        const response = await fetch('/api/render-latex', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type, definition, format: 'auto' }),
            signal: controller.signal,
        });

        if (!response.ok) {
            // FastAPI nests HTTPException payloads under `detail`.
            let detail: any = {};
            try {
                const body = await response.json();
                detail = body?.detail ?? body ?? {};
            } catch {
                detail = {};
            }

            // 501 is capability, not failure — show install guidance instead.
            if (response.status === 501 || detail.kind === 'not_installed') {
                installNotice(container, detail, definition, isDarkMode);
                return;
            }

            const heading =
                detail.kind === 'rejected' ? 'LaTeX rejected for safety'
                : detail.kind === 'timeout' ? 'LaTeX render timed out'
                : detail.kind === 'unsupported_type' ? 'Unsupported diagram type'
                : 'LaTeX compile error';
            const message =
                detail.message ||
                // A pydantic validation error arrives as an array, not an object.
                (Array.isArray(detail) ? detail.map((d: any) => d?.msg).join('; ') : '') ||
                `Server returned ${response.status}`;
            errorBox(container, heading, message, detail.log_excerpt || '', definition, isDarkMode);
            return;
        }

        const contentType = response.headers.get('content-type') || '';
        let artifact: RenderedArtifact;
        if (contentType.includes('svg')) {
            artifact = { fmt: 'svg', payload: await response.text() };
        } else {
            const blob = await response.blob();
            artifact = {
                fmt: 'png',
                payload: await new Promise<string>((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(String(reader.result));
                    reader.onerror = () => reject(reader.error);
                    reader.readAsDataURL(blob);
                }),
            };
        }

        artifactCache.set(key, artifact);
        mount(container, artifact, isDarkMode);
    } catch (err) {
        // An aborted request is a supersede, not a failure; leave the DOM alone.
        if (err instanceof DOMException && err.name === 'AbortError') return;
        errorBox(
            container,
            'Could not reach the LaTeX renderer',
            err instanceof Error ? err.message : String(err),
            '',
            definition,
            isDarkMode,
        );
    }

    return () => controller.abort();
}

export const latexPlugin: D3RenderPlugin = {
    name: 'latex-renderer',
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
    canHandle: (spec: any): boolean =>
        typeof spec === 'object' &&
        spec !== null &&
        LATEX_TYPES.has(String(spec.type || '').toLowerCase()) &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0,
    isDefinitionComplete: (definition: string): boolean =>
        typeof definition === 'string' && definition.trim().length > 0,
    render,
};
