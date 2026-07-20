/**
 * Tests for the currency-format rewriter in vegaLitePlugin.
 *
 * d3-format only accepts `$` as its currency placeholder. Literal symbols
 * such as `£` in a format string ("£,.0f") throw `invalid format: £,.0f`,
 * which aborts the Vega view build and surfaces as the misleading
 * "Cannot read properties of undefined (reading 'marktype')" error.
 *
 * rewriteCurrencyFormats() swaps the literal symbol for `$` (and reports it
 * so a matching formatLocale can restore the glyph in rendered output).
 */
import {
  rewriteCurrencyFormats,
  buildCurrencyLocale,
  CURRENCY_SYMBOL_RE,
} from '../vegaLitePlugin';

describe('rewriteCurrencyFormats', () => {
  it('rewrites a top-level axis format "£,.0f" to "$,.0f" and reports £', () => {
    const spec: any = {
      encoding: { y: { axis: { format: '£,.0f' } } },
    };
    const symbol = rewriteCurrencyFormats(spec);
    expect(symbol).toBe('£');
    expect(spec.encoding.y.axis.format).toBe('$,.0f');
  });

  it('rewrites every format string across nested layers (the reported bug)', () => {
    const spec: any = {
      layer: [
        { encoding: { y: { axis: { format: '£,.0f' } } } },
        { encoding: { text: { format: '£,.0f' } } },
        {
          encoding: {
            tooltip: [{ field: 'modern', format: '£,.2f' }],
          },
        },
      ],
    };
    const symbol = rewriteCurrencyFormats(spec);
    expect(symbol).toBe('£');
    expect(spec.layer[0].encoding.y.axis.format).toBe('$,.0f');
    expect(spec.layer[1].encoding.text.format).toBe('$,.0f');
    expect(spec.layer[2].encoding.tooltip[0].format).toBe('$,.2f');
  });

  it('handles multiple occurrences of the symbol within one format string', () => {
    const spec: any = { encoding: { y: { axis: { format: '£,.0f (£)' } } } };
    const symbol = rewriteCurrencyFormats(spec);
    expect(symbol).toBe('£');
    expect(spec.encoding.y.axis.format).toBe('$,.0f ($)');
  });

  it('supports other currency symbols (€, ¥, ₹)', () => {
    for (const sym of ['€', '¥', '₹']) {
      const spec: any = { encoding: { y: { axis: { format: `${sym},.0f` } } } };
      expect(rewriteCurrencyFormats(spec)).toBe(sym);
      expect(spec.encoding.y.axis.format).toBe('$,.0f');
    }
  });

  it('leaves plain d3 formats untouched and reports null', () => {
    const spec: any = { encoding: { y: { axis: { format: '$,.0f' } } } };
    expect(rewriteCurrencyFormats(spec)).toBeNull();
    expect(spec.encoding.y.axis.format).toBe('$,.0f');
  });

  it('does not rewrite time/utc format strings', () => {
    const spec: any = {
      encoding: { x: { axis: { format: '%Y £', formatType: 'time' } } },
    };
    expect(rewriteCurrencyFormats(spec)).toBeNull();
    expect(spec.encoding.x.axis.format).toBe('%Y £');
  });

  it('returns null for specs with no format strings', () => {
    const spec: any = { mark: 'bar', encoding: { x: { field: 'a' } } };
    expect(rewriteCurrencyFormats(spec)).toBeNull();
  });

  it('tolerates null / non-object input', () => {
    expect(rewriteCurrencyFormats(null)).toBeNull();
    expect(rewriteCurrencyFormats(undefined)).toBeNull();
    expect(rewriteCurrencyFormats(42 as any)).toBeNull();
  });
});

describe('CURRENCY_SYMBOL_RE', () => {
  it('matches supported currency glyphs', () => {
    for (const sym of ['£', '€', '¥', '₹', '₩', '₪', '₫', '฿']) {
      expect(CURRENCY_SYMBOL_RE.test(`${sym},.0f`)).toBe(true);
    }
  });

  it('does not match the d3 `$` placeholder or plain formats', () => {
    expect(CURRENCY_SYMBOL_RE.test('$,.0f')).toBe(false);
    expect(CURRENCY_SYMBOL_RE.test(',.2f')).toBe(false);
    expect(CURRENCY_SYMBOL_RE.test('%Y-%m-%d')).toBe(false);
  });
});

describe('buildCurrencyLocale', () => {
  it('produces a d3 formatLocale whose currency prefix is the given symbol', () => {
    const locale = buildCurrencyLocale('£');
    expect(locale.currency).toEqual(['£', '']);
    expect(locale.decimal).toBe('.');
    expect(locale.thousands).toBe(',');
    expect(locale.grouping).toEqual([3]);
  });

  it('round-trips: rewritten format + locale symbol reproduce the intent', () => {
    const spec: any = { encoding: { y: { axis: { format: '£,.0f' } } } };
    const symbol = rewriteCurrencyFormats(spec)!;
    const locale = buildCurrencyLocale(symbol);
    expect(spec.encoding.y.axis.format).toBe('$,.0f');
    expect(locale.currency).toEqual(['£', '']);
  });
});
