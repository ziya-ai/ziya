/**
 * show_document on the frontend.
 *
 * The server's tool_display text for show_document is the document itself,
 * prefixed by a caption comment (app/utils/show_document.py).  Two seams:
 *
 *   renderToolPart  must pass it through verbatim.  Every other tool goes
 *                   through formatMCPOutput, which truncates at 10,000 chars;
 *                   the positive control shows that still happens for
 *                   file_read on the same text.
 *   MarkdownRenderer must render the fenced block as a document (rendered
 *                   markdown, expanded), not as a collapsed tool output.
 */
import * as fs from 'fs';
import * as path from 'path';
import { renderToolPart, toolPartFromEvent, parseDocumentCaption } from '../renderToolPart';

const caption = '<!-- ziya:show_document path="docs/r.md" lines="1-400" total_lines="400" bytes="20000" sha256="abcdef0123456789" -->';
const lines = Array.from({ length: 400 }, (_, i) => `Line ${i + 1}: the per-host median outage was ${i * 7} s.`);
const doc = `${caption}\n# Report\n\n${lines.join('\n')}`;

const part = (toolName: string) =>
  toolPartFromEvent({ tool_id: 't1', tool_name: toolName, result: doc, verified: true }, new Map());

describe('renderToolPart', () => {
  it('passes a show_document body through untruncated', () => {
    const out = renderToolPart(part('show_document'), 'Here:\n\n');
    expect(out.display).toContain(lines[399]);
    expect(out.display).toContain(caption);
    expect(out.display).toMatch(/^\n````tool:mcp_show_document\|🔐 Show Document\|markdown\n/);
    expect(out.display.endsWith('\n````\n\n')).toBe(true);
  });

  it('the body is the server text byte for byte, with nothing added inside the fence', () => {
    const out = renderToolPart(part('show_document'), 'x');
    expect(out.display).toBe(`\n\n\`\`\`\`tool:mcp_show_document|🔐 Show Document|markdown\n${doc}\n\`\`\`\`\n\n`);
  });

  it('a caption-less show_document result falls back to the generic path', () => {
    const p = toolPartFromEvent({ tool_id: 't2', tool_name: 'show_document', result: 'ERROR: File not found' }, new Map());
    expect(renderToolPart(p, '').display).toContain('ERROR: File not found');
  });
});

describe('parseDocumentCaption', () => {
  it('splits caption fields from the body', () => {
    const { fields, body } = parseDocumentCaption(doc);
    expect(fields).toMatchObject({ path: 'docs/r.md', lines: '1-400', sha256: 'abcdef0123456789' });
    expect(body.startsWith('# Report')).toBe(true);
  });

  it('leaves text without a caption alone', () => {
    expect(parseDocumentCaption('plain')).toEqual({ fields: {}, body: 'plain' });
  });
});

// MarkdownRenderer cannot be imported under craco's jest (an ESM dependency
// is not transformed), so its wiring is asserted at source level.
describe('MarkdownRenderer wiring', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', '..', 'components', 'MarkdownRenderer.tsx'), 'utf8');

  it('ToolBlock dispatches show_document to DocumentBlock', () => {
    expect(src).toMatch(/import \{ DOCUMENT_CAPTION_PREFIX, parseDocumentCaption \} from '\.\.\/utils\/renderToolPart'/);
    expect(src).toMatch(/name\.replace\(\/\^mcp_\/, ''\) === 'show_document'\s*&& props\.content\.startsWith\(DOCUMENT_CAPTION_PREFIX\)/);
    expect(src).toMatch(/return <DocumentBlock content=\{props\.content\}/);
    expect(src).toMatch(/return <GenericToolBlock \{\.\.\.props\} \/>/);
  });

  it('DocumentBlock renders the body (caption stripped) as markdown, expanded', () => {
    expect(src).toMatch(/const \{ fields, body \} = parseDocumentCaption\(content\)/);
    expect(src).toMatch(/<MarkdownRenderer markdown=\{body\}/);
    expect(src).toMatch(/data-testid="show-document"/);
  });
});
