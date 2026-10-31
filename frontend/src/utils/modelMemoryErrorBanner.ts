/**
 * Banner for a local model server refusing a prompt on memory grounds
 * (oMLX prefill memory guard, llama.cpp / Ollama OOM).  Looks like a
 * context-size error but the remedy differs: the model's window is fine,
 * the machine's RAM is not.  Kept out of chatApi.ts so showError() stays a
 * dispatcher rather than a template file.
 */

/** Mirrors the backend tag from app/utils/provider_error_chunks.py plus a
 *  text fallback for runs through older servers (error_type 'unknown'). */
export function isModelMemoryError(errorDetail: string, errorType?: string): boolean {
    return errorType === 'model_memory_error' ||
        /memory guard|memory_guard|Prefill would require|free RAM|out of memory/i.test(errorDetail);
}

/** "Prefill would require ~112.59 GB peak ... the prefill limit is 110.42 GB" → the two figures. */
export const MEMORY_FIGURES = /require ~?([\d.]+\s*GB).*?limit is ([\d.]+\s*GB)/i;

export function extractMemoryFigures(errorDetail: string): { needed: string; limit: string } | null {
    const m = errorDetail.match(MEMORY_FIGURES);
    return m ? { needed: m[1], limit: m[2] } : null;
}

const RETRY_BUTTON_STYLE =
    'padding: 10px 24px; background-color: #1890ff; color: white; border: none; border-radius: 6px; ' +
    'cursor: pointer; font-size: 14px; font-weight: 500; box-shadow: 0 2px 4px rgba(0,0,0,0.1); transition: background-color 0.2s;';

/**
 * @param safeDetailHtml  the raw server text, already escaped and with newlines as <br>
 * @param escape          HTML escaper (chatApi's escapeHtml) applied to the extracted figures
 */
export function renderModelMemoryErrorBanner(
    errorDetail: string,
    safeDetailHtml: string,
    conversationId: string,
    escape: (s: string) => string,
): string {
    const figures = extractMemoryFigures(errorDetail);
    const figuresLine = figures
        ? `<div style="font-size: 13px; margin-bottom: 10px; color: var(--auth-error-muted, #8c8c8c);">This prompt needs about <strong>${escape(figures.needed)}</strong>; the server's limit is <strong>${escape(figures.limit)}</strong>.</div>`
        : '';
    // Reuses the context-error-retry-button class so MarkdownRenderer's existing
    // retry wiring handles the click; the recovery action (resend) is the same.
    return `<div class="model-memory-error-banner" style="margin: 16px 0; padding: 16px 20px; background: var(--auth-error-bg, linear-gradient(135deg, #fff2f0 0%, #fff7f0 100%)); border: 1px solid var(--auth-error-border, #ffccc7); border-left: 4px solid var(--auth-error-accent, #ff4d4f); border-radius: 6px; color: var(--auth-error-text, #434343);">
<div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
<span style="font-size: 24px;">🧠</span>
<span style="font-size: 16px; font-weight: 600; color: var(--auth-error-heading, #cf1322);">Model Server Out of Memory</span>
</div>
<div style="font-size: 14px; line-height: 1.6; margin-bottom: 8px;">
The local model server refused this request because loading the prompt would exceed the memory it is allowed to use. The model's context window is not the problem; the machine's RAM is.
</div>
${figuresLine}
<ul style="font-size: 14px; line-height: 1.6; margin: 0 0 16px 18px; padding: 0;">
<li><strong>Shrink the prompt:</strong> mute earlier turns, remove files from context, or fork / hand off this conversation.</li>
<li><strong>Free memory:</strong> close other applications, then retry.</li>
<li><strong>Raise the server's ceiling:</strong> on oMLX, set Memory Guard to a higher tier (safe → balanced → aggressive) or raise its custom ceiling.</li>
</ul>
<div style="display: flex; align-items: center; gap: 12px;">
<button class="context-error-retry-button" data-conversation-id="${conversationId}" style="${RETRY_BUTTON_STYLE}" onmouseover="this.style.backgroundColor='#40a9ff'" onmouseout="this.style.backgroundColor='#1890ff'">🔄 Retry Request</button>
</div>
<details style="margin-top: 14px; border-top: 1px solid var(--auth-error-border, #ffd6cc); padding-top: 10px;">
<summary style="cursor: pointer; font-size: 12px; color: var(--auth-error-muted, #8c8c8c);">Technical details</summary>
<div style="margin-top: 8px; white-space: pre-wrap; font-family: monospace; font-size: 12px; color: var(--auth-error-detail, #8c1f1f); max-height: 200px; overflow-y: auto;">
${safeDetailHtml}
</div>
</details>
</div>`;
}
