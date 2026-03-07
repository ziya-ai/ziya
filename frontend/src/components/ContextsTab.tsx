/**
 * ContextsTab - Browse and select contexts and skills
 */
import React, { useState, useMemo } from 'react';
import { useProject } from '../context/ProjectContext';
import { Context } from '../types/context';
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
    deleteSkill,
    tokenInfo,
  } = useProject();
  
  const { checkedKeys } = useFolderContext();
  
  const [searchQuery, setSearchQuery] = useState('');
  const [isCreatingContext, setIsCreatingContext] = useState(false);
  const [newContextName, setNewContextName] = useState('');
  
  // Filter by search
  const filteredContexts = useMemo(() => 
    contexts.filter(c => c.name.toLowerCase().includes(searchQuery.toLowerCase())),
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
      message.error('Please enter a context name');
      return;
    }
    
    const filesToSave = Array.from(checkedKeys).map(String);
    if (filesToSave.length === 0) {
      message.error('No files selected');
      return;
    }
    
    try {
      await createContext(newContextName, filesToSave);
      message.success(`Context "${newContextName}" created`);
      setNewContextName('');
      setIsCreatingContext(false);
    } catch (error) {
      message.error('Failed to create context');
    }
  };
  
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      
      {/* Search */}
      <div style={{ padding: '8px' }}>
        <Input
          placeholder="Search contexts and skills..."
          prefix={<SearchOutlined style={{ color: '#666' }} />}
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          style={{ background: '#252525', border: '1px solid #333' }}
        />
      </div>
      
      {/* Scrollable list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        
        {/* FILE CONTEXTS SECTION */}
        <div style={{ 
          fontSize: '10px', 
          color: '#666', 
          textTransform: 'uppercase', 
          padding: '8px 4px 4px',
          letterSpacing: '0.5px'
        }}>
          File Contexts
        </div>
        
        {filteredContexts.map(ctx => {
          const isActive = activeContextIds.includes(ctx.id);
          const tokenCalc = calculateAddedTokens(ctx);
          
          return (
            <div
              key={ctx.id}
              style={{
                padding: '8px 10px',
                background: isActive ? `${ctx.color}20` : '#1f1f1f',
                borderLeft: `3px solid ${isActive ? ctx.color : '#333'}`,
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
                <div style={{ fontSize: '10px', color: '#666' }}>
                  {ctx.files.length} files
                  {tokenCalc.overlap > 0 && ` · ${tokenCalc.overlap} overlap`}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ 
                  fontSize: '11px', 
                  color: isActive ? ctx.color : '#888',
                  fontWeight: 500
                }}>
                  {isActive ? `${tokenCalc.total.toLocaleString()}` : `+${tokenCalc.added.toLocaleString()}`}
                </div>
                {!isActive && tokenCalc.overlap > 0 && (
                  <div style={{ fontSize: '9px', color: '#666', textDecoration: 'line-through' }}>
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
            color: '#666', 
            fontSize: '12px' 
          }}>
            No saved contexts yet
          </div>
        )}
        
        
        <Divider style={{ margin: '16px 0', borderColor: '#333' }} />
        
        <SkillsSection
          skills={skills}
          activeSkillIds={activeSkillIds}
          addSkillToLens={addSkillToLens}
          removeSkillFromLens={removeSkillFromLens}
          createSkill={createSkill}
          deleteSkill={deleteSkill}
          searchQuery={searchQuery}
        />
      </div>
      
      {/* Footer actions */}
      <div style={{ padding: '8px', borderTop: '1px solid #333', background: '#0a0a0a' }}>
        <Button
          size="small"
          style={{ width: '100%' }}
          disabled={checkedKeys.size === 0}
          onClick={() => setIsCreatingContext(true)}
        >
          Save files as context...
        </Button>
      </div>
      
      {/* Inline context creation */}
      {isCreatingContext && (
        <div style={{ padding: '12px', background: '#1f1f1f', border: '1px solid #333', margin: '8px', borderRadius: '8px' }}>
          <Input
            placeholder="Context name..."
            value={newContextName}
            onChange={e => setNewContextName(e.target.value)}
            onPressEnter={handleSaveContext}
            autoFocus
            style={{ marginBottom: '8px' }}
          />
          <div style={{ fontSize: '11px', color: '#666', marginBottom: '8px' }}>
            {additionalFiles.length} files selected
          </div>
          <div style={{ display: 'flex', gap: '6px' }}>
            <Button size="small" onClick={() => setIsCreatingContext(false)}>Cancel</Button>
            <Button size="small" type="primary" onClick={handleSaveContext}>Save</Button>
          </div>
        </div>
      )}
    </div>
  );
};
