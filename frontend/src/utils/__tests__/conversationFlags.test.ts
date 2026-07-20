import {
  CONVERSATION_FLAG_LABELS,
  CONVERSATION_FLAG_COLORS,
  getFlagLabelDef,
  getFlagColorDef,
} from '../conversationFlags';

describe('CONVERSATION_FLAG_LABELS', () => {
  it('has unique ids', () => {
    const ids = CONVERSATION_FLAG_LABELS.map(f => f.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('every label has a non-empty label and emoji', () => {
    for (const f of CONVERSATION_FLAG_LABELS) {
      expect(f.label.trim().length).toBeGreaterThan(0);
      expect(f.emoji.trim().length).toBeGreaterThan(0);
    }
  });

  // The row indicator (MUIChatHistory) and the "..." Flags menu both
  // iterate this list generically, so a new entry here surfaces in both.
  it('includes the "complete" flag with a green-checkmark glyph', () => {
    const complete = CONVERSATION_FLAG_LABELS.find(f => f.id === 'complete');
    expect(complete).toBeDefined();
    expect(complete!.label).toBe('Complete');
    expect(complete!.emoji).toBe('✅');
  });

  it('resolves the complete flag via getFlagLabelDef', () => {
    expect(getFlagLabelDef('complete')?.emoji).toBe('✅');
  });

  it('returns undefined for an unknown label id', () => {
    expect(getFlagLabelDef('nope')).toBeUndefined();
  });
});

describe('CONVERSATION_FLAG_COLORS', () => {
  it('has unique ids', () => {
    const ids = CONVERSATION_FLAG_COLORS.map(c => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('every color has a valid hex value', () => {
    for (const c of CONVERSATION_FLAG_COLORS) {
      expect(c.hex).toMatch(/^#[0-9a-fA-F]{6}$/);
      expect(c.label.trim().length).toBeGreaterThan(0);
    }
  });

  it('resolves a known color via getFlagColorDef', () => {
    expect(getFlagColorDef('green')?.hex).toBe('#52c41a');
  });

  it('returns undefined for null/undefined/unknown color ids', () => {
    expect(getFlagColorDef(null)).toBeUndefined();
    expect(getFlagColorDef(undefined)).toBeUndefined();
    expect(getFlagColorDef('chartreuse')).toBeUndefined();
  });
});
