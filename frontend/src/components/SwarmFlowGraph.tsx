/**
 * SwarmFlowGraph — Interactive D3 DAG visualization for delegate swarms.
 *
 * Renders delegates as nodes in a directed acyclic graph based on their
 * dependency relationships. Nodes are colored/animated by live status.
 * Edges show data flow from dependencies → dependents.
 *
 * Layout: topological layering (BFS from roots) with even spacing per layer.
 */

import React, { useRef, useEffect, useMemo } from 'react';
import * as d3 from 'd3';

export interface SwarmNode {
  id: string;
  name: string;
  emoji: string;
  status: 'queued' | 'running' | 'compacting' | 'crystal' | 'failed' | 'interrupted' | 'blocked' | 'proposed' | 'ready';
  dependencies: string[];
}

interface SwarmFlowGraphProps {
  nodes: SwarmNode[];
  planName: string;
  /** Fires when user clicks a delegate node */
  onNodeClick?: (delegateId: string) => void;
}

// Status → visual styling
const STATUS_COLORS: Record<string, { fill: string; stroke: string; text: string }> = {
  queued:      { fill: '#2a2a3e', stroke: '#555',    text: '#aaa' },
  proposed:    { fill: '#2a2a3e', stroke: '#555',    text: '#aaa' },
  ready:       { fill: '#2a2a3e', stroke: '#6366f1', text: '#ccc' },
  running:     { fill: '#1a1a40', stroke: '#6366f1', text: '#e0e0ff' },
  compacting:  { fill: '#1a2a1a', stroke: '#a78bfa', text: '#d8b4fe' },
  crystal:     { fill: '#0a2a0a', stroke: '#52c41a', text: '#b7eb8f' },
  failed:      { fill: '#2a0a0a', stroke: '#ff4d4f', text: '#ffa39e' },
  interrupted: { fill: '#2a1a0a', stroke: '#fa8c16', text: '#ffd591' },
  blocked:     { fill: '#2a2a0a', stroke: '#faad14', text: '#ffe58f' },
};

// Layout constants
const NODE_W = 140;
const NODE_H = 44;
const LAYER_GAP_X = 180;
const NODE_GAP_Y = 58;
const PAD_X = 60;
const PAD_Y = 40;

/**
 * Assign each node to a layer via BFS from roots.
 * Layer = longest path from any root to this node.
 */
function computeLayers(nodes: SwarmNode[]): Map<string, number> {
  const idSet = new Set(nodes.map(n => n.id));
  const layers = new Map<string, number>();
  const dependents = new Map<string, string[]>(); // dep → nodes that depend on it

  for (const n of nodes) {
    const validDeps = n.dependencies.filter(d => idSet.has(d));
    for (const d of validDeps) {
      if (!dependents.has(d)) dependents.set(d, []);
      dependents.get(d)!.push(n.id);
    }
  }

  // Roots: nodes with no dependencies (or deps outside this graph)
  const roots = nodes.filter(n => n.dependencies.filter(d => idSet.has(d)).length === 0);

  // BFS — assign max depth (longest path from any root)
  const queue: Array<{ id: string; depth: number }> = roots.map(r => ({ id: r.id, depth: 0 }));
  while (queue.length > 0) {
    const { id, depth } = queue.shift()!;
    if (layers.has(id) && layers.get(id)! >= depth) continue;
    layers.set(id, depth);
    for (const child of dependents.get(id) || []) {
      queue.push({ id: child, depth: depth + 1 });
    }
  }

  // Assign any unvisited nodes to layer 0 (safety)
  for (const n of nodes) {
    if (!layers.has(n.id)) layers.set(n.id, 0);
  }

  return layers;
}

const SwarmFlowGraph: React.FC<SwarmFlowGraphProps> = ({ nodes, planName, onNodeClick }) => {
  const svgRef = useRef<SVGSVGElement>(null);

  // Compute graph layout
  const layout = useMemo(() => {
    if (nodes.length === 0) return { positions: new Map(), edges: [], width: 0, height: 0, maxLayer: 0 };

    const layers = computeLayers(nodes);
    const maxLayer = Math.max(...layers.values());

    // Group nodes by layer
    const byLayer = new Map<number, SwarmNode[]>();
    for (const n of nodes) {
      const l = layers.get(n.id) ?? 0;
      if (!byLayer.has(l)) byLayer.set(l, []);
      byLayer.get(l)!.push(n);
    }

    // Position nodes
    const maxNodesInLayer = Math.max(...Array.from(byLayer.values()).map(a => a.length));
    const positions = new Map<string, { x: number; y: number }>();

    for (const [layer, layerNodes] of byLayer) {
      const totalHeight = layerNodes.length * NODE_H + (layerNodes.length - 1) * (NODE_GAP_Y - NODE_H);
      const maxHeight = maxNodesInLayer * NODE_H + (maxNodesInLayer - 1) * (NODE_GAP_Y - NODE_H);
      const startY = PAD_Y + (maxHeight - totalHeight) / 2;

      layerNodes.forEach((n, i) => {
        positions.set(n.id, {
          x: PAD_X + layer * LAYER_GAP_X,
          y: startY + i * NODE_GAP_Y,
        });
      });
    }

    // Build edges
    const idSet = new Set(nodes.map(n => n.id));
    const edges: Array<{ from: string; to: string }> = [];
    for (const n of nodes) {
      for (const dep of n.dependencies) {
        if (idSet.has(dep)) {
          edges.push({ from: dep, to: n.id });
        }
      }
    }

    const width = PAD_X * 2 + maxLayer * LAYER_GAP_X + NODE_W;
    const height = PAD_Y * 2 + maxNodesInLayer * NODE_GAP_Y;

    return { positions, edges, width: Math.max(width, 300), height: Math.max(height, 100), maxLayer };
  }, [nodes]);

  // D3 render
  useEffect(() => {
    if (!svgRef.current || nodes.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const { positions, edges, width, height } = layout;

    svg.attr('viewBox', `0 0 ${width} ${height}`);

    // Defs: arrow marker + glow filter
    const defs = svg.append('defs');

    defs.append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 0 10 6')
      .attr('refX', 10)
      .attr('refY', 3)
      .attr('markerWidth', 8)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,0 L10,3 L0,6 Z')
      .attr('fill', '#555');

    const glowFilter = defs.append('filter')
      .attr('id', 'glow')
      .attr('x', '-30%').attr('y', '-30%')
      .attr('width', '160%').attr('height', '160%');
    glowFilter.append('feGaussianBlur')
      .attr('stdDeviation', '3')
      .attr('result', 'blur');
    glowFilter.append('feMerge').selectAll('feMergeNode')
      .data(['blur', 'SourceGraphic'])
      .enter().append('feMergeNode')
      .attr('in', d => d);

    // Pulse animation for running nodes
    const pulseFilter = defs.append('filter')
      .attr('id', 'pulse-glow')
      .attr('x', '-40%').attr('y', '-40%')
      .attr('width', '180%').attr('height', '180%');
    pulseFilter.append('feGaussianBlur')
      .attr('stdDeviation', '4')
      .attr('result', 'blur');
    const pulseMerge = pulseFilter.append('feMerge');
    pulseMerge.append('feMergeNode').attr('in', 'blur');
    pulseMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    // Draw edges
    const edgeGroup = svg.append('g').attr('class', 'edges');
    for (const edge of edges) {
      const from = positions.get(edge.from);
      const to = positions.get(edge.to);
      if (!from || !to) continue;

      const x1 = from.x + NODE_W;
      const y1 = from.y + NODE_H / 2;
      const x2 = to.x;
      const y2 = to.y + NODE_H / 2;
      const midX = (x1 + x2) / 2;

      // Source node status determines edge color
      const sourceNode = nodes.find(n => n.id === edge.from);
      const edgeColor = sourceNode?.status === 'crystal' ? '#52c41a'
        : sourceNode?.status === 'running' ? '#6366f1'
        : '#444';

      edgeGroup.append('path')
        .attr('d', `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`)
        .attr('fill', 'none')
        .attr('stroke', edgeColor)
        .attr('stroke-width', sourceNode?.status === 'crystal' ? 2 : 1.5)
        .attr('stroke-dasharray', sourceNode?.status === 'crystal' ? 'none' : '4,3')
        .attr('marker-end', 'url(#arrow)')
        .attr('opacity', 0.7);
    }

    // Draw nodes
    const nodeGroup = svg.append('g').attr('class', 'nodes');

    for (const node of nodes) {
      const pos = positions.get(node.id);
      if (!pos) continue;

      const colors = STATUS_COLORS[node.status] || STATUS_COLORS.queued;
      const g = nodeGroup.append('g')
        .attr('transform', `translate(${pos.x}, ${pos.y})`)
        .attr('cursor', onNodeClick ? 'pointer' : 'default')
        .on('click', () => onNodeClick?.(node.id));

      // Node background
      g.append('rect')
        .attr('width', NODE_W)
        .attr('height', NODE_H)
        .attr('rx', 8)
        .attr('ry', 8)
        .attr('fill', colors.fill)
        .attr('stroke', colors.stroke)
        .attr('stroke-width', node.status === 'running' ? 2 : 1.5)
        .attr('filter', node.status === 'running' ? 'url(#pulse-glow)'
          : node.status === 'crystal' ? 'url(#glow)' : null);

      // Emoji
      g.append('text')
        .attr('x', 12)
        .attr('y', NODE_H / 2 + 1)
        .attr('dominant-baseline', 'central')
        .attr('font-size', '16px')
        .text(node.emoji);

      // Name (truncated)
      const displayName = node.name.length > 12 ? node.name.slice(0, 11) + '…' : node.name;
      g.append('text')
        .attr('x', 34)
        .attr('y', NODE_H / 2 + 1)
        .attr('dominant-baseline', 'central')
        .attr('fill', colors.text)
        .attr('font-size', '12px')
        .attr('font-weight', 500)
        .text(displayName);

      // Status indicator dot
      const dotColor = node.status === 'crystal' ? '#52c41a'
        : node.status === 'running' ? '#6366f1'
        : node.status === 'failed' ? '#ff4d4f'
        : node.status === 'compacting' ? '#a78bfa'
        : 'transparent';

      if (dotColor !== 'transparent') {
        g.append('circle')
          .attr('cx', NODE_W - 14)
          .attr('cy', NODE_H / 2)
          .attr('r', 4)
          .attr('fill', dotColor);

        // Pulse animation on running indicator
        if (node.status === 'running') {
          g.append('circle')
            .attr('cx', NODE_W - 14)
            .attr('cy', NODE_H / 2)
            .attr('r', 4)
            .attr('fill', 'none')
            .attr('stroke', '#6366f1')
            .attr('stroke-width', 1.5)
            .attr('opacity', 0.8)
            .append('animate')
            .attr('attributeName', 'r')
            .attr('from', '4')
            .attr('to', '12')
            .attr('dur', '1.5s')
            .attr('repeatCount', 'indefinite');

          g.select('circle:last-child')
            .append('animate')
            .attr('attributeName', 'opacity')
            .attr('from', '0.8')
            .attr('to', '0')
            .attr('dur', '1.5s')
            .attr('repeatCount', 'indefinite');
        }
      }

      // Crystal icon for completed
      if (node.status === 'crystal') {
        g.append('text')
          .attr('x', NODE_W - 16)
          .attr('y', NODE_H / 2 + 1)
          .attr('dominant-baseline', 'central')
          .attr('font-size', '12px')
          .attr('text-anchor', 'middle')
          .text('💎');
      }
    }

  }, [nodes, layout, onNodeClick]);

  if (nodes.length === 0) return null;

  // Summary stats
  const running = nodes.filter(n => n.status === 'running' || n.status === 'compacting').length;
  const done = nodes.filter(n => n.status === 'crystal').length;
  const failed = nodes.filter(n => n.status === 'failed').length;

  return (
    <div style={{
      margin: '12px 20px',
      background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.06) 0%, rgba(139, 92, 246, 0.06) 100%)',
      border: '1px solid rgba(99, 102, 241, 0.25)',
      borderRadius: 10,
      overflow: 'hidden',
    }}>
      {/* Header bar */}
      <div style={{
        padding: '10px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        borderBottom: '1px solid rgba(99, 102, 241, 0.15)',
      }}>
        <span style={{
          fontSize: 20,
          animation: running > 0 ? 'pulse 2s infinite' : undefined,
        }}>⚡</span>
        <div style={{ flex: 1 }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-color, #333)' }}>
            {planName}
          </span>
          <span style={{
            marginLeft: 10, fontSize: 12,
            color: 'var(--text-secondary, #888)',
          }}>
            {running > 0 && <span style={{ color: '#6366f1' }}>● {running} running </span>}
            {done > 0 && <span style={{ color: '#52c41a' }}>💎 {done}/{nodes.length} done </span>}
            {failed > 0 && <span style={{ color: '#ff4d4f' }}>✗ {failed} failed </span>}
            {running === 0 && done === 0 && failed === 0 && (
              <span>{nodes.length} delegate{nodes.length !== 1 ? 's' : ''} queued</span>
            )}
          </span>
        </div>
        <span style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 10,
          background: 'rgba(99, 102, 241, 0.15)', color: '#6366f1', fontWeight: 500,
        }}>swarm</span>
      </div>

      {/* D3 graph */}
      <div style={{ padding: '8px 0', overflowX: 'auto' }}>
        <svg
          ref={svgRef}
          width="100%"
          style={{ minHeight: 80, display: 'block' }}
          preserveAspectRatio="xMidYMid meet"
        />
      </div>
    </div>
  );
};

export default SwarmFlowGraph;
