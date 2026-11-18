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
    // Choosing a preset also drops any custom range, or the preset would
    // be selected in the menu while the custom range still filtered.
    expect(button).toContain('onClick={() => { setRecencyWindow(w); setRecencyCustom(null); setRecencyMenuAnchor(null); }}');
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

/**
 * Custom range (utils/chatTreeRecency.ts resolveCustomRange and friends,
 * components/RecencyCustomPopover.tsx).  The logic has its own unit test
 * (chatTreeRecencyCustom.test.ts); these check each hop from the menu to
 * the filter: state is session-scoped, the applied range reaches
 * applyRecencyFilter and invalidates its memo, the menu opens the popover,
 * the popover hands the range back, every way of leaving a custom range
 * clears it, and the tree cache notices createdAt (which "first activity"
 * and "lifetime" read).
 */
describe('sidebar custom recency range wiring', () => {
  it('state loads from and saves to the session store, not the window preference', () => {
    expect(code).toMatch(/import \{[^}]*customRecencyLabel[^}]*loadCustomRecency[^}]*saveCustomRecency[^}]*\} from '..\/utils\/chatTreeRecency'/);
    expect(code).toContain('useState<CustomRecency | null>(() => loadCustomRecency())');
    expect(code).toContain('saveCustomRecency(recencyCustom)');
    // The preset effect must not start persisting the custom value.
    const presetEffect = slice('localStorage.setItem(RECENCY_STORAGE_KEY', '}, [recencyWindow]);');
    expect(presetEffect).not.toContain('recencyCustom');
  });

  it('the applied range reaches the filter and is a dependency of its memo', () => {
    const memo = slice('const recencyFiltered = useMemo(', 'const treeDataVisible = recencyFiltered.nodes;');
    expect(memo).toContain('applyRecencyFilter(treeDataRaw,');
    expect(memo).toContain('custom: recencyCustom');
    expect(memo).toMatch(/\[treeDataRaw, recencyWindow, recencyCustom, recencyExempt\]/);
  });

  it('the menu offers Custom, which opens the popover on the toolbar button', () => {
    const button = slice('data-testid="recency-filter-button"', '<Tooltip title="New folder">');
    expect(button).toContain('data-testid="recency-custom-item"');
    expect(button).toContain('setRecencyCustomAnchor(recencyMenuAnchor); setRecencyMenuAnchor(null);');
    // A preset is not shown as selected while a custom range is filtering.
    expect(button).toContain('selected={!recencyCustom && recencyWindow === w}');
  });

  it('the popover is mounted with the applied range and hands a new one back', () => {
    const popover = slice('<RecencyCustomPopover', '/>');
    expect(code).toMatch(/import RecencyCustomPopover from '\.\/RecencyCustomPopover'/);
    expect(popover).toContain('anchorEl={recencyCustomAnchor}');
    expect(popover).toContain('initial={recencyCustom}');
    expect(popover).toContain('onApply={(c) => { setRecencyCustom(c); setRecencyCustomAnchor(null); }}');
    expect(popover).toContain('onClose={() => setRecencyCustomAnchor(null)}');
  });

  it('button state, tooltip and caption follow the custom range', () => {
    expect(code).toContain("const recencyActive = recencyCustom !== null || recencyWindow !== 'all';");
    expect(code).toMatch(/const recencyLabel = recencyCustom\s*\?\s*customRecencyLabel\(recencyCustom, Date\.now\(\)\)/);
    const button = slice('data-testid="recency-filter-button"', '<HistoryIcon');
    expect(button).toContain('aria-pressed={recencyActive}');
    const caption = slice('data-testid="recency-filter-caption"', '{/* Search Results or Tree View */}');
    expect(caption).toContain('{recencyLabel}');
    // "show all" has to clear both, or the list stays filtered.
    expect(caption).toContain("onClick={() => { setRecencyWindow('all'); setRecencyCustom(null); }}");
    // The caption itself is gated on either filter being active.
    const gateAt = code.lastIndexOf('{recencyActive && !searchQuery && (', code.indexOf('data-testid="recency-filter-caption"'));
    expect(gateAt).toBeGreaterThan(-1);
  });

  it('the tree cache is invalidated when createdAt changes', () => {
    // Same hazard as lastActiveAt: a sync that fills createdAt without the
    // hash moving leaves the cached node holding the old conversation
    // object, and the filter judges the row without its start time.
    const sortHash = slice('const oh = fnv1a();', 'const sortHash = oh.value();');
    expect(sortHash).toMatch(/\(?c(?: as any\))?\.createdAt/);
  });

  it('the popover validates with the same resolver the filter uses', () => {
    const POPOVER = fs.readFileSync(path.resolve(__dirname, '..', 'RecencyCustomPopover.tsx'), 'utf8');
    expect(POPOVER).toMatch(/import \{[^}]*resolveCustomRange[^}]*\} from '..\/utils\/chatTreeRecency'/);
    expect(POPOVER).toContain('resolveCustomRange(draft, Date.now())');
    expect(POPOVER).toContain('disabled={!range}');
    expect(POPOVER).toContain('RECENCY_BASES.map(');
    expect(POPOVER).toContain('RECENCY_UNITS.map(');
    expect(POPOVER).toContain('data-testid="recency-custom-popover"');
  });
});
