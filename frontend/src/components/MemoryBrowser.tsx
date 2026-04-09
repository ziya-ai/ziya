/**
 * MemoryBrowser — Interactive memory inspection and management UI.
 *
 * Features:
 * - Dashboard with stats, layer distribution ring, proposal badges
 * - Knowledge graph: force-directed SVG visualization of mind-map + memories
 * - Memory explorer: searchable, filterable, inline-editable list
 * - Proposal review queue with approve/dismiss
 * - Health view: stale memories, oversized nodes, orphans, maintenance
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Modal, Input, Button, Tag, Tooltip, message, Tabs, Badge, Empty, Popconfirm, Select } from 'antd';
import {
  SearchOutlined, CheckCircleOutlined, CloseCircleOutlined, DeleteOutlined,
  EditOutlined, ThunderboltOutlined, ReloadOutlined, StarFilled,
} from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import * as api from '../api/memoryApi';
import type { MemoryItem, MemoryProposal, MindMapNode, MemoryStatus, ReviewSummary } from '../api/memoryApi';

const { LAYER_COLORS, LAYER_LABELS, LAYER_ICONS } = api;

// ── Force-directed graph simulation ──────────────────────────────────

interface GraphNode {
  id: string;
  label: string;
  type: 'domain' | 'memory';
  layer?: string;
  importance?: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  pinned?: boolean;
}

interface GraphLink {
  source: string;
  target: string;
  type: 'child' | 'cross_link' | 'ref';
}

function buildGraph(nodes: MindMapNode[], memories: MemoryItem[]): { nodes: GraphNode[]; links: GraphLink[] } {
  const gNodes: GraphNode[] = [];
  const gLinks: GraphLink[] = [];
  const seen = new Set<string>();

  // Mind-map nodes → large domain circles
  for (const n of nodes) {
    if (seen.has(n.id)) continue;
    seen.add(n.id);
    gNodes.push({
      id: n.id, label: n.handle, type: 'domain',
      x: Math.random() * 600 + 100, y: Math.random() * 400 + 100,
      vx: 0, vy: 0, radius: 28 + Math.min(n.memory_refs.length * 2, 20),
    });
    for (const childId of n.children) {
      gLinks.push({ source: n.id, target: childId, type: 'child' });
    }
    for (const linkId of n.cross_links) {
      gLinks.push({ source: n.id, target: linkId, type: 'cross_link' });
    }
    for (const memId of n.memory_refs) {
      gLinks.push({ source: n.id, target: memId, type: 'ref' });
    }
  }

  // Memories → small colored dots
  for (const m of memories) {
    if (seen.has(m.id)) continue;
    seen.add(m.id);
    gNodes.push({
      id: m.id, label: m.content.slice(0, 60), type: 'memory',
      layer: m.layer, importance: m.importance,
      x: Math.random() * 600 + 100, y: Math.random() * 400 + 100,
      vx: 0, vy: 0, radius: 6 + m.importance * 8,
    });
  }

  // Filter links to only those whose both endpoints exist
  const nodeIds = new Set(gNodes.map(n => n.id));
  const validLinks = gLinks.filter(l => nodeIds.has(l.source) && nodeIds.has(l.target));

  return { nodes: gNodes, links: validLinks };
}

function simulate(nodes: GraphNode[], links: GraphLink[], width: number, height: number) {
  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  const cx = width / 2, cy = height / 2;

  // Center gravity
  for (const n of nodes) {
    n.vx += (cx - n.x) * 0.002;
    n.vy += (cy - n.y) * 0.002;
  }

  // Repulsion (O(n²) but n is small — typically < 200)
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const minDist = a.radius + b.radius + 20;
      if (dist < minDist) dist = minDist;
      const force = 800 / (dist * dist);
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      a.vx -= fx; a.vy -= fy;
      b.vx += fx; b.vy += fy;
    }
  }

  // Attraction along links
  for (const link of links) {
    const a = nodeMap.get(link.source), b = nodeMap.get(link.target);
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    const idealDist = link.type === 'ref' ? 80 : 120;
    const force = (dist - idealDist) * 0.005;
    const fx = (dx / dist) * force, fy = (dy / dist) * force;
    a.vx += fx; a.vy += fy;
    b.vx -= fx; b.vy -= fy;
  }

  // Apply velocity with damping + boundary clamping
  for (const n of nodes) {
    if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
    n.vx *= 0.85; n.vy *= 0.85;
    n.x += n.vx; n.y += n.vy;
    n.x = Math.max(n.radius, Math.min(width - n.radius, n.x));
    n.y = Math.max(n.radius, Math.min(height - n.radius, n.y));
  }
}

// ── Knowledge Graph Component ────────────────────────────────────────

const KnowledgeGraph: React.FC<{
  mindmap: MindMapNode[];
  memories: MemoryItem[];
  isDarkMode: boolean;
  onSelectMemory: (id: string) => void;
}> = ({ mindmap, memories, isDarkMode, onSelectMemory }) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const animRef = useRef<number>(0);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });
  const graphRef = useRef<{ nodes: GraphNode[]; links: GraphLink[] }>({ nodes: [], links: [] });
  const [tick, setTick] = useState(0);
  const dragRef = useRef<{ nodeId: string; offsetX: number; offsetY: number } | null>(null);

  // Measure container
  useEffect(() => {
    const el = svgRef.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      setDimensions({ width: e.contentRect.width || 800, height: Math.max(400, e.contentRect.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Build graph when data changes
  useEffect(() => {
    graphRef.current = buildGraph(mindmap, memories);
  }, [mindmap, memories]);

  // Animate
  useEffect(() => {
    let running = true;
    const step = () => {
      if (!running) return;
      simulate(graphRef.current.nodes, graphRef.current.links, dimensions.width, dimensions.height);
      setTick(t => t + 1);
      animRef.current = requestAnimationFrame(step);
    };
    animRef.current = requestAnimationFrame(step);
    return () => { running = false; cancelAnimationFrame(animRef.current); };
  }, [dimensions]);

  const { nodes, links } = graphRef.current;
  const nodeMap = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes, tick]);

  const handleMouseDown = (nodeId: string, e: React.MouseEvent) => {
    e.preventDefault();
    const node = nodeMap.get(nodeId);
    if (!node) return;
    node.pinned = true;
    dragRef.current = { nodeId, offsetX: e.clientX - node.x, offsetY: e.clientY - node.y };
  };

  useEffect(() => {
    const handleMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const node = nodeMap.get(dragRef.current.nodeId);
      if (!node) return;
      const svgRect = svgRef.current?.getBoundingClientRect();
      if (!svgRect) return;
      node.x = e.clientX - svgRect.left;
      node.y = e.clientY - svgRect.top;
    };
    const handleUp = () => {
      if (dragRef.current) {
        const node = nodeMap.get(dragRef.current.nodeId);
        if (node) node.pinned = false;
        dragRef.current = null;
      }
    };
    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    return () => { window.removeEventListener('mousemove', handleMove); window.removeEventListener('mouseup', handleUp); };
  }, [nodeMap]);

  if (nodes.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 400, opacity: 0.5 }}>
        <Empty description="No mind-map nodes yet. Memories will appear here as the model organizes them into domains." />
      </div>
    );
  }

  const bg = isDarkMode ? '#0d1117' : '#f8fafc';

  return (
    <svg ref={svgRef} width={dimensions.width} height={dimensions.height}
      style={{ background: bg, borderRadius: 12, cursor: dragRef.current ? 'grabbing' : 'default' }}>
      <defs>
        {Object.entries(LAYER_COLORS).map(([layer, color]) => (
          <radialGradient key={layer} id={`glow-${layer}`}>
            <stop offset="0%" stopColor={color} stopOpacity="0.6" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </radialGradient>
        ))}
        <radialGradient id="glow-domain">
          <stop offset="0%" stopColor={isDarkMode ? '#fff' : '#1e293b'} stopOpacity="0.15" />
          <stop offset="100%" stopColor={isDarkMode ? '#fff' : '#1e293b'} stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* Links */}
      {links.map((link, i) => {
        const s = nodeMap.get(link.source), t = nodeMap.get(link.target);
        if (!s || !t) return null;
        const isDashed = link.type === 'cross_link';
        const opacity = link.type === 'ref' ? 0.2 : 0.4;
        return (
          <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
            stroke={isDarkMode ? '#334155' : '#cbd5e1'}
            strokeWidth={link.type === 'child' ? 2 : 1}
            strokeDasharray={isDashed ? '6,4' : undefined}
            opacity={hoveredNode && (link.source === hoveredNode || link.target === hoveredNode) ? 0.9 : opacity}
          />
        );
      })}

      {/* Glow halos for memory nodes */}
      {nodes.filter(n => n.type === 'memory').map(n => (
        <circle key={`glow-${n.id}`} cx={n.x} cy={n.y} r={n.radius * 2.5}
          fill={`url(#glow-${n.layer || 'domain_context'})`}
          opacity={(n.importance || 0.5) * 0.4}
          style={{ transition: 'opacity 0.3s' }}
        />
      ))}

      {/* Domain node halos */}
      {nodes.filter(n => n.type === 'domain').map(n => (
        <circle key={`glow-${n.id}`} cx={n.x} cy={n.y} r={n.radius * 2}
          fill="url(#glow-domain)" opacity={0.4}
        />
      ))}

      {/* Node circles */}
      {nodes.map(n => {
        const isHovered = hoveredNode === n.id;
        const isSelected = selectedNode === n.id;
        const color = n.type === 'domain'
          ? (isDarkMode ? '#e2e8f0' : '#1e293b')
          : (LAYER_COLORS[n.layer || 'domain_context'] || '#888');
        return (
          <g key={n.id}
            onMouseEnter={() => setHoveredNode(n.id)}
            onMouseLeave={() => setHoveredNode(null)}
            onMouseDown={(e) => handleMouseDown(n.id, e)}
            onClick={() => {
              setSelectedNode(n.id === selectedNode ? null : n.id);
              if (n.type === 'memory') onSelectMemory(n.id);
            }}
            style={{ cursor: 'pointer' }}
          >
            <circle cx={n.x} cy={n.y} r={n.radius}
              fill={n.type === 'domain' ? (isDarkMode ? '#1e293b' : '#f1f5f9') : color}
              stroke={isSelected ? '#fbbf24' : (isHovered ? '#fff' : color)}
              strokeWidth={isSelected ? 3 : (isHovered ? 2 : (n.type === 'domain' ? 2 : 0))}
              opacity={n.type === 'memory' ? 0.6 + (n.importance || 0.5) * 0.4 : 1}
              style={{ transition: 'stroke-width 0.15s, opacity 0.2s' }}
            />
            {n.type === 'domain' && (
              <text x={n.x} y={n.y} textAnchor="middle" dominantBaseline="central"
                fill={isDarkMode ? '#e2e8f0' : '#334155'} fontSize={11} fontWeight={600}
                style={{ pointerEvents: 'none', userSelect: 'none' }}>
                {n.label.length > 18 ? n.label.slice(0, 16) + '…' : n.label}
              </text>
            )}
            {isHovered && n.type === 'memory' && (
              <foreignObject x={n.x + n.radius + 6} y={n.y - 16} width={220} height={60}>
                <div style={{
                  background: isDarkMode ? '#1e293b' : '#fff',
                  border: `1px solid ${color}`,
                  borderRadius: 6, padding: '4px 8px',
                  fontSize: 11, color: isDarkMode ? '#e2e8f0' : '#334155',
                  boxShadow: '0 4px 12px rgba(0,0,0,0.2)', lineHeight: 1.3,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>{LAYER_ICONS[n.layer || '']} {LAYER_LABELS[n.layer || ''] || n.layer}</div>
                  {n.label}
                </div>
              </foreignObject>
            )}
          </g>
        );
      })}
    </svg>
  );
};

// ── Layer Ring Chart ─────────────────────────────────────────────────

const LayerRing: React.FC<{ byLayer: Record<string, number>; total: number; isDarkMode: boolean }> = ({ byLayer, total, isDarkMode }) => {
  const size = 160, cx = size / 2, cy = size / 2, r = 58, strokeWidth = 18;
  const circumference = 2 * Math.PI * r;
  let offset = 0;
  const arcs = Object.entries(byLayer).filter(([, v]) => v > 0).map(([layer, count]) => {
    const pct = count / (total || 1);
    const dashLen = pct * circumference;
    const arc = { layer, count, pct, dashLen, offset };
    offset += dashLen;
    return arc;
  });

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={cx} cy={cy} r={r} fill="none"
        stroke={isDarkMode ? '#1e293b' : '#e2e8f0'} strokeWidth={strokeWidth} />
      {arcs.map(a => (
        <Tooltip key={a.layer} title={`${LAYER_LABELS[a.layer] || a.layer}: ${a.count}`}>
          <circle cx={cx} cy={cy} r={r} fill="none"
            stroke={LAYER_COLORS[a.layer] || '#888'} strokeWidth={strokeWidth}
            strokeDasharray={`${a.dashLen} ${circumference - a.dashLen}`}
            strokeDashoffset={-a.offset}
            transform={`rotate(-90 ${cx} ${cy})`}
            style={{ cursor: 'pointer', transition: 'stroke-width 0.2s' }}
            onMouseEnter={(e) => (e.target as SVGElement).setAttribute('stroke-width', String(strokeWidth + 4))}
            onMouseLeave={(e) => (e.target as SVGElement).setAttribute('stroke-width', String(strokeWidth))}
          />
        </Tooltip>
      ))}
      <text x={cx} y={cy - 8} textAnchor="middle" fontSize={28} fontWeight={800}
        fill={isDarkMode ? '#e2e8f0' : '#1e293b'}>{total}</text>
      <text x={cx} y={cy + 14} textAnchor="middle" fontSize={11}
        fill={isDarkMode ? '#94a3b8' : '#64748b'}>memories</text>
    </svg>
  );
};

// ── Stat Card ────────────────────────────────────────────────────────

const StatCard: React.FC<{ icon: string; label: string; value: number | string; color: string; isDarkMode: boolean }> = ({ icon, label, value, color, isDarkMode }) => (
  <div style={{
    background: isDarkMode ? '#1e293b' : '#fff',
    border: `1px solid ${isDarkMode ? '#334155' : '#e2e8f0'}`,
    borderRadius: 12, padding: '16px 20px', minWidth: 130,
    display: 'flex', flexDirection: 'column', gap: 4,
    boxShadow: `0 0 0 1px ${color}22, 0 4px 12px ${color}11`,
  }}>
    <div style={{ fontSize: 22 }}>{icon}</div>
    <div style={{ fontSize: 24, fontWeight: 800, color, lineHeight: 1 }}>{value}</div>
    <div style={{ fontSize: 11, color: isDarkMode ? '#94a3b8' : '#64748b', fontWeight: 500 }}>{label}</div>
  </div>
);

// ── Memory Card ──────────────────────────────────────────────────────

const MemoryCard: React.FC<{
  mem: MemoryItem;
  isDarkMode: boolean;
  onEdit: (m: MemoryItem) => void;
  onDelete: (id: string) => void;
  highlight?: boolean;
}> = ({ mem, isDarkMode, onEdit, onDelete, highlight }) => {
  const color = LAYER_COLORS[mem.layer] || '#888';
  const daysSinceAccess = Math.floor((Date.now() - new Date(mem.last_accessed).getTime()) / 86400000);
  const freshnessLabel = daysSinceAccess <= 1 ? 'Today' : daysSinceAccess <= 7 ? `${daysSinceAccess}d ago` : `${Math.floor(daysSinceAccess / 7)}w ago`;

  return (
    <div style={{
      background: isDarkMode ? '#1e293b' : '#fff',
      border: `1px solid ${highlight ? '#fbbf24' : (isDarkMode ? '#334155' : '#e2e8f0')}`,
      borderLeft: `4px solid ${color}`,
      borderRadius: 8, padding: '12px 16px', marginBottom: 8,
      boxShadow: highlight ? `0 0 12px ${LAYER_COLORS[mem.layer]}44` : undefined,
      transition: 'box-shadow 0.3s, border-color 0.3s',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <span style={{ fontSize: 14 }}>{LAYER_ICONS[mem.layer] || '📝'}</span>
            <Tag color={color} style={{ margin: 0, fontSize: 11 }}>{LAYER_LABELS[mem.layer] || mem.layer}</Tag>
            <span style={{ fontSize: 10, color: isDarkMode ? '#64748b' : '#94a3b8' }}>
              {freshnessLabel}
            </span>
            <Tooltip title={`Importance: ${(mem.importance * 100).toFixed(0)}%`}>
              <span style={{ fontSize: 10, color: '#fbbf24' }}>
                {'★'.repeat(Math.ceil(mem.importance * 5))}
                {'☆'.repeat(5 - Math.ceil(mem.importance * 5))}
              </span>
            </Tooltip>
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.5, color: isDarkMode ? '#e2e8f0' : '#334155', wordBreak: 'break-word' }}>
            {mem.content}
          </div>
          {mem.tags.length > 0 && (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
              {mem.tags.map(tag => (
                <Tag key={tag} style={{ fontSize: 10, margin: 0, borderRadius: 4 }}>{tag}</Tag>
              ))}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flexShrink: 0 }}>
          <Tooltip title="Edit"><Button type="text" size="small" icon={<EditOutlined />} onClick={() => onEdit(mem)} /></Tooltip>
          <Popconfirm title="Delete this memory?" onConfirm={() => onDelete(mem.id)} okText="Delete" okButtonProps={{ danger: true }}>
            <Tooltip title="Delete"><Button type="text" size="small" icon={<DeleteOutlined />} danger /></Tooltip>
          </Popconfirm>
        </div>
      </div>
    </div>
  );
};

// ── Proposal Card ────────────────────────────────────────────────────

const ProposalCard: React.FC<{
  proposal: MemoryProposal;
  isDarkMode: boolean;
  onApprove: (id: string) => void;
  onDismiss: (id: string) => void;
}> = ({ proposal, isDarkMode, onApprove, onDismiss }) => {
  const color = LAYER_COLORS[proposal.layer] || '#888';
  const age = Math.floor((Date.now() - proposal.proposed_at) / 60000);
  const ageLabel = age < 60 ? `${age}m ago` : age < 1440 ? `${Math.floor(age / 60)}h ago` : `${Math.floor(age / 1440)}d ago`;

  return (
    <div style={{
      background: isDarkMode ? '#1a1a2e' : '#fffbeb',
      border: `1px solid ${isDarkMode ? '#334155' : '#fde68a'}`,
      borderLeft: `4px solid #f59e0b`,
      borderRadius: 8, padding: '12px 16px', marginBottom: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <Tag color={color} style={{ margin: 0, fontSize: 11 }}>{LAYER_LABELS[proposal.layer] || proposal.layer}</Tag>
        <span style={{ fontSize: 10, color: isDarkMode ? '#64748b' : '#94a3b8' }}>Proposed {ageLabel}</span>
      </div>
      <div style={{ fontSize: 13, lineHeight: 1.5, color: isDarkMode ? '#e2e8f0' : '#334155', marginBottom: 8 }}>
        {proposal.content}
      </div>
      {proposal.tags.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
          {proposal.tags.map(tag => <Tag key={tag} style={{ fontSize: 10, margin: 0 }}>{tag}</Tag>)}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <Button type="primary" size="small" icon={<CheckCircleOutlined />}
          onClick={() => onApprove(proposal.id)}
          style={{ background: '#10b981', borderColor: '#10b981' }}>Approve</Button>
        <Button size="small" icon={<CloseCircleOutlined />}
          onClick={() => onDismiss(proposal.id)}>Dismiss</Button>
      </div>
    </div>
  );
};

// ── Edit Modal ───────────────────────────────────────────────────────

const EditMemoryModal: React.FC<{
  mem: MemoryItem | null;
  visible: boolean;
  isDarkMode: boolean;
  onClose: () => void;
  onSave: (id: string, updates: Partial<Pick<MemoryItem, 'content' | 'layer' | 'tags'>>) => void;
}> = ({ mem, visible, isDarkMode, onClose, onSave }) => {
  const [content, setContent] = useState('');
  const [layer, setLayer] = useState('domain_context');
  const [tagsStr, setTagsStr] = useState('');

  useEffect(() => {
    if (mem) {
      setContent(mem.content);
      setLayer(mem.layer);
      setTagsStr(mem.tags.join(', '));
    }
  }, [mem]);

  if (!mem) return null;

  return (
    <Modal title="Edit Memory" open={visible} onCancel={onClose}
      onOk={() => {
        onSave(mem.id, { content, layer, tags: tagsStr.split(',').map(t => t.trim()).filter(Boolean) });
        onClose();
      }}
      styles={{ body: { background: isDarkMode ? '#0f172a' : '#fff' } }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <label style={{ fontSize: 12, fontWeight: 600 }}>Content</label>
          <Input.TextArea value={content} onChange={e => setContent(e.target.value)} rows={4} />
        </div>
        <div>
          <label style={{ fontSize: 12, fontWeight: 600 }}>Layer</label>
          <Select value={layer} onChange={setLayer} style={{ width: '100%' }}
            options={Object.entries(LAYER_LABELS).map(([k, v]) => ({ value: k, label: `${LAYER_ICONS[k]} ${v}` }))} />
        </div>
        <div>
          <label style={{ fontSize: 12, fontWeight: 600 }}>Tags (comma-separated)</label>
          <Input value={tagsStr} onChange={e => setTagsStr(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
};

// ═══════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════

interface MemoryBrowserProps {
  visible: boolean;
  onClose: () => void;
}

const MemoryBrowser: React.FC<MemoryBrowserProps> = ({ visible, onClose }) => {
  const { isDarkMode } = useTheme();
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [proposals, setProposals] = useState<MemoryProposal[]>([]);
  const [mindmap, setMindmap] = useState<MindMapNode[]>([]);
  const [review, setReview] = useState<ReviewSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MemoryItem[] | null>(null);
  const [filterLayer, setFilterLayer] = useState<string | null>(null);
  const [editingMem, setEditingMem] = useState<MemoryItem | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('dashboard');
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

  // Load all data
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [s, m, p, mm] = await Promise.all([
        api.getMemoryStatus(),
        api.getAllMemories(),
        api.getProposals(),
        api.getMindMap().catch(() => [] as MindMapNode[]),
      ]);
      setStatus(s);
      setMemories(m);
      setProposals(p);
      setMindmap(mm);
      // Load review lazily (can be slow with many memories)
      api.getReview().then(setReview).catch(() => {});
    } catch (err) {
      console.error('Memory browser load failed:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (visible) loadData(); }, [visible, loadData]);

  // Search handler with debounce
  const handleSearch = useCallback((query: string) => {
    setSearchQuery(query);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    if (!query.trim()) { setSearchResults(null); return; }
    searchTimeoutRef.current = setTimeout(async () => {
      try {
        const results = await api.searchMemories(query);
        setSearchResults(results);
      } catch { setSearchResults([]); }
    }, 250);
  }, []);

  // Action handlers
  const handleApprove = useCallback(async (id: string) => {
    try {
      await api.approveProposal(id);
      message.success('Memory approved');
      loadData();
    } catch { message.error('Approve failed'); }
  }, [loadData]);

  const handleDismiss = useCallback(async (id: string) => {
    try {
      await api.dismissProposal(id);
      message.success('Proposal dismissed');
      setProposals(prev => prev.filter(p => p.id !== id));
    } catch { message.error('Dismiss failed'); }
  }, []);

  const handleApproveAll = useCallback(async () => {
    try {
      const result = await api.approveAllProposals();
      message.success(`${result.approved} proposals approved`);
      loadData();
    } catch { message.error('Approve all failed'); }
  }, [loadData]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await api.deleteMemory(id);
      message.success('Memory deleted');
      setMemories(prev => prev.filter(m => m.id !== id));
    } catch { message.error('Delete failed'); }
  }, []);

  const handleEditSave = useCallback(async (id: string, updates: Partial<Pick<MemoryItem, 'content' | 'layer' | 'tags'>>) => {
    try {
      await api.updateMemory(id, updates);
      message.success('Memory updated');
      loadData();
    } catch { message.error('Update failed'); }
  }, [loadData]);

  const handleMaintenance = useCallback(async () => {
    try {
      const result = await api.runMaintenance();
      message.success(`Maintenance complete: ${result.divided.length} divisions, ${result.cross_linked.length} cross-links`);
      loadData();
    } catch { message.error('Maintenance failed'); }
  }, [loadData]);

  const handleGraphSelect = useCallback((id: string) => {
    setHighlightId(id);
    setActiveTab('explorer');
    // Clear highlight after 3 seconds
    setTimeout(() => setHighlightId(null), 3000);
  }, []);

  // Filtered memories for explorer tab
  const displayedMemories = useMemo(() => {
    let list = searchResults !== null ? searchResults : memories;
    if (filterLayer) list = list.filter(m => m.layer === filterLayer);
    return list;
  }, [memories, searchResults, filterLayer]);

  // Sort memories: highlighted first, then by importance desc
  const sortedMemories = useMemo(() => {
    return [...displayedMemories].sort((a, b) => {
      if (highlightId === a.id) return -1;
      if (highlightId === b.id) return 1;
      return b.importance - a.importance;
    });
  }, [displayedMemories, highlightId]);

  const bg = isDarkMode ? '#0f172a' : '#f8fafc';
  const textColor = isDarkMode ? '#e2e8f0' : '#1e293b';

  const tabItems = [
    {
      key: 'dashboard',
      label: '📊 Dashboard',
      children: (
        <div style={{ padding: '16px 0' }}>
          {/* Stat cards row */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 24 }}>
            <StatCard icon="🧠" label="Total Memories" value={status?.total || 0} color="#3b82f6" isDarkMode={isDarkMode} />
            <StatCard icon="💡" label="Pending Proposals" value={proposals.length} color="#f59e0b" isDarkMode={isDarkMode} />
            <StatCard icon="🗺️" label="Mind-Map Domains" value={mindmap.filter(n => !n.parent).length} color="#8b5cf6" isDarkMode={isDarkMode} />
            <StatCard icon="🔗" label="Cross-Links" value={mindmap.reduce((s, n) => s + n.cross_links.length, 0)} color="#06b6d4" isDarkMode={isDarkMode} />
            <StatCard icon="⚠️" label="Needs Review"
              value={(review?.stale?.length || 0) + (review?.orphans?.length || 0)}
              color="#ef4444" isDarkMode={isDarkMode} />
          </div>

          {/* Ring chart + layer legend */}
          <div style={{ display: 'flex', gap: 32, alignItems: 'center', flexWrap: 'wrap' }}>
            {status && <LayerRing byLayer={status.by_layer} total={status.total} isDarkMode={isDarkMode} />}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {Object.entries(LAYER_LABELS).map(([layer, label]) => {
                const count = status?.by_layer[layer] || 0;
                if (count === 0) return null;
                return (
                  <div key={layer} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                    <div style={{ width: 12, height: 12, borderRadius: 3, background: LAYER_COLORS[layer] }} />
                    <span style={{ color: textColor }}>{LAYER_ICONS[layer]} {label}</span>
                    <span style={{ color: isDarkMode ? '#64748b' : '#94a3b8', fontWeight: 600 }}>{count}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Recent memories preview */}
          {memories.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <h4 style={{ color: textColor, marginBottom: 12, fontSize: 14 }}>✨ Most Important Memories</h4>
              {memories
                .sort((a, b) => b.importance - a.importance)
                .slice(0, 5)
                .map(m => (
                  <MemoryCard key={m.id} mem={m} isDarkMode={isDarkMode} onEdit={setEditingMem} onDelete={handleDelete} />
                ))}
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'graph',
      label: '🌐 Knowledge Graph',
      children: (
        <div style={{ height: 'calc(70vh - 100px)', minHeight: 400 }}>
          <KnowledgeGraph mindmap={mindmap} memories={memories} isDarkMode={isDarkMode} onSelectMemory={handleGraphSelect} />
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 12, justifyContent: 'center' }}>
            {Object.entries(LAYER_LABELS).map(([layer, label]) => (
              <div key={layer} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, opacity: 0.7 }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: LAYER_COLORS[layer] }} />
                <span style={{ color: textColor }}>{label}</span>
              </div>
            ))}
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, opacity: 0.7 }}>
              <div style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${isDarkMode ? '#e2e8f0' : '#1e293b'}`, background: 'none' }} />
              <span style={{ color: textColor }}>Domain node</span>
            </div>
          </div>
        </div>
      ),
    },
    {
      key: 'explorer',
      label: `📚 Explorer (${memories.length})`,
      children: (
        <div style={{ padding: '12px 0' }}>
          {/* Search + filter bar */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            <Input
              prefix={<SearchOutlined />}
              placeholder="Search memories..."
              value={searchQuery}
              onChange={e => handleSearch(e.target.value)}
              allowClear
              style={{ flex: 1, minWidth: 200 }}
            />
            <Select
              allowClear
              placeholder="Filter by layer"
              value={filterLayer}
              onChange={setFilterLayer}
              style={{ width: 180 }}
              options={[
                { value: null as any, label: 'All layers' },
                ...Object.entries(LAYER_LABELS).map(([k, v]) => ({ value: k, label: `${LAYER_ICONS[k]} ${v}` })),
              ]}
            />
          </div>

          {/* Memory list */}
          <div style={{ maxHeight: 'calc(70vh - 160px)', overflowY: 'auto', paddingRight: 4 }}>
            {sortedMemories.length === 0 ? (
              <Empty description={searchQuery ? 'No memories match your search' : 'No memories stored yet'} />
            ) : (
              sortedMemories.map(m => (
                <MemoryCard key={m.id} mem={m} isDarkMode={isDarkMode}
                  onEdit={setEditingMem} onDelete={handleDelete}
                  highlight={m.id === highlightId} />
              ))
            )}
          </div>
        </div>
      ),
    },
    {
      key: 'proposals',
      label: <Badge count={proposals.length} offset={[8, 0]} size="small">💡 Proposals</Badge>,
      children: (
        <div style={{ padding: '12px 0' }}>
          {proposals.length > 0 && (
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
              <Button type="primary" icon={<CheckCircleOutlined />} onClick={handleApproveAll}
                style={{ background: '#10b981', borderColor: '#10b981' }}>
                Approve All ({proposals.length})
              </Button>
            </div>
          )}
          <div style={{ maxHeight: 'calc(70vh - 120px)', overflowY: 'auto' }}>
            {proposals.length === 0 ? (
              <Empty description="No pending proposals. The model will suggest memories during conversations." />
            ) : (
              proposals.map(p => (
                <ProposalCard key={p.id} proposal={p} isDarkMode={isDarkMode}
                  onApprove={handleApprove} onDismiss={handleDismiss} />
              ))
            )}
          </div>
        </div>
      ),
    },
    {
      key: 'health',
      label: '🩺 Health',
      children: (
        <div style={{ padding: '12px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
            <Button icon={<ThunderboltOutlined />} onClick={handleMaintenance}>Run Maintenance</Button>
            <Button icon={<ReloadOutlined />} onClick={loadData} style={{ marginLeft: 8 }}>Refresh</Button>
          </div>

          {review ? (
            <>
              {/* Stale memories */}
              <h4 style={{ color: textColor, marginBottom: 8 }}>
                ⏰ Stale Memories ({review.stale?.length || 0})
                <span style={{ fontSize: 11, fontWeight: 400, marginLeft: 8, color: isDarkMode ? '#64748b' : '#94a3b8' }}>
                  Not accessed in 90+ days
                </span>
              </h4>
              {(review.stale?.length || 0) === 0 ? (
                <div style={{ padding: 12, opacity: 0.5, marginBottom: 16 }}>✅ No stale memories</div>
              ) : (
                <div style={{ marginBottom: 16 }}>
                  {review.stale.slice(0, 10).map(m => (
                    <MemoryCard key={m.id} mem={m} isDarkMode={isDarkMode} onEdit={setEditingMem} onDelete={handleDelete} />
                  ))}
                </div>
              )}

              {/* Oversized nodes */}
              <h4 style={{ color: textColor, marginBottom: 8 }}>
                📦 Oversized Nodes ({review.oversized_nodes?.length || 0})
                <span style={{ fontSize: 11, fontWeight: 400, marginLeft: 8, color: isDarkMode ? '#64748b' : '#94a3b8' }}>
                  Nodes with 12+ memories that should split
                </span>
              </h4>
              {(review.oversized_nodes?.length || 0) === 0 ? (
                <div style={{ padding: 12, opacity: 0.5, marginBottom: 16 }}>✅ All nodes are healthy</div>
              ) : (
                <div style={{ marginBottom: 16 }}>
                  {review.oversized_nodes.map(n => {
                    const node = mindmap.find(mm => mm.id === n.node_id);
                    return (
                      <div key={n.node_id} style={{
                        padding: '8px 12px', marginBottom: 4,
                        background: isDarkMode ? '#1e293b' : '#fff',
                        border: `1px solid ${isDarkMode ? '#334155' : '#e2e8f0'}`,
                        borderRadius: 6, display: 'flex', alignItems: 'center', gap: 8,
                      }}>
                        <Tag color="orange">{n.memory_count} memories</Tag>
                        <span style={{ color: textColor, fontSize: 13 }}>{node?.handle || n.node_id}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Orphans */}
              <h4 style={{ color: textColor, marginBottom: 8 }}>
                🔗 Orphan Memories ({review.orphans?.length || 0})
                <span style={{ fontSize: 11, fontWeight: 400, marginLeft: 8, color: isDarkMode ? '#64748b' : '#94a3b8' }}>
                  Not linked to any mind-map node
                </span>
              </h4>
              {(review.orphans?.length || 0) === 0 ? (
                <div style={{ padding: 12, opacity: 0.5 }}>✅ All memories are placed</div>
              ) : (
                review.orphans.slice(0, 10).map(m => (
                  <MemoryCard key={m.id} mem={m} isDarkMode={isDarkMode} onEdit={setEditingMem} onDelete={handleDelete} />
                ))
              )}
            </>
          ) : (
            <div style={{ textAlign: 'center', padding: 32, opacity: 0.5 }}>Loading health data...</div>
          )}
        </div>
      ),
    },
  ];

  return (
    <>
      <Modal
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 22 }}>🧠</span>
            <span style={{ fontSize: 16, fontWeight: 700 }}>Memory Browser</span>
            {proposals.length > 0 && (
              <Badge count={proposals.length} style={{ marginLeft: 4 }} />
            )}
          </div>
        }
        open={visible}
        onCancel={onClose}
        footer={null}
        width={Math.min(960, window.innerWidth - 48)}
        centered
        styles={{
          body: { background: bg, padding: '0 24px 24px', maxHeight: '80vh', overflow: 'auto' },
          header: { background: bg, borderBottom: `1px solid ${isDarkMode ? '#1e293b' : '#e2e8f0'}` },
        }}
        destroyOnClose
      >
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 300, color: textColor }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 32, marginBottom: 12, animation: 'pulse 2s infinite' }}>🧠</div>
              <div>Loading memory store...</div>
            </div>
          </div>
        ) : (
          <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems}
            style={{ color: textColor }}
          />
        )}
      </Modal>

      <EditMemoryModal
        mem={editingMem}
        visible={!!editingMem}
        isDarkMode={isDarkMode}
        onClose={() => setEditingMem(null)}
        onSave={handleEditSave}
      />

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.6; transform: scale(1.05); }
        }
      `}</style>
    </>
  );
};

export default MemoryBrowser;
