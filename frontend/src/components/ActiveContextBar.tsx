/**
 * ActiveContextBar - Shows current active contexts and skills
 */
import React from 'react';
import { useProject } from '../context/ProjectContext';
import { useTheme } from '../context/ThemeContext';
import { CloseOutlined } from '@ant-design/icons';

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
  } = useProject();
  
  const activeContexts = contexts.filter(c => activeContextIds.includes(c.id));
  const activeSkills = skills.filter(s => activeSkillIds.includes(s.id));
  const hasAdditionalFiles = additionalFiles.length > 0;
  const hasAnySelection = activeContexts.length > 0 || activeSkills.length > 0 || hasAdditionalFiles;
  
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
        {activeSkills.map(skill => (
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
              style={{ fontSize: '9px', opacity: 0.7, cursor: 'pointer' }}
              onClick={() => removeSkillFromLens(skill.id)}
            />
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
