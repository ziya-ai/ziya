/**
 * ProjectSwitcher - Dropdown to switch between projects
 */
import React, { useState, useRef, useEffect } from 'react';
import { useProject } from '../context/ProjectContext';
import { Dropdown, Menu, Button, Input, message, Tooltip } from 'antd';
import { FolderOutlined, DownOutlined, EditOutlined, CheckOutlined, CloseOutlined, FolderOpenOutlined } from '@ant-design/icons';
import { DirectoryBrowserModal } from './DirectoryBrowserModal';

export const ProjectSwitcher: React.FC = () => {
  const { 
    currentProject, 
    projects, 
    isLoadingProject, 
    switchProject,
    createProject,
    updateProject 
  } = useProject();
  const [isOpen, setIsOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editedName, setEditedName] = useState('');
  const [showDirectoryBrowser, setShowDirectoryBrowser] = useState(false);
  const inputRef = useRef<any>(null);
  
  // Focus input when editing starts
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);
  
  const handleStartEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditedName(currentProject?.name || '');
    setIsEditing(true);
  };
  
  const handleSaveEdit = async () => {
    if (!currentProject || !editedName.trim()) {
      message.error('Project name cannot be empty');
      return;
    }
    
    try {
      await updateProject(currentProject.id, { name: editedName.trim() });
      message.success('Project name updated');
      setIsEditing(false);
    } catch (error) {
      message.error('Failed to update project name');
      console.error(error);
    }
  };
  
  const handleCancelEdit = () => {
    setIsEditing(false);
    setEditedName('');
  };
  
  const handleOpenFolderClick = () => {
    setIsOpen(false);
    setShowDirectoryBrowser(true);
  };
  
  const handleDirectorySelect = async (path: string) => {
    try {
      const newProject = await createProject(path);
      message.success(`Project "${newProject.name}" created`);
      
      // Switch to the newly created project
      await switchProject(newProject.id);
      
      setShowDirectoryBrowser(false);
    } catch (error) {
      message.error('Failed to create project');
      console.error('Project creation error:', error);
    }
  };
  
  if (isLoadingProject) {
    return (
      <div style={{ padding: '10px 12px', background: '#0a0a0a', borderBottom: '1px solid #333' }}>
        <div style={{ height: '32px', background: '#1f1f1f', borderRadius: '6px', animation: 'pulse 1.5s infinite' }} />
      </div>
    );
  }
  
  // If no project after loading, show error state
  if (!currentProject) {
    return (
      <div style={{ padding: '10px 12px', background: '#0a0a0a', borderBottom: '1px solid #333' }}>
        <div style={{ padding: '8px', background: '#1f1f1f', borderRadius: '6px', color: '#ef4444', fontSize: '12px', textAlign: 'center' }}>
          Failed to load project
        </div>
      </div>
    );
  }
  
  const menu = (
    <Menu
      style={{ 
        background: '#1f1f1f', 
        border: '1px solid #444',
        borderRadius: '8px',
        minWidth: '280px'
      }}
      onClick={({ key }) => {
        if (key === '__open_folder') {
          handleOpenFolderClick();
        } else if (key !== currentProject.id) {
          switchProject(key);
        }
        setIsOpen(false);
      }}
    >
      {/* Current project */}
      <Menu.ItemGroup 
        title={<span style={{ color: '#666', fontSize: '9px', textTransform: 'uppercase' }}>Current</span>}
      >
        <Menu.Item 
          key={currentProject.id}
          style={{ 
            background: '#2563eb', 
            color: '#fff',
            borderRadius: '6px',
            margin: '4px 8px'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{ width: '8px', height: '8px', background: '#22c55e', borderRadius: '50%' }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500 }}>{currentProject.name}</div>
              <div style={{ fontSize: '10px', opacity: 0.8 }}>{currentProject.path}</div>
            </div>
          </div>
        </Menu.Item>
      </Menu.ItemGroup>
      
      {/* Other projects */}
      {projects.filter(p => p.id !== currentProject.id).length > 0 && (
        <Menu.ItemGroup 
          title={<span style={{ color: '#666', fontSize: '9px', textTransform: 'uppercase' }}>Recent</span>}
        >
          {projects.filter(p => p.id !== currentProject.id).map(project => (
            <Menu.Item 
              key={project.id}
              style={{ margin: '4px 8px', borderRadius: '6px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '8px', height: '8px', background: '#22c55e', borderRadius: '50%' }} />
                <div style={{ flex: 1 }}>
                  <div>{project.name}</div>
                  <div style={{ fontSize: '10px', color: '#666' }}>{project.path}</div>
                </div>
              </div>
            </Menu.Item>
          ))}
        </Menu.ItemGroup>
      )}
      
      {/* Divider */}
      <Menu.Divider style={{ margin: '4px 0', background: '#333' }} />
      
      {/* Open folder action */}
      <Menu.Item 
        key="__open_folder"
        style={{ color: '#3b82f6', fontWeight: 500, margin: '4px 8px' }}
        icon={<FolderOpenOutlined />}
      >
        Open folder as project...
      </Menu.Item>
    </Menu>
  );
  
  return (
    <div style={{ padding: '10px 12px', borderBottom: '1px solid #333', background: '#0a0a0a' }}>
      {isEditing ? (
        // Inline editing mode
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          gap: '6px',
          padding: '6px 10px',
          background: '#1f1f1f',
          border: '1px solid #2563eb',
          borderRadius: '6px'
        }}>
          <div style={{ width: '8px', height: '8px', background: '#22c55e', borderRadius: '50%', flexShrink: 0 }} />
          <Input
            ref={inputRef}
            value={editedName}
            onChange={e => setEditedName(e.target.value)}
            onPressEnter={handleSaveEdit}
            onBlur={handleSaveEdit}
            style={{ 
              flex: 1, 
              fontSize: '13px',
              height: '24px',
              padding: '0 6px',
              background: '#141414',
              border: '1px solid #333'
            }}
            placeholder="Project name"
          />
          <Button 
            type="text" 
            size="small" 
            icon={<CheckOutlined />} 
            onClick={handleSaveEdit}
            style={{ padding: '0 4px', height: '24px', minWidth: '24px', color: '#22c55e' }}
          />
          <Button 
            type="text" 
            size="small" 
            icon={<CloseOutlined />} 
            onClick={handleCancelEdit}
            style={{ padding: '0 4px', height: '24px', minWidth: '24px', color: '#666' }}
          />
        </div>
      ) : (
        // Normal display mode
        <Dropdown 
        overlay={menu} 
        trigger={['click']}
        open={isOpen}
        onOpenChange={setIsOpen}
      >
        <div 
          style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: '8px', 
            padding: '6px 10px', 
            background: '#1f1f1f', 
            border: '1px solid #333',
            borderRadius: '6px',
            cursor: 'pointer'
          }}
        >
          <div style={{ width: '8px', height: '8px', background: '#22c55e', borderRadius: '50%' }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '13px', fontWeight: 500, display: 'flex', alignItems: 'center', gap: '6px' }}>
              {currentProject.name}
              <Tooltip title="Edit project name">
                <EditOutlined 
                  style={{ 
                    fontSize: '11px', 
                    color: '#666',
                    cursor: 'pointer',
                    opacity: 0.6,
                    transition: 'opacity 0.2s'
                  }}
                  onClick={handleStartEdit}
                  onMouseEnter={(e) => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={(e) => e.currentTarget.style.opacity = '0.6'}
                />
              </Tooltip>
            </div>
            <div style={{ fontSize: '9px', color: '#666', marginTop: '2px' }}>{currentProject.path}</div>
          </div>
          <DownOutlined style={{ fontSize: '10px', color: '#666' }} />
        </div>
      </Dropdown>
      )}
      
      {/* Directory Browser Modal */}
      <DirectoryBrowserModal
        open={showDirectoryBrowser}
        onClose={() => setShowDirectoryBrowser(false)}
        onSelect={handleDirectorySelect}
      />
    </div>
  );
};
