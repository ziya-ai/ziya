/**
 * show_document bodies keep their own fenced blocks (diagrams, code).
 *
 * The defect: renderToolPart wraps the document in a four-backtick
 * ````tool:mcp_show_document fence.  MarkdownRenderer's outer pass runs
 * escapeNestedBacktickFences over the WHOLE message, which rewrites the
 * leading backtick run of every content line inside a backtick fence to
 * &#96; entities.  The document's own ```mermaid / ```vega-lite openers and
 * closers are such lines, so DocumentBlock handed the inner renderer
 * "&#96;&#96;&#96;mermaid" -- not a fence -- and every diagram rendered as
 * literal text.
 *
 * The seam runs the real path: renderToolPart's display text, through the
 * real outer escaping pass, out of the fence the way marked takes the token
 * text, into parseDocumentCaption, which DocumentBlock renders.
 */
import { renderToolPart, toolPartFromEvent, parseDocumentCaption } from '../renderToolPart';
import { classifyFenceLines, escapeNestedBacktickFences } from '../../components/fenceScanner';

const caption = '<!-- ziya:show_document path="docs/d.md" lines="1-12" total_lines="12" bytes="200" sha256="0123456789ab" -->';
const docBody = [
  '# Design',
  '',
  '```mermaid',
  'graph TD',
  '  A --> B',
  '```',
  '',
  'Text between.',
  '',
  '```vega-lite',
  '{"mark": "bar"}',
  '```',
].join('\n');

/** The tool-fence body as marked's code token would carry it. */
function toolFenceBody(markdown: string): string {
  const lines = markdown.split('\n');
  const classes = classifyFenceLines(markdown);
  const open = classes.findIndex(c => c.kind === 'open' && c.info.startsWith('tool:mcp_show_document'));
  expect(open).toBeGreaterThanOrEqual(0);
  const close = classes.findIndex((c, i) => i > open && c.kind === 'close');
  expect(close).toBeGreaterThan(open);
  return lines.slice(open + 1, close).join('\n');
}

function displayedBody(): string {
  const part = toolPartFromEvent(
    { tool_id: 't1', tool_name: 'show_document', result: `${caption}\n${docBody}`, verified: true },
    new Map());
  const outer = escapeNestedBacktickFences(renderToolPart(part, 'Here:\n\n').display);
  return parseDocumentCaption(toolFenceBody(outer)).body;
}

describe('show_document nested fences survive the outer render', () => {
  it('control: the outer pass does escape the inner fences', () => {
    const part = toolPartFromEvent(
      { tool_id: 't1', tool_name: 'show_document', result: `${caption}\n${docBody}` }, new Map());
    const outer = escapeNestedBacktickFences(renderToolPart(part, '').display);
    expect(toolFenceBody(outer)).toContain('&#96;&#96;&#96;mermaid');
  });

  it('the body DocumentBlock renders has real fence lines again', () => {
    const body = displayedBody();
    expect(body).toBe(docBody);
    const opens = classifyFenceLines(body)
      .filter(c => c.kind === 'open')
      .map(c => (c as { info: string }).info);
    expect(opens).toEqual(['mermaid', 'vega-lite']);
  });

  it('only leading runs are decoded; an entity mid-line is left alone', () => {
    const { body } = parseDocumentCaption(`${caption}\nuse &#96;&#96;&#96; for fences\n&#96;&#96;&#96;py\nx\n&#96;&#96;&#96;`);
    expect(body).toBe('use &#96;&#96;&#96; for fences\n```py\nx\n```');
  });

  it('text without a caption is untouched', () => {
    expect(parseDocumentCaption('&#96;&#96;&#96;x').body).toBe('&#96;&#96;&#96;x');
  });
});
