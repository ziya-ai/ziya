/**
 * `--` inside a composite state separates concurrent regions; it must survive
 * preprocessing.
 *
 * critical-state-divider-fix deleted every line that was only `--`, which
 * merged the regions of `state Device { ... -- ... }` into one.  Only a `--`
 * outside any composite is invalid, and only that one is removed now.
 *
 * The first test fails against the unpatched pass: the divider is gone.
 */
import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  jest.spyOn(console, 'log').mockImplementation(() => {});
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  initMermaidEnhancer();
});

const DIVIDER = /^\s*--\s*$/m;

const CONCURRENT = [
  'stateDiagram-v2',
  '    [*] --> Device',
  '    state Device {',
  '        [*] --> Off',
  '        Off --> On : power',
  '        --',
  '        [*] --> Disconnected',
  '        Disconnected --> Connecting : link up',
  '    }',
  '    Device --> [*]',
].join('\n');

describe('stateDiagram concurrent-region divider', () => {
  it('keeps a `--` inside a composite state', () => {
    expect(CONCURRENT).toMatch(DIVIDER);
    const out = preprocessDefinition(CONCURRENT, 'stateDiagram-v2');
    expect(out).toMatch(DIVIDER);
    // Both regions and the composite survive.
    expect(out).toMatch(/^\s*Off --> On : power$/m);
    expect(out).toMatch(/^\s*Disconnected --> Connecting : link up$/m);
    expect(out).toMatch(/^\s*state Device \{$/m);
  });

  it('keeps it inside a nested composite too', () => {
    const src = 'stateDiagram-v2\n    state A {\n        state B {\n            x --> y\n            --\n            p --> q\n        }\n    }';
    expect(preprocessDefinition(src, 'stateDiagram-v2')).toMatch(DIVIDER);
  });

  it('still removes a stray `--` outside any composite', () => {
    const src = 'stateDiagram-v2\n    A --> B\n    --\n    B --> C';
    const out = preprocessDefinition(src, 'stateDiagram-v2');
    expect(out).not.toMatch(DIVIDER);
    expect(out).toMatch(/^\s*A --> B$/m);
    expect(out).toMatch(/^\s*B --> C$/m);
  });
});
