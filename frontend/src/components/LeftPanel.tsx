/**
 * LeftPanel - Main navigation panel with tabs
 */
import React, { useState, useMemo } from 'react';
import { Tabs, Dropdown, Menu } from 'antd';
import { 
  FileOutlined, FolderOutlined, MessageOutlined, MoreOutlined 
} from '@ant-design/icons';
import { ProjectSwitcher } from './ProjectSwitcher';
import { ActiveContextBar } from './ActiveContextBar';
import { FolderTree } from './FolderTree';
import { ContextsTab } from './ContextsTab';
import { ChatsTab } from './ChatsTab';

const { TabPane } = Tabs;

export const LeftPanel: React.FC = () => {
  const [panelWidth, setPanelWidth] = useState(320);
  const [activeTab, setActiveTab] = useState('files');
  
  // Define all tabs with icons
  const allTabs = useMemo(() => [
    { key: 'files', label: 'Files', icon: <FileOutlined />, fullLabel: '📁 Files' },
    { key: 'contexts', label: 'Contexts', icon: <FolderOutlined />, fullLabel: '📦 Contexts' },
    { key: 'chats', label: 'Chats', icon: <MessageOutlined />, fullLabel: '💬 Chats' },
  ], []);
  
  // Determine if we need compact mode (icon-only tabs)
  const isCompact = panelWidth < 250;
  
  // Observe panel width changes
  React.useEffect(() => {
    const panel = document.querySelector('.left-panel-container') as HTMLElement;
    if (!panel) return;
    
    const observer = new ResizeObserver((entries) => {
      const width = entries[0].contentRect.width;
      setPanelWidth(width);
    });
    
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);
  
  // Create dropdown menu for overflow tabs
  const overflowMenuProps = {
    items: allTabs.map(tab => ({
      key: tab.key,
      icon: tab.icon,
      label: tab.label,
    })),
    onClick: ({ key }: { key: string }) => setActiveTab(key),
    selectedKeys: [activeTab],
  };
  
  return (
    <div style={{ 
      width: '320px', 
      background: '#141414', 
      borderRight: '1px solid #333',
      display: 'flex',
      flexDirection: 'column',
      height: '100%'
    }} className="left-panel-container">
      
      {/* Project switcher at top */}
      <ProjectSwitcher />
      
      {/* Tabs */}
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        type="card"
        size="small"
        tabBarStyle={{ 
          margin: 0, 
          borderBottom: '1px solid #333',
          background: '#0d0d0d',
          padding: '4px',
          display: 'flex',
          flexWrap: 'nowrap',
          overflow: 'visible'
        }}
        tabBarExtraContent={
          isCompact && (
            <Dropdown menu={overflowMenuProps} trigger={['click']} placement="bottomRight">
              <div style={{
                padding: '4px 8px',
                cursor: 'pointer',
                borderRadius: '4px',
                display: 'flex',
                alignItems: 'center',
                background: '#1f1f1f',
                border: '1px solid #333',
                marginLeft: '4px'
              }}>
                <MoreOutlined style={{ fontSize: '14px', color: '#999' }} />
              </div>
            </Dropdown>
          )
        }
      >
        <TabPane 
          tab={
            isCompact ? (
              <span style={{ fontSize: '16px' }}>📁</span>
            ) : (
              <span style={{ fontSize: '12px' }}>📁 Files</span>
            )
          }
          key="files"
          style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
        >
          <ActiveContextBar />
          <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
            <FolderTree isPanelCollapsed={false} />
          </div>
        </TabPane>
        
        <TabPane 
          tab={
            isCompact ? (
              <span style={{ fontSize: '16px' }}>📦</span>
            ) : (
              <span style={{ fontSize: '12px' }}>📦 Contexts</span>
            )
          }
          key="contexts"
          style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
        >
          <ActiveContextBar />
          <ContextsTab />
        </TabPane>
        
        <TabPane 
          tab={
            isCompact ? (
              <span style={{ fontSize: '16px' }}>💬</span>
            ) : (
              <span style={{ fontSize: '12px' }}>💬 Chats</span>
            )
          }
          key="chats"
          style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
        >
          <ActiveContextBar />
          <ChatsTab />
        </TabPane>
      </Tabs>
      
    </div>
  );
};
