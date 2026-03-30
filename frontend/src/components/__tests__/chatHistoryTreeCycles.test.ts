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
