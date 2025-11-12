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
 * Handles hex, rgb(), and named color formats
 */
export function isLightBackground(color: string): boolean {
    if (!color || color === 'transparent' || color === 'none') {
        return false;
    }
    
    // Parse color to RGB values
    let r = 0, g = 0, b = 0;
    
    // Handle hex format
    const hexMatch = color.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
    if (hexMatch) {
        r = parseInt(hexMatch[1], 16);
        g = parseInt(hexMatch[2], 16);
        b = parseInt(hexMatch[3], 16);
    }
    // Handle rgb() format
    else if (color.startsWith('rgb')) {
        const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
        if (rgbMatch) {
            r = parseInt(rgbMatch[1]);
            g = parseInt(rgbMatch[2]);
            b = parseInt(rgbMatch[3]);
        } else {
            return false;
        }
    }
    // Handle named colors
    else {
        const lightNamedColors = [
            'white', 'lightblue', 'lightgreen', 'lightyellow', 'lightgrey', 'lightgray', 'pink',
            'yellow', '#aed6f1', '#d4e6f1', '#d5f5e3', '#f5f5f5', '#e6e6e6', '#f0f0f0',
            '#ffffff', '#f8f9fa', '#e9ecef', '#dee2e6', '#ced4da', '#adb5bd'
        ];
        return lightNamedColors.some(c => c.toLowerCase() === color.toLowerCase());
    }
    
    // Calculate proper sRGB luminance
    const lum = luminance(r, g, b);
    
    // Use threshold where anything above 0.4 luminance is considered light
    return lum > 0.4;
}

/**
 * Get optimal text color (black or white) for a given background
 * Includes special handling for yellow and yellow-ish colors
 */
export function getOptimalTextColor(backgroundColor: string): string {
    const rgb = hexToRgb(backgroundColor);
    if (!rgb) return '#000000';
    
    // Special handling for yellow and yellow-ish colors
    if (rgb.r > 200 && rgb.g > 200 && rgb.b < 100) {
        return '#000000'; // Always use black on yellow
    }
    
    // Calculate luminance and use conservative threshold
    const lum = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
    return lum > 0.4 ? '#000000' : '#ffffff';
}
