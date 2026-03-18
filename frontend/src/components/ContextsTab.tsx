/**
 * ContextsTab - Browse and select contexts and skills
 */
import React, { useState, useMemo } from 'react';
import { useProject } from '../context/ProjectContext';
import { Context } from '../types/context';
import { FileOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import { Input, Button, Divider, message } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { SkillsSection } from './SkillsSection';
import { useFolderContext } from '../context/FolderContext';

export const ContextsTab: React.FC = () => {
  const {
    contexts,
    skills,
    activeContextIds,
    activeSkillIds,
    additionalFiles,
    addContextToLens,
    removeContextFromLens,
    addSkillToLens,
    removeSkillFromLens,
    createContext,
    createSkill,
    updateSkill,
    deleteSkill,
    tokenInfo,
  } = useProject();
  
  const { checkedKeys } = useFolderContext();
  
  const [searchQuery, setSearchQuery] = useState('');
  const [isCreatingContext, setIsCreatingContext] = useState(false);
  const [newContextName, setNewContextName] = useState('');
  const [groupFiles, setGroupFiles] = useState<string[]>([]);        // stable list
  const [groupFileChecked, setGroupFileChecked] = useState<Set<string>>(new Set()); // which are included

  const { isDarkMode } = useTheme();

  const t = {
    searchBg:      isDarkMode ? '#252525' : '#ffffff',
    searchBorder:  isDarkMode ? '#333'    : '#d1d5db',
    searchIcon:    isDarkMode ? '#666'    : '#9ca3af',
    headerColor:   isDarkMode ? '#666'    : '#6b7280',
    cardBg:        isDarkMode ? '#1f1f1f' : '#ffffff',
    cardBorder:    isDarkMode ? '#333'    : '#e5e7eb',
    textSecondary: isDarkMode ? '#666'    : '#6b7280',
    tokenColor:    isDarkMode ? '#888'    : '#6b7280',
    dividerColor:  isDarkMode ? '#333'    : '#e5e7eb',
    footerBg:      isDarkMode ? '#0a0a0a' : '#f9fafb',
    footerBorder:  isDarkMode ? '#333'    : '#e5e7eb',
    formBg:        isDarkMode ? '#1f1f1f' : '#ffffff',
    formBorder:    isDarkMode ? '#333'    : '#d1d5db',
    emptyColor:    isDarkMode ? '#666'    : '#9ca3af',
    fileRowBg:     isDarkMode ? '#252525' : '#f9fafb',
    fileRowBorder: isDarkMode ? '#333'    : '#e5e7eb',
    fileRowText:   isDarkMode ? '#ccc'    : '#374151',
  };
  
  // Filter by search
  const filteredContexts = useMemo(() => 
    contexts.filter(c => c.name.toLowerCase().includes(searchQuery.toLowerCase()))
      .filter(c => !c.name.startsWith('[D] ')),
    [contexts, searchQuery]
  );
  
  
  // Calculate what each inactive context would add
  const calculateAddedTokens = (ctx: Context): { added: number; total: number; overlap: number } => {
    if (activeContextIds.includes(ctx.id)) {
      return { added: ctx.tokenCount, total: ctx.tokenCount, overlap: 0 };
    }
    
    // Estimate overlap with active files
    const activeFiles = new Set<string>();
    for (const activeId of activeContextIds) {
      const activeCtx = contexts.find(c => c.id === activeId);
      activeCtx?.files.forEach(f => activeFiles.add(f));
    }
    additionalFiles.forEach(f => activeFiles.add(f));
    
    const overlappingFiles = ctx.files.filter(f => activeFiles.has(f));
    const newFiles = ctx.files.filter(f => !activeFiles.has(f));
    const overlapRatio = newFiles.length / ctx.files.length;
    const added = Math.round(ctx.tokenCount * overlapRatio);
    
    return { 
      added, 
      total: ctx.tokenCount, 
      overlap: overlappingFiles.length 
    };
  };
  const handleSaveContext = async () => {
    if (!newContextName.trim()) {
      message.error('Please enter a group name');
      return;
    }
    
    const filesToSave = groupFiles.filter(f => groupFileChecked.has(f));
    if (filesToSave.length === 0) {
      message.error('No files selected');
      return;
    }
    
    try {
      await createContext(newContextName, filesToSave);
      message.success(`Group "${newContextName}" created`);
      setNewContextName('');
      setIsCreatingContext(false);
      setGroupFiles([]);
      setGroupFileChecked(new Set());
    } catch (error) {
      message.error('Failed to create group');
    }
  };

  const openGroupDialog = () => {
    // Seed with all currently checked files — both lists start identical
    const files = Array.from(checkedKeys).map(String).sort();
    setGroupFiles(files);
    setGroupFileChecked(new Set(files));
    setIsCreatingContext(true);
  };
  
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      
      {/* Search */}
      <div style={{ padding: '8px' }}>
        <Input
          placeholder="Search file groups and skills..."
          prefix={<SearchOutlined style={{ color: t.searchIcon }} />}
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          style={{ background: t.searchBg, border: `1px solid ${t.searchBorder}` }}
        />
      </div>
      
      {/* Scrollable list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        {/* FILE GROUPS SECTION HEADER */}        
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px', 
          padding: '12px 4px 8px', 
        }}>
          <span style={{ fontSize: '11px', fontWeight: 600, color: t.headerColor, textTransform: 'uppercase', letterSpacing: '0.6px' }}> 
            File Groups
          </span>
          <div style={{ flex: 1, height: '1px', background: t.dividerColor }} />
        </div>
        
        {filteredContexts.map(ctx => {
          const isActive = activeContextIds.includes(ctx.id);
          const tokenCalc = calculateAddedTokens(ctx);
          
          return (
            <div
              key={ctx.id}
              style={{
                padding: '8px 10px',
                background: isActive ? `${ctx.color}20` : t.cardBg,
                borderLeft: `3px solid ${isActive ? ctx.color : t.cardBorder}`,
                borderRadius: '0 6px 6px 0',
                marginBottom: '4px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}
              onClick={() => isActive ? removeContextFromLens(ctx.id) : addContextToLens(ctx.id)}
            >
              <input 
                type="checkbox" 
                checked={isActive} 
                onChange={() => {}}
                onClick={e => {
                  e.stopPropagation();
                  isActive ? removeContextFromLens(ctx.id) : addContextToLens(ctx.id);
                }}
              />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '12px', fontWeight: isActive ? 500 : 400 }}>
                  {ctx.name}
                </div>
                <div style={{ fontSize: '10px', color: t.textSecondary }}>
                  {ctx.files.length} files
                  {tokenCalc.overlap > 0 && ` · ${tokenCalc.overlap} overlap`}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ 
                  fontSize: '11px', 
                  color: isActive ? ctx.color : t.tokenColor,
                  fontWeight: 500
                }}>
                  {isActive ? `${tokenCalc.total.toLocaleString()}` : `+${tokenCalc.added.toLocaleString()}`}
                </div>
                {!isActive && tokenCalc.overlap > 0 && (
                  <div style={{ fontSize: '9px', color: t.textSecondary, textDecoration: 'line-through' }}>
                    {tokenCalc.total.toLocaleString()}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        
        {filteredContexts.length === 0 && !searchQuery && (
          <div style={{ 
            padding: '20px', 
            textAlign: 'center', 
            color: t.emptyColor,
            fontSize: '12px' 
          }}>
            No file groups yet
          </div>
        )}
        
        {/* Save files as group — directly below the file groups list */}
        <div style={{ padding: '4px 0 8px' }}>
          <Button
            size="small"
            style={{ width: '100%' }}
            disabled={checkedKeys.size === 0}
            onClick={openGroupDialog}
          >
            Save selected files as group...
          </Button>
        </div>
      
      {/* Inline context creation */}
      {isCreatingContext && (
        <div style={{ padding: '12px', background: t.formBg, border: `1px solid ${t.formBorder}`, borderRadius: '8px', marginBottom: '8px' }}>
          <Input
            placeholder="Group name..."
            value={newContextName}
            onChange={e => setNewContextName(e.target.value)}
            onPressEnter={handleSaveContext}
            autoFocus
            style={{ marginBottom: '10px' }}
          />
          {/* Sparse file tree — deselect any files before saving */}
          <div style={{
            fontSize: '10px', color: t.textSecondary,
            marginBottom: '4px', fontWeight: 500,
          }}>
            {groupFileChecked.size} of {groupFiles.length} files — uncheck any to exclude:
          </div>
          <div style={{
            maxHeight: '180px', overflowY: 'auto',
            border: `1px solid ${t.fileRowBorder}`, borderRadius: '5px',
            marginBottom: '10px',
          }}>
            {groupFiles.map(f => {
              const isIncluded = groupFileChecked.has(f);
              const parts = f.split('/');
              const filename = parts[parts.length - 1];
              const dir = parts.length > 1 ? parts.slice(0, -1).join('/') + '/' : '';
              return (
                <label
                  key={f}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '6px',
                    padding: '4px 8px',
                    background: t.fileRowBg,
                    borderBottom: `1px solid ${t.fileRowBorder}`,
                    cursor: 'pointer', userSelect: 'none',
                    opacity: isIncluded ? 1 : 0.45,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={isIncluded}
                    onChange={() => {
                      const next = new Set(groupFileChecked);
                      if (isIncluded) next.delete(f);
                      else next.add(f);
                      setGroupFileChecked(next);
                    }}
                    style={{ flexShrink: 0 }}
                  />
                  <FileOutlined style={{ fontSize: '10px', color: isIncluded ? t.textSecondary : t.emptyColor, flexShrink: 0 }} />
                  <span style={{ fontSize: '11px', color: t.fileRowText, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                    {dir && <span style={{ color: t.textSecondary }}>{dir}</span>}
                    <span>{filename}</span>
                  </span>
                </label>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
            <Button size="small" onClick={() => { setIsCreatingContext(false); setNewContextName(''); setGroupFiles([]); setGroupFileChecked(new Set()); }}>
              Cancel
            </Button>
            <Button size="small" type="primary" disabled={groupFileChecked.size === 0 || !newContextName.trim()} onClick={handleSaveContext}>
              Save group
            </Button>
          </div>
        </div>
      )}
        
        {/* SKILLS SECTION HEADER */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px',
          padding: '12px 4px 8px',
        }}>
          <span style={{ fontSize: '11px', fontWeight: 600, color: t.headerColor, textTransform: 'uppercase', letterSpacing: '0.6px' }}>
            Skills
          </span>
          <div style={{ flex: 1, height: '1px', background: t.dividerColor }} />
        </div>

        <SkillsSection
          skills={skills}
          activeSkillIds={activeSkillIds}
          addSkillToLens={addSkillToLens}
          removeSkillFromLens={removeSkillFromLens}
          createSkill={createSkill}
          updateSkill={updateSkill}
          deleteSkill={deleteSkill}
          searchQuery={searchQuery}
        />
      </div>
      
    </div>
  );
};
