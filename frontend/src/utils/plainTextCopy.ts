/**
 * Clipboard helpers for text that must paste as plain, machine-readable text
 * (JSON config stanzas).  Rich-paste targets otherwise take the HTML flavour
 * of a selection and may turn runs of spaces into U+00A0.
 */
import type React from 'react';

/** NBSP-family spaces -> U+0020; zero-width space and BOM removed. */
export const cleanCopiedText = (s: string): string =>
    s.replace(/[\u00A0\u2007\u202F]/g, ' ').replace(/[\u200B\uFEFF]/g, '');

/** onCopy handler: put only cleaned text/plain on the clipboard. */
export function handlePlainTextCopy(e: React.ClipboardEvent | ClipboardEvent): void {
    const selection = window.getSelection();
    const text = selection ? selection.toString() : '';
    if (!text || !e.clipboardData) return;
    e.preventDefault();
    e.clipboardData.setData('text/plain', cleanCopiedText(text));
}

/** Serialize the object itself, never the DOM, and write it to the clipboard. */
export async function copyJsonToClipboard(value: unknown): Promise<boolean> {
    const text = JSON.stringify(value, null, 2);
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}
