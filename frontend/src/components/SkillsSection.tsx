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
} from '@ant-design/icons';
import { Skill, SkillCreate } from '../types/skill';

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
const sourceBadge = (source?: string) => {
  const styles: Record<string, { bg: string; fg: string; label: string }> = {
    builtin:  { bg: '#333',    fg: '#888', label: 'built-in' },
    project:  { bg: '#164e63', fg: '#67e8f9', label: 'project' },
    custom:   { bg: '#3b2f1a', fg: '#fbbf24', label: 'custom' },
  };
  const s = styles[source || ''] || styles.custom;
  return (
    <span style={{
      fontSize: '9px', marginLeft: '6px', padding: '1px 5px',
      background: s.bg, borderRadius: '3px', color: s.fg,
    }}>
      {s.label}
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

  /* ---- group by source ---- */
  const projectSkills = filtered.filter(s => s.source === 'project');
  const builtinSkills = filtered.filter(s => s.source === 'builtin');
  const customSkills  = filtered.filter(s => s.source !== 'project' && s.source !== 'builtin');

  /* ---- handlers ---- */
  const toggle = (id: string) => {
    activeSkillIds.includes(id) ? removeSkillFromLens(id) : addSkillToLens(id);
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
      e.target.value = '';          // reset so same file can be re-imported
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

  /* ---- render a single skill card ---- */
  const renderCard = (skill: Skill) => {
    const isActive = activeSkillIds.includes(skill.id);
    const isExpanded = expandedId === skill.id;

    return (
      <div
        key={skill.id}
        style={{
          background: isActive ? `${skill.color}15` : '#1a1a1a',
          borderLeft: `3px solid ${isActive ? skill.color : '#333'}`,
          borderRadius: '0 6px 6px 0',
          marginBottom: '4px',
          overflow: 'hidden',
        }}
      >
        {/* Main row */}
        <div
          style={{
            padding: '8px 10px',
            display: 'flex', alignItems: 'center', gap: '8px',
            cursor: 'pointer',
          }}
          onClick={() => toggle(skill.id)}
        >
          <input
            type="checkbox" checked={isActive} readOnly
            onClick={e => { e.stopPropagation(); toggle(skill.id); }}
            style={{ accentColor: skill.color }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', fontSize: '12px', fontWeight: isActive ? 500 : 400 }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {skill.name}
              </span>
              {sourceBadge(skill.source)}
            </div>
            <div style={{ fontSize: '10px', color: '#777', marginTop: '2px' }}>
              {skill.description}
            </div>
            {/* Keywords */}
            {skill.keywords && skill.keywords.length > 0 && (
              <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginTop: '3px' }}>
                {skill.keywords.slice(0, 5).map(kw => (
                  <span key={kw} style={{
                    fontSize: '9px', padding: '0 4px', borderRadius: '3px',
                    background: '#252525', color: '#666',
                  }}>
                    {kw}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Right side: tokens + expand */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
            <span style={{ fontSize: '11px', color: isActive ? skill.color : '#666' }}>
              {isActive ? skill.tokenCount.toLocaleString() : `+${skill.tokenCount.toLocaleString()}`}
            </span>
            <span
              style={{ fontSize: '10px', color: '#555', cursor: 'pointer', padding: '2px' }}
              onClick={e => { e.stopPropagation(); setExpandedId(isExpanded ? null : skill.id); }}
            >
              {isExpanded ? <DownOutlined /> : <RightOutlined />}
            </span>
          </div>
        </div>

        {/* Expanded detail */}
        {isExpanded && (
          <div style={{
            padding: '8px 12px 10px 28px',
            borderTop: '1px solid #252525',
            fontSize: '11px', color: '#999',
          }}>
            {/* Metadata row */}
            <div style={{ display: 'flex', gap: '10px', marginBottom: '6px', flexWrap: 'wrap' }}>
              {skill.source === 'project' && skill.skillPath && (
                <Tooltip title={skill.skillPath}>
                  <span style={{ color: '#67e8f9' }}><FolderOutlined /> {skill.skillPath.split('/').slice(-2).join('/')}</span>
                </Tooltip>
              )}
              {skill.hasScripts && <span><CodeOutlined /> scripts</span>}
              {skill.hasReferences && <span><BookOutlined /> references</span>}
              {skill.hasAssets && <span><FolderOutlined /> assets</span>}
            </div>

            {/* Prompt preview */}
            <div style={{
              background: '#111', borderRadius: '4px', padding: '8px',
              maxHeight: '200px', overflowY: 'auto',
              fontFamily: 'monospace', fontSize: '11px', lineHeight: '1.4',
              whiteSpace: 'pre-wrap', color: '#aaa',
            }}>
              {skill.prompt || '(prompt loaded on activation)'}
            </div>

            {/* Actions */}
            {skill.source === 'custom' && (
              <div style={{ marginTop: '6px', textAlign: 'right' }}>
                <Button
                  type="text" size="small" danger
                  icon={<DeleteOutlined />}
                  onClick={() => handleDelete(skill.id, skill.name)}
                >
                  Delete
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  /* ---- render a group header ---- */
  const groupHeader = (label: string, count: number) => (
    <div style={{
      fontSize: '9px', color: '#555', textTransform: 'uppercase',
      letterSpacing: '0.5px', padding: '8px 4px 4px', marginTop: '4px',
    }}>
      {label} ({count})
    </div>
  );

  /* ================================================================ */
  return (
    <>
      {/* Section header */}
      <div style={{
        fontSize: '10px', color: '#666', textTransform: 'uppercase',
        padding: '8px 4px 2px', letterSpacing: '0.5px',
      }}>
        Skills
      </div>
      <div style={{
        fontSize: '10px', color: '#555', padding: '0 4px 8px',
        lineHeight: '1.4',
      }}>
        Skills add instructions to every message when active.
        Toggle a skill to shape how the AI responds.
      </div>

      {/* Project skills */}
      {projectSkills.length > 0 && (
        <>
          {groupHeader('Project', projectSkills.length)}
          {projectSkills.map(renderCard)}
        </>
      )}

      {/* Built-in skills */}
      {builtinSkills.length > 0 && (
        <>
          {groupHeader('Built-in', builtinSkills.length)}
          {builtinSkills.map(renderCard)}
        </>
      )}

      {/* Custom skills */}
      {customSkills.length > 0 && (
        <>
          {groupHeader('Custom', customSkills.length)}
          {customSkills.map(renderCard)}
        </>
      )}

      {/* Empty state */}
      {filtered.length === 0 && !searchQuery && (
        <div style={{ padding: '16px', textAlign: 'center', color: '#555', fontSize: '11px' }}>
          No skills available
        </div>
      )}

      {/* Project skills onboarding */}
      {projectSkills.length === 0 && !searchQuery && (
        <div style={{
          margin: '8px 0', padding: '10px 12px', borderRadius: '6px',
          background: '#111', border: '1px dashed #333',
          fontSize: '11px', color: '#666', lineHeight: '1.5',
        }}>
          <strong style={{ color: '#888' }}>Project skills</strong><br />
          Place <code style={{ color: '#67e8f9', background: '#1a2e3a', padding: '1px 4px', borderRadius: '3px' }}>
            SKILL.md
          </code> files in your project to auto-discover them:
          <pre style={{
            margin: '6px 0 0', padding: '6px 8px', borderRadius: '4px',
            background: '#0a0a0a', color: '#888', fontSize: '10px',
            lineHeight: '1.4', overflow: 'auto',
          }}>{`.agents/skills/
  my-skill/
    SKILL.md      ← YAML frontmatter + markdown
    scripts/      ← optional executables
    references/   ← optional docs
    assets/       ← optional templates`}</pre>
          <div style={{ marginTop: '4px', color: '#555' }}>
            Format: <a
              href="https://agentskills.io/specification"
              target="_blank" rel="noopener noreferrer"
              style={{ color: '#67e8f9', textDecoration: 'none' }}
            >agentskills.io/specification</a>
          </div>
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
          padding: '12px', background: '#1a1a1a', border: '1px solid #333',
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
