/**
 * Regression tests: the flowchart quote-consolidator must not treat bracketed
 * text inside an author-QUOTED NODE LABEL as a nested node definition.
 *
 * Failure this pins: given
 *
 *     A["db.rs:327 full_sync()<br/>db.rs:341 clear(&transaction)"]
 *
 * the consolidator's node-definition regex is lazy and line-wide. It first
 * matched `A["db.rs:327 full_sync(` … `)` (skipped, starts with a quote) and
 * then RESUMED scanning inside the same label, matching `clear(&transaction)`
 * as though `clear` were a node id with content `&transaction`. The `&` made
 * it decide the content "needs quotes", emitting `clear("&transaction")` --
 * quote characters injected INSIDE an already-quoted node label. Mermaid then
 * failed with `Expecting ... got 'STR'` and the render produced an empty SVG.
 *
 * Sibling of mermaidEdgeLabelBracketMasking.test.ts, which pins the same
 * defect for edge labels.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const Q = String.fromCharCode(34);

describe('quoted node labels containing bracketed pseudo-nodes', () => {
  it('preserves a function call with an ampersand argument inside a quoted label', () => {
    const label = 'db.rs:327 full_sync()<br/>db.rs:341 clear(&transaction)';
    const result = preprocessDefinition(
      `flowchart TD\n    A[${Q}${label}${Q}] --> B[${Q}db.rs:351 upsert(...)${Q}]`,
      'flowchart',
    );
    // Positive: the label survives verbatim.
    expect(result).toContain(`A[${Q}${label}${Q}]`);
    // Negative: the specific corruption is gone.
    expect(result).not.toContain(`clear(${Q}&transaction${Q})`);
  });

  it('does not inject quotes for any bracketed content that follows an earlier bracket pair', () => {
    // Each label has TWO bracket groups; the second one is what the lazy regex
    // resumes on. Content chosen so that `needsQuotes` fires (':' '/' '&' '<').
    const cases = [
      'f() g(&x)',
      'a[0] b[k:v]',
      'x() y(1/2)',
      'p() q(<T>)',
      'is_before_min_safe_lead_time()?<br/>range(np, start_gps)',
    ];
    for (const label of cases) {
      const result = preprocessDefinition(
        `flowchart LR\n  N[${Q}${label}${Q}] --> M`,
        'flowchart',
      );
      expect(result).toContain(`N[${Q}${label}${Q}]`);
    }
  });

  it('renders the full failing spec without corrupting any label', () => {
    const spec = [
      'flowchart TD',
      `    A[${Q}db.rs:327 full_sync()<br/>db.rs:341 clear(&transaction)${Q}] --> B[${Q}db.rs:351 upsert(...)${Q}]`,
      `    B --> C[${Q}db.rs:557<br/>if !resource.resource.is_before_min_safe_lead_time()?${Q}]`,
      `    C --> D[${Q}types.rs:125 SqlDbSupportedResource::is_before_min_safe_lead_time<br/>types.rs:142 start_gps = self.time_range()?.start_gps${Q}]`,
      `    D --> E[${Q}halplan_db_types.rs:320-331 HalPlanType::is_before_min_safe_lead_time(np, start_gps)<br/>if start_gps − now <= PMMA_MINIMUM_LEAD → warn + return false${Q}]`,
      `    C -->|false| G[${Q}db.rs:558-560<br/>return Ok(None)${Q}]`,
      '    style E fill:#fdd,stroke:#c00',
    ].join('\n');

    const result = preprocessDefinition(spec, 'flowchart');

    expect(result).toContain('clear(&transaction)');
    expect(result).not.toContain(`clear(${Q}&transaction${Q})`);
    expect(result).toContain('is_before_min_safe_lead_time(np, start_gps)');
    expect(result).toContain('return Ok(None)');
    // Every quoted node label must still be exactly one balanced quote pair:
    // no line may carry an odd number of quotes.
    for (const line of result.split('\n')) {
      expect((line.match(/"/g) ?? []).length % 2).toBe(0);
    }
  });

  it('still processes real unquoted node labels (masking is not a blanket bypass)', () => {
    // Positive control: a pass that simply stopped running would satisfy every
    // assertion above.
    const result = preprocessDefinition(
      `flowchart LR\n  A[ratio: 3/4] --> B[plain]`,
      'flowchart',
    );
    expect(result).toContain(`A[${Q}ratio: 3/4${Q}]`);
  });

  it('leaves doubled-quote labels exactly as before (mask only targets complete single-quoted labels)', () => {
    // `""text""` is not a single complete quoted label, so the mask must not
    // touch it; it falls through to the pre-existing path, which today passes
    // it through unchanged (the startsWith('"') guard returns early).
    const result = preprocessDefinition(
      `flowchart LR\n  A[${Q}${Q}text${Q}${Q}] --> B`,
      'flowchart',
    );
    expect(result).toContain(`A[${Q}${Q}text${Q}${Q}]`);
  });

  it('is idempotent over the quoted label', () => {
    const src = `flowchart LR\n  A[${Q}f()<br/>g(&x)${Q}] --> B`;
    const once = preprocessDefinition(src, 'flowchart');
    const twice = preprocessDefinition(once, 'flowchart');
    expect(twice).toContain(`A[${Q}f()<br/>g(&x)${Q}]`);
  });

  it('applies to round and curly quoted labels too', () => {
    const round = preprocessDefinition(
      `flowchart LR\n  A(${Q}f() g(&x)${Q}) --> B`,
      'flowchart',
    );
    expect(round).toContain(`A(${Q}f() g(&x)${Q})`);
    const curly = preprocessDefinition(
      `flowchart LR\n  A{${Q}f() g(&x)${Q}} --> B`,
      'flowchart',
    );
    expect(curly).toContain(`A{${Q}f() g(&x)${Q}}`);
  });
});
