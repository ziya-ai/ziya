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

/**
 * Always-on skill pills shown before the rest stack behind a "+N skills"
 * chip.  The bar sits at the top of every sidebar tab, so a user with
 * sixteen on-hand skills saw six rows of pills on each tab.  At or under
 * the cap nothing stacks and no controls appear.
 */
export const CONTEXT_BAR_SKILL_CAP = 4;
/** Solid dots drawn in the one-line form before the rest become "+N". */
export const CONTEXT_BAR_DOT_CAP = 12;

/**
 * Per-project display preference for the skill pills, used only when more
 * than CONTEXT_BAR_SKILL_CAP skills are always-on.  ``collapsed`` folds the
 * pills to a single line of colour dots; ``expanded`` shows every pill
 * instead of the capped set.  Independent bits, so leaving the one-line
 * form returns to whichever of capped/all the user was in.
 */
export type SkillBarPrefs = { collapsed: boolean; expanded: boolean };
const NO_PREFS: SkillBarPrefs = { collapsed: false, expanded: false };
const prefsKey = (projectId: string) => `ZIYA_CONTEXT_BAR_SKILLS:${projectId}`;

export function readSkillBarPrefs(projectId: string | undefined): SkillBarPrefs {
  if (!projectId) return NO_PREFS;
  try {
    const raw = localStorage.getItem(prefsKey(projectId));
    if (!raw) return NO_PREFS;
    const p = JSON.parse(raw);
    return { collapsed: !!p.collapsed, expanded: !!p.expanded };
  } catch {
    return NO_PREFS;
  }
}

export function writeSkillBarPrefs(projectId: string | undefined, prefs: SkillBarPrefs): void {
  if (!projectId) return;
  try { localStorage.setItem(prefsKey(projectId), JSON.stringify(prefs)); } catch { /* quota / private mode */ }
}

type NamedColor = { id: string; name: string; color: string };

/** One-line form: a solid dot per always-on skill, a dashed ring per skill
 *  the model loaded on demand this turn, and a count.  Click to reopen. */
const CollapsedSkillLine: React.FC<{
  always: NamedColor[]; pickedUp: NamedColor[]; text: string; accent: string; onOpen: () => void;
}> = ({ always, pickedUp, text, accent, onOpen }) => (
  <button
    type="button"
    data-testid="skill-line"
    aria-expanded={false}
    aria-label={`${always.length} skills on hand${pickedUp.length ? `, ${pickedUp.length} loaded on demand` : ''}; show skills`}
    title={[...always.map(s => s.name), ...pickedUp.map(s => `✦ ${s.name}`)].join(' · ')}
    onClick={onOpen}
    style={{
      display: 'flex', alignItems: 'center', gap: 8, width: '100%',
      background: 'none', border: 'none', padding: 0, cursor: 'pointer',
      color: text, fontSize: 11, textAlign: 'left',
    }}
  >
    <span style={{ display: 'inline-flex', gap: 2, alignItems: 'center' }}>
      {always.slice(0, CONTEXT_BAR_DOT_CAP).map(s => (
        <i key={s.id} data-testid="skill-dot" title={s.name}
          style={{ width: 7, height: 7, borderRadius: '50%', background: s.color, display: 'inline-block' }} />
      ))}
      {always.length > CONTEXT_BAR_DOT_CAP && (
        <span style={{ fontSize: 9, margin: '0 2px', opacity: 0.7 }}>+{always.length - CONTEXT_BAR_DOT_CAP}</span>
      )}
      {pickedUp.map(s => (
        <i key={`pickup-${s.id}`} data-testid="skill-dot-pickup" title={`✦ ${s.name}`}
          style={{ width: 7, height: 7, borderRadius: '50%', border: `1px dashed ${s.color}`, boxSizing: 'border-box', display: 'inline-block' }} />
      ))}
    </span>
    <span style={{ whiteSpace: 'nowrap' }}>
      {always.length} on hand{pickedUp.length ? ` · ${pickedUp.length} ✦` : ''}
    </span>
    <span aria-hidden="true" style={{ marginLeft: 'auto', color: accent }}>▸</span>
  </button>
);

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

/**
 * Active skill rows for the current conversation — shared by the sidebar
 * context bar and the collapsed-panel strip so the two can never disagree.
 * Skills come from the bench view-model (design/capabilities-hub.md §Problem
 * 1): only ``effective === 'always'`` is active.  The legacy activeSkillIds
 * list carried two meanings by visibility and showed SUPPRESSED discoverable
 * skills as active.  Until the bench has answered, fall back to the legacy
 * list read through its real semantics (never as raw membership).
 */
export function useActiveSkillRows() {
  const { skills, activeSkillIds, removeSkillFromLens, currentProject, skillsProjectId } = useProject();
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
  return { activeSkills, pickedUpSkills, unloadSkill };
}

export const ActiveContextBar: React.FC = () => {
  const { isDarkMode } = useTheme();

  const t = {
    barBg:     isDarkMode ? '#0d0d0d' : '#f9fafb',
    barBorder: isDarkMode ? '#333'    : '#e5e7eb',
    emptyText: isDarkMode ? '#666'    : '#9ca3af',
    lineText:  isDarkMode ? '#bbb'    : '#4b5563',
    chipBorder: isDarkMode ? '#666'   : '#9ca3af',
    accent:    isDarkMode ? '#2dd4bf' : '#0d9488',
  };

  const {
    contexts,
    skills,
    activeContextIds,
    activeSkillIds,
    additionalFiles,
    removeContextFromLens,
    removeSkillFromLens,
    currentProject,
    skillsProjectId,
  } = useProject();
  
  const activeContexts = contexts.filter(c => activeContextIds.includes(c.id));
  const { activeSkills, pickedUpSkills, unloadSkill } = useActiveSkillRows();

  // Stacking preferences, per project.  Hooks run before the early return.
  const projectId = currentProject?.id;
  const [prefs, setPrefs] = React.useState<SkillBarPrefs>(() => readSkillBarPrefs(projectId));
  React.useEffect(() => { setPrefs(readSkillBarPrefs(projectId)); }, [projectId]);
  const updatePrefs = (patch: Partial<SkillBarPrefs>) => {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    writeSkillBarPrefs(projectId, next);
  };
  // Nothing to hide at or under the cap: no chip, no collapse control, and
  // a stored "collapsed" preference is ignored rather than folding 3 pills.
  const stackable = activeSkills.length > CONTEXT_BAR_SKILL_CAP;
  const collapsed = stackable && prefs.collapsed;
  const shownSkills = stackable && !prefs.expanded
    ? activeSkills.slice(0, CONTEXT_BAR_SKILL_CAP) : activeSkills;
  const stackedSkills = activeSkills.slice(shownSkills.length);

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
  
  // No token figure here: the context budget (files · chat · tools · ast,
  // against the model's real limit) is the footer's TokenBreakdownBar.  The
  // gauge this bar used to draw counted only named contexts and skills,
  // against a hard-coded 200k, so it read "0 tokens" beside a 581k footer.
  return (
    <div style={{ 
      padding: '10px 12px', 
      borderBottom: `1px solid ${t.barBorder}`,
      background: t.barBg,
    }}>
      
      {/* Context pills */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
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
        
        {collapsed ? (
          <CollapsedSkillLine
            always={activeSkills.map(({ skill }) => skill)}
            pickedUp={pickedUpSkills}
            text={t.lineText}
            accent={t.accent}
            onOpen={() => updatePrefs({ collapsed: false })}
          />
        ) : (<>
        {/* Skill pills - different styling.  Capped unless expanded. */}
        {shownSkills.map(({ skill, item }) => (
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

        {/* The rest of the always-on skills, stacked behind one chip */}
        {stackable && (
          <button
            type="button"
            data-testid="skill-stack-toggle"
            aria-expanded={prefs.expanded}
            title={prefs.expanded ? 'Show fewer' : stackedSkills.map(r => r.skill.name).join(' · ')}
            onClick={() => updatePrefs({ expanded: !prefs.expanded })}
            style={{
              padding: '4px 8px', borderRadius: 12, fontSize: 11, cursor: 'pointer',
              background: 'none', border: `1px dashed ${t.chipBorder}`, color: t.lineText,
            }}
          >
            {prefs.expanded ? '▴ fewer' : `+${stackedSkills.length} skills ▸`}
          </button>
        )}

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
        </>)}
        
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

        {/* Fold the skills to one line; only offered when something stacks */}
        {stackable && !collapsed && (
          <button
            type="button"
            data-testid="skill-collapse"
            aria-label="Collapse skills to one line"
            title="Collapse skills to one line"
            onClick={() => updatePrefs({ collapsed: true })}
            style={{
              marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer',
              color: t.accent, fontSize: 11, padding: '0 2px',
            }}
          >▴</button>
        )}
      </div>
    </div>
  );
};
