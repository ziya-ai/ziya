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
    static fixAllForeignObjects(svgElement: SVGSVGElement, graph?: any, options?: { explicitLayout?: boolean }): void {
        const isExplicitLayout = options?.explicitLayout ?? graph?.__hasExplicitLayout ?? false;
        console.log('🔧 DrawIOEnhancer: explicitLayout =', isExplicitLayout);

        // For explicit layouts with a graph instance, first force-correct any
        // text-only cells whose foreignObject positioning got confused by
        // maxGraph (common symptom: all text-only cells share the same wrong
        // margin-left). We look up each cell's geometry from the model and
        // compute the absolute SVG coordinates.
        if (isExplicitLayout && graph) {
            DrawIOEnhancer.forceTextCellPositioning(svgElement, graph);
        }

        console.log('🔧 DrawIOEnhancer: Fixing ALL foreignObject positioning');

        const cellGeometryUpdates: Array<{ cellId: string, dx: number, dy: number }> = [];

        // Find ALL foreignObjects, not just those in scaled groups
        const allForeignObjects = svgElement.querySelectorAll('foreignObject');
        console.log(`📊 Found ${allForeignObjects.length} total foreignObjects`);

        allForeignObjects.forEach((fo: Element, idx: number) => {
            const foreignObj = fo as SVGForeignObjectElement;
            const innerDiv = foreignObj.querySelector('div') as HTMLDivElement;

            if (!innerDiv) return;

            // Skip foreignObjects already force-positioned by
            // forceTextCellPositioning() above.
            if (foreignObj.getAttribute('data-force-positioned') === 'true') {
                return;
            }

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

                // For explicit layouts, use the sibling shape element's SVG
                // bounding box as the authoritative size. The content div's
                // computed CSS size is unreliable when width=100% inside a
                // scaled SVG group — it resolves to the full viewport width.
                const parentGroup = foreignObj.parentElement;
                const siblingShape = parentGroup?.querySelector(
                    'rect, ellipse, path[fill]:not([fill="none"]), polygon'
                ) as SVGGraphicsElement | null;

                if (isExplicitLayout && siblingShape) {
                    try {
                        const bbox = siblingShape.getBBox();
                        foreignObj.setAttribute('width', Math.max(bbox.width, 40).toString());
                        foreignObj.setAttribute('height', Math.max(bbox.height, 20).toString());
                        console.log(`  ✅ FO ${idx}: Used sibling shape bounds - width=${bbox.width}px, height=${bbox.height}px`);
                    } catch (e) {
                        foreignObj.setAttribute('width', '100');
                        foreignObj.setAttribute('height', '30');
                    }
                } else {
                    // Auto-layout: use content div's computed size
                    const contentDiv = innerDiv.querySelector('div:last-child') as HTMLElement;

                    if (contentDiv) {
                        const computedStyle = window.getComputedStyle(contentDiv);
                        const contentWidth = contentDiv.offsetWidth || parseFloat(computedStyle.width) || 100;
                        const contentHeight = contentDiv.offsetHeight || parseFloat(computedStyle.height) || 30;

                        foreignObj.setAttribute('width', Math.max(contentWidth, 100).toString());
                        foreignObj.setAttribute('height', Math.max(contentHeight, 30).toString());

                        console.log(`  ✅ FO ${idx}: Fixed dimensions - width=${contentWidth}px, height=${contentHeight}px`);
                    } else {
                        foreignObj.setAttribute('width', '100');
                        foreignObj.setAttribute('height', '30');
                        console.log(`  ⚠️ FO ${idx}: Used fallback dimensions - width=100px, height=30px`);
                    }
                }
            }

            // CRITICAL: ALWAYS apply CSS positioning fix when margin/padding exists
            // Even if we just fixed dimensions above - the two fixes are independent
            const paddingTop = paddingTopMatch ? parseFloat(paddingTopMatch[1]) : 0;
            const marginLeft = marginLeftMatch ? parseFloat(marginLeftMatch[1]) : 0;

            // For explicit-layout diagrams, DON'T convert CSS offsets to SVG positions.
            // MaxGraph uses margin/padding for label alignment within the foreignObject
            // (e.g. centering text). Moving these offsets to SVG x/y displaces the
            // label away from its backing shape. Instead, just ensure overflow is
            // clipped so text stays within its box boundaries.
            if (isExplicitLayout) {
                // Check if there's a sibling shape in the same group.
                // If YES: CSS offsets are for text alignment → skip conversion.
                // If NO: this is a text-only cell where CSS offsets ARE the
                // positioning mechanism → fall through to conversion below.
                const parentGroup = foreignObj.parentElement;
                const hasSiblingShape = parentGroup?.querySelector(
                    'rect, ellipse, path[fill]:not([fill="none"]), polygon'
                );
                if (hasSiblingShape) {
                    innerDiv.style.overflow = 'hidden';
                    foreignObj.style.overflow = 'hidden';
                    foreignObj.setAttribute('data-enhanced', 'true');
                    console.log(`  FO ${idx}: Explicit layout with shape sibling — overflow:hidden, skipped repositioning`);
                    return; // Skip the CSS→SVG conversion below
                }
                console.log(`  FO ${idx}: Explicit layout, no sibling shape — will convert CSS positioning`);
                // Fall through to the CSS→SVG conversion below
            }

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

            }

            // Mark as enhanced (after all fixes)
            foreignObj.setAttribute('data-enhanced', 'true');
        });

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
     * This method walks the maxGraph model, finds text-only cells, matches
     * them to their rendered foreignObjects via normalized label text, and
     * rewrites the foreignObject to use absolute SVG x/y/w/h from the cell's
     * geometry. The wrong CSS margin-left and padding-top are neutralized.
     */
    static forceTextCellPositioning(svgElement: SVGSVGElement, graph: any): void {
        const model = graph.getModel?.() || graph.model;
        if (!model) return;

        // Collect text-only cells from the model. We resolve each cell's
        // actual ON-SCREEN position from the view state (which includes
        // translate + scale), not from raw XML geometry — because maxGraph's
        // other rendering (e.g. sibling background rects) uses view state
        // coordinates, and we need our foreignObject to match those.
        const textCells: Array<{ id: string; value: string; x: number; y: number; w: number; h: number }> = [];
        const visit = (cell: any, offsetX: number, offsetY: number) => {
            if (!cell) return;
            const geom = cell.getGeometry?.();
            const style = cell.getStyle?.();
            // maxGraph parses `text;...` style strings as {text: 1} since the
            // leading "text" has no '=' and isn't in the shape name list.
            const isText = style && typeof style === 'object' &&
                (style['shape'] === 'text' || style['text'] === 1 || style['text'] === '1');
            const absX = geom ? offsetX + (geom.x || 0) : offsetX;
            const absY = geom ? offsetY + (geom.y || 0) : offsetY;

            if (cell.isVertex?.() && isText && geom && cell.getValue()) {
                // Use raw absolute cell geometry (pre-scale). This matches
                // the coordinate frame that maxGraph's CSS margin-left uses
                // inside the scale(N) parent group. Using view-state coords
                // here would give us screen-space values that render in the
                // wrong position after the parent's scale transform applies.
                textCells.push({
                    id: cell.getId(),
                    value: String(cell.getValue()).trim(),
                    x: absX, y: absY, w: geom.width, h: geom.height,
                });
            }

            const childCount = cell.getChildCount?.() || (cell.children?.length ?? 0);
            for (let i = 0; i < childCount; i++) {
                const child = cell.getChildAt?.(i) ?? cell.children?.[i];
                // Children of root cells (0 and 1) render at their own geometry.
                // Children of non-root cells (e.g. swimlanes) render relative
                // to the parent's absolute position.
                const cellId = cell.getId?.();
                const parentIsRoot = cellId === '0' || cellId === '1';
                visit(child, parentIsRoot ? 0 : absX, parentIsRoot ? 0 : absY);
            }
        };
        visit(model.getRoot?.(), 0, 0);

        if (textCells.length === 0) {
            console.log('🎯 forceTextCellPositioning: No text-only cells found');
            return;
        }
        console.log(`🎯 forceTextCellPositioning: Found ${textCells.length} text cells to verify`);

        // Use maxGraph's own view state to locate each cell's rendered DOM
        // nodes — this is the authoritative cell→DOM pairing, no text
        // matching or coordinate assumptions needed. For each text cell:
        //   state.shape.node — SVG group containing the background rect
        //   state.text.node  — SVG group containing the label foreignObject
        // We then read both elements' actual rendered screen positions and
        // shift the label's margin-left so the text aligns with its shape.
        const view = graph.view;

        textCells.forEach(tc => {
            const cell = typeof model.getCell === 'function'
                ? model.getCell(tc.id)
                : null;
            const state = cell && view?.getState ? view.getState(cell) : null;
            if (!state) {
                console.log(`  ⚠️ No view state for text cell ${tc.id}`);
                return;
            }

            const shapeNode: Element | null = state.shape?.node || null;
            const labelNode: Element | null = state.text?.node || null;
            if (!shapeNode || !labelNode) {
                console.log(`  ⚠️ Missing shape or label node for ${tc.id}`);
                return;
            }

            const foreignObj = labelNode.querySelector('foreignObject') as SVGForeignObjectElement | null;
            if (!foreignObj) {
                console.log(`  ⚠️ No foreignObject under label node for ${tc.id}`);
                return;
            }
            const innerDiv = foreignObj.querySelector('div') as HTMLDivElement | null;
            if (!innerDiv) return;

            // DIAGNOSTIC: log what state.shape.node actually IS for this cell.
            // For text-style cells, it may be an invisible/empty element rather
            // than the visible background rect we want to align with.
            if (tc.id === 'dp_note' || tc.id === 'merlin_note' || tc.id === 'mirror_note') {
                const shapeInner = shapeNode.querySelector('rect, ellipse, path, polygon');
                console.log(`🔬 DIAG ${tc.id}:`, {
                    shapeTag: shapeNode.tagName,
                    shapeInnerTag: shapeInner?.tagName,
                    shapeHasFill: shapeInner?.getAttribute('fill'),
                    shapeRect: shapeNode.getBoundingClientRect(),
                    labelTag: labelNode.tagName,
                    labelRect: labelNode.getBoundingClientRect(),
                    foRect: foreignObj.getBoundingClientRect(),
                    divRect: innerDiv.getBoundingClientRect(),
                    // What visible gray rects exist in the SVG
                    grayRects: Array.from((foreignObj.ownerSVGElement?.querySelectorAll('rect[fill="#f5f5f5"]') || []))
                        .map(r => ({ attrs: { x: r.getAttribute('x'), y: r.getAttribute('y'), w: r.getAttribute('width'), h: r.getAttribute('height') }, screen: r.getBoundingClientRect() })),
                });
            }

            // Measure actual on-screen positions of the shape (background)
            // and the label's inner div (where the text renders).
            const shapeScreen = shapeNode.getBoundingClientRect();
            const divScreen = innerDiv.getBoundingClientRect();
            const screenDx = shapeScreen.x - divScreen.x;

            // Convert screen-px delta to the margin-left coordinate frame,
            // which is scaled by the accumulated parent-chain scale.
            let accumScale = 1;
            let p: Element | null = foreignObj.parentElement;
            while (p && p.tagName !== 'svg' && p.tagName !== 'SVG') {
                const t = (p.getAttribute && p.getAttribute('transform')) || '';
                const m = t.match(/scale\(([\d.]+)\)/);
                if (m) accumScale *= parseFloat(m[1]);
                p = p.parentElement;
            }
            if (!accumScale || !isFinite(accumScale)) accumScale = 1;

            const styleStr = innerDiv.getAttribute('style') || '';
            const mlMatch = styleStr.match(/margin-left:\s*(-?[\d.]+)px/);
            const currentMl = mlMatch ? parseFloat(mlMatch[1]) : 0;
            const newMl = currentMl + screenDx / accumScale;

            const newStyle = mlMatch
                ? styleStr.replace(/margin-left:\s*-?[\d.]+px/, `margin-left: ${newMl}px`)
                : styleStr + `; margin-left: ${newMl}px;`;
            innerDiv.setAttribute('style', newStyle);
            // Do NOT set overflow:hidden — it interacts badly with
            // maxGraph's vertical-centering hack (height:1px + padding-top).

            foreignObj.setAttribute('data-force-positioned', 'true');
            console.log(
                `  ✅ Aligned ${tc.id}: screenDx=${screenDx.toFixed(1)}px, `+
                `margin-left ${currentMl.toFixed(1)} → ${newMl.toFixed(1)} `+
                `(scale=${accumScale.toFixed(3)})`
            );
        });
    }

    /*
     * MaxGraph draws markers as inline SVG paths whose size is
     * (endSize + strokeWidth). After fit() scales the view, markers
     * can appear disproportionately large. This post-processes the
     * SVG to cap marker paths at a reasonable pixel size.
     *
     * Markers are identified as small filled path elements that are
     * children of edge shape groups (groups containing a polyline or
     * path with no fill).
     */
    static scaleDownArrowMarkers(svgElement: SVGSVGElement, maxMarkerPx: number = 12): void {
        // Find groups that contain edge shapes (polyline or unfilled path + filled path siblings)
        const allPaths = svgElement.querySelectorAll('path[fill]:not([fill="none"])');
        let scaled = 0;

        allPaths.forEach((pathEl: Element) => {
            const path = pathEl as SVGPathElement;
            // Marker paths are small, filled, and siblings of an unfilled line/path
            const parent = path.parentElement;
            if (!parent) return;

            // Check if this group contains a line element (edge body)
            const hasEdgeLine = parent.querySelector(
                'path[fill="none"], polyline, line'
            );
            if (!hasEdgeLine) return;

            // Measure the path's bounding box
            try {
                const bbox = path.getBBox();
                const maxDim = Math.max(bbox.width, bbox.height);

                if (maxDim > maxMarkerPx) {
                    const scaleFactor = maxMarkerPx / maxDim;
                    // Scale around the path's center point
                    const cx = bbox.x + bbox.width / 2;
                    const cy = bbox.y + bbox.height / 2;
                    const existing = path.getAttribute('transform') || '';
                    path.setAttribute('transform',
                        `${existing} translate(${cx},${cy}) scale(${scaleFactor.toFixed(3)}) translate(${-cx},${-cy})`
                    );
                    scaled++;
                }
            } catch (e) {
                // getBBox can throw if element isn't rendered yet
            }
        });

        if (scaled > 0) {
            console.log(`🔧 DrawIOEnhancer: Scaled down ${scaled} oversized arrow markers (max ${maxMarkerPx}px)`);
        }
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
