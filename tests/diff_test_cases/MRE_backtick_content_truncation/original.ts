/**
 * Math preprocessing passes.
 */

export function preprocessDisplayMath(markdown: string): string {
    const parts = markdown.split(/(```[^\n]*\n[\s\S]*?```)/g);
    return parts.map((part, idx) => {
        if (idx % 2 === 1 && part.startsWith('```')) {
            return part; // code fence — leave untouched
        }
        return part.replace(/\$\$([\s\S]*?)\$\$/g, (_match, innerContent) => {
            const encoded = btoa(unescape(encodeURIComponent(innerContent.trim())));
            return `\n\n<div class="math-display-encoded" data-math="${encoded}"></div>\n\n`;
        });
    }).join('');
}
