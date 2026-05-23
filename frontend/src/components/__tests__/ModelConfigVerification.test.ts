/**
 * Tests for the model settings verification logic extracted from
 * ModelConfigButton.tsx: `isSupported`, `isClose`, and the `settingsMatch`
 * comparison that gates whether a save is accepted or rejected.
 *
 * These are logic-only tests (no React context needed) modelled after the
 * MessageActions test pattern in this directory.
 */

// ---------------------------------------------------------------------------
// Pure re-implementations of the production helpers (kept in sync manually)
// ---------------------------------------------------------------------------

type Capabilities = {
  temperature_range?: [number, number] | null;
  top_k_range?: [number, number] | null;
  supports_thinking_level?: boolean;
  supports_adaptive_thinking?: boolean;
  supported_parameters?: string[];
};

const makeIsSupported = (capabilities: Capabilities) => {
  const supportedParams = capabilities.supported_parameters ?? [];
  return (param: string): boolean => {
    switch (param) {
      case 'temperature':
        return capabilities.temperature_range != null;
      case 'top_k':
        return capabilities.top_k_range != null || supportedParams.includes('top_k');
      case 'thinking_level':
        return capabilities.supports_thinking_level ?? false;
      case 'thinking_effort':
        return capabilities.supports_adaptive_thinking ?? false;
      case 'thinking_mode':
      case 'max_output_tokens':
      case 'max_input_tokens':
        return true;
      default:
        return supportedParams.includes(param);
    }
  };
};

const isClose = (a: number, b: number, tolerance = 0.001) =>
  Math.abs(a - b) <= tolerance;

type Settings = Record<string, any>;

const buildSettingsMatch = (
  isSupported: (p: string) => boolean,
  normalizedExpected: Settings,
  normalizedActual: Settings,
) => ({
  temperature:
    isSupported('temperature') && normalizedExpected.temperature !== undefined
      ? isClose(normalizedActual.temperature, normalizedExpected.temperature)
      : true,
  top_k:
    isSupported('top_k') && normalizedExpected.top_k !== undefined
      ? normalizedActual.top_k === normalizedExpected.top_k
      : true,
  max_output_tokens:
    isSupported('max_output_tokens') && normalizedExpected.max_output_tokens !== undefined
      ? normalizedActual.max_output_tokens === normalizedExpected.max_output_tokens
      : true,
  thinking_mode:
    isSupported('thinking_mode') && normalizedExpected.thinking_mode !== undefined
      ? normalizedActual.thinking_mode === normalizedExpected.thinking_mode
      : true,
  thinking_level:
    isSupported('thinking_level') && normalizedExpected.thinking_level !== undefined
      ? normalizedActual.thinking_level === normalizedExpected.thinking_level
      : true,
  thinking_effort:
    isSupported('thinking_effort') && normalizedExpected.thinking_effort !== undefined
      ? normalizedActual.thinking_effort === normalizedExpected.thinking_effort
      : true,
});

// ---------------------------------------------------------------------------
// isSupported
// ---------------------------------------------------------------------------

describe('isSupported', () => {
  describe('temperature', () => {
    it('returns true when temperature_range is present', () => {
      const isSupported = makeIsSupported({ temperature_range: [0, 1] });
      expect(isSupported('temperature')).toBe(true);
    });

    it('returns false when temperature_range is null', () => {
      const isSupported = makeIsSupported({ temperature_range: null });
      expect(isSupported('temperature')).toBe(false);
    });

    it('returns false when temperature_range is absent', () => {
      const isSupported = makeIsSupported({});
      expect(isSupported('temperature')).toBe(false);
    });
  });

  describe('top_k', () => {
    it('returns true when top_k_range is present', () => {
      const isSupported = makeIsSupported({ top_k_range: [1, 100] });
      expect(isSupported('top_k')).toBe(true);
    });

    it('returns true when top_k is listed in supported_parameters', () => {
      const isSupported = makeIsSupported({ supported_parameters: ['top_k'] });
      expect(isSupported('top_k')).toBe(true);
    });

    it('returns false when neither top_k_range nor supported_parameters lists it', () => {
      const isSupported = makeIsSupported({ supported_parameters: [] });
      expect(isSupported('top_k')).toBe(false);
    });
  });

  describe('thinking_level', () => {
    it('returns true when supports_thinking_level is true', () => {
      const isSupported = makeIsSupported({ supports_thinking_level: true });
      expect(isSupported('thinking_level')).toBe(true);
    });

    it('returns false when supports_thinking_level is false', () => {
      const isSupported = makeIsSupported({ supports_thinking_level: false });
      expect(isSupported('thinking_level')).toBe(false);
    });

    it('returns false when supports_thinking_level is absent', () => {
      const isSupported = makeIsSupported({});
      expect(isSupported('thinking_level')).toBe(false);
    });
  });

  describe('thinking_effort', () => {
    it('returns true when supports_adaptive_thinking is true', () => {
      const isSupported = makeIsSupported({ supports_adaptive_thinking: true });
      expect(isSupported('thinking_effort')).toBe(true);
    });

    it('returns false when supports_adaptive_thinking is false', () => {
      const isSupported = makeIsSupported({ supports_adaptive_thinking: false });
      expect(isSupported('thinking_effort')).toBe(false);
    });

    it('returns false when supports_adaptive_thinking is absent', () => {
      const isSupported = makeIsSupported({});
      expect(isSupported('thinking_effort')).toBe(false);
    });
  });

  describe('always-supported params', () => {
    it('max_output_tokens is always true regardless of capabilities', () => {
      const isSupported = makeIsSupported({});
      expect(isSupported('max_output_tokens')).toBe(true);
    });

    it('max_input_tokens is always true regardless of capabilities', () => {
      const isSupported = makeIsSupported({});
      expect(isSupported('max_input_tokens')).toBe(true);
    });

    it('thinking_mode is always true regardless of capabilities', () => {
      const isSupported = makeIsSupported({});
      expect(isSupported('thinking_mode')).toBe(true);
    });
  });

  describe('unknown params fall back to supported_parameters list', () => {
    it('returns true when param is explicitly listed', () => {
      const isSupported = makeIsSupported({ supported_parameters: ['some_custom_param'] });
      expect(isSupported('some_custom_param')).toBe(true);
    });

    it('returns false when param is not listed', () => {
      const isSupported = makeIsSupported({ supported_parameters: [] });
      expect(isSupported('some_custom_param')).toBe(false);
    });
  });
});

// ---------------------------------------------------------------------------
// isClose
// ---------------------------------------------------------------------------

describe('isClose', () => {
  it('returns true for identical values', () => {
    expect(isClose(0.3, 0.3)).toBe(true);
  });

  it('returns true when difference is within default tolerance', () => {
    expect(isClose(0.3, 0.3009)).toBe(true);
  });

  it('returns false when difference exceeds default tolerance', () => {
    expect(isClose(0.3, 0.302)).toBe(false);
  });

  it('returns false when one operand is undefined (NaN guard)', () => {
    // This is the bug that was fixed: isClose(0.3, undefined) must not
    // silently pass. The settingsMatch layer now guards with !== undefined
    // before calling isClose, but we also verify the raw behaviour.
    expect(isClose(0.3, undefined as any)).toBe(false);
  });

  it('respects custom tolerance', () => {
    expect(isClose(0.3, 0.35, 0.1)).toBe(true);
    expect(isClose(0.3, 0.45, 0.1)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// settingsMatch — the core verification table
// ---------------------------------------------------------------------------

describe('settingsMatch', () => {
  // Capabilities for a model that hides temperature + top_k (e.g. claude opus4.7)
  const opusLikeCaps: Capabilities = {
    supports_adaptive_thinking: true,
  };

  // Capabilities for a model that exposes everything
  const fullCaps: Capabilities = {
    temperature_range: [0, 1],
    top_k_range: [1, 500],
    supports_thinking_level: true,
    supports_adaptive_thinking: true,
  };

  it('passes for a model that hides temperature when temperature is not submitted', () => {
    const isSupported = makeIsSupported(opusLikeCaps);
    const match = buildSettingsMatch(
      isSupported,
      { max_output_tokens: 32000, thinking_mode: true, thinking_effort: 'max' },
      { max_output_tokens: 32000, thinking_mode: true, thinking_effort: 'max' },
    );
    expect(Object.values(match).every(Boolean)).toBe(true);
  });

  it('fails when thinking_effort was submitted but backend returned a different value', () => {
    const isSupported = makeIsSupported(opusLikeCaps);
    const match = buildSettingsMatch(
      isSupported,
      { thinking_effort: 'max' },   // expected
      { thinking_effort: 'medium' }, // actual
    );
    expect(match.thinking_effort).toBe(false);
  });

  it('passes thinking_effort when it matches', () => {
    const isSupported = makeIsSupported(opusLikeCaps);
    const match = buildSettingsMatch(
      isSupported,
      { thinking_effort: 'max' },
      { thinking_effort: 'max' },
    );
    expect(match.thinking_effort).toBe(true);
  });

  it('treats thinking_effort as matching when model does not support adaptive thinking', () => {
    const isSupported = makeIsSupported({});
    const match = buildSettingsMatch(
      isSupported,
      { thinking_effort: 'max' },
      { thinking_effort: 'medium' }, // would be a mismatch, but param not supported
    );
    expect(match.thinking_effort).toBe(true);
  });

  it('temperature comparison uses tolerance for full-caps model', () => {
    const isSupported = makeIsSupported(fullCaps);
    const match = buildSettingsMatch(
      isSupported,
      { temperature: 0.3 },
      { temperature: 0.3001 }, // within tolerance
    );
    expect(match.temperature).toBe(true);
  });

  it('temperature fails when values are clearly different', () => {
    const isSupported = makeIsSupported(fullCaps);
    const match = buildSettingsMatch(
      isSupported,
      { temperature: 0.3 },
      { temperature: 0.5 },
    );
    expect(match.temperature).toBe(false);
  });

  it('temperature is skipped (passes) when model does not support it', () => {
    const isSupported = makeIsSupported(opusLikeCaps);
    const match = buildSettingsMatch(
      isSupported,
      { temperature: 0.3 }, // submitted but model doesn't support it
      { temperature: 0.9 },
    );
    expect(match.temperature).toBe(true);
  });

  it('max_output_tokens mismatch is caught', () => {
    const isSupported = makeIsSupported(fullCaps);
    const match = buildSettingsMatch(
      isSupported,
      { max_output_tokens: 32000 },
      { max_output_tokens: 4096 }, // backend didn't apply
    );
    expect(match.max_output_tokens).toBe(false);
  });
});
