/**
* useCopyCleanup — strips dark-mode *theme* colors from clipboard HTML when
* users copy message content, while preserving intentional colors like syntax
* highlighting, colored badges, and link colors.
 *
* Specifically: removes dark backgrounds (low luminance) and near-white
* foreground text (high luminance). Keeps everything else — syntax colors,
* accent colors, etc.
 */
import { useEffect, useRef } from 'react';

/**
 * Attach to a container ref — only intercepts copies whose selection
 * originates within that container.
 */
export function useCopyCleanup(containerRef: React.RefObject<HTMLElement | null>) {
    // Stable ref so the listener closure doesn't go stale
    const ref = useRef(containerRef);
    ref.current = containerRef;

    useEffect(() => {
        const handler = (e: ClipboardEvent) => {
            // Skip if another handler (e.g. LaTeX copy) already handled this
            if (e.defaultPrevented) return;

            const selection = window.getSelection();
            if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;

            // Only act when the selection anchor is inside our container
            const container = ref.current?.current;
            if (!container) return;
            const anchor = selection.anchorNode;
            if (!anchor || !container.contains(anchor)) return;

            // Clone the selected DOM fragment — this gives us raw HTML
            // elements with their tag semantics but WITHOUT computed/inherited
            // styles baked in as inline attributes.
            const range = selection.getRangeAt(0);
            const fragment = range.cloneContents();

            // Serialize via a scratch container
            const scratch = document.createElement('div');
            scratch.appendChild(fragment);

            // Selectively strip only dark-mode theme colors:
            // - backgrounds with low luminance (dark bgs)
            // - foreground text with high luminance (white/near-white)
            // Keeps syntax highlighting, link colors, badges, etc.
            scratch.querySelectorAll('[style]').forEach((el) => {
                const htmlEl = el as HTMLElement;
                const bg = htmlEl.style.backgroundColor || htmlEl.style.background;
                if (bg && isDarkColor(bg)) {
                    htmlEl.style.removeProperty('background-color');
                    htmlEl.style.removeProperty('background');
                }
                const fg = htmlEl.style.color;
                if (fg && isLightColor(fg)) {
                    htmlEl.style.removeProperty('color');
                }
                // Remove the style attribute entirely if now empty
                if (!htmlEl.style.cssText.trim()) {
                    htmlEl.removeAttribute('style');
                }
            });

            const html = scratch.innerHTML;
            const plain = selection.toString();

            // Only override if we actually produced HTML content
            if (html) {
                e.preventDefault();
                e.clipboardData?.setData('text/html', html);
                e.clipboardData?.setData('text/plain', plain);
            }
        };

        document.addEventListener('copy', handler);
        return () => document.removeEventListener('copy', handler);
    }, []);
}

/**
 * Parse a CSS color string to RGB. Handles hex (#rgb, #rrggbb, #rrggbbaa),
 * rgb(), and rgba() formats. Returns null if unparseable.
 */
function parseColor(color: string): { r: number; g: number; b: number } | null {
    const s = color.trim().toLowerCase();

    // hex
    const hexMatch = s.match(/^#([0-9a-f]{3,8})$/);
    if (hexMatch) {
        let hex = hexMatch[1];
        if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
        if (hex.length === 4) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2]+hex[3]+hex[3];
        return {
            r: parseInt(hex.slice(0, 2), 16),
            g: parseInt(hex.slice(2, 4), 16),
            b: parseInt(hex.slice(4, 6), 16),
        };
    }

    // rgb()/rgba()
    const rgbMatch = s.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
    if (rgbMatch) {
        return { r: +rgbMatch[1], g: +rgbMatch[2], b: +rgbMatch[3] };
    }

    return null;
}

/** Relative luminance (0 = black, 1 = white) per WCAG formula */
function luminance(r: number, g: number, b: number): number {
    const [rs, gs, bs] = [r, g, b].map((c) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

/** True if the color is "dark" (luminance < 0.15) — i.e. a dark-mode background */
function isDarkColor(color: string): boolean {
    const rgb = parseColor(color);
    if (!rgb) return false;
    return luminance(rgb.r, rgb.g, rgb.b) < 0.15;
}

/** True if the color is "light" (luminance > 0.85) — i.e. white/near-white text */
function isLightColor(color: string): boolean {
    const rgb = parseColor(color);
    if (!rgb) return false;
    return luminance(rgb.r, rgb.g, rgb.b) > 0.85;
}
