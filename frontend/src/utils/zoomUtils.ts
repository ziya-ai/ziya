/**
 * Shared zoom utility functions for D3 visualizations
 * Consolidates duplicate implementations from graphviz, mermaid, vega, and D3Renderer
 */

export const ZOOM_SCALE_FACTOR = 1.2;
export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 10;

/**
 * Zoom in on an SVG element
 */
export function zoomIn(svgElement: SVGSVGElement | null): void {
    if (!svgElement) return;
    
    const viewBox = svgElement.viewBox.baseVal;
    const newWidth = viewBox.width / ZOOM_SCALE_FACTOR;
    const newHeight = viewBox.height / ZOOM_SCALE_FACTOR;
    const dx = (viewBox.width - newWidth) / 2;
    const dy = (viewBox.height - newHeight) / 2;
    
    svgElement.setAttribute('viewBox', 
        `${viewBox.x + dx} ${viewBox.y + dy} ${newWidth} ${newHeight}`);
}

/**
 * Zoom out on an SVG element
 */
export function zoomOut(svgElement: SVGSVGElement | null): void {
    if (!svgElement) return;
    
    const viewBox = svgElement.viewBox.baseVal;
    const newWidth = viewBox.width * ZOOM_SCALE_FACTOR;
    const newHeight = viewBox.height * ZOOM_SCALE_FACTOR;
    const dx = (viewBox.width - newWidth) / 2;
    const dy = (viewBox.height - newHeight) / 2;
    
    svgElement.setAttribute('viewBox', 
        `${viewBox.x + dx} ${viewBox.y + dy} ${newWidth} ${newHeight}`);
}

/**
 * Reset zoom to original viewBox
 */
export function resetZoom(svgElement: SVGSVGElement | null, originalViewBox?: string): void {
    if (!svgElement) return;
    
    if (originalViewBox) {
        svgElement.setAttribute('viewBox', originalViewBox);
    } else {
        // Try to restore from data attribute if available
        const stored = svgElement.getAttribute('data-original-viewbox');
        if (stored) {
            svgElement.setAttribute('viewBox', stored);
        }
    }
}

/**
 * Store original viewBox for later reset
 */
export function storeOriginalViewBox(svgElement: SVGSVGElement): void {
    const viewBox = svgElement.getAttribute('viewBox');
    if (viewBox && !svgElement.getAttribute('data-original-viewbox')) {
        svgElement.setAttribute('data-original-viewbox', viewBox);
    }
}
