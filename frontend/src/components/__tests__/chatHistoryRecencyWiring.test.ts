/**
 * Seam guard for the sidebar recency filter.  utils/chatTreeRecency.ts is
 * covered by its own unit test; this checks that MUIChatHistory actually
 * mounts it: the filter runs on the cached tree's output (treeDataRaw) and
 * the debounce consumes the filtered tree, the exempt set is current +
 * streaming + running-task ids, the toolbar button and menu exist and are
 * hidden while searching, and the preference is persisted under the
 * documented key.  Static, like the other wiring tests, because rendering
 * the virtualized tree needs the full context stack.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.resolve(__dirname, '..', 'MUIChatHistory.tsx'), 'utf8');
const code = SRC.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

function slice(start: string, end: string): string {
  const s = code.indexOf(start);
  expect(s).toBeGreaterThan(-1);
  const e = code.indexOf(end, s);
  expect(e).toBeGreaterThan(s);
  return code.slice(s, e);
}

describe('sidebar recency filter wiring', () => {
  it('imports the filter and persists the window under RECENCY_STORAGE_KEY', () => {
    expect(code).toMatch(/import \{[^}]*applyRecencyFilter[^}]*loadRecencyWindow[^}]*\} from '..\/utils\/chatTreeRecency'/);
    expect(code).toContain("useState<RecencyWindow>(() => loadRecencyWindow())");
    expect(code).toContain('localStorage.setItem(RECENCY_STORAGE_KEY, recencyWindow)');
  });

  it('filters the cached tree output, not inside the tree memo, and the debounce consumes the filtered tree', () => {
    const memoEnd = code.indexOf('jobsSeen]); // eslint-disable-line react-hooks/exhaustive-deps');
    const filterAt = code.indexOf('applyRecencyFilter(treeDataRaw,');
    expect(memoEnd).toBeGreaterThan(-1);
    expect(filterAt).toBeGreaterThan(memoEnd);
    // Comment lines are stripped above, so the boundary is the next code line.
    const debounce = slice('const [treeData, setTreeData] = useState<any[]>([]);', 'const expandedSet = useMemo(');
    expect(debounce).toContain('setTreeData(treeDataVisible)');
    expect(debounce).not.toContain('setTreeData(treeDataRaw)');
    expect(debounce).toContain('}, [treeDataVisible]);');
  });

  it('exempts the current, streaming and running-task conversations', () => {
    const exempt = slice('const recencyExempt = useMemo(', 'const recencyFiltered = useMemo(');
    expect(exempt).toContain('if (currentConversationId) s.add(currentConversationId);');
    expect(exempt).toContain('streamingConversations.forEach(id => s.add(id));');
    expect(exempt).toContain('runningTaskConversations.forEach(id => s.add(id));');
  });

  it('toolbar button + menu are present, offer every window, and hide while searching', () => {
    const button = slice('data-testid="recency-filter-button"', '<Tooltip title="New folder">');
    expect(button).toContain('RECENCY_WINDOWS.map((w) =>');
    expect(button).toContain('onClick={() => { setRecencyWindow(w); setRecencyMenuAnchor(null); }}');
    // The whole block is gated on !searchQuery.
    const gateAt = code.lastIndexOf('{!searchQuery && (', code.indexOf('data-testid="recency-filter-button"'));
    expect(gateAt).toBeGreaterThan(code.indexOf('placeholder="Search conversations..."'));
  });

  it('caption shows the hidden count and offers show all', () => {
    const caption = slice('data-testid="recency-filter-caption"', '{/* Search Results or Tree View */}');
    expect(caption).toContain('recencyFiltered.hiddenConversations');
    expect(caption).toContain("setRecencyWindow('all')");
  });
});
