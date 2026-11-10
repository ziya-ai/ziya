/**
 * SkillsSection — skills UI for the sidebar Skills tab.
 *
 * Activation is the bench's (design/capabilities-hub.md): a card shows its
 * item's effective state and a toggle writes a placement at TAB_LAYER.
 *
 * Displays built-in, custom, and project-discovered skills with:
 *   - Clear explanation of what skills do
 *   - Source badges (built-in / custom / project)
 *   - Expandable prompt preview per skill
 *   - Project-skill onboarding hint when none are discovered
 *   - Import from SKILL.md file
 *   - Inline skill creation form
 */
import React, { useState, useMemo, useRef } from 'react';
import { Input, Button, message, Tooltip } from 'antd';
import {
  PlusOutlined,
  ImportOutlined,
  EditOutlined,
  CodeOutlined,
  BookOutlined,
  FolderOutlined,
   DownOutlined,
  RightOutlined,
  DeleteOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { Skill, SkillCreate, SkillUpdate } from '../types/skill';
import { useTheme } from '../context/ThemeContext';
import type { Layer, PlacementState } from '../utils/activation';
import type { BenchItemView } from '../hooks/useBench';
import { viewAtLens } from '../context/LensContext';
import { parseSkillMd, SKILL_MD_INVALID } from '../utils/skillMd';

/* ------------------------------------------------------------------ */
/*  Source badge                                                      */
/* ------------------------------------------------------------------ */
const sourceBadge = (source?: string, isDarkMode: boolean = true) => {
  const styles: Record<string, { bg: [string, string]; fg: [string, string]; label: string }> = {
    builtin:  { bg: ['#e5e7eb', '#2a2a2a'], fg: ['#6b7280', '#aaa'], label: 'built-in' },
    model_discoverable: { bg: ['#dcfce7', '#1a2e1a'], fg: ['#16a34a', '#4ade80'], label: 'AI-available' },
    project:  { bg: ['#cffafe', '#164e63'], fg: ['#0891b2', '#67e8f9'], label: 'project' },
    user:     { bg: ['#ffedd5', '#3b2410'], fg: ['#ea580c', '#fb923c'], label: 'user' },
    custom:   { bg: ['#fef3c7', '#3b2f1a'], fg: ['#d97706', '#fbbf24'], label: 'custom' },
  };
  const s = styles[source || ''] || styles.custom;
  const bg = isDarkMode ? s.bg[1] : s.bg[0];
  const fg = isDarkMode ? s.fg[1] : s.fg[0];
  return (
    <span style={{
      fontSize: '9px', marginLeft: '6px', padding: '1px 5px', 
      background: bg, borderRadius: '3px', color: fg,
    }}>
      {s.label}
    </span>
  );
};

/* ------------------------------------------------------------------ */
/*  Activation level helpers                                          */
/* ------------------------------------------------------------------ */
export type ActivationLevel = 'active' | 'on-demand' | 'off';

/** The layer this tab edits.  The other layers are the Bench's. */
export const TAB_LAYER: Layer = 'project';

/** Placements are keyed by name, never Skill.id (design: Key). */
export const benchKeyForSkill = (skill: Pick<Skill, 'name'>): string => `skill:${skill.name}`;

const LEVEL_OF_STATE: Record<PlacementState, ActivationLevel> = { always: 'active', ondemand: 'on-demand', off: 'off' };
const STATE_OF_LEVEL: Record<ActivationLevel, PlacementState> = { active: 'always', 'on-demand': 'ondemand', off: 'off' };
const ORIGIN_TEXT: Record<string, string> = {
  default: 'the default', project: 'this project', user: 'your defaults',
  conversation: 'this chat', environment: 'the environment',
};

/**
 * Level a card shows: the bench item's effective state, or, before the bench
 * has answered, the skill's default by visibility.  One mapping for every
 * visibility -- the legacy list read a discoverable skill's membership as OFF
 * and anything else's as ON, so an always-on discoverable skill showed "off".
 */
export function tabLevel(skill: Pick<Skill, 'visibility'>, item?: BenchItemView): ActivationLevel {
  if (item) return LEVEL_OF_STATE[item.effective];
  return skill.visibility === 'model_discoverable' ? 'on-demand' : 'off';
}

/**
 * Placement to write at TAB_LAYER for a chosen level: null (clear the pin)
 * when that level is what the layer would inherit anyway, else the explicit
 * state.  Same rule as dropping a card on its own ghost (laneDnd.dropIntent),
 * so the tab and the Board never disagree about what a pin means.
 */
export function tabPlacementFor(item: BenchItemView, target: ActivationLevel): PlacementState | null {
  const state = STATE_OF_LEVEL[target];
  return viewAtLens(item, TAB_LAYER).inherited === state ? null : state;
}

const levelDot = (level: ActivationLevel, isDarkMode: boolean) => {
  const colors: Record<ActivationLevel, { bg: [string, string]; glow?: string }> = {
    'active':    { bg: ['#16a34a', '#4ade80'], glow: isDarkMode ? '#4ade8066' : '#16a34a44' },
    'on-demand': { bg: ['#2563eb', '#60a5fa'] },
    'off':       { bg: ['#e5e7eb', '#333'] },
  };
  const c = colors[level];
  const bg = isDarkMode ? c.bg[1] : c.bg[0];
  const size = level === 'off' ? '5px' : '7px';
  return (
    <span style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      background: bg, display: 'inline-block',
      border: level === 'off' ? `1px solid ${isDarkMode ? '#555' : '#d1d5db'}` : 'none',
      boxShadow: c.glow ? `0 0 4px ${c.glow}` : 'none',
    }} />
  );
};

const levelLabel = (level: ActivationLevel, isDarkMode: boolean) => {
  const labels: Record<ActivationLevel, { text: string; color: [string, string] }> = {
    'active':    { text: 'always on',  color: ['#16a34a', '#4ade80'] },
    'on-demand': { text: 'on-demand',  color: ['#2563eb', '#60a5fa'] },
    'off':       { text: 'off',        color: ['#9ca3af', '#555'] },
  };
  const l = labels[level];
  return (
    <span style={{ fontSize: '9px', color: isDarkMode ? l.color[1] : l.color[0], whiteSpace: 'nowrap' }}>
      {l.text}
    </span>
  );
};

/* ------------------------------------------------------------------ */
/*  Props                                                             */
/* ------------------------------------------------------------------ */
interface Props {
  skills: Skill[];
  /** The bench view-model's items (useBench().items); skills are looked up by key. */
  benchItems: BenchItemView[];
  benchReady: boolean;
  /** Write a placement at TAB_LAYER; the caller's bench.place announces it. */
  onPlace: (key: string, state: PlacementState | null) => Promise<unknown> | void;
  createSkill: (data: SkillCreate) => Promise<Skill>;
  updateSkill: (id: string, updates: SkillUpdate) => Promise<void>;
  deleteSkill: (id: string) => Promise<void>;
  searchQuery: string;
}

export const SkillsSection: React.FC<Props> = ({
  skills, benchItems, benchReady, onPlace,
  createSkill, updateSkill, deleteSkill, searchQuery,
}) => {
  const { isDarkMode } = useTheme();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<{ name: string; description: string; prompt: string }>({ name: '', description: '', prompt: '' });
  const [isCreating, setIsCreating] = useState(false);
  const [newSkill, setNewSkill] = useState({ name: '', description: '', prompt: '' });
  const fileInputRef = useRef<HTMLInputElement>(null);

  /* ---- filter ---- */
  const filtered = useMemo(() =>
    skills.filter(s =>
      s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (s.keywords || []).some(k => k.toLowerCase().includes(searchQuery.toLowerCase()))
    ),
    [skills, searchQuery]
  );

  /* ---- group by source: user stuff first, then built-in ---- */
  const userSkills   = filtered.filter(s => s.source === 'custom' || s.source === 'project' || s.source === 'user' || (!s.source && !s.isBuiltIn));
  const builtinSkills = filtered.filter(s => s.source === 'builtin' || s.isBuiltIn);

  /* ---- bench lookup ---- */
  const itemFor = (skill: Skill): BenchItemView | undefined =>
    benchItems.find(i => i.kind === 'skill' && i.key === benchKeyForSkill(skill));
  const levelFor = (skill: Skill) => tabLevel(skill, itemFor(skill));

  /* ---- sort within groups: active first, then on-demand, then off ---- */
  const levelOrder: Record<ActivationLevel, number> = { 'active': 0, 'on-demand': 1, 'off': 2 };
  const sortByLevel = (a: Skill, b: Skill) => levelOrder[levelFor(a)] - levelOrder[levelFor(b)];
  const sortedUser = [...userSkills].sort(sortByLevel);
  const sortedBuiltin = [...builtinSkills].sort(sortByLevel);

  /* ---- handlers ---- */
  // Writes TAB_LAYER through the bench.  place() announces the change, so
  // every other useBench() instance (the chip bar above this tab) re-fetches
  // on its own; nothing here needs to know they exist.
  const setLevel = (skill: Skill, target: ActivationLevel) => {
    const item = itemFor(skill);
    if (!benchReady || !item || tabLevel(skill, item) === target) return;
    Promise.resolve(onPlace(item.key, tabPlacementFor(item, target)))
      .catch(err => {
        console.error(`Failed to place ${item.key}:`, err);
        message.error(`Could not change ${skill.name}`);
      });
  };

  const handleCreate = async () => {
    if (!newSkill.name.trim() || !newSkill.prompt.trim()) {
      message.error('Name and prompt are required');
      return;
    }
    try {
      await createSkill(newSkill);
      message.success(`Skill "${newSkill.name}" created`);
      setNewSkill({ name: '', description: '', prompt: '' });
      setIsCreating(false);
    } catch { message.error('Failed to create skill'); }
  };

  const startEditing = (skill: Skill) => {
    setEditingId(skill.id);
    setEditValues({
      name: skill.name,
      description: skill.description,
      prompt: skill.prompt,
    });
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditValues({ name: '', description: '', prompt: '' });
  };

  const handleSaveEdit = async (skillId: string) => {
    if (!editValues.name.trim() || !editValues.prompt.trim()) {
      message.error('Name and prompt are required');
      return;
    }
    try {
      await updateSkill(skillId, {
        name: editValues.name,
        description: editValues.description,
        prompt: editValues.prompt,
      });
      message.success(`Skill "${editValues.name}" updated`);
      setEditingId(null);
      setEditValues({ name: '', description: '', prompt: '' });
    } catch (err: any) {
      message.error(err?.message || 'Failed to update skill');
    }
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = parseSkillMd(text);
      if (!parsed) {
        message.error(SKILL_MD_INVALID);
        return;
      }
      await createSkill(parsed);
      message.success(`Imported skill "${parsed.name}"`);
    } catch {
      message.error('Failed to import skill');
    } finally {
      e.target.value = '';
    }
  };

  const handleDelete = async (id: string, name: string) => {
    try {
      await deleteSkill(id);
      message.success(`Deleted "${name}"`);
    } catch (err: any) {
      message.error(err?.message || 'Cannot delete this skill');
    }
  };

  /* ---- theme ---- */
  const t = {
    cardBg:        isDarkMode ? '#1a1a1a' : '#ffffff',
    cardBorder:    isDarkMode ? '#1a1a1a' : '#f3f4f6',
    cardHover:     isDarkMode ? '#222'    : '#f9fafb',
    textPrimary:   isDarkMode ? '#e5e7eb' : '#1f2937',
    textSecondary: isDarkMode ? '#777'    : '#6b7280',
    textMuted:     isDarkMode ? '#555'    : '#9ca3af',
    textFaint:     isDarkMode ? '#666'    : '#d1d5db',
    keywordBg:     isDarkMode ? '#2a2a2a' : '#f3f4f6',
    keywordFg:     isDarkMode ? '#888'    : '#6b7280',
    expandBg:      isDarkMode ? '#151515' : '#fafafa',
    expandBorder:  isDarkMode ? '#252525' : '#f3f4f6',
    expandText:    isDarkMode ? '#aaa'    : '#6b7280',
    promptBg:      isDarkMode ? '#0d0d0d' : '#f3f4f6',
    promptFg:      isDarkMode ? '#888'    : '#4b5563',
    headerColor:   isDarkMode ? '#666'    : '#6b7280',
    sectionBg:     isDarkMode ? '#111'    : '#f9fafb',
    sectionBorder: isDarkMode ? '#333'    : '#e5e7eb',
    sectionText:   isDarkMode ? '#666'    : '#6b7280',
    accentCyan:    isDarkMode ? '#67e8f9' : '#0891b2',
    accentCyanBg:  isDarkMode ? '#1a2e3a' : '#cffafe',
    formBg:        isDarkMode ? '#1a1a1a' : '#ffffff',
    formBorder:    isDarkMode ? '#333'    : '#d1d5db',
    // Level selector
    segBg:         isDarkMode ? '#111'    : '#ffffff',
    segBorder:     isDarkMode ? '#333'    : '#e5e7eb',
    segText:       isDarkMode ? '#999'    : '#9ca3af',
    segActiveBg:   isDarkMode ? '#1a2e1a' : '#f0fdf4',
    segActiveFg:   isDarkMode ? '#4ade80' : '#16a34a',
    segDemandBg:   isDarkMode ? '#1a2636' : '#eff6ff',
    segDemandFg:   isDarkMode ? '#60a5fa' : '#2563eb',
    segOffBg:      isDarkMode ? '#1a1a1a' : '#ffffff',
    segOffFg:      isDarkMode ? '#666'    : '#6b7280',
  };

  /* ---- render a single skill card ---- */
  const renderCard = (skill: Skill) => {
    const item = itemFor(skill);
    const level = tabLevel(skill, item);
    const isExpanded = expandedId === skill.id;
    const isEditing = editingId === skill.id;
    const isOff = level === 'off';
    // Set at another layer (your defaults, this chat): drawn the way the
    // Board draws an inherited card, so the tab never claims a pin it lacks.
    const inheritedElsewhere = !!item && item.origin !== 'default' && item.origin !== TAB_LAYER;

    return (
      <div
        key={skill.id}
        style={{
          background: t.cardBg, borderRadius: '6px', marginBottom: '3px',
          overflow: 'hidden',
          border: isExpanded
            ? `1px solid ${t.expandBorder}`
            : `1px ${inheritedElsewhere ? 'dashed' : 'solid'} ${inheritedElsewhere ? t.textFaint : t.cardBorder}`,
        }}
      >
        {/* Compact row */}
        <div
          style={{
            padding: '7px 10px', display: 'flex', alignItems: 'center', gap: '8px',
            cursor: 'pointer',
          }}
          onClick={() => setExpandedId(isExpanded ? null : skill.id)}
        >
          {levelDot(level, isDarkMode)}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', fontSize: '12px', color: t.textPrimary }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {skill.name}
              </span>
              {sourceBadge(skill.source, isDarkMode)}
            </div>
            <div style={{ fontSize: '10px', color: t.textSecondary, marginTop: '1px' }}>
              {skill.description}
            </div>
          </div>
          {isOff && (
            <Button
              type="text"
              size="small"
              disabled={!benchReady}
              onClick={(e) => {
                e.stopPropagation();
                setLevel(skill, skill.visibility === 'model_discoverable' ? 'on-demand' : 'active');
              }}
              style={{
                fontSize: '10px', height: '20px', padding: '0 6px',
                color: t.segDemandFg,
              }}
            >
              Enable
            </Button>
          )}
          {levelLabel(level, isDarkMode)}
          <span style={{
            fontSize: '9px', color: t.textFaint, transition: 'transform 0.15s',
            transform: isExpanded ? 'rotate(90deg)' : 'none', display: 'inline-block',
          }}>›</span>
        </div>

        {/* Expanded detail */}
        {isExpanded && (
          <div style={{
            padding: '10px 12px 12px',
            borderTop: `1px solid ${t.expandBorder}`,
            background: t.expandBg,
          }}>
            {/* Segmented level selector */}
            <div style={{
              display: 'flex', borderRadius: '6px', overflow: 'hidden',
              border: `1px solid ${t.segBorder}`, marginBottom: '10px',
            }}>
              {(skill.visibility === 'model_discoverable'
                ? [
                    { key: 'off' as ActivationLevel,       label: 'Off',        hint: 'disabled',
                      selBg: t.segOffBg,    selFg: t.segOffFg },
                    { key: 'on-demand' as ActivationLevel, label: 'On-demand',  hint: 'AI loads when needed',
                      selBg: t.segDemandBg, selFg: t.segDemandFg },
                    { key: 'active' as ActivationLevel,    label: 'Always on',  hint: 'every message',
                      selBg: t.segActiveBg, selFg: t.segActiveFg },
                  ]
                : [
                    { key: 'off' as ActivationLevel,    label: 'Off',       hint: 'disabled',
                      selBg: t.segOffBg,    selFg: t.segOffFg },
                    { key: 'active' as ActivationLevel, label: 'Always on', hint: 'every message',
                      selBg: t.segActiveBg, selFg: t.segActiveFg },
                  ]
              ).map((opt, i, arr) => {
                const isSel = level === opt.key;
                return (
                  <button
                    key={opt.key}
                    onClick={() => setLevel(skill, opt.key)}
                    disabled={!benchReady}
                    style={{
                      flex: 1, padding: '6px 4px', textAlign: 'center', cursor: 'pointer',
                      fontSize: '10px', border: 'none', display: 'flex', flexDirection: 'column',
                      alignItems: 'center', gap: '1px',
                      background: isSel ? opt.selBg : t.segBg,
                      color: isSel ? opt.selFg : t.segText,
                      borderRight: i < arr.length - 1 ? `1px solid ${t.segBorder}` : 'none',
                    }}
                  >
                    <span style={{ fontSize: '9px', fontWeight: 600 }}>{opt.label}</span>
                    <span style={{ fontSize: '8px', opacity: 0.6 }}>{opt.hint}</span>
                  </button>
                );
              })}
            </div>

            {item && (
              <div data-testid={`skill-effective-${skill.name}`}
                   style={{ fontSize: '10px', color: t.expandText, marginBottom: '8px' }}>
                Effective here: <b>{level === 'active' ? 'always on' : level}</b>
                {' — from '}{ORIGIN_TEXT[item.origin] ?? item.origin}
              </div>
            )}

            {/* Keywords */}
            {skill.keywords && skill.keywords.length > 0 && (
              <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap', marginBottom: '6px' }}>
                {skill.keywords.slice(0, 6).map(kw => (
                  <span key={kw} style={{
                    fontSize: '8px', padding: '0 4px', borderRadius: '2px',
                    background: t.keywordBg, color: t.keywordFg,
                  }}>
                    {kw}
                  </span>
                ))}
              </div>
            )}

            {/* Metadata row */}
            <div style={{ display: 'flex', gap: '10px', marginBottom: '6px', flexWrap: 'wrap', fontSize: '11px', color: t.expandText }}>
              {skill.source === 'project' && skill.skillPath && (
                <Tooltip title={skill.skillPath}>
                  <span style={{ color: t.accentCyan }}><FolderOutlined /> {skill.skillPath.split('/').slice(-2).join('/')}</span>
                </Tooltip>
              )}
              {skill.hasScripts && <span><CodeOutlined /> scripts</span>}
              {skill.hasReferences && <span><BookOutlined /> references</span>}
              {skill.hasAssets && <span><FolderOutlined /> assets</span>}
            </div>

            {/* Prompt preview */}
            {isEditing ? (
              /* ---- Inline edit form ---- */
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <Input
                  size="small"
                  placeholder="Skill name..."
                  value={editValues.name}
                  onChange={e => setEditValues(p => ({ ...p, name: e.target.value }))}
                  autoFocus
                />
                <Input
                  size="small"
                  placeholder="Short description..."
                  value={editValues.description}
                  onChange={e => setEditValues(p => ({ ...p, description: e.target.value }))}
                />
                <Input.TextArea
                  placeholder="Instructions for the AI..."
                  value={editValues.prompt}
                  onChange={e => setEditValues(p => ({ ...p, prompt: e.target.value }))}
                  rows={8}
                  style={{ fontFamily: 'monospace', fontSize: '11px' }}
                />
                <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
                  <Button size="small" onClick={cancelEditing}>
                    Cancel
                  </Button>
                  <Button size="small" type="primary" onClick={() => handleSaveEdit(skill.id)}>
                    Save
                  </Button>
                </div>
              </div>
            ) : (
              /* ---- Read-only prompt preview ---- */
              <div style={{
                background: t.promptBg, borderRadius: '4px', padding: '8px',
                maxHeight: '100px', overflowY: 'auto',
                fontFamily: 'monospace', fontSize: '10px', lineHeight: '1.4',
                whiteSpace: 'pre-wrap', color: t.promptFg,
              }}>
                {skill.prompt || '(prompt loaded on activation)'}
              </div>
            )}

            {/* Footer meta + actions */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '6px' }}>
              <span style={{ fontSize: '9px', color: t.textMuted }}>
                {skill.tokenCount.toLocaleString()} tokens · {skill.source || 'custom'}
              </span>
              {skill.source === 'custom' && !isEditing && (
                <div style={{ display: 'flex', gap: '4px' }}>
                  <Button
                    type="text" size="small"
                    icon={<EditOutlined />}
                    onClick={() => startEditing(skill)}
                    style={{ fontSize: '11px' }}
                  >
                    Edit
                  </Button>
                  <Button
                    type="text" size="small" danger
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(skill.id, skill.name)}
                    style={{ fontSize: '11px' }}
                  >
                    Delete
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  };

  /* ================================================================ */
  return (
    <>
      {/* Explanatory hint */}
      <div style={{
        fontSize: '10px', color: t.textMuted, padding: '0 4px 8px',
        lineHeight: '1.4',
      }}>
        Skills add instructions to every message when active.
        Toggle a skill to shape how the AI responds.
      </div>

      {/* Custom + project skills */}
      {sortedUser.length > 0 && sortedUser.map(renderCard)}

      {/* Project skills status — always shown so user knows the feature exists */}
      {!filtered.some(s => s.source === 'project') && !searchQuery && (
        <div style={{
          margin: '8px 0', padding: '10px 12px', borderRadius: '6px', 
          background: t.sectionBg, border: `1px dashed ${t.sectionBorder}`,
          fontSize: '11px', color: t.sectionText, lineHeight: '1.5',
        }}>
          <strong style={{ color: t.textSecondary }}>Project skills</strong><br />
          No <code style={{ color: t.accentCyan, background: t.accentCyanBg, padding: '1px 4px', borderRadius: '3px' }}>
            SKILL.md
          </code> files found. Place them in your project to auto-discover:
          <pre style={{
            margin: '6px 0 0', padding: '6px 8px', borderRadius: '4px', 
            background: isDarkMode ? '#0a0a0a' : '#f3f4f6', color: t.textSecondary, fontSize: '10px',
            lineHeight: '1.4', overflow: 'auto',
          }}>{`.agents/skills/my-skill/SKILL.md`}</pre>
          <div style={{ marginTop: '4px', color: t.textMuted }}>
            <a
              href="https://agentskills.io/specification"
              target="_blank" rel="noopener noreferrer"
              style={{ color: t.accentCyan, textDecoration: 'none' }}
            >agentskills.io spec</a>
          </div>
        </div>
      )}

      {/* Built-in skills */}
      {sortedBuiltin.length > 0 && (
        <>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '8px',
            padding: '8px 0 4px',
          }}>
            <span style={{ fontSize: '9px', color: t.headerColor, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              Built-in
            </span>
            <div style={{ flex: 1, height: '1px', background: t.expandBorder }} />
          </div>
          {sortedBuiltin.map(renderCard)}
        </>
      )}

      {/* Empty state */}
      {filtered.length === 0 && !searchQuery && (
        <div style={{ padding: '16px', textAlign: 'center', color: t.textMuted, fontSize: '11px' }}>
          No skills available
        </div>
      )}

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: '6px', padding: '6px 0' }}>
        <Button
          size="small" icon={<PlusOutlined />}
          onClick={() => setIsCreating(true)}
          style={{ flex: 1 }}
        >
          New skill
        </Button>
        <Button
          size="small" icon={<ImportOutlined />}
          onClick={() => fileInputRef.current?.click()}
          style={{ flex: 1 }}
        >
          Import SKILL.md
        </Button>
        <input
          ref={fileInputRef} type="file" accept=".md"
          style={{ display: 'none' }}
          onChange={handleImport}
        />
      </div>

      {/* Inline creation form */}
      {isCreating && (
        <div style={{
          padding: '12px', background: t.formBg, border: `1px solid ${t.formBorder}`,
          borderRadius: '8px', marginTop: '6px',
        }}>
          <Input
            placeholder="Skill name..."
            value={newSkill.name}
            onChange={e => setNewSkill(p => ({ ...p, name: e.target.value }))}
            style={{ marginBottom: '6px' }} autoFocus
          />
          <Input
            placeholder="Short description (when to use this skill)..."
            value={newSkill.description}
            onChange={e => setNewSkill(p => ({ ...p, description: e.target.value }))}
            style={{ marginBottom: '6px' }}
          />
          <Input.TextArea
            placeholder="Instructions for the AI when this skill is active..."
            value={newSkill.prompt}
            onChange={e => setNewSkill(p => ({ ...p, prompt: e.target.value }))}
            rows={5} style={{ marginBottom: '8px', fontFamily: 'monospace', fontSize: '12px' }}
          />
          <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
            <Button size="small" onClick={() => { setIsCreating(false); setNewSkill({ name: '', description: '', prompt: '' }); }}>
              Cancel
            </Button>
            <Button size="small" type="primary" onClick={handleCreate}>
              Create
            </Button>
          </div>
        </div>
      )}
    </>
  );
};
