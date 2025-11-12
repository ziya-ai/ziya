/**
 * Shared utilities for generating inline scripts in popup windows
 */

export const getZoomScript = () => `
    let currentScale = 1;
    const svg = document.querySelector('svg');
    
    function zoomIn() {
        currentScale *= 1.2;
        svg.style.transform = \`scale(\${currentScale})\`;
    }
    
    function zoomOut() {
        currentScale /= 1.2;
        svg.style.transform = \`scale(\${currentScale})\`;
    }
    
    function resetZoom() {
        currentScale = 1;
        svg.style.transform = 'scale(1)';
    }
`;

export const getDownloadSvgScript = (filename: string = 'diagram.svg') => `
    function downloadSvg() {
        const svgData = new XMLSerializer().serializeToString(svg);
        const svgBlob = new Blob([svgData], {type: 'image/svg+xml'});
        const url = URL.createObjectURL(svgBlob);
        
        const link = document.createElement('a');
        link.href = url;
        link.download = '${filename}';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
`;
