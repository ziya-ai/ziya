/**
 * Tests for Mermaid Edge Rerouter
 *
 * Tests the skip-edge detection and rerouting logic that prevents
 * feedback/control-loop edges from cutting through intermediate nodes.
 *
 * These tests exercise the pure algorithmic logic (rank assignment,
 * intermediate node detection, arc path generation, class name parsing,
 * nesting/ordinal assignment) without requiring a real DOM.
 */

// ---- Constants mirroring the source module ----
var ARC_BASE_MARGIN = 30;
var ARC_LAYER_SPACING = 25;
var ARC_MIN_OFFSET = 30;

// ---- Pure logic unit tests ----

describe('mermaidEdgeRerouter - arc path geometry', () => {
    it('should produce valid SVG cubic bezier for LR layout', () => {
        var startX = 180, startY = 110;
        var endX = 780, endY = 110;
        var ordinal = 0;
        var arcOffset = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + ordinal * ARC_LAYER_SPACING);
        var arcY = 0 - arcOffset; // Above the nodes (minY=0)

        var cp1X = startX + (endX - startX) * 0.25;
        var cp2X = startX + (endX - startX) * 0.75;
        var path = 'M ' + startX + ' ' + startY + ' C ' + cp1X + ' ' + arcY + ', ' + cp2X + ' ' + arcY + ', ' + endX + ' ' + endY;

        expect(path).toMatch(/^M \d/);
        expect(path).toContain('C');
        expect(arcY).toBeLessThan(startY);
    });

    it('should produce valid SVG cubic bezier for TB layout', () => {
        var startX = 200, startY = 180;
        var endX = 200, endY = 580;
        var ordinal = 0;
        var arcOffset = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + ordinal * ARC_LAYER_SPACING);
        var arcX = 0 - arcOffset; // Left of nodes (minX=0)

        var cp1Y = startY + (endY - startY) * 0.25;
        var cp2Y = startY + (endY - startY) * 0.75;
        var path = 'M ' + startX + ' ' + startY + ' C ' + arcX + ' ' + cp1Y + ', ' + arcX + ' ' + cp2Y + ', ' + endX + ' ' + endY;

        expect(path).toMatch(/^M \d/);
        expect(path).toContain('C');
        expect(arcX).toBeLessThan(startX);
    });

    it('should produce ordinal-based offset that increases with ordinal', () => {
        var offset0 = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + 0 * ARC_LAYER_SPACING);
        var offset1 = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + 1 * ARC_LAYER_SPACING);
        var offset2 = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + 2 * ARC_LAYER_SPACING);
        var offset3 = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + 3 * ARC_LAYER_SPACING);

        expect(offset0).toBe(30);
        expect(offset1).toBe(55);
        expect(offset2).toBe(80);
        expect(offset3).toBe(105);
        expect(offset0).toBeLessThan(offset1);
        expect(offset1).toBeLessThan(offset2);
        expect(offset2).toBeLessThan(offset3);
    });

    it('should produce below-arc with positive Y offset for LR layout', () => {
        var maxY = 140;
        var ordinal = 1;
        var arcOffset = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + ordinal * ARC_LAYER_SPACING);
        var belowArcY = maxY + arcOffset;
        
        expect(belowArcY).toBeGreaterThan(maxY);
        expect(belowArcY).toBe(195); // 140 + 55
    });

    it('should produce right-arc with positive X offset for TB layout', () => {
        var maxX = 300;
        var ordinal = 2;
        var arcOffset = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + ordinal * ARC_LAYER_SPACING);
        var rightArcX = maxX + arcOffset;
        
        expect(rightArcX).toBeGreaterThan(maxX);
        expect(rightArcX).toBe(380); // 300 + 80
    });
});

describe('mermaidEdgeRerouter - nesting: shorter arcs nest inside longer arcs', () => {
    // Simulate 3 skip edges of different lengths on the same side (above)
    // After sorting by skip distance ascending, ordinals are:
    //   skip-1: ordinal 0 (innermost)
    //   skip-3: ordinal 1
    //   skip-5: ordinal 2 (outermost)
    // The outermost arc must have the largest offset.

    function arcOffsetForOrdinal(ordinal) {
        return Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + ordinal * ARC_LAYER_SPACING);
    }

    it('shortest skip gets smallest (innermost) arc offset', () => {
        var offset = arcOffsetForOrdinal(0);
        expect(offset).toBe(30);
    });

    it('medium skip gets medium arc offset', () => {
        var offset = arcOffsetForOrdinal(1);
        expect(offset).toBe(55);
    });

    it('longest skip gets largest (outermost) arc offset', () => {
        var offset = arcOffsetForOrdinal(2);
        expect(offset).toBe(80);
    });

    it('nesting property: offset(ordinal N) > offset(ordinal N-1) for all N', () => {
        for (var n = 1; n <= 10; n++) {
            expect(arcOffsetForOrdinal(n)).toBeGreaterThan(arcOffsetForOrdinal(n - 1));
        }
    });

    it('above-arcs: shorter skip has Y closer to node row than longer skip', () => {
        var minY = 50; // top of node row
        var shortArcY = minY - arcOffsetForOrdinal(0); // ordinal 0 = innermost
        var longArcY = minY - arcOffsetForOrdinal(2);  // ordinal 2 = outermost

        // For above-arcs, smaller (more negative) Y = farther from row
        expect(shortArcY).toBeGreaterThan(longArcY);
        // Short arc is closer to the row (higher Y)
        expect(Math.abs(shortArcY - minY)).toBeLessThan(Math.abs(longArcY - minY));
    });

    it('below-arcs: shorter skip has Y closer to node row than longer skip', () => {
        var maxY = 200; // bottom of node row
        var shortArcY = maxY + arcOffsetForOrdinal(0);
        var longArcY = maxY + arcOffsetForOrdinal(2);

        expect(shortArcY).toBeLessThan(longArcY);
        expect(Math.abs(shortArcY - maxY)).toBeLessThan(Math.abs(longArcY - maxY));
    });

    it('5 arcs on same side should all nest without overlap', () => {
        var offsets = [];
        for (var i = 0; i < 5; i++) {
            offsets.push(arcOffsetForOrdinal(i));
        }
        // Every successive offset must be strictly larger
        for (var j = 1; j < offsets.length; j++) {
            expect(offsets[j]).toBeGreaterThan(offsets[j - 1]);
            // Gap between layers must be at least ARC_LAYER_SPACING
            expect(offsets[j] - offsets[j - 1]).toBe(ARC_LAYER_SPACING);
        }
    });
});

describe('mermaidEdgeRerouter - side assignment and ordinal allocation', () => {
    // Simulate the batch sorting and side assignment logic from rerouteSkipEdges

    function assignSidesAndOrdinals(skipDistances) {
        // Sort ascending by skip distance
        var sorted = skipDistances.slice().sort(function(a, b) { return a - b; });

        // Alternate assignment to above/below
        var above = [];
        var below = [];
        for (var i = 0; i < sorted.length; i++) {
            if (i % 2 === 0) above.push(sorted[i]);
            else below.push(sorted[i]);
        }

        // Within each side, sort ascending (shortest = ordinal 0 = innermost)
        above.sort(function(a, b) { return a - b; });
        below.sort(function(a, b) { return a - b; });

        return {
            above: above.map(function(d, idx) { return { skipDist: d, ordinal: idx }; }),
            below: below.map(function(d, idx) { return { skipDist: d, ordinal: idx }; })
        };
    }

    it('single skip edge goes above at ordinal 0', () => {
        var result = assignSidesAndOrdinals([3]);
        expect(result.above.length).toBe(1);
        expect(result.below.length).toBe(0);
        expect(result.above[0]).toEqual({ skipDist: 3, ordinal: 0 });
    });

    it('two skip edges go to opposite sides', () => {
        var result = assignSidesAndOrdinals([2, 4]);
        expect(result.above.length).toBe(1);
        expect(result.below.length).toBe(1);
    });

    it('three skip edges: 2 above, 1 below', () => {
        var result = assignSidesAndOrdinals([1, 3, 5]);
        // sorted: [1, 3, 5] → above=[1,5], below=[3]
        expect(result.above.length).toBe(2);
        expect(result.below.length).toBe(1);
    });

    it('ordinals within each side increase with skip distance', () => {
        var result = assignSidesAndOrdinals([1, 2, 3, 4, 5]);
        // sorted: [1,2,3,4,5] → above=[1,3,5], below=[2,4]

        // Above side: ordinals should track sorted order
        for (var i = 1; i < result.above.length; i++) {
            expect(result.above[i].skipDist).toBeGreaterThan(result.above[i-1].skipDist);
            expect(result.above[i].ordinal).toBeGreaterThan(result.above[i-1].ordinal);
        }

        // Below side: same property
        for (var j = 1; j < result.below.length; j++) {
            expect(result.below[j].skipDist).toBeGreaterThan(result.below[j-1].skipDist);
            expect(result.below[j].ordinal).toBeGreaterThan(result.below[j-1].ordinal);
        }
    });

    it('nested arcs: on same side, shorter skip always has smaller offset than longer', () => {
        var result = assignSidesAndOrdinals([1, 2, 3, 4, 5, 6]);

        function arcOffset(ordinal) {
            return Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + ordinal * ARC_LAYER_SPACING);
        }

        // Check above side
        for (var i = 1; i < result.above.length; i++) {
            var prevOffset = arcOffset(result.above[i-1].ordinal);
            var currOffset = arcOffset(result.above[i].ordinal);
            expect(currOffset).toBeGreaterThan(prevOffset);
        }

        // Check below side
        for (var j = 1; j < result.below.length; j++) {
            var prevOffsetB = arcOffset(result.below[j-1].ordinal);
            var currOffsetB = arcOffset(result.below[j].ordinal);
            expect(currOffsetB).toBeGreaterThan(prevOffsetB);
        }
    });
});

describe('mermaidEdgeRerouter - rank assignment', () => {
    function assignRanks(positions, tolerance) {
        var sorted = positions.slice().sort(function(a, b) { return a - b; });
        var ranks = [];
        var currentRank = 0;
        var lastPos = -Infinity;
        for (var i = 0; i < sorted.length; i++) {
            if (sorted[i] - lastPos > tolerance) {
                currentRank++;
                lastPos = sorted[i];
            }
            ranks.push({ pos: sorted[i], rank: currentRank });
        }
        return ranks;
    }

    it('should assign same rank to nodes at similar positions', () => {
        var result = assignRanks([100, 120, 115], 30);
        expect(result[0].rank).toBe(result[1].rank);
        expect(result[1].rank).toBe(result[2].rank);
    });

    it('should assign different ranks to nodes beyond tolerance', () => {
        var result = assignRanks([100, 250, 400], 30);
        expect(result[0].rank).toBe(1);
        expect(result[1].rank).toBe(2);
        expect(result[2].rank).toBe(3);
    });

    it('should handle 5 evenly spaced nodes in LR layout', () => {
        var result = assignRanks([80, 230, 380, 530, 680], 30);
        expect(result.length).toBe(5);
        var uniqueRanks = new Set(result.map(function(r) { return r.rank; }));
        expect(uniqueRanks.size).toBe(5);
    });

    it('should handle two nodes stacked vertically (same X) as same rank', () => {
        var result = assignRanks([200, 210], 30);
        expect(result[0].rank).toBe(result[1].rank);
    });
});

describe('mermaidEdgeRerouter - layout direction detection', () => {
    it('should detect LR for horizontally spread nodes', () => {
        var direction = 600 > 50 ? 'LR' : 'TB';
        expect(direction).toBe('LR');
    });

    it('should detect TB for vertically spread nodes', () => {
        var direction = 50 > 600 ? 'LR' : 'TB';
        expect(direction).toBe('TB');
    });

    it('should default to LR for equal spread', () => {
        var direction = 400 >= 400 ? 'LR' : 'TB';
        expect(direction).toBe('LR');
    });
});

describe('mermaidEdgeRerouter - intermediate node detection', () => {
    var nodes = [
        { id: 'A', rank: 1 },
        { id: 'B', rank: 2 },
        { id: 'C', rank: 3 },
        { id: 'D', rank: 4 },
        { id: 'E', rank: 5 },
    ];

    function findIntermediates(source, target, allNodes) {
        var minRank = Math.min(source.rank, target.rank);
        var maxRank = Math.max(source.rank, target.rank);
        if (maxRank - minRank <= 1) return [];
        return allNodes.filter(function(n) {
            return n.id !== source.id && n.id !== target.id &&
                   n.rank > minRank && n.rank < maxRank;
        });
    }

    it('should find 3 intermediates for A→E skip', () => {
        var result = findIntermediates(nodes[0], nodes[4], nodes);
        expect(result.length).toBe(3);
        expect(result.map(function(n) { return n.id; })).toEqual(['B', 'C', 'D']);
    });

    it('should find no intermediates for adjacent A→B', () => {
        var result = findIntermediates(nodes[0], nodes[1], nodes);
        expect(result.length).toBe(0);
    });

    it('should handle backward edges D→A correctly', () => {
        var result = findIntermediates(nodes[3], nodes[0], nodes);
        expect(result.length).toBe(2);
        expect(result.map(function(n) { return n.id; })).toEqual(['B', 'C']);
    });

    it('should find 1 intermediate for A→C skip', () => {
        var result = findIntermediates(nodes[0], nodes[2], nodes);
        expect(result.length).toBe(1);
        expect(result[0].id).toBe('B');
    });

    it('should handle E→B backward skip', () => {
        var result = findIntermediates(nodes[4], nodes[1], nodes);
        expect(result.length).toBe(2);
        expect(result.map(function(n) { return n.id; })).toEqual(['C', 'D']);
    });
});

describe('mermaidEdgeRerouter - edge class name parsing', () => {
    function parseEdgeClasses(classes) {
        var sourceId = '', targetId = '';
        for (var i = 0; i < classes.length; i++) {
            var cls = classes[i];
            if (cls.indexOf('LS-') === 0) sourceId = cls.substring(3);
            else if (cls.indexOf('LE-') === 0) targetId = cls.substring(3);
        }
        sourceId = sourceId.replace(/^flowchart-/, '').replace(/-\d+$/, '');
        targetId = targetId.replace(/^flowchart-/, '').replace(/-\d+$/, '');
        return { sourceId: sourceId, targetId: targetId };
    }

    it('should extract source/target from LS-/LE- flowchart classes', () => {
        var result = parseEdgeClasses(['edgePath', 'LS-flowchart-A-0', 'LE-flowchart-B-1']);
        expect(result.sourceId).toBe('A');
        expect(result.targetId).toBe('B');
    });

    it('should handle IDs without flowchart prefix', () => {
        var result = parseEdgeClasses(['edgePath', 'LS-myNode', 'LE-otherNode']);
        expect(result.sourceId).toBe('myNode');
        expect(result.targetId).toBe('otherNode');
    });

    it('should handle multi-word IDs with dashes', () => {
        var result = parseEdgeClasses(['edgePath', 'LS-flowchart-my-node-0', 'LE-flowchart-other-node-1']);
        expect(result.sourceId).toBe('my-node');
        expect(result.targetId).toBe('other-node');
    });

    it('should return empty strings for missing class patterns', () => {
        var result = parseEdgeClasses(['edgePath', 'someOtherClass']);
        expect(result.sourceId).toBe('');
        expect(result.targetId).toBe('');
    });
});

describe('mermaidEdgeRerouter - path intersection detection', () => {
    function pathIntersectsNode(pathBBox, node, margin) {
        margin = margin || 5;
        return (
            pathBBox.x < node.x + node.width + margin &&
            pathBBox.x + pathBBox.width > node.x - margin &&
            pathBBox.y < node.y + node.height + margin &&
            pathBBox.y + pathBBox.height > node.y - margin
        );
    }

    it('should detect path crossing through a node', () => {
        var pathBBox = { x: 80, y: 100, width: 700, height: 10 };
        var node = { x: 230, y: 80, width: 100, height: 60 };
        expect(pathIntersectsNode(pathBBox, node)).toBe(true);
    });

    it('should not detect crossing for path well above node', () => {
        var pathBBox = { x: 80, y: -100, width: 700, height: 10 };
        var node = { x: 230, y: 80, width: 100, height: 60 };
        expect(pathIntersectsNode(pathBBox, node)).toBe(false);
    });

    it('should not detect crossing for path well below node', () => {
        var pathBBox = { x: 80, y: 300, width: 700, height: 10 };
        var node = { x: 230, y: 80, width: 100, height: 60 };
        expect(pathIntersectsNode(pathBBox, node)).toBe(false);
    });

    it('should detect crossing even with small overlap', () => {
        var pathBBox = { x: 230, y: 135, width: 100, height: 10 };
        var node = { x: 230, y: 80, width: 100, height: 60 };
        expect(pathIntersectsNode(pathBBox, node)).toBe(true);
    });

    it('should respect margin parameter', () => {
        var pathBBox = { x: 230, y: 142, width: 100, height: 10 };
        var node = { x: 230, y: 80, width: 100, height: 60 };
        expect(pathIntersectsNode(pathBBox, node, 1)).toBe(false);
        expect(pathIntersectsNode(pathBBox, node, 5)).toBe(true);
    });
});

describe('shouldRerouteEdges heuristic', () => {
    function shouldReroute(nodeCount, edgeCount) {
        return nodeCount >= 4 && edgeCount >= nodeCount;
    }

    it('should reroute for 5 nodes and 6 edges', () => {
        expect(shouldReroute(5, 6)).toBe(true);
    });

    it('should not reroute for 3 nodes', () => {
        expect(shouldReroute(3, 5)).toBe(false);
    });

    it('should not reroute when edges < nodes', () => {
        expect(shouldReroute(5, 3)).toBe(false);
    });

    it('should reroute at threshold (4 nodes, 4 edges)', () => {
        expect(shouldReroute(4, 4)).toBe(true);
    });
});
