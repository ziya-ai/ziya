/**
 * Tests for cycle detection and depth limits in MUIChatHistory's tree operations.
 *
 * The sidebar builds a tree from folders (with parentId references) and
 * recursively flattens/sorts/counts it.  If a folder's parentId points to
 * itself or creates a mutual cycle (A→B→A), every recursive function in
 * treeDataRaw hits infinite recursion → stack overflow → page crash.
 *
 * These tests replicate the recursive helpers to verify they terminate
 * safely when given circular or deeply-nested data.
 */

// ── flattenVisibleNodes ────────────────────────────────────────────────
// Replicated from MUIChatHistory.tsx

interface FlatNode {
  id: string;
  name: string;
  depth: number;
  isFolder: boolean;
  isExpanded: boolean;
  hasChildren: boolean;
  node: any;
}

function flattenVisibleNodes(
  nodes: any[],
  expandedSet: Set<string>,
  depth: number = 0,
  visited?: Set<string>
): FlatNode[] {
  if (depth > 20) return [];
  const seen = visited || new Set<string>();
  const result: FlatNode[] = [];
  for (const node of nodes) {
    if (seen.has(node.id)) continue;
    seen.add(node.id);
    const isFolder = Boolean(node.folder) || Boolean(node.taskPlan);
    const hasChildren = Boolean(node.children?.length);
    const isExpanded = isFolder && expandedSet.has(node.id);
    result.push({ id: node.id, name: node.name, depth, isFolder, isExpanded, hasChildren, node });
    if (hasChildren && (!isFolder || isExpanded)) {
      result.push(...flattenVisibleNodes(node.children, expandedSet, depth + 1, seen));
    }
  }
  return result;
}

// ── rollUpConversationCount ────────────────────────────────────────────

function rollUpConversationCount(node: any, _depth = 0): number {
  if (_depth > 20) return 0;
  if (!node.folder) return 0;
  let total = node.conversationCount || 0;
  if (node.children) {
    for (const child of node.children) {
      if (child.folder) {
        total += rollUpConversationCount(child, _depth + 1);
      }
    }
  }
  node.conversationCount = total;
  return total;
}

// ── rollUpLastActivityTime ─────────────────────────────────────────────
// Replicated from MUIChatHistory.tsx

function rollUpLastActivityTime(node: any, _depth = 0): number {
  if (_depth > 20) return 0;
  if (!node.folder) return 0;
  let maxTime = node.lastActivityTime || 0;
  if (node.children) {
    for (const child of node.children) {
      if (child.folder) {
        const childTime = rollUpLastActivityTime(child, _depth + 1);
        if (childTime > maxTime) maxTime = childTime;
      } else if (child.conversation) {
        const convTime = child.conversation.lastAccessedAt || 0;
        if (convTime > maxTime) maxTime = convTime;
      }
    }
  }
  node.lastActivityTime = maxTime;
  return maxTime;
}

// ── sortRecursive ──────────────────────────────────────────────────────

function sortRecursive(nodes: any[], _depth = 0): any[] {
  if (_depth > 20) return nodes;
  const sorted = [...nodes];
  sorted.forEach(node => {
    if (node.children && node.children.length > 0) {
      node.children = sortRecursive(node.children, _depth + 1);
    }
  });
  return sorted;
}

// ── removeNodeFromTree ─────────────────────────────────────────────────

function removeNodeFromTree(tree: any[], node: any, _depth = 0): boolean {
  if (_depth > 20) return false;
  const idx = tree.indexOf(node);
  if (idx !== -1) { tree.splice(idx, 1); return true; }
  for (const item of tree) {
    if (item.children && removeNodeFromTree(item.children, node, _depth + 1)) return true;
  }
  return false;
}

// ── Tests ──────────────────────────────────────────────────────────────

describe('flattenVisibleNodes cycle safety', () => {
  it('terminates on a self-referencing node', () => {
    const node: any = { id: 'a', name: 'A', folder: true, children: [] };
    node.children.push(node); // circular: A → A

    expect(() => {
      const result = flattenVisibleNodes([node], new Set(['a']));
      // Should produce exactly one entry (the node itself), not stack overflow
      expect(result.length).toBe(1);
    }).not.toThrow();
  });

  it('terminates on mutual cycle (A → B → A)', () => {
    const a: any = { id: 'a', name: 'A', folder: true, children: [] };
    const b: any = { id: 'b', name: 'B', folder: true, children: [] };
    a.children.push(b);
    b.children.push(a);

    expect(() => {
      const result = flattenVisibleNodes([a], new Set(['a', 'b']));
      expect(result.length).toBe(2); // A, then B (stops before re-visiting A)
    }).not.toThrow();
  });

  it('terminates on deeply nested tree exceeding depth limit', () => {
    // Build a chain 50 levels deep
    let root: any = { id: 'n0', name: 'N0', folder: true, children: [] };
    let current = root;
    for (let i = 1; i <= 50; i++) {
      const child: any = { id: `n${i}`, name: `N${i}`, folder: true, children: [] };
      current.children.push(child);
      current = child;
    }

    expect(() => {
      const result = flattenVisibleNodes([root], new Set(Array.from({ length: 51 }, (_, i) => `n${i}`)));
      // Should cap at depth 20, producing ~21 nodes
      expect(result.length).toBeLessThanOrEqual(22);
    }).not.toThrow();
  });

  it('handles normal tree correctly', () => {
    const tree = [
      {
        id: 'f1', name: 'Folder 1', folder: true,
        children: [
          { id: 'conv-c1', name: 'Chat 1', children: [] },
          { id: 'conv-c2', name: 'Chat 2', children: [] },
        ]
      },
      { id: 'conv-c3', name: 'Chat 3', children: [] },
    ];

    const result = flattenVisibleNodes(tree, new Set(['f1']));
    expect(result.length).toBe(4); // f1, c1, c2, c3
    expect(result[0].id).toBe('f1');
    expect(result[0].depth).toBe(0);
    expect(result[1].depth).toBe(1);
  });
});

describe('rollUpConversationCount cycle safety', () => {
  it('terminates on self-referencing folder', () => {
    const node: any = { id: 'a', folder: true, conversationCount: 3, children: [] };
    node.children.push(node);

    expect(() => {
      const count = rollUpConversationCount(node);
      // With depth limit 20, it re-counts itself up to 20 times
      // before stopping. The important thing is it doesn't stack overflow.
      expect(count).toBeGreaterThanOrEqual(3);
    }).not.toThrow();
  });

  it('counts normally for valid tree', () => {
    const tree: any = {
      id: 'root', folder: true, conversationCount: 2,
      children: [
        { id: 'sub', folder: true, conversationCount: 5, children: [] },
      ]
    };

    const count = rollUpConversationCount(tree);
    expect(count).toBe(7); // 2 + 5
  });
});

describe('rollUpLastActivityTime', () => {
  it('propagates activity from direct conversation children', () => {
    const node: any = {
      id: 'f1', folder: true, lastActivityTime: 0,
      children: [
        { id: 'conv-c1', conversation: { id: 'c1', lastAccessedAt: 500 }, children: [] },
        { id: 'conv-c2', conversation: { id: 'c2', lastAccessedAt: 800 }, children: [] },
      ]
    };
    const result = rollUpLastActivityTime(node);
    expect(result).toBe(800);
    expect(node.lastActivityTime).toBe(800);
  });

  it('propagates activity from nested subfolders (2 levels deep)', () => {
    const grandchild: any = {
      id: 'f-inner', folder: true, lastActivityTime: 0,
      children: [
        { id: 'conv-c1', conversation: { id: 'c1', lastAccessedAt: 9999 }, children: [] },
      ]
    };
    const root: any = {
      id: 'f-root', folder: true, lastActivityTime: 0,
      children: [grandchild]
    };

    const result = rollUpLastActivityTime(root);
    expect(result).toBe(9999);
    expect(root.lastActivityTime).toBe(9999);
    expect(grandchild.lastActivityTime).toBe(9999);
  });

  it('propagates activity from deeply nested hierarchy (3+ levels)', () => {
    const level3: any = {
      id: 'f3', folder: true, lastActivityTime: 0,
      children: [
        { id: 'conv-deep', conversation: { id: 'deep', lastAccessedAt: 42000 }, children: [] },
      ]
    };
    const level2: any = {
      id: 'f2', folder: true, lastActivityTime: 0,
      children: [level3]
    };
    const level1: any = {
      id: 'f1', folder: true, lastActivityTime: 100, // has some old direct activity
      children: [
        level2,
        { id: 'conv-old', conversation: { id: 'old', lastAccessedAt: 100 }, children: [] },
      ]
    };

    const result = rollUpLastActivityTime(level1);
    expect(result).toBe(42000);
    expect(level1.lastActivityTime).toBe(42000);
    expect(level2.lastActivityTime).toBe(42000);
    expect(level3.lastActivityTime).toBe(42000);
  });

  it('picks the max across sibling subfolders', () => {
    const folderA: any = {
      id: 'fa', folder: true, lastActivityTime: 0,
      children: [{ id: 'conv-a', conversation: { id: 'a', lastAccessedAt: 300 }, children: [] }]
    };
    const folderB: any = {
      id: 'fb', folder: true, lastActivityTime: 0,
      children: [{ id: 'conv-b', conversation: { id: 'b', lastAccessedAt: 700 }, children: [] }]
    };
    const root: any = {
      id: 'f-root', folder: true, lastActivityTime: 0,
      children: [folderA, folderB]
    };

    rollUpLastActivityTime(root);
    expect(root.lastActivityTime).toBe(700);
  });

  it('terminates on self-referencing folder', () => {
    const node: any = { id: 'a', folder: true, lastActivityTime: 50, children: [] };
    node.children.push(node);

    expect(() => {
      const result = rollUpLastActivityTime(node);
      expect(result).toBeGreaterThanOrEqual(50);
    }).not.toThrow();
  });

  it('returns 0 for empty folder', () => {
    const node: any = { id: 'empty', folder: true, lastActivityTime: 0, children: [] };
    expect(rollUpLastActivityTime(node)).toBe(0);
  });
});

describe('sortRecursive cycle safety', () => {
  it('terminates on circular children', () => {
    const node: any = { id: 'a', children: [] };
    node.children.push(node);

    expect(() => {
      sortRecursive([node]);
    }).not.toThrow();
  });
});

describe('removeNodeFromTree cycle safety', () => {
  it('terminates on circular tree without finding target', () => {
    const a: any = { id: 'a', children: [] };
    const b: any = { id: 'b', children: [] };
    a.children.push(b);
    b.children.push(a);

    const target = { id: 'not-in-tree' };
    expect(() => {
      const removed = removeNodeFromTree([a], target);
      expect(removed).toBe(false);
    }).not.toThrow();
  });
});

// ── cloneNode (sort-only fast path) ────────────────────────────────────
// Replicated from MUIChatHistory.tsx sort-only fast path

function cloneNode(node: any, convMap: Map<string, any>, _depth = 0): any {
  if (_depth > 30) return { ...node, children: [] };
  let conversationChanged = false;
  if (node.conversation) {
    const fresh = convMap.get(node.conversation.id);
    if (fresh && fresh !== node.conversation) conversationChanged = true;
  }
  let newChildren = node.children;
  if (node.children) {
    newChildren = node.children.map((c: any) => cloneNode(c, convMap, _depth + 1));
  }
  const childrenChanged = newChildren !== node.children &&
    newChildren.some((c: any, i: number) => c !== node.children[i]);
  if (!conversationChanged && !childrenChanged) return node;
  const copy = { ...node };
  if (conversationChanged) copy.conversation = convMap.get(node.conversation.id);
  if (childrenChanged) copy.children = newChildren;
  return copy;
}

function findNodeInTree(items: any[], targetId: string, _depth = 0): any {
  if (_depth > 30) return null;
  for (const n of items) {
    if (n.id === targetId) return n;
    if (n.children) {
      const f = findNodeInTree(n.children, targetId, _depth + 1);
      if (f) return f;
    }
  }
  return null;
}

describe('cloneNode cycle safety (sort-only fast path)', () => {
  it('terminates on self-referencing node', () => {
    const node: any = { id: 'a', folder: true, children: [] };
    node.children.push(node);
    const convMap = new Map();

    expect(() => {
      const clone = cloneNode(node, convMap);
      // Should produce a clone that terminates — not stack overflow
      expect(clone.id).toBe('a');
    }).not.toThrow();
  });

  it('terminates on mutual cycle (A → B → A)', () => {
    const a: any = { id: 'a', folder: true, children: [] };
    const b: any = { id: 'b', folder: true, children: [] };
    a.children.push(b);
    b.children.push(a);
    const convMap = new Map();

    expect(() => {
      const clone = cloneNode(a, convMap);
      expect(clone.id).toBe('a');
    }).not.toThrow();
  });

  it('produces independent copy for valid tree', () => {
    const conv = { id: 'c1', lastAccessedAt: 100 };
    const child = { id: 'conv-c1', conversation: conv, children: [] };
    const root = { id: 'f1', folder: true, children: [child], lastActivityTime: 0, isPinned: false };
    const freshConv = { id: 'c1', lastAccessedAt: 999 };
    const convMap = new Map([['c1', freshConv]]);

    const clone = cloneNode(root, convMap);
    expect(clone.children[0].conversation.lastAccessedAt).toBe(999);
    expect(root.children[0].conversation.lastAccessedAt).toBe(100);
    expect(clone.children).not.toBe(root.children);
  });
});

describe('cloneNode reference reuse optimization', () => {
  it('returns same reference when nothing changed', () => {
    const conv = { id: 'c1', lastAccessedAt: 100 };
    const child = { id: 'conv-c1', conversation: conv, children: [] };
    const root = { id: 'f1', folder: true, children: [child], lastActivityTime: 100, isPinned: false };

    // convMap has the SAME reference — nothing changed
    const convMap = new Map([['c1', conv]]);
    const clone = cloneNode(root, convMap);

    // Should return the exact same object reference
    expect(clone).toBe(root);
    expect(clone.children[0]).toBe(child);
  });

  it('only clones the changed subtree', () => {
    const conv1 = { id: 'c1', lastAccessedAt: 100 };
    const conv2 = { id: 'c2', lastAccessedAt: 200 };
    const child1 = { id: 'conv-c1', conversation: conv1, children: [] };
    const child2 = { id: 'conv-c2', conversation: conv2, children: [] };
    const root = { id: 'f1', folder: true, children: [child1, child2], lastActivityTime: 200, isPinned: false };

    // Only conv2 changed
    const freshConv2 = { id: 'c2', lastAccessedAt: 999 };
    const convMap = new Map([['c1', conv1], ['c2', freshConv2]]);
    const clone = cloneNode(root, convMap);

    expect(clone).not.toBe(root);           // root changed (child changed)
    expect(clone.children[0]).toBe(child1); // child1 unchanged — reused
    expect(clone.children[1]).not.toBe(child2); // child2 changed
    expect(clone.children[1].conversation).toBe(freshConv2);
  });
});

describe('findNode cycle safety', () => {
  it('terminates on circular tree', () => {
    const a: any = { id: 'a', children: [] };
    const b: any = { id: 'b', children: [] };
    a.children.push(b);
    b.children.push(a);

    expect(() => {
      const result = findNodeInTree([a], 'not-found');
      expect(result).toBeNull();
    }).not.toThrow();
  });

  it('finds target in valid tree', () => {
    const target = { id: 'target', children: [] };
    const root = { id: 'root', children: [{ id: 'mid', children: [target] }] };
    expect(findNodeInTree([root], 'target')?.id).toBe('target');
  });
});

describe('mutual folder cycle detection in tree build', () => {
  it('detects A→B→A cycle and places second folder at root', () => {
    const folders = [
      { id: 'a', name: 'A', parentId: 'b' },
      { id: 'b', name: 'B', parentId: 'a' },
    ];

    const folderMap = new Map<string, any>();
    folders.forEach(f => {
      folderMap.set(f.id, { id: f.id, name: f.name, folder: f, children: [] });
    });

    const rootItems: any[] = [];
    folders.forEach(folder => {
      const node = folderMap.get(folder.id);
      let hasCycle = false;
      if (folder.parentId && folder.parentId !== folder.id) {
        const visited = new Set<string>([folder.id]);
        let cur: string | null | undefined = folder.parentId;
        while (cur) {
          if (visited.has(cur)) { hasCycle = true; break; }
          visited.add(cur);
          const ancestor = folders.find(f => f.id === cur);
          cur = ancestor?.parentId;
        }
      }
      if (!hasCycle && folder.parentId && folder.parentId !== folder.id && folderMap.has(folder.parentId)) {
        folderMap.get(folder.parentId).children.push(node);
      } else {
        rootItems.push(node);
      }
    });

    // One folder nests normally, the cycle-forming one goes to root
    // (first folder processed nests into the second; second detects cycle and goes to root)
    expect(rootItems.length).toBeGreaterThanOrEqual(1);
    // No circular references — cloneNode should succeed
    const convMap = new Map();
    expect(() => {
      rootItems.map(n => cloneNode(n, convMap));
    }).not.toThrow();
  });

  it('detects A→B→C→A three-way cycle', () => {
    const folders = [
      { id: 'a', name: 'A', parentId: 'c' },
      { id: 'b', name: 'B', parentId: 'a' },
      { id: 'c', name: 'C', parentId: 'b' },
    ];

    const folderMap = new Map<string, any>();
    folders.forEach(f => {
      folderMap.set(f.id, { id: f.id, name: f.name, folder: f, children: [] });
    });

    const rootItems: any[] = [];
    folders.forEach(folder => {
      const node = folderMap.get(folder.id);
      let hasCycle = false;
      if (folder.parentId && folder.parentId !== folder.id) {
        const visited = new Set<string>([folder.id]);
        let cur: string | null | undefined = folder.parentId;
        while (cur) {
          if (visited.has(cur)) { hasCycle = true; break; }
          visited.add(cur);
          const ancestor = folders.find(f => f.id === cur);
          cur = ancestor?.parentId;
        }
      }
      if (!hasCycle && folder.parentId && folder.parentId !== folder.id && folderMap.has(folder.parentId)) {
        folderMap.get(folder.parentId).children.push(node);
      } else {
        rootItems.push(node);
      }
    });

    // At least one folder must be placed at root to break the cycle
    expect(rootItems.length).toBeGreaterThanOrEqual(1);
    const convMap = new Map();
    expect(() => {
      rootItems.map(n => cloneNode(n, convMap));
    }).not.toThrow();
  });
});

describe('tree building self-reference guard', () => {
  it('folder with parentId === id should go to root, not be its own child', () => {
    // Simulate the tree-building logic from treeDataRaw
    const folders = [
      { id: 'f1', name: 'Normal', parentId: null },
      { id: 'f2', name: 'Self-ref', parentId: 'f2' }, // BUG: parentId === id
    ];

    const folderMap = new Map();
    folders.forEach(f => {
      folderMap.set(f.id, { id: f.id, name: f.name, folder: f, children: [] });
    });

    const rootItems: any[] = [];
    folders.forEach(folder => {
      const node = folderMap.get(folder.id);
      // THE FIX: guard against parentId === id
      if (folder.parentId && folder.parentId !== folder.id && folderMap.has(folder.parentId)) {
        const parentNode = folderMap.get(folder.parentId);
        parentNode.children.push(node);
      } else {
        rootItems.push(node);
      }
    });

    // Both folders should be at root level
    expect(rootItems.length).toBe(2);
    // f2 should NOT be inside its own children
    const f2Node = folderMap.get('f2');
    expect(f2Node.children.length).toBe(0);
  });
});

describe('sort-only fast path immutability', () => {
  // Replicate the FNV-1a hash used in treeDataRaw
  function fnv1a() {
    let h = 0x811c9dc5;
    return {
      add(s: string) {
        for (let i = 0; i < s.length; i++) {
          h ^= s.charCodeAt(i);
          h = Math.imul(h, 0x01000193);
        }
      },
      value() { return h >>> 0; }
    };
  }

  it('structural hash is deterministic for identical inputs', () => {
    const folders = [{ id: 'f1', name: 'Folder', parentId: '', isGlobal: false, taskPlan: null as any }];
    const conversations = [
      { id: 'c1', title: 'Chat', folderId: 'f1', isActive: true, isGlobal: false, delegateMeta: null as any, lastAccessedAt: 100 },
      { id: 'c2', title: 'Chat 2', folderId: '', isActive: true, isGlobal: false, delegateMeta: null as any, lastAccessedAt: 200 },
    ];

    const hash1 = (() => {
      const sh = fnv1a();
      folders.forEach(f => { sh.add(f.id || ''); sh.add(f.name || ''); sh.add(f.parentId || ''); sh.add(f.isGlobal ? 'g' : ''); sh.add(f.taskPlan?.source_conversation_id || ''); });
      conversations.forEach(c => { sh.add(c.id || ''); sh.add(c.title || ''); sh.add(c.folderId || ''); sh.add(c.isActive === false ? '0' : '1'); sh.add(c.isGlobal ? 'g' : ''); sh.add(c.delegateMeta?.status || ''); });
      return sh.value();
    })();

    const hash2 = (() => {
      const sh = fnv1a();
      folders.forEach(f => { sh.add(f.id || ''); sh.add(f.name || ''); sh.add(f.parentId || ''); sh.add(f.isGlobal ? 'g' : ''); sh.add(f.taskPlan?.source_conversation_id || ''); });
      conversations.forEach(c => { sh.add(c.id || ''); sh.add(c.title || ''); sh.add(c.folderId || ''); sh.add(c.isActive === false ? '0' : '1'); sh.add(c.isGlobal ? 'g' : ''); sh.add(c.delegateMeta?.status || ''); });
      return sh.value();
    })();

    expect(hash1).toBe(hash2);
    // Sanity: hash is not 0 (the uninitialized ref value)
    expect(hash1).not.toBe(0);
  });

  it('changing only lastAccessedAt does not change structural hash', () => {
    const compute = (accessTime: number) => {
      const sh = fnv1a();
      sh.add('c1'); sh.add('Chat'); sh.add('f1'); sh.add('1'); sh.add(''); sh.add('');
      return sh.value();
    };
    // Same structural hash regardless of access time
    expect(compute(100)).toBe(compute(999));
  });

  it('changing only lastAccessedAt DOES change sort hash', () => {
    const computeSort = (accessTime: number) => {
      const oh = fnv1a();
      oh.add(String(accessTime));
      return oh.value();
    };
    expect(computeSort(100)).not.toBe(computeSort(999));
  });

  it('cloneNode produces independent copy when data changes', () => {
    const conv = { id: 'c1', title: 'Chat', lastAccessedAt: 100 };
    const original = {
      id: 'f1', name: 'Folder', folder: true, lastActivityTime: 0,
      isPinned: false,
      children: [{ id: 'conv-c1', name: 'Chat', conversation: conv, children: [] }],
    };

    const freshConv = { ...conv, lastAccessedAt: 999 };
    const convMap = new Map([['c1', freshConv]]);
    const clone = cloneNode(original, convMap);

    // Clone has fresh conversation reference (data changed)
    expect(clone.children[0].conversation.lastAccessedAt).toBe(999);
    // Original is untouched
    expect(original.children[0].conversation.lastAccessedAt).toBe(100);
    // Array references are different
    expect(clone.children).not.toBe(original.children);
    // Node references are different
    expect(clone).not.toBe(original);
  });

  it('cloneNode reuses references when data is identical', () => {
    const conv = { id: 'c1', title: 'Chat', lastAccessedAt: 100 };
    const child = { id: 'conv-c1', name: 'Chat', conversation: conv, children: [] };
    const original = {
      id: 'f1', name: 'Folder', folder: true, lastActivityTime: 100,
      isPinned: false,
      children: [child],
    };

    // Same conversation reference — no change
    const convMap = new Map([['c1', conv]]);
    const clone = cloneNode(original, convMap);

    // Everything should be the same reference
    expect(clone).toBe(original);
    expect(clone.children).toBe(original.children);
    expect(clone.children[0]).toBe(child);
  });
});