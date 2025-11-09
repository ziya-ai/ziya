/**
 * Shared color utility functions for D3 plugins
 * Consolidates duplicate implementations from graphviz, mermaid, and vega plugins
 */

/**
 * Convert hex color to RGB components
 */
export function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
    } : null;
}

/**
 * Get luminance component for a color value (0-255)
 */
export function getLuminanceComponent(c: number): number {
    const normalized = c / 255;
    return normalized <= 0.03928
        ? normalized / 12.92
        : Math.pow((normalized + 0.055) / 1.055, 2.4);
}

/**
 * Calculate relative luminance of an RGB color
 * Returns value between 0 (darkest) and 1 (lightest)
 */
export function luminance(r: number, g: number, b: number): number {
    const rLum = getLuminanceComponent(r);
    const gLum = getLuminanceComponent(g);
    const bLum = getLuminanceComponent(b);
    return 0.2126 * rLum + 0.7152 * gLum + 0.0722 * bLum;
}

/**
 * Determine if a background color is light
 */
export function isLightBackground(bgColor: string): boolean {
    const rgb = hexToRgb(bgColor);
    if (!rgb) return false;
    const lum = luminance(rgb.r, rgb.g, rgb.b);
    return lum > 0.5;
}

/**
 * Get optimal text color (black or white) for a given background
 */
export function getOptimalTextColor(bgColor: string): string {
    return isLightBackground(bgColor) ? '#000000' : '#ffffff';
}
