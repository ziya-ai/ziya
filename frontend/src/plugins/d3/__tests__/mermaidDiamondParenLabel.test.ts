/**
 * Unquoted decision-diamond / other bracket labels containing parentheses and
 * comparison operators must be quoted before the lexer sees them.
 *
 * Failure this pins (seen in the browser console):
 *
 *     B{getPciDeviceCount() >= max_marvell_device_count?}
 *     -> Parse error ... got 'PS'
 *
 * `(` inside an unquoted `{...}` label is read as the start of a new shape.
 * Rectangles `id[...]` were already handled by quoteBracketLabelsWithParens;
 * diamonds were not.
 */
import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const SRC = `flowchart TD
    A[PMM-A assert] --> B{getPciDeviceCount() >= max_marvell_device_count?}
    B -- no --> C[do nothing this second]
    B -- yes --> D[buildMarvellDevList → systemctl start switch-mgr@X0 / @Y1]
    D --> E{allSwmActive()?<br/>dev_list.size() >= max AND every SWM active}
    C --> F{tick_delay < pcie_scan_timeout = 360 s?}
    E -- no --> F
    F -- yes --> B
    E -- yes --> G[Ack SUCCESS]
    F -- no --> H[Ack FAIL → handlePmmDeAssertMsg()<br/>stops any started SWM, clears dev_list]`;

describe('unquoted diamond labels with parentheses', () => {
  it('quotes the diamond label', () => {
    const out = preprocessDefinition(SRC, 'flowchart');
    expect(out).toContain('B{"getPciDeviceCount() >= max_marvell_device_count?"}');
    expect(out).toContain('F{"tick_delay < pcie_scan_timeout = 360 s?"}');
    expect(out).toContain('E{"allSwmActive()?<br/>dev_list.size() >= max AND every SWM active"}');
  });

  it('leaves already-quoted diamonds alone', () => {
    const src = 'flowchart TD\n  A{"f() >= 1?"} --> B';
    const out = preprocessDefinition(src, 'flowchart');
    expect(out).toContain('A{"f() >= 1?"}');
    expect(out).not.toContain('""');
  });

  it('does not touch plain diamonds or hexagons/braces in other contexts', () => {
    const src = 'flowchart TD\n  A{Is it ok} -->|yes| B{{Hex}}';
    const out = preprocessDefinition(src, 'flowchart');
    expect(out).toContain('A{Is it ok}');
    expect(out).toContain('B{{Hex}}');
  });
});
