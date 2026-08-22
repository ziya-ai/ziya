/**
 * Tests for the chain-exit invariant layer.
 *
 * This is a DIFFERENT guarantee from the per-pass postcondition mechanism
 * (see mermaidPostconditionRepair.test.ts), and the distinction is the whole
 * reason this layer exists:
 *
 *   runPostcondition   inspects ONE pass's own output, and blames that pass.
 *   chain-exit         inspects the FINAL definition, after every pass has
 *                      run, immediately before it reaches the mermaid lexer.
 *
 * The gap the per-pass contracts structurally cannot see: label-backtick-escape
 * runs at priority 730 and correctly emits '&#96;'. Roughly twenty label passes
 * run AFTER it. If any of them ever HTML-unescapes those entities, or truncates
 * a label such that a bare backtick run ends up opening it, every per-pass
 * contract still reports clean -- each pass is only asked about its own output,
 * and the offending shape was reintroduced downstream of the pass that owns it.
 * The user gets the original "Lexical error ... Unrecognized text" and an empty
 * SVG, with nothing in the logs pointing at the cause.
 *
 * The chain exit is the only place that can observe what the lexer will
 * actually receive, so the invariant is asserted there and repaired there.
 *
 * Entities are assembled from fragments ('&#' + '96;') throughout, for the same
 * reason as the sibling suites: a one-piece literal is exactly what got
 * collapsed to cause the original bug, and the original test file was collapsed
 * the same way -- which is why all eight of its assertions passed against a
 * no-op pass.
 */

import {
  registerPreprocessor,
  preprocessDefinition,
  initMermaidEnhancer,
  __getChainExitViolations,
  __resetChainExitViolations,
} from '../mermaidEnhancer';

const BACKTICK = String.fromCharCode(96);
const QUOTE = String.fromCharCode(34);
const FENCE = BACKTICK.repeat(3);
const BACKTICK_ENTITY = '&#' + '96;';
const PIPE_ENTITY = '&#' + '124;';

const countOf = (haystack: string, needle: string): number =>
  haystack.split(needle).length - 1;

/** Does any label in this definition open with a bare 2+ backtick run? */
const opensLabelWithBacktickRun = (def: string): boolean =>
  new RegExp(QUOTE + BACKTICK + '{2,}').test(def);

beforeAll(() => {
  initMermaidEnhancer();
});

beforeEach(() => {
  __resetChainExitViolations();
});

describe('chain-exit invariant: catches reintroduction', () => {
  // The scenario the per-pass postcondition cannot catch. A low-priority pass
  // registered AFTER label-backtick-escape undoes its work, mimicking a future
  // label pass that HTML-unescapes entities.
  const registerUnescaper = () =>
    registerPreprocessor(
      (def: string): string => def.split(BACKTICK_ENTITY).join(BACKTICK),
      {
        name: 'test-entity-unescaper',
        priority: 1, // runs last, long after label-backtick-escape (730)
      },
    );

  it('repairs a backtick run reintroduced by a later pass', () => {
    const unregister = registerUnescaper();
    try {
      const def = 'flowchart LR\n    A[' + QUOTE + FENCE + 'html-mockup' + QUOTE + '] --> B{x}';
      const result = preprocessDefinition(def, 'flowchart');

      // Without the chain-exit layer this is where the lexer error came from.
      expect(opensLabelWithBacktickRun(result)).toBe(false);
      expect(result).toContain(QUOTE + BACKTICK_ENTITY.repeat(3) + 'html-mockup');
    } finally {
      unregister();
    }
  });

  it('records the violated invariant by name', () => {
    const unregister = registerUnescaper();
    try {
      const def = 'flowchart LR\n    A[' + QUOTE + FENCE + 'x' + QUOTE + '] --> B{y}';
      preprocessDefinition(def, 'flowchart');
      expect(__getChainExitViolations()).toContain('no-label-opening-backtick-run');
    } finally {
      unregister();
    }
  });

  it('reports no violation when the chain is healthy', () => {
    // No saboteur registered: label-backtick-escape does its job, so the
    // chain exit should have nothing to fix.
    const def = 'flowchart LR\n    A[' + QUOTE + FENCE + 'html-mockup' + QUOTE + '] --> B{x}';
    const result = preprocessDefinition(def, 'flowchart');
    expect(__getChainExitViolations()).toEqual([]);
    expect(opensLabelWithBacktickRun(result)).toBe(false);
  });

  it('repairs a pipe reintroduced inside a quoted edge label', () => {
    const unregister = registerPreprocessor(
      (def: string): string => def.split(PIPE_ENTITY).join('|'),
      { name: 'test-pipe-unescaper', priority: 1 },
    );
    try {
      const def = 'flowchart LR\n    A -->|' + QUOTE + '|phase| shift' + QUOTE + '| B';
      const result = preprocessDefinition(def, 'flowchart');
      expect(__getChainExitViolations()).toContain('no-pipe-inside-quoted-edge-label');
      expect(result).toContain(PIPE_ENTITY);
      expect(result).not.toContain(QUOTE + '|phase| shift' + QUOTE);
    } finally {
      unregister();
    }
  });

  it('repairs every reintroduced occurrence, not just the first', () => {
    const unregister = registerUnescaper();
    try {
      const def = [
        'flowchart LR',
        '    A[' + QUOTE + FENCE + 'one' + QUOTE + '] --> B[' + QUOTE + FENCE + 'two' + QUOTE + ']',
      ].join('\n');
      const result = preprocessDefinition(def, 'flowchart');
      expect(opensLabelWithBacktickRun(result)).toBe(false);
      expect(countOf(result, BACKTICK_ENTITY)).toBe(6);
    } finally {
      unregister();
    }
  });
});

describe('chain-exit invariant: no false positives on legal syntax', () => {
  // Each of these renders correctly in mermaid 11. The chain exit must return
  // them byte-identical -- a guard that mutates working diagrams is worse than
  // no guard, because it converts a rendering success into a silent corruption.
  const legal: Array<[string, string, string]> = [
    [
      'single backtick is mermaid markdown-string mode',
      'flowchart LR\n    A[' + QUOTE + BACKTICK + '**bold**' + BACKTICK + QUOTE + '] --> B[x]',
      'flowchart',
    ],
    [
      'mid-label backtick run parses fine',
      'flowchart LR\n    A[' + QUOTE + 'use ' + FENCE + 'code' + FENCE + ' mid' + QUOTE + '] --> B[x]',
      'flowchart',
    ],
    [
      'plain unquoted edge label',
      'flowchart LR\n    A -->|yes| B',
      'flowchart',
    ],
    [
      'pipe-free quoted edge label',
      'flowchart LR\n    A -->|' + QUOTE + 'yes' + QUOTE + '| B',
      'flowchart',
    ],
    [
      'sequenceDiagram with a pipe in a message',
      'sequenceDiagram\n    A->>B: a|b',
      'sequenceDiagram',
    ],
    [
      'classDiagram',
      'classDiagram\n    class Foo {\n      +bar()\n    }',
      'classDiagram',
    ],
    [
      'stateDiagram',
      'stateDiagram-v2\n    [*] --> Still',
      'stateDiagram',
    ],
    [
      'erDiagram',
      'erDiagram\n    CUSTOMER ||--o{ ORDER : places',
      'erDiagram',
    ],
    [
      'mindmap',
      'mindmap\n  root((core))\n    a\n    b',
      'mindmap',
    ],
    [
      'label containing br tags',
      'flowchart LR\n    A[' + QUOTE + 'a<br/>b' + QUOTE + '] --> B[x]',
      'flowchart',
    ],
  ];

  it.each(legal)('leaves legal syntax alone: %s', (_label, def, type) => {
    const result = preprocessDefinition(def, type);
    expect(__getChainExitViolations()).toEqual([]);
  });

  it('does not flag an already-escaped backtick entity', () => {
    const def = 'flowchart LR\n    A[' + QUOTE + BACKTICK_ENTITY.repeat(3) + 'x' + QUOTE + '] --> B[y]';
    preprocessDefinition(def, 'flowchart');
    expect(__getChainExitViolations()).toEqual([]);
  });

  it('does not flag an already-escaped pipe entity', () => {
    const def = 'flowchart LR\n    A -->|' + QUOTE + 'a' + PIPE_ENTITY + 'b' + QUOTE + '| B';
    preprocessDefinition(def, 'flowchart');
    expect(__getChainExitViolations()).toEqual([]);
  });
});

describe('chain-exit invariant: scoping and stability', () => {
  it('does not apply the flowchart pipe invariant to other diagram types', () => {
    // A sequence message may legitimately contain a bare pipe; the pipe
    // invariant is flowchart/graph-only and must not reach it.
    const def = 'sequenceDiagram\n    A->>B: value is a|b';
    preprocessDefinition(def, 'sequenceDiagram');
    expect(__getChainExitViolations()).not.toContain('no-pipe-inside-quoted-edge-label');
  });

  it('is idempotent: re-running over its own output changes nothing', () => {
    const unregister = registerUnescaperForIdempotency();
    try {
      const def = 'flowchart LR\n    A[' + QUOTE + FENCE + 'x' + QUOTE + '] --> B{y}';
      const once = preprocessDefinition(def, 'flowchart');
      __resetChainExitViolations();
      const twice = preprocessDefinition(once, 'flowchart');
      expect(__getChainExitViolations()).toEqual([]);
      expect(countOf(twice, BACKTICK_ENTITY)).toBe(countOf(once, BACKTICK_ENTITY));
    } finally {
      unregister();
    }
  });

  function registerUnescaperForIdempotency() {
    return registerPreprocessor(
      (def: string): string => def,
      { name: 'test-noop', priority: 1 },
    );
  }

  it('resolves the originally reported definition end to end', () => {
    // The verbatim definition from the bug report, which produced
    // "Lexical error on line 3. Unrecognized text" and an empty SVG.
    const def = [
      'flowchart LR',
      '    A[' + QUOTE + FENCE + 'html-mockup' + QUOTE + '] --> B{' + QUOTE + 'variant<br/>modifier?' + QUOTE + '}',
      '    B -->|' + QUOTE + 'none<br/>(default)' + QUOTE + '| C[' + QUOTE + 'mockup' + QUOTE + ']',
      '    B -->|' + QUOTE + 'figure / inline' + QUOTE + '| D[' + QUOTE + 'figure' + QUOTE + ']',
    ].join('\n');
    const result = preprocessDefinition(def, 'flowchart');
    expect(opensLabelWithBacktickRun(result)).toBe(false);
    expect(countOf(result, BACKTICK)).toBe(0);
  });
});
