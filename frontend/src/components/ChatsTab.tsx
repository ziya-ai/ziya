/**
 * ChatsTab - Browse conversations with groups
 */
import React, { useState, useEffect } from 'react';
import { useChatContext } from '../context/ChatContext';
import { useProject } from '../context/ProjectContext';
import { Button, Input, Dropdown, Menu } from 'antd';
import { DownOutlined, PlusOutlined, MoreOutlined } from '@ant-design/icons';

export const ChatsTab: React.FC = () => {
  const {
    conversations,
    currentConversationId,
    loadConversation,
    startNewChat,
    folders,
    createFolder,
    deleteFolder,
    moveConversationToFolder,
  } = useChatContext();
  
  const { contexts, skills, activeContextIds, activeSkillIds } = useProject();
  
  const [searchQuery, setSearchQuery] = useState('');
  
  // Filter conversations by search
  const filteredConversations = conversations.filter(conv =>
    conv.title.toLowerCase().includes(searchQuery.toLowerCase())
  );
  
  // Group conversations
  const groupedConversations = filteredConversations.reduce((acc, conv) => {
    const groupId = conv.folderId || 'ungrouped';
    if (!acc[groupId]) acc[groupId] = [];
    acc[groupId].push(conv);
    return acc;
  }, {} as Record<string, typeof conversations>);
  
  // Get context indicators for a conversation
  const getContextIndicators = (conv: any) => {
    if (!conv.contextIds || conv.contextIds.length === 0) return null;
    
    return conv.contextIds.map((ctxId: string) => {
      const ctx = contexts.find(c => c.id === ctxId);
      return ctx ? (
        <div
          key={ctxId}
          style={{
            width: '8px',
            height: '8px',
            background: ctx.color,
            borderRadius: '50%',
            display: 'inline-block',
            marginRight: '4px'
          }}
          title={ctx.name}
        />
      ) : null;
    });
  };
  
  // Render a chat group
  const renderGroup = (folder: any, chats: any[]) => {
    const [isCollapsed, setIsCollapsed] = useState(folder.collapsed || false);
    
    const groupMenu = (
      <Menu style={{ background: '#252525', border: '1px solid #444', borderRadius: '8px' }}>
        <Menu.Item key="rename">Rename</Menu.Item>
        <Menu.Item key="contexts">Set default contexts...</Menu.Item>
        <Menu.Item key="skills">Set default skills...</Menu.Item>
        <Menu.Divider />
        <Menu.Item key="delete" danger>Delete group</Menu.Item>
      </Menu>
    );
    
    return (
      <div key={folder.id} style={{ marginBottom: '12px' }}>
        {/* Group header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px',
            cursor: 'pointer',
            borderRadius: '4px'
          }}
          onClick={() => setIsCollapsed(!isCollapsed)}
        >
          <span style={{ color: '#666', fontSize: '10px' }}>
            {isCollapsed ? '▶' : '▼'}
          </span>
          <span style={{ flex: 1, fontSize: '12px', fontWeight: 500 }}>
            {folder.name}
          </span>
          <span style={{ fontSize: '10px', color: '#666' }}>
            {chats.length}
          </span>
          <Dropdown overlay={groupMenu} trigger={['click']}>
            <MoreOutlined 
              style={{ fontSize: '12px', color: '#666', padding: '4px' }}
              onClick={e => e.stopPropagation()}
            />
          </Dropdown>
        </div>
        
        {/* Group's default contexts indicator */}
        {!isCollapsed && folder.defaultContextIds && folder.defaultContextIds.length > 0 && (
          <div style={{ marginLeft: '16px', padding: '4px 8px', marginBottom: '4px' }}>
            <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
              {folder.defaultContextIds.map((ctxId: string) => {
                const ctx = contexts.find(c => c.id === ctxId);
                return ctx ? (
                  <span 
                    key={ctxId}
                    style={{ 
                      fontSize: '9px', 
                      background: ctx.color, 
                      padding: '2px 6px', 
                      borderRadius: '3px',
                      color: '#fff'
                    }}
                  >
                    {ctx.name}
                  </span>
                ) : null;
              })}
            </div>
          </div>
        )}
        
        {/* Chats in group */}
        {!isCollapsed && (
          <div style={{ marginLeft: '16px', borderLeft: '1px solid #333', paddingLeft: '8px' }}>
            {chats.map(conv => renderChat(conv))}
          </div>
        )}
      </div>
    );
  };
  
  // Render a single chat
  const renderChat = (conv: any) => {
    const isActive = conv.id === currentConversationId;
    const timeAgo = formatTimeAgo(conv.lastActiveAt || conv.createdAt);
    
    return (
      <div
        key={conv.id}
        style={{
          padding: '10px',
          background: isActive ? '#2563eb' : '#1f1f1f',
          borderRadius: '6px',
          marginBottom: '4px',
          cursor: 'pointer'
        }}
        onClick={() => loadConversation(conv.id)}
      >
        <div style={{ fontSize: '12px', marginBottom: '4px', fontWeight: isActive ? 500 : 400 }}>
          {conv.title}
        </div>
        <div style={{ 
          fontSize: '10px', 
          color: isActive ? 'rgba(255,255,255,0.8)' : '#666',
          display: 'flex',
          alignItems: 'center',
          gap: '6px'
        }}>
          <span>{timeAgo}</span>
          {getContextIndicators(conv)}
        </div>
      </div>
    );
  };
  
  const formatTimeAgo = (timestamp: number): string => {
    const seconds = Math.floor((Date.now() - timestamp) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };
  
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      
      {/* New chat button */}
      <div style={{ padding: '8px' }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => startNewChat()}
          style={{ width: '100%' }}
        >
          New Chat
        </Button>
      </div>
      
      {/* Search */}
      <div style={{ padding: '0 8px 8px' }}>
        <Input
          placeholder="Search chats..."
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          style={{ background: '#252525', border: '1px solid #333' }}
        />
      </div>
      
      {/* Chat list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        
        {/* Grouped chats */}
        {folders.map(folder => {
          const chats = groupedConversations[folder.id] || [];
          if (chats.length === 0) return null;
          return renderGroup(folder, chats);
        })}
        
        {/* Ungrouped chats */}
        {groupedConversations['ungrouped'] && groupedConversations['ungrouped'].length > 0 && (
          <div>
            <div style={{ 
              fontSize: '10px', 
              color: '#666', 
              padding: '8px 4px', 
              textTransform: 'uppercase',
              borderTop: folders.length > 0 ? '1px solid #333' : 'none',
              marginTop: folders.length > 0 ? '8px' : 0
            }}>
              Ungrouped
            </div>
            {groupedConversations['ungrouped'].map(conv => renderChat(conv))}
          </div>
        )}
        
        {filteredConversations.length === 0 && (
          <div style={{ 
            padding: '40px 20px', 
            textAlign: 'center', 
            color: '#666', 
            fontSize: '12px' 
          }}>
            {searchQuery ? 'No matching chats' : 'No chats yet'}
          </div>
        )}
      </div>
      
    </div>
  );
};
