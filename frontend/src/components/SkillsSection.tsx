/**
 * SkillsSection — self-contained skills UI for the Contexts tab.
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
  CodeOutlined,
  BookOutlined,
  FolderOutlined,
   DownOutlined,
  RightOutlined,
  DeleteOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { Skill, SkillCreate } from '../types/skill';
import { useTheme } from '../context/ThemeContext';

/* ------------------------------------------------------------------ */
/*  Helper: parse a SKILL.md string into name / description / prompt  */
/* ------------------------------------------------------------------ */
function parseSkillMd(text: string): { name: string; description: string; prompt: string; keywords?: string[] } | null {
  const match = text.match(/^\s*---[ \t]*\n([\s\S]*?)\n---[ \t]*\n([\s\S]*)$/);
  if (!match) return null;

  const frontmatter = match[1];
  const body = match[2].trim();

  const get = (key: string): string => {
    const m = frontmatter.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'));
    return m ? m[1].trim().replace(/^["']|["']$/g, '') : '';
  };

  const name = get('name');
  const description = get('description');
  if (!name || !description) return null;

  const kwRaw = get('keywords');
  const keywords = kwRaw ? kwRaw.split(/[,\s]+/).filter(Boolean) : undefined;

  return { name, description, prompt: body, keywords };
}

/* ------------------------------------------------------------------ */
/*  Source badge                                                      */
/* ------------------------------------------------------------------ */
const sourceBadge = (source?: string, isDarkMode: boolean = true) => {
  const styles: Record<string, { bg: [string, string]; fg: [string, string]; label: string }> = {
    builtin:  { bg: ['#e5e7eb', '#2a2a2a'], fg: ['#6b7280', '#aaa'], label: 'built-in' },
    model_discoverable: { bg: ['#dcfce7', '#1a2e1a'], fg: ['#16a34a', '#4ade80'], label: 'AI-available' },
    project:  { bg: ['#cffafe', '#164e63'], fg: ['#0891b2', '#67e8f9'], label: 'project' },
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
type ActivationLevel = 'active' | 'on-demand' | 'off';

/** Determine the effective activation level for a skill. */
function getLevel(skill: Skill, activeSkillIds: string[]): ActivationLevel {
  const inActive = activeSkillIds.includes(skill.id);
  if (skill.visibility === 'model_discoverable') {
    // Model-discoverable: on-demand by default, user toggles into activeIds to disable
    return inActive ? 'off' : 'on-demand';
  }
  // Everything else: off by default, user toggles into activeIds to enable
  return inActive ? 'active' : 'off';
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
  activeSkillIds: string[];
  addSkillToLens: (id: string) => void;
  removeSkillFromLens: (id: string) => void;
  createSkill: (data: SkillCreate) => Promise<Skill>;
  deleteSkill: (id: string) => Promise<void>;
  searchQuery: string;
}

export const SkillsSection: React.FC<Props> = ({
  skills, activeSkillIds, addSkillToLens, removeSkillFromLens,
  createSkill, deleteSkill, searchQuery,
}) => {
  const { isDarkMode } = useTheme();
  const [expandedId, setExpandedId] = useState<string | null>(null);
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
  const userSkills   = filtered.filter(s => s.source === 'custom' || s.source === 'project' || (!s.source && !s.isBuiltIn));
  const builtinSkills = filtered.filter(s => s.source === 'builtin' || s.isBuiltIn);

  /* ---- sort within groups: active first, then on-demand, then off ---- */
  const levelOrder: Record<ActivationLevel, number> = { 'active': 0, 'on-demand': 1, 'off': 2 };
  const sortByLevel = (a: Skill, b: Skill) =>
    levelOrder[getLevel(a, activeSkillIds)] - levelOrder[getLevel(b, activeSkillIds)];
  const sortedUser = [...userSkills].sort(sortByLevel);
  const sortedBuiltin = [...builtinSkills].sort(sortByLevel);

  /* ---- handlers ---- */
  const cycleLevel = (skill: Skill) => {
    const current = getLevel(skill, activeSkillIds);
    if (skill.visibility === 'model_discoverable') {
      // on-demand (default) → active → off → on-demand
      if (current === 'on-demand') addSkillToLens(skill.id);     // → marks as "active override"
      else if (current === 'off') removeSkillFromLens(skill.id); // → back to on-demand default
      else addSkillToLens(skill.id);                             // active → off (toggle)
    } else {
      // off (default) → active → off
      activeSkillIds.includes(skill.id) ? removeSkillFromLens(skill.id) : addSkillToLens(skill.id);
    }
  };

  const setLevel = (skill: Skill, target: ActivationLevel) => {
    const current = getLevel(skill, activeSkillIds);
    if (current === target) return;

    if (skill.visibility === 'model_discoverable') {
      // Default state is on-demand (NOT in activeSkillIds)
      // In activeSkillIds means user overrode it
      if (target === 'on-demand') {
        // Return to default — remove from activeSkillIds
        if (activeSkillIds.includes(skill.id)) removeSkillFromLens(skill.id);
      } else if (target === 'off') {
        // Disable — add to activeSkillIds as disable marker
        if (!activeSkillIds.includes(skill.id)) addSkillToLens(skill.id);
      } else {
        // 'active' — for now treat same as on-demand (always loaded)
        // Future: track separately for full prompt injection
        if (activeSkillIds.includes(skill.id)) removeSkillFromLens(skill.id);
      }
    } else {
      // Non-discoverable: in activeSkillIds = active, not in = off
      if (target === 'active' || target === 'on-demand') {
        if (!activeSkillIds.includes(skill.id)) addSkillToLens(skill.id);
      } else {
        if (activeSkillIds.includes(skill.id)) removeSkillFromLens(skill.id);
      }
    }
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

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = parseSkillMd(text);
      if (!parsed) {
        message.error('Invalid SKILL.md — needs YAML frontmatter with name and description');
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
    const level = getLevel(skill, activeSkillIds);
    const isExpanded = expandedId === skill.id;
    const isOff = level === 'off';

    return (
      <div
        key={skill.id}
        style={{
          background: t.cardBg, borderRadius: '6px', marginBottom: '3px',
          overflow: 'hidden',
          border: isExpanded ? `1px solid ${t.expandBorder}` : `1px solid ${t.cardBorder}`,
        }}
      >
        {/* Compact row */}
        <div
          style={{
            padding: '7px 10px', display: 'flex', alignItems: 'center', gap: '8px',
            cursor: 'pointer', opacity: isOff ? 0.55 : 1,
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
            <div style={{
              background: t.promptBg, borderRadius: '4px', padding: '8px',
              maxHeight: '100px', overflowY: 'auto',
              fontFamily: 'monospace', fontSize: '10px', lineHeight: '1.4',
              whiteSpace: 'pre-wrap', color: t.promptFg,
            }}>
              {skill.prompt || '(prompt loaded on activation)'}
            </div>

            {/* Footer meta + actions */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '6px' }}>
              <span style={{ fontSize: '9px', color: t.textMuted }}>
                {skill.tokenCount.toLocaleString()} tokens · {skill.source || 'custom'}
              </span>
              {skill.source === 'custom' && (
                <Button
                  type="text" size="small" danger
                  icon={<DeleteOutlined />}
                  onClick={() => handleDelete(skill.id, skill.name)}
                  style={{ fontSize: '11px' }}
                >
                  Delete
                </Button>
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
