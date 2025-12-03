/**
 * DrawIO Diagram Enhancement Library
 * Fixes rendering issues with maxGraph-generated SVGs, particularly:
 * - ForeignObject positioning when parent has scale transforms
 * - Label positioning for AWS resource icons
 * - Text overflow and clipping issues
 */

export class DrawIOEnhancer {
    /**
     * Fix foreignObject positioning issues caused by scaled parent groups
     * maxGraph uses CSS positioning inside SVG which breaks with transforms
     */
    static fixForeignObjectPositioning(svgElement: SVGSVGElement): void {
        console.log('🔧 DrawIOEnhancer: Fixing foreignObject positioning');
        
        // Find all g elements with scale transforms
        const scaledGroups = svgElement.querySelectorAll('g[transform*="scale"]');
        console.log(`📊 Found ${scaledGroups.length} scaled groups`);
        
        scaledGroups.forEach((group: Element) => {
            const transform = (group as SVGGElement).getAttribute('transform');
            if (!transform) return;
            
            // Extract scale value
            const scaleMatch = transform.match(/scale\(([\d.]+)\)/);
            if (!scaleMatch) return;
            
            const scale = parseFloat(scaleMatch[1]);
            console.log(`🔄 Processing scaled group with scale=${scale.toFixed(4)}`);
            
            // Find all foreignObjects in this scaled group
            const foreignObjects = (group as SVGGElement).querySelectorAll('foreignObject');
            console.log(`  Found ${foreignObjects.length} foreignObjects`);
            
            foreignObjects.forEach((fo: Element, foIdx: number) => {
                const foreignObj = fo as SVGForeignObjectElement;
                const innerDiv = foreignObj.querySelector('div') as HTMLDivElement;
                
                if (!innerDiv) return;
                
                // Get the positioning from the inner div's style
                const style = innerDiv.getAttribute('style') || '';
                const paddingTopMatch = style.match(/padding-top:\s*([\d.]+)px/);
                const marginLeftMatch = style.match(/margin-left:\s*([\d.]+)px/);
                
                if (!paddingTopMatch || !marginLeftMatch) return;
                
                const paddingTop = parseFloat(paddingTopMatch[1]);
                const marginLeft = parseFloat(marginLeftMatch[1]);
                
                // Convert CSS positioning to SVG coordinates accounting for scale
                const svgX = marginLeft / scale;
                const svgY = paddingTop / scale;
                
                // Get the width/height from the foreignObject or inner content
                let width = parseFloat(foreignObj.getAttribute('width') || '0');
                let height = parseFloat(foreignObj.getAttribute('height') || '0');
                
                // If dimensions are percentages (e.g., "544%"), we need to calculate actual dimensions
                const widthAttr = foreignObj.getAttribute('width') || '';
                const heightAttr = foreignObj.getAttribute('height') || '';
                
                if (widthAttr.includes('%')) {
                    // For percentages, use the SVG viewBox or actual content size
                    const content = innerDiv.querySelector('div:last-child') as HTMLElement;
                    if (content) {
                        width = content.offsetWidth / scale;
                    } else {
                        width = 100; // fallback
                    }
                }
                
                if (heightAttr.includes('%')) {
                    const content = innerDiv.querySelector('div:last-child') as HTMLElement;
                    if (content) {
                        height = content.offsetHeight / scale;
                    } else {
                        height = 30; // fallback
                    }
                }
                
                // Set absolute SVG coordinates
                foreignObj.setAttribute('x', svgX.toFixed(2));
                foreignObj.setAttribute('y', svgY.toFixed(2));
                foreignObj.setAttribute('width', width.toFixed(2));
                foreignObj.setAttribute('height', height.toFixed(2));
                
                // Remove percentage-based dimensions
                if (foreignObj.getAttribute('width')?.includes('%')) {
                    foreignObj.removeAttribute('width');
                }
                if (foreignObj.getAttribute('height')?.includes('%')) {
                    foreignObj.removeAttribute('height');
                }
                
                // Simplify inner div to just flex positioning (SVG will handle x/y)
                innerDiv.setAttribute('style', 'display: flex; align-items: center; justify-content: center;');
                
                console.log(`  ✅ FO ${foIdx}: Fixed positioning x=${svgX.toFixed(1)}, y=${svgY.toFixed(1)}, w=${width.toFixed(1)}, h=${height.toFixed(1)}`);
            });
        });
    }
    
    /**
     * Fix label positioning for AWS resource icons
     * Ensures labels appear below icons instead of far to the right
     */
    static fixAWSIconLabeling(svgElement: SVGSVGElement): void {
        console.log('🔧 DrawIOEnhancer: Fixing AWS icon label positioning');
        
        const foreignObjects = svgElement.querySelectorAll('foreignObject');
        let fixedCount = 0;
        
        foreignObjects.forEach((fo: Element) => {
            const innerDiv = fo.querySelector('div') as HTMLDivElement;
            if (!innerDiv) return;
            
            // Add flex centering for better label positioning
            const currentStyle = innerDiv.getAttribute('style') || '';
            if (!currentStyle.includes('display: flex')) {
                innerDiv.setAttribute('style', 'display: flex; flex-direction: column; align-items: center; justify-content: center;');
                fixedCount++;
            }
        });
        
        console.log(`✅ Fixed ${fixedCount} AWS icon labels`);
    }
}
