/**
 * Shared SVG utility functions
 * Consolidates duplicate implementations from D3 plugins
 */

/**
 * Download SVG element as a file
 */
export function downloadSvg(svgElement: SVGSVGElement | null, filename: string = 'diagram.svg'): void {
    if (!svgElement) {
        console.error('No SVG element to download');
        return;
    }

    try {
        // Clone the SVG to avoid modifying the original
        const clonedSvg = svgElement.cloneNode(true) as SVGSVGElement;
        
        // Serialize the SVG
        const serializer = new XMLSerializer();
        const svgString = serializer.serializeToString(clonedSvg);
        
        // Create blob and download
        const blob = new Blob([svgString], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    } catch (error) {
        console.error('Error downloading SVG:', error);
    }
}

/**
 * Convert SVG to PNG and download
 */
export function downloadSvgAsPng(svgElement: SVGSVGElement | null, filename: string = 'diagram.png'): void {
    if (!svgElement) {
        console.error('No SVG element to download');
        return;
    }

    try {
        const serializer = new XMLSerializer();
        const svgString = serializer.serializeToString(svgElement);
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const img = new Image();

        img.onload = () => {
            canvas.width = img.width;
            canvas.height = img.height;
            ctx?.drawImage(img, 0, 0);
            canvas.toBlob((blob) => {
                if (blob) {
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = filename;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                }
            });
        };

        img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgString)));
    } catch (error) {
        console.error('Error downloading SVG as PNG:', error);
    }
}
