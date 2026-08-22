/**
 * Tests for the preprocessor postcondition + auto-repair mechanism.
 *
 * Background. A preprocessor can silently degrade into an identity function
 * while still logging that it did its job. That is exactly what happened to
 * the label-backtick-escape pass: its replacement emitted a bare backtick
 * instead of the '&#96;' entity, so "```label became "```label -- byte
 * identical -- and the mermaid lexer still died with "Lexical error ...
 * Unrecognized text", surfacing to the user as an empty SVG. Every log line
 * in that pass reported success.
 *
 * The mechanism under test closes that class of failure generically. A pass
 * may declare a `detects` regex describing the offending shape it exists to
 * eliminate, plus the characters that must be entity-escaped. After the pass
 * runs, the dispatcher re-tests its OUTPUT:
 *
 *   - shape gone      -> pass worked, nothing happens
 *   - shape remains   -> the pass failed its own contract: warn loudly AND
 *                        repair the output by escaping the declared chars
 *                        inside capture group 1
 *
 * The repair is the point. This layer exists to render whatever an LLM emits,
 * so a broken pass must degrade to "still renders, logs a warning", never to
 * an error shown to the user.
 *
 * Entities are assembled from fragments ('&#' + '96;') throughout, because a
 * one-piece literal is precisely what got collapsed to cause the original bug
 * -- and the original test file was collapsed the same way, which is why all
 * eight of its assertions passed against a no-op.
 */

import {
  registerPreprocessor,
  preprocessDefinition,
  initMermaidEnhancer,
  __getPostconditionViolations,
  __resetPostconditionViolations,
} from '../mermaidEnhancer';

const BACKTICK = String.fromCharCode(96);
const QUOTE = String.fromCharCode(34);
const FENCE = BACKTICK.repeat(3);
const BACKTICK_ENTITY = '&#' + '96;';
const PIPE_ENTITY = '&#' + '124;';

const countOf = (haystack: string, needle: string): number =>
  haystack.split(needle).length - 1;

beforeAll(() => {
  initMermaidEnhancer();
});

beforeEach(() => {
  __resetPostconditionViolations();
});

describe('postcondition detection', () => {
  it('flags a pass that returns its input unchanged when the shape remains', () => {
    const unregister = registerPreprocessor(
      // A deliberately broken pass: claims to escape, actually a no-op.
      (definition: string): string => definition,
      {
        name: 'test-broken-noop',
        priority: 9000,
        detects: new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
        escapes: [BACKTICK],
      },
    );

    try {
      preprocessDefinition(
        'flowchart LR\n    A[' + QUOTE + FENCE + 'x' + QUOTE + '] --> B',
        'flowchart',
      );
      const violations = __getPostconditionViolations();
      expect(violations).toContain('test-broken-noop');
    } finally {
      unregister();
    }
  });

  it('does not flag a pass that actually eliminates the shape', () => {
    const unregister = registerPreprocessor(
      (definition: string): string =>
        definition.replace(
          new RegExp(QUOTE + '(' + BACKTICK + '{2,})', 'g'),
          (_m: string, run: string) => QUOTE + BACKTICK_ENTITY.repeat(run.length),
        ),
      {
        name: 'test-working-pass',
        priority: 9000,
        detects: new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
        escapes: [BACKTICK],
      },
    );

    try {
      preprocessDefinition(
        'flowchart LR\n    A[' + QUOTE + FENCE + 'x' + QUOTE + '] --> B',
        'flowchart',
      );
      expect(__getPostconditionViolations()).not.toContain('test-working-pass');
    } finally {
      unregister();
    }
  });

  it('does not flag a pass whose shape was never present', () => {
    const unregister = registerPreprocessor(
      (definition: string): string => definition,
      {
        name: 'test-not-applicable',
        priority: 9000,
        detects: new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
        escapes: [BACKTICK],
      },
    );

    try {
      preprocessDefinition('flowchart LR\n    A["plain"] --> B', 'flowchart');
      expect(__getPostconditionViolations()).not.toContain('test-not-applicable');
    } finally {
      unregister();
    }
  });
});

describe('auto-repair', () => {
  it('repairs the output of a broken pass so the diagram still renders', () => {
    const unregister = registerPreprocessor(
      (definition: string): string => definition, // broken: no-op
      {
        name: 'test-repair-backtick',
        priority: 9000,
        detects: new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
        escapes: [BACKTICK],
      },
    );

    try {
      const result = preprocessDefinition(
        'flowchart LR\n    A[' + QUOTE + FENCE + 'html-mockup' + QUOTE + '] --> B{' +
          QUOTE + 'variant?' + QUOTE + '}',
        'flowchart',
      );

      // The offending shape must be gone despite the pass doing nothing.
      expect(result).not.toContain(QUOTE + FENCE);
      expect(result).toContain(QUOTE + BACKTICK_ENTITY.repeat(3) + 'html-mockup');
    } finally {
      unregister();
    }
  });

  it('produces exactly what a correctly-written pass would produce', () => {
    const definition =
      'flowchart LR\n    A[' + QUOTE + FENCE + 'html-mockup' + QUOTE + '] --> B{' +
      QUOTE + 'variant?' + QUOTE + '}';
    const detects = new RegExp(QUOTE + '(' + BACKTICK + '{2,})');

    const unregisterBroken = registerPreprocessor((d: string) => d, {
      name: 'test-parity-broken',
      priority: 9000,
      detects,
      escapes: [BACKTICK],
    });
    const repaired = preprocessDefinition(definition, 'flowchart');
    unregisterBroken();

    const unregisterGood = registerPreprocessor(
      (d: string) =>
        d.replace(
          new RegExp(QUOTE + '(' + BACKTICK + '{2,})', 'g'),
          (_m: string, run: string) => QUOTE + BACKTICK_ENTITY.repeat(run.length),
        ),
      { name: 'test-parity-good', priority: 9000, detects, escapes: [BACKTICK] },
    );
    const correct = preprocessDefinition(definition, 'flowchart');
    unregisterGood();

    // Auto-repair is not a lossy fallback; it is the same transformation.
    expect(repaired).toBe(correct);
  });

  it('repairs pipes inside quoted edge labels', () => {
    const unregister = registerPreprocessor((d: string) => d, {
      name: 'test-repair-pipe',
      priority: 9000,
      detects: /(?:==>|-->|-\.->|--[xo]>|---|->>|-->>)\|"([^"]*\|[^"]*)"\|/,
      escapes: ['|'],
    });

    try {
      const result = preprocessDefinition(
        'flowchart LR\n    A -->|' + QUOTE + '|phase| shift' + QUOTE + '| B',
        'flowchart',
      );
      expect(result).toContain(PIPE_ENTITY + 'phase' + PIPE_ENTITY);
      expect(result).not.toContain(QUOTE + '|phase');
    } finally {
      unregister();
    }
  });

  it('repairs every occurrence, not just the first', () => {
    const unregister = registerPreprocessor((d: string) => d, {
      name: 'test-repair-multi',
      priority: 9000,
      detects: new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
      escapes: [BACKTICK],
    });

    try {
      const result = preprocessDefinition(
        'flowchart LR\n    A[' + QUOTE + FENCE + 'one' + QUOTE + '] --> B[' +
          QUOTE + FENCE + 'two' + QUOTE + ']',
        'flowchart',
      );
      expect(result).not.toContain(QUOTE + FENCE);
      expect(countOf(result, BACKTICK_ENTITY)).toBe(6);
    } finally {
      unregister();
    }
  });

  it('is idempotent - repaired output is not re-escaped', () => {
    const unregister = registerPreprocessor((d: string) => d, {
      name: 'test-repair-idempotent',
      priority: 9000,
      detects: new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
      escapes: [BACKTICK],
    });

    try {
      const definition =
        'flowchart LR\n    A[' + QUOTE + FENCE + 'x' + QUOTE + '] --> B';
      const once = preprocessDefinition(definition, 'flowchart');
      const twice = preprocessDefinition(once, 'flowchart');
      expect(countOf(twice, BACKTICK_ENTITY)).toBe(countOf(once, BACKTICK_ENTITY));
    } finally {
      unregister();
    }
  });
});

describe('false-positive safety', () => {
  // The repair must never touch syntax that mermaid already accepts. These
  // boundaries were established by rendering against real mermaid 11.
  const cases: Array<[string, string, RegExp, string[]]> = [
    [
      'single backtick is mermaid markdown-string mode',
      'flowchart LR\n    A[' + QUOTE + BACKTICK + '**bold**' + BACKTICK + QUOTE + '] --> B',
      new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
      [BACKTICK],
    ],
    [
      'mid-label backtick run parses fine',
      'flowchart LR\n    A[' + QUOTE + 'use ' + FENCE + 'code' + FENCE + ' mid' + QUOTE + '] --> B',
      new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
      [BACKTICK],
    ],
    [
      'unquoted edge label',
      'flowchart LR\n    A -->|yes| B',
      /(?:==>|-->|-\.->|--[xo]>|---|->>|-->>)\|"([^"]*\|[^"]*)"\|/,
      ['|'],
    ],
    [
      'pipe-free quoted edge label',
      'flowchart LR\n    A -->|' + QUOTE + 'yes' + QUOTE + '| B',
      /(?:==>|-->|-\.->|--[xo]>|---|->>|-->>)\|"([^"]*\|[^"]*)"\|/,
      ['|'],
    ],
  ];

  cases.forEach(([label, definition, detects, escapes]) => {
    it('leaves legal syntax alone: ' + label, () => {
      const unregister = registerPreprocessor((d: string) => d, {
        name: 'test-fp-' + label.replace(/\W+/g, '-'),
        priority: 9000,
        detects,
        escapes,
      });

      try {
        const result = preprocessDefinition(definition, 'flowchart');
        expect(result).not.toContain(BACKTICK_ENTITY);
        expect(result).not.toContain(PIPE_ENTITY);
        expect(__getPostconditionViolations()).toHaveLength(0);
      } finally {
        unregister();
      }
    });
  });
});

describe('opt-in behaviour', () => {
  it('leaves passes without a declared postcondition completely untouched', () => {
    const unregister = registerPreprocessor(
      (d: string) => d + '\n%% touched',
      { name: 'test-no-postcondition', priority: 9000 },
    );

    try {
      const result = preprocessDefinition('flowchart LR\n    A --> B', 'flowchart');
      expect(result).toContain('%% touched');
      expect(__getPostconditionViolations()).toHaveLength(0);
    } finally {
      unregister();
    }
  });

  it('does not let a throwing pass break the chain', () => {
    const unregister = registerPreprocessor(
      () => {
        throw new Error('boom');
      },
      {
        name: 'test-throwing',
        priority: 9000,
        detects: new RegExp(QUOTE + '(' + BACKTICK + '{2,})'),
        escapes: [BACKTICK],
      },
    );

    try {
      const definition =
        'flowchart LR\n    A[' + QUOTE + FENCE + 'x' + QUOTE + '] --> B';
      // A pass that throws still leaves the offending shape in place, so the
      // repair layer is the last line of defence and must still fire.
      const result = preprocessDefinition(definition, 'flowchart');
      expect(result).not.toContain(QUOTE + FENCE);
    } finally {
      unregister();
    }
  });
});

describe('the real registered passes satisfy their own postconditions', () => {
  // End-to-end guard: if either shipped pass regresses to a no-op, the
  // violation list names it. This is the alarm the original bug lacked.
  it('label-backtick-escape eliminates the shape it declares', () => {
    const result = preprocessDefinition(
      'flowchart LR\n    A[' + QUOTE + FENCE + 'html-mockup' + QUOTE + '] --> B{' +
        QUOTE + 'variant?' + QUOTE + '}',
      'flowchart',
    );
    expect(result).not.toContain(QUOTE + FENCE);
    expect(__getPostconditionViolations()).not.toContain('label-backtick-escape');
  });

  it('edge-label-pipe-escape eliminates the shape it declares', () => {
    const result = preprocessDefinition(
      'flowchart LR\n    A -->|' + QUOTE + '|phase| shift' + QUOTE + '| B',
      'flowchart',
    );
    expect(result).toContain(PIPE_ENTITY);
    expect(__getPostconditionViolations()).not.toContain('edge-label-pipe-escape');
  });
});
