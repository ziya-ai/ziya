/**
 * Memory API client — typed wrappers around /api/v1/memory endpoints.
 */

export interface MemoryItem {
  id: string;
  content: string;
  layer: string;
  tags: string[];
  learned_from: string;
  created: string;
  last_accessed: string;
  status: string;
  importance: number;
  scope?: { domain_node?: string | null; project_paths?: string[] };
  relations?: Record<string, string[]>;
}

export interface MemoryProposal {
  id: string;
  content: string;
  layer: string;
  tags: string[];
  learned_from: string;
  proposed_at: number;
}

export interface MindMapNode {
  id: string;
  handle: string;
  parent: string | null;
  children: string[];
  cross_links: string[];
  memory_refs: string[];
  tags: string[];
  access_count: number;
  last_accessed: string;
}

export interface MemoryStatus {
  total: number;
  by_layer: Record<string, number>;
  by_status: Record<string, number>;
  pending_proposals: number;
}

export interface ReviewSummary {
  stale: MemoryItem[];
  oversized_nodes: Array<{ node_id: string; memory_count: number }>;
  orphans: MemoryItem[];
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  if (path) h['X-Project-Root'] = path;
  return h;
}

export async function getMemoryStatus(): Promise<MemoryStatus> {
  const res = await fetch('/api/v1/memory', { headers: headers() });
  if (!res.ok) throw new Error(`Memory status failed: ${res.status}`);
  return res.json();
}

export async function getAllMemories(): Promise<MemoryItem[]> {
  const res = await fetch('/api/v1/memory/all', { headers: headers() });
  if (!res.ok) throw new Error(`List memories failed: ${res.status}`);
  return res.json();
}

export async function searchMemories(query: string, limit = 20): Promise<MemoryItem[]> {
  const res = await fetch(`/api/v1/memory/search?q=${encodeURIComponent(query)}&limit=${limit}`, { headers: headers() });
  if (!res.ok) throw new Error(`Search failed: ${res.status}`);
  return res.json();
}

export async function saveMemory(content: string, layer: string, tags: string[]): Promise<MemoryItem> {
  const res = await fetch('/api/v1/memory', {
    method: 'POST', headers: headers(),
    body: JSON.stringify({ content, layer, tags }),
  });
  if (!res.ok) throw new Error(`Save failed: ${res.status}`);
  return res.json();
}

export async function updateMemoryScope(id: string, projectPaths: string[]): Promise<MemoryItem> {
  const res = await fetch(`/api/v1/memory/${id}`, {
    method: 'PUT', headers: headers(),
    body: JSON.stringify({ scope: { project_paths: projectPaths } }),
  });
  if (!res.ok) throw new Error(`Scope update failed: ${res.status}`);
  return res.json();
}

export async function updateMemory(id: string, updates: Partial<Pick<MemoryItem, 'content' | 'layer' | 'tags' | 'status'>>): Promise<MemoryItem> {
  const res = await fetch(`/api/v1/memory/${id}`, {
    method: 'PUT', headers: headers(),
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Update failed: ${res.status}`);
  return res.json();
}

export async function deleteMemory(id: string): Promise<void> {
  const res = await fetch(`/api/v1/memory/${id}`, { method: 'DELETE', headers: headers() });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

export async function getProposals(): Promise<MemoryProposal[]> {
  const res = await fetch('/api/v1/memory/proposals', { headers: headers() });
  if (!res.ok) throw new Error(`Proposals failed: ${res.status}`);
  return res.json();
}

export async function approveProposal(id: string): Promise<MemoryItem> {
  const res = await fetch(`/api/v1/memory/proposals/${id}/approve`, { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Approve failed: ${res.status}`);
  return res.json();
}

export async function approveAllProposals(): Promise<{ approved: number }> {
  const res = await fetch('/api/v1/memory/proposals/approve-all', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Approve all failed: ${res.status}`);
  return res.json();
}

export async function dismissProposal(id: string): Promise<void> {
  const res = await fetch(`/api/v1/memory/proposals/${id}`, { method: 'DELETE', headers: headers() });
  if (!res.ok) throw new Error(`Dismiss failed: ${res.status}`);
}

export async function getMindMap(): Promise<MindMapNode[]> {
  const res = await fetch('/api/v1/memory/mindmap', { headers: headers() });
  if (!res.ok) throw new Error(`Mind map failed: ${res.status}`);
  return res.json();
}

export async function expandMindMapNode(nodeId: string): Promise<{ node: MindMapNode; memories: MemoryItem[]; count: number }> {
  const res = await fetch(`/api/v1/memory/mindmap/${nodeId}/expand`, { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Expand failed: ${res.status}`);
  return res.json();
}

export async function getReview(): Promise<ReviewSummary> {
  const res = await fetch('/api/v1/memory/review', { headers: headers() });
  if (!res.ok) throw new Error(`Review failed: ${res.status}`);
  return res.json();
}

export async function runMaintenance(): Promise<{ divided: string[]; cross_linked: string[] }> {
  const res = await fetch('/api/v1/memory/maintenance', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Maintenance failed: ${res.status}`);
  return res.json();
}

export async function startOrganize(): Promise<{ status: string }> {
  const res = await fetch('/api/v1/memory/organize', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Organize failed: ${res.status}`);
  return res.json();
}

export async function getOrganizeStatus(): Promise<{
  running: boolean;
  result: OrganizeResult | null;
  error: string | null;
  started_at: number | null;
}> {
  const res = await fetch('/api/v1/memory/organize/status', { headers: headers() });
  if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
  return res.json();
}

export interface OrganizeResult {
  cleanup: { status: string; removed?: number; merged?: number; reviewed?: number };
  bootstrap: { status: string; domains_created?: number; domains_updated?: number; memories_placed?: number };
  relations: { status: string; relations_found?: number };
  cross_links: string[];
  divisions: string[];
}

export async function runOrganize(): Promise<OrganizeResult> {
  await startOrganize();
  // Poll for completion
  const POLL_INTERVAL = 2000;
  const MAX_WAIT = 120000; // 2 minutes
  const start = Date.now();
  while (Date.now() - start < MAX_WAIT) {
    await new Promise(r => setTimeout(r, POLL_INTERVAL));
    const status = await getOrganizeStatus();
    if (!status.running) {
      if (status.error) throw new Error(status.error);
      if (status.result) return status.result;
      throw new Error('Organize completed with no result');
    }
  }
  throw new Error('Organize timed out after 2 minutes');
}

export const LAYER_COLORS: Record<string, string> = {
  domain_context: '#3b82f6',
  architecture: '#8b5cf6',
  lexicon: '#06b6d4',
  decision: '#f59e0b',
  active_thread: '#10b981',
  process: '#6366f1',
  preference: '#ec4899',
  negative_constraint: '#ef4444',
};

export const LAYER_LABELS: Record<string, string> = {
  domain_context: 'Domain',
  architecture: 'Architecture',
  lexicon: 'Vocabulary',
  decision: 'Decisions',
  active_thread: 'Active Work',
  process: 'Process',
  preference: 'Preferences',
  negative_constraint: 'Lessons (avoid)',
};

export const LAYER_ICONS: Record<string, string> = {
  domain_context: '🌐',
  architecture: '🏗️',
  lexicon: '📖',
  decision: '⚖️',
  active_thread: '🔄',
  process: '📋',
  preference: '💜',
  negative_constraint: '🚫',
};
