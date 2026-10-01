/**
 * The kind of a capability -- skill or MCP server -- as one mark used
 * everywhere the two share a surface: the board card, the detail drawer,
 * the palette row, the Find hit.  Skills and servers mix in every lane and
 * every search column, so the mark must be read at a glance and must not
 * depend on colour alone: a glyph, a colour, and the word.
 *
 * The colours are chosen away from every other channel on the bench --
 * the provenance spine (grey / teal / berry / amber), the ✦ pickup purple,
 * the teal selection ring and the orange warnings -- so a card's left edge
 * still says WHERE it came from and this chip says WHAT it is.
 */
import React from 'react';
import type { CapabilityKind } from '../../utils/activation';

export const KIND_COLOR: Record<string, string> = { skill: '#2563eb', mcp: '#65a30d' };
const KIND_GLYPH: Record<string, string> = { skill: '◆', mcp: '⚙' };
const KIND_WORD: Record<string, string> = { skill: 'skill', mcp: 'MCP' };
const KIND_TITLE: Record<string, string> = {
    skill: 'Skill: prompt text loaded into the conversation',
    mcp: 'MCP server: tools the model can call',
};

export const kindColor = (k: CapabilityKind | string): string => KIND_COLOR[k] ?? '#6b7280';

export const KindMark: React.FC<{ kind: CapabilityKind | string; compact?: boolean }> = ({ kind, compact = false }) => {
    const color = kindColor(kind);
    return (
        <span data-testid="kind-mark" data-kind={kind} title={KIND_TITLE[kind] ?? kind}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 3, flexShrink: 0, position: 'relative',
                       fontSize: 10, lineHeight: 1, fontWeight: 600, letterSpacing: '.02em',
                       padding: compact ? '1px 4px' : '2px 6px', borderRadius: 8,
                       color, background: `${color}26`, border: `1px solid ${color}66` }}>
            <span aria-hidden="true">{KIND_GLYPH[kind] ?? '•'}</span>
            {!compact && <span>{KIND_WORD[kind] ?? kind}</span>}
            {compact && <span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{KIND_WORD[kind] ?? kind}</span>}
        </span>
    );
};

/** A Find column's kind: the MCP registry answers with servers; every skill
 *  source (folder, git, aim) answers with skills. */
export const columnKind = (sourceKind: string): CapabilityKind => (sourceKind === 'mcp' ? 'mcp' : 'skill');
