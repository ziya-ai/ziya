/**
 * The token panel's "tools" figure follows bench placement in every state.
 *
 * Before: with no files selected the panel skipped POST /api/context-estimate
 * and showed token_costs.enabled_tool_tokens from an /api/mcp/status call
 * made without the project header -- the weight of every enabled server,
 * shelved and within-reach ones included.  And a placement change did not
 * refresh the figure until the file selection changed.
 *
 * Source assertions: the component needs half the app's contexts to mount.
 * The server half is tests/test_request_tool_defs.py.
 */
import * as fs from 'fs';
import * as path from 'path';

const src = fs.readFileSync(path.resolve(__dirname, '..', 'TokenCountDisplay.tsx'), 'utf8')
  .split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

function estimateEffect(): string {
  const fetchAt = src.indexOf("fetch('/api/context-estimate'");
  expect(fetchAt).toBeGreaterThan(-1);
  const start = src.lastIndexOf('useEffect(() => {', fetchAt);
  const end = src.indexOf('currentConversationId', src.indexOf('}, 800);', fetchAt));
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(fetchAt);
  return src.slice(start, src.indexOf(');', end));
}

describe('the estimate runs with no files selected', () => {
  it('does not bail out or clear the estimate on an empty selection', () => {
    const body = estimateEffect();
    expect(body).not.toContain('checkedKeys.length === 0');
    expect(body).not.toContain('setServerEstimate(null)');
    expect(body).toContain('(checkedKeys || []).map(String)');
    // Positive: the request and its conversation scope are still there.
    expect(body).toContain('body.conversation_id = currentConversationId');
  });
});

describe('placement and server changes refresh the figure', () => {
  it('the estimate effect depends on a revision counter', () => {
    expect(estimateEffect()).toMatch(/\[checkedKeys, folders, activeModelPin\?\.model, currentConversationId, toolsRevision\]$/);
    expect(src).toContain('const [toolsRevision, setToolsRevision] = useState(0);');
  });

  it('the counter is bumped by bench and MCP status events, and unsubscribed', () => {
    for (const ev of ['BENCH_CHANGED_EVENT', 'MCP_STATUS_CHANGED_EVENT']) {
      expect(src).toContain(`window.addEventListener(${ev}, bump);`);
      expect(src).toContain(`window.removeEventListener(${ev}, bump);`);
    }
    expect(src).toMatch(/import \{ BENCH_CHANGED_EVENT, MCP_STATUS_CHANGED_EVENT \} from '\.\.\/hooks\/useBench';/);
  });
});

describe('the fallback figure (estimate unavailable) is what is sent, for this project', () => {
  it('both status reads carry the project header', () => {
    expect(src).not.toContain("fetch('/api/mcp/status');");
    expect(src.split("fetch('/api/mcp/status', { headers: getProjectHeaders() })").length - 1).toBe(2);
  });

  it('both use the breakdown total, falling back to the old field', () => {
    const uses = src.split(
      'setMcpTokenCount((data.token_costs.breakdown?.sent_total ?? data.token_costs.enabled_tool_tokens) || 0);').length - 1;
    expect(uses).toBe(2);
    expect(src).not.toContain('setMcpTokenCount(data.token_costs.enabled_tool_tokens || 0);');
  });
});

describe('tooltip', () => {
  it('an adjusted baseline is still described as learned', () => {
    expect(src).toContain("serverEstimate.overhead_source.startsWith('learned_baseline')");
  });
});
