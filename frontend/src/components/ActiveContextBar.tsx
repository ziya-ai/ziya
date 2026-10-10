/**
 * ActiveContextBar - Shows current active contexts and skills
 */
import React from 'react';
import { useProject } from '../context/ProjectContext';
import { useTheme } from '../context/ThemeContext';
import { useActiveChatOptional } from '../context/ActiveChatContext';
import { useBench, BenchItemView } from '../hooks/useBench';
import { placementForLegacySkill } from '../utils/benchMigration';
import type { Layer, PlacementState } from '../utils/activation';
import { CloseOutlined } from '@ant-design/icons';

const LAYER_ORDER: Layer[] = ['conversation', 'project', 'user'];

/**
 * The placement that takes a skill out of the always-set: written at the
 * layer that put it there (``origin``).  Clearing that pin (null) is enough
 * unless a lower layer also pins ``always`` -- then clearing would just
 * expose it, so the origin layer gets the skill's non-always default.
 * Returns null when no writable layer is responsible (default/environment).
 */
export function unloadPlacement(item: BenchItemView): { layer: Layer; state: PlacementState | null } | null {
  const layer = item.origin as Layer;
  const idx = LAYER_ORDER.indexOf(layer);
  if (idx < 0) return null;
  const below = LAYER_ORDER.slice(idx + 1)
    .map(l => item.placements[l])
    .find(p => p !== null && p !== undefined);
  const fallback = below ?? item.default;
  if (fallback !== 'always') return { layer, state: null };
  return { layer, state: item.default === 'always' ? 'off' : item.default };
}

export const ActiveContextBar: React.FC = () => {
  const { isDarkMode } = useTheme();

  const t = {
    barBg:     isDarkMode ? '#0d0d0d' : '#f9fafb',
    barBorder: isDarkMode ? '#333'    : '#e5e7eb',
    emptyText: isDarkMode ? '#666'    : '#9ca3af',
    tokenText: isDarkMode ? '#888'    : '#6b7280',
    trackBg:   isDarkMode ? '#333'    : '#e5e7eb',
  };

  const {
    contexts,
    skills,
    activeContextIds,
    activeSkillIds,
    additionalFiles,
    removeContextFromLens,
    removeSkillFromLens,
    tokenInfo,
    isCalculatingTokens,
    currentProject,
    skillsProjectId,
  } = useProject();
  
  const activeContexts = contexts.filter(c => activeContextIds.includes(c.id));
  // Skills come from the bench view-model (design/capabilities-hub.md §Problem
  // 1): only ``effective === 'always'`` is active.  The legacy activeSkillIds
  // list carried two meanings by visibility and showed SUPPRESSED discoverable
  // skills as active.  Until the bench has answered, fall back to the legacy
  // list read through its real semantics (never as raw membership).
  const activeChat = useActiveChatOptional();
  const bench = useBench({
    projectId: currentProject?.id,
    conversationId: activeChat?.currentConversationId ?? null,
    legacy: {
      skillIds: activeSkillIds,
      skills,
      skillsLoadedForProject: !!currentProject && skillsProjectId === currentProject.id,
    },
  });
  type SkillRow = { skill: typeof skills[number]; item?: BenchItemView };
  const activeSkills: SkillRow[] = bench.ready
    ? bench.alwaysItems
        .filter(i => i.kind === 'skill')
        .map(i => ({ skill: skills.find(s => s.name === i.name), item: i }))
        .filter((r): r is { skill: typeof skills[number]; item: BenchItemView } => !!r.skill)
    : skills
        .filter(s => activeSkillIds.includes(s.id) && placementForLegacySkill(s, true) === 'always')
        .map(skill => ({ skill }));

  // The chip reflects the bench, so unloading must write the bench.  The
  // legacy list is cleared too so an unmigrated entry cannot re-pin it.
  const unloadSkill = (skillId: string, item?: BenchItemView) => {
    removeSkillFromLens(skillId);
    if (!bench.ready || !item) return;
    const p = unloadPlacement(item);
    if (!p) return;
    Promise.resolve(bench.place(item.key, p.state, p.layer))
      .catch(err => console.error(`Failed to unload skill ${item.key}:`, err));
  };
  // ✦ on-demand skills the model loaded this turn (slice 5).
  const pickedUpSkills = bench.ready
    ? bench.pickedUpItems
        .filter(i => i.kind === 'skill')
        .map(i => skills.find(s => s.name === i.name))
        .filter((s): s is typeof skills[number] => !!s)
    : [];
  const hasAdditionalFiles = additionalFiles.length > 0;
  const hasAnySelection = activeContexts.length > 0 || activeSkills.length > 0
    || pickedUpSkills.length > 0 || hasAdditionalFiles;
  
  if (!hasAnySelection) {
    return (
      <div style={{ 
        padding: '10px 12px', 
        borderBottom: `1px solid ${t.barBorder}`,
        background: t.barBg,
        fontSize: '12px',
        color: t.emptyText,
        fontStyle: 'italic'
      }}>
        No context selected
      </div>
    );
  }
  
  // Calculate token percentages for visual bar
  const totalTokens = tokenInfo?.deduplicatedTokens || 0;
  const contextLimit = 200000; // TODO: Get from model config
  const percentage = Math.min(100, (totalTokens / contextLimit) * 100);
  
  return (
    <div style={{ 
      padding: '10px 12px', 
      borderBottom: `1px solid ${t.barBorder}`,
      background: t.barBg,
    }}>
      
      {/* Context pills */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '8px' }}>
        {activeContexts.map(ctx => (
          <div 
            key={ctx.id}
            style={{ 
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              background: ctx.color,
              padding: '4px 8px',
              borderRadius: '6px',
              fontSize: '11px',
              color: '#fff',
              fontWeight: 500
            }}
          >
            <span>{ctx.name}</span>
            <CloseOutlined 
              style={{ fontSize: '9px', opacity: 0.7, cursor: 'pointer' }}
              onClick={() => removeContextFromLens(ctx.id)}
            />
          </div>
        ))}
        
        {/* Skill pills - different styling */}
        {activeSkills.map(({ skill, item }) => (
          <div 
            key={skill.id}
            style={{ 
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              background: skill.color,
              padding: '4px 8px',
              borderRadius: '12px',
              fontSize: '11px',
              color: '#fff',
              fontWeight: 500,
              border: skill.source === 'project'
                ? '1px solid rgba(103,232,249,0.4)'
                : '1px dashed rgba(255,255,255,0.3)'
            }}
          >
            {skill.source === 'project' && (
              <span style={{ fontSize: '8px', opacity: 0.7 }}>📁</span>
            )}
            <span>{skill.name}</span>
            <CloseOutlined
              data-testid={`skill-unload-${skill.name}`}
              aria-label={`Unload ${skill.name}`}
              style={{ fontSize: '9px', opacity: 0.7, cursor: 'pointer' }}
              onClick={() => unloadSkill(skill.id, item)}
            />
          </div>
        ))}

        {/* ✦ picked up on demand this turn: not removable here, it was
            the model's choice and ends with the turn */}
        {pickedUpSkills.map(skill => (
          <div
            key={`pickup-${skill.id}`}
            data-testid="skill-pickup"
            title="Loaded on demand by the model this turn"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '4px 8px',
              borderRadius: '12px',
              fontSize: '11px',
              fontWeight: 500,
              border: `1px dashed ${skill.color}`,
              color: skill.color,
            }}
          >
            <span aria-hidden="true">✦</span>
            <span>{skill.name}</span>
          </div>
        ))}
        
        {hasAdditionalFiles && (
          <div style={{ 
            padding: '4px 8px',
            background: '#fbbf24',
            borderRadius: '6px',
            fontSize: '11px',
            color: '#000'
          }}>
            +{additionalFiles.length} files
          </div>
        )}
      </div>
      
      {/* Token count and bar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: t.tokenText, marginBottom: '6px' }}>
        <span>
          {tokenInfo ? Object.keys(tokenInfo.fileTokens).length : 0} files
          {tokenInfo?.astTokens ? ` · AST ${tokenInfo.astTokens.toLocaleString()}t` : ''}
        </span>
        <span>{isCalculatingTokens ? 'calculating...' : `${totalTokens.toLocaleString()} tokens`}</span>
      </div>
      
      <div style={{ height: '3px', background: t.trackBg, borderRadius: '2px', overflow: 'hidden' }}>
        <div style={{ 
          height: '100%', 
          width: `${percentage}%`, 
          background: 'linear-gradient(90deg, #2563eb, #7c3aed)',
          borderRadius: '2px',
          transition: 'width 0.3s ease'
        }} />
      </div>
    </div>
  );
};
