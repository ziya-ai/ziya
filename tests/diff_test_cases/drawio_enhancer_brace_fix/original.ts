/**
 * DrawIO Diagram Enhancement Library
 * Fixes rendering issues with maxGraph-generated SVGs, particularly:
 * - ForeignObject positioning when parent has scale transforms
 * - Label positioning for AWS resource icons
 * - Text overflow and clipping issues
 */

export class DrawIOEnhancer {
    /**
     * Fix ALL foreignObject positioning issues
     * MaxGraph uses CSS positioning (margin-left, padding-top) which doesn't work correctly in SVG
     * 
     * @param svgElement The SVG element containing foreignObjects to fix
     * @param graph Optional: The maxGraph instance to update cell geometries (for bounds recalculation)
     */
    static fixAllForeignObjects(svgElement: SVGSVGElement, graph?: any): void {
        console.log('🔧 DrawIOEnhancer: Fixing ALL foreignObject positioning');
        
        const cellGeometryUpdates: Array<{cellId: string, dx: number, dy: number}> = [];
        
        // Find ALL foreignObjects, not just those in scaled groups
        const allForeignObjects = svgElement.querySelectorAll('foreignObject');
        console.log(`📊 Found ${allForeignObjects.length} total foreignObjects`);
        
        allForeignObjects.forEach((fo: Element, idx: number) => {
            const foreignObj = fo as SVGForeignObjectElement;
            const innerDiv = foreignObj.querySelector('div') as HTMLDivElement;
            
            if (!innerDiv) return;
            
            // Get current SVG attributes
            const currentX = parseFloat(foreignObj.getAttribute('x') || '0');
            const currentY = parseFloat(foreignObj.getAttribute('y') || '0');
            
            // Get the CSS positioning from inner div FIRST (before checking dimensions)
            // We need this for both fixes
            const style = innerDiv.getAttribute('style') || '';
            const paddingTopMatch = style.match(/padding-top:\s*([\d.]+)px/);
            const marginLeftMatch = style.match(/margin-left:\s*([\d.]+)px/);
            
            // Try to find the cell ID from parent group
            let cellId: string | null = null;
            let parentElement = foreignObj.parentElement;
            while (parentElement && !cellId) {
                cellId = parentElement.getAttribute('data-cell-id') || parentElement.id;
                parentElement = parentElement.parentElement;
            }
            
            // CRITICAL FIX: Also fix percentage-based dimensions (width="100%", height="100%")
            // These cause foreignObjects to extend infinitely, breaking bounds calculation
            const currentWidth = foreignObj.getAttribute('width');
            const currentHeight = foreignObj.getAttribute('height');
            
            if (currentWidth === '100%' || currentHeight === '100%' || 
                (currentWidth && currentWidth.includes('%')) || 
                (currentHeight && currentHeight.includes('%'))) {
                console.log(`  FO ${idx}: Found percentage dimensions - width: ${currentWidth}, height: ${currentHeight}`);
                
                // Calculate actual content dimensions from inner div
                const contentDiv = innerDiv.querySelector('div:last-child') as HTMLElement;
                
                if (contentDiv) {
                    // Force layout calculation
                    const computedStyle = window.getComputedStyle(contentDiv);
                    const contentWidth = contentDiv.offsetWidth || parseFloat(computedStyle.width) || 100;
                    const contentHeight = contentDiv.offsetHeight || parseFloat(computedStyle.height) || 30;
                    
                    // Set fixed dimensions based on actual content
                    foreignObj.setAttribute('width', Math.max(contentWidth, 100).toString());
                    foreignObj.setAttribute('height', Math.max(contentHeight, 30).toString());
                    
                    console.log(`  ✅ FO ${idx}: Fixed dimensions - width=${contentWidth}px, height=${contentHeight}px`);
                } else {
                    // Fallback: use reasonable defaults
                    foreignObj.setAttribute('width', '100');
                    foreignObj.setAttribute('height', '30');
                    console.log(`  ⚠️ FO ${idx}: Used fallback dimensions - width=100px, height=30px`);
                }
            }
            
            // CRITICAL: ALWAYS apply CSS positioning fix when margin/padding exists
            // Even if we just fixed dimensions above - the two fixes are independent
            const paddingTop = paddingTopMatch ? parseFloat(paddingTopMatch[1]) : 0;
            const marginLeft = marginLeftMatch ? parseFloat(marginLeftMatch[1]) : 0;
            
            if (marginLeft !== 0 || paddingTop !== 0) {
                // We found CSS positioning - this is the bug!
                // Get CURRENT x/y (which might be null/0)
                const currentSvgX = parseFloat(foreignObj.getAttribute('x') || '0');
                const currentSvgY = parseFloat(foreignObj.getAttribute('y') || '0');
                
                console.log(`  FO ${idx}: Found CSS positioning - margin-left: ${marginLeft}px, padding-top: ${paddingTop}px`);
                console.log(`  FO ${idx}: Current SVG position - x: ${currentSvgX}, y: ${currentSvgY}`);
                
                // Move CSS offsets to SVG coordinates
                foreignObj.setAttribute('x', (currentSvgX + marginLeft).toString());
                foreignObj.setAttribute('y', (currentSvgY + paddingTop).toString());
                
                // Remove CSS positioning from inner div
                innerDiv.style.paddingTop = '0';
                innerDiv.style.marginLeft = '0';
                
                // Track geometry updates for maxGraph model
                if (cellId && (marginLeft !== 0 || paddingTop !== 0)) {
                    cellGeometryUpdates.push({
                        cellId,
                        dx: marginLeft,
                        dy: paddingTop
                    });
                }
                
                console.log(`  ✅ FO ${idx}: Fixed positioning - new x=${currentSvgX + marginLeft}, y=${currentSvgY + paddingTop}`);
                
            
            // Mark as enhanced (after all fixes)
            foreignObj.setAttribute('data-enhanced', 'true');
            }
        
        // If we have a graph instance, update cell geometries in the model
        if (graph && cellGeometryUpdates.length > 0) {
            console.log('🔧 DrawIOEnhancer: Updating cell geometries in maxGraph model');
            const model = graph.getModel();
            
            model.beginUpdate();
            try {
                cellGeometryUpdates.forEach(update => {
                    const cell = model.getCell(update.cellId);
                    if (cell) {
                        const geometry = cell.getGeometry();
                        if (geometry) {
                            // Clone geometry to trigger change detection
                            const newGeometry = geometry.clone();
                            
                            // Note: For labels, we don't change the cell position,
                            // we're just ensuring the rendering is correct
                            // The CSS positioning was wrong, not the geometry
                            // So we actually DON'T need to update geometry here
                            
                            console.log(`  Cell ${update.cellId}: Geometry already correct, CSS positioning was the issue`);
                        }
                    }
                });
            } finally {
                model.endUpdate();
            }
        }
        
        console.log(`✅ Fixed ${allForeignObjects.length} foreignObjects`);
    }
    
    /**
     * Fix foreignObject positioning issues caused by scaled parent groups
     * maxGraph uses CSS positioning inside SVG which breaks with transforms
     * @deprecated Use fixAllForeignObjects instead
     */
    static fixForeignObjectPositioning(svgElement: SVGSVGElement): void {
        console.log('🔧 DrawIOEnhancer: Fixing foreignObject positioning in scaled groups');
        
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
