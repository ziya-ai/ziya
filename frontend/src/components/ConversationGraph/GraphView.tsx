import React, { useEffect, useState, useCallback, useMemo } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  useNodesState,
  useEdgesState,
  MarkerType,
  Position,
  NodeProps,
  Handle,
} from 'reactflow';
import 'reactflow/dist/style.css';
import './GraphView.css';

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

interface GraphApiResponse {
  conversationId: string;
  graphMode: string;
  nodes: any[];
  edges: any[];
  rootId: string | null;
  currentId: string | null;
}

interface Props {
  projectId: string;
  chatId: string;
  onNodeClick?: (nodeId: string, fullContext: string) => void;
}

/* ------------------------------------------------------------------ */
/* Status helpers                                                      */
/* ------------------------------------------------------------------ */

const STATUS_COLORS: Record<string, string> = {
  agreed: '#3fb950',
  exploring: '#58a6ff',
  proposed: '#91d5ff',
  rejected: '#6e7681',
  deferred: '#d9d9d9',
  open_question: '#d29922',
  running: '#58a6ff',
  compacting: '#3fb950',
  failed: '#f85149',
};

const STATUS_ICONS: Record<string, string> = {
  agreed: '✓',
  exploring: '◐',
  open_question: '?',
  rejected: '✗',
  deferred: '⏸',
  proposed: '○',
  running: '⟳',
  failed: '✗',
};

/* ------------------------------------------------------------------ */
/* Custom node                                                         */
/* ------------------------------------------------------------------ */

function CustomNode({ data }: NodeProps) {
  const color = STATUS_COLORS[data.status] || '#58a6ff';
  const diameter = 20 + (data.importance ?? 0.5) * 20; // 20-40 px

  return (
    <div className={`cg-node status-${data.status} type-${data.nodeType}`}>
      <Handle type="target" position={Position.Top} style={{ visibility: 'hidden' }} />
      <div
        className="cg-node-circle"
        style={{ width: diameter, height: diameter, backgroundColor: color }}
      >
        {STATUS_ICONS[data.status] && (
          <span className="cg-node-icon">{STATUS_ICONS[data.status]}</span>
        )}
      </div>
      <div className="cg-node-label">{data.label}</div>
      <Handle type="source" position={Position.Bottom} style={{ visibility: 'hidden' }} />
    </div>
  );
}

const nodeTypes = { custom: CustomNode };

/* ------------------------------------------------------------------ */
/* Layout                                                              */
/* ------------------------------------------------------------------ */

function layoutGraph(data: GraphApiResponse) {
  const nodeMap = new Map<string, any>(data.nodes.map((n) => [n.id, n]));
  const childrenOf = new Map<string, string[]>();
  data.edges.forEach((e) => {
    if (!childrenOf.has(e.from)) childrenOf.set(e.from, []);
    childrenOf.get(e.from)!.push(e.to);
  });

  const rfNodes: Node[] = [];
  const placed = new Set<string>();

  const visit = (id: string, x: number, y: number) => {
    if (placed.has(id)) return;
    placed.add(id);
    const nd = nodeMap.get(id);
    if (!nd) return;

    rfNodes.push({
      id,
      type: 'custom',
      position: { x, y },
      data: {
        label: nd.content,
        fullContext: nd.fullContext,
        status: nd.status,
        nodeType: nd.type,
        importance: nd.importance,
        author: nd.author,
      },
    });

    const kids = childrenOf.get(id) || [];
    if (kids.length === 1) {
      visit(kids[0], x, y + 120);
    } else if (kids.length > 1) {
      const spacing = 280;
      const totalW = spacing * (kids.length - 1);
      const startX = x - totalW / 2;
      kids.forEach((kid, i) => visit(kid, startX + i * spacing, y + 150));
    }
  };

  if (data.rootId) visit(data.rootId, 400, 50);

  const rfEdges: Edge[] = data.edges.map((e) => ({
    id: `e-${e.from}-${e.to}`,
    source: e.from,
    target: e.to,
    type: 'bezier',
    animated: e.type === 'continues',
    style: {
      stroke: e.type === 'continues' ? '#58a6ff' : '#484f58',
      strokeWidth: e.type === 'continues' ? 2.5 : 1.5,
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 12,
      height: 12,
      color: e.type === 'continues' ? '#58a6ff' : '#484f58',
    },
  }));

  return { rfNodes, rfEdges };
}

/* ------------------------------------------------------------------ */
/* Component                                                           */
/* ------------------------------------------------------------------ */

export function ConversationGraphView({ projectId, chatId, onNodeClick }: Props) {
  const [graphData, setGraphData] = useState<GraphApiResponse | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selected, setSelected] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId || !chatId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`/api/v1/projects/${projectId}/chats/${chatId}/graph`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data: GraphApiResponse) => {
        if (cancelled) return;
        setGraphData(data);
        const { rfNodes, rfEdges } = layoutGraph(data);
        setNodes(rfNodes);
        setEdges(rfEdges);
      })
      .catch((err) => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [projectId, chatId]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setSelected(node.data);
      onNodeClick?.(node.id, node.data.fullContext);
    },
    [onNodeClick],
  );

  const memoNodeTypes = useMemo(() => nodeTypes, []);

  if (loading) return <div className="cg-status">🌳 Building graph…</div>;
  if (error) return <div className="cg-status cg-error">Error: {error}</div>;
  if (!graphData || nodes.length === 0) return <div className="cg-status">No structure found.</div>;

  return (
    <div className="cg-container">
      <div className="cg-header">
        <h3>💭 Conversation Flow</h3>
        <div className="cg-legend">
          {Object.entries({ agreed: 'Agreed', exploring: 'Exploring', open_question: 'Question', rejected: 'Rejected' }).map(
            ([s, label]) => (
              <span key={s} className="cg-legend-item">
                <span className="cg-dot" style={{ background: STATUS_COLORS[s] }} />
                {label}
              </span>
            ),
          )}
        </div>
      </div>

      <div className="cg-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={handleNodeClick}
          nodeTypes={memoNodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
          minZoom={0.2}
          maxZoom={2}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnScroll={true}
          zoomOnDoubleClick={false}
          proOptions={{ hideAttribution: true }}
        >
          <Controls position="bottom-left" showInteractive={false} />
        </ReactFlow>
      </div>

      {selected && (
        <div className="cg-detail">
          <div className="cg-detail-header">
            <span className="cg-badge" style={{ background: STATUS_COLORS[selected.status] }}>
              {STATUS_ICONS[selected.status] || '●'}
            </span>
            <h4>{selected.label}</h4>
            <button className="cg-close" onClick={() => setSelected(null)}>✕</button>
          </div>
          <div className="cg-detail-meta">
            <span>{selected.author}</span>
            <span>{selected.nodeType}</span>
          </div>
          <div className="cg-detail-body">{selected.fullContext}</div>
        </div>
      )}
    </div>
  );
}

export default ConversationGraphView;
