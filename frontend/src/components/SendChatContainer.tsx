/**
 * SendChatContainer - Handles sending messages with context
 */
import React, { useState, useRef, useCallback, useEffect, KeyboardEvent, useMemo } from 'react';
import { useChatContext } from '../context/ChatContext';
import { useProject } from '../context/ProjectContext';
import { useFolderContext } from '../context/FolderContext';
import { sendPayload } from '../apis/chatApi';
import { Button, message } from 'antd';
import { SendOutlined, PictureOutlined } from '@ant-design/icons';
import { ImageAttachment, Message } from '../utils/types';
import { useTheme } from '../context/ThemeContext';
import StopStreamButton from './StopStreamButton';
import { ThrottlingErrorDisplay } from './ThrottlingErrorDisplay';
import { detectIncompleteResponse } from '../utils/responseUtils';
import { v4 as uuidv4 } from 'uuid';

interface SendChatContainerProps {
  fixed?: boolean;
}

export const SendChatContainer: React.FC<SendChatContainerProps> = ({ fixed }) => {
  const [inputValue, setInputValue] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [attachedImages, setAttachedImages] = useState<ImageAttachment[]>([]);
  const [supportsVision, setSupportsVision] = useState(false);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [isSendingFeedback, setIsSendingFeedback] = useState(false);
  const [currentToolId, setCurrentToolId] = useState<string | null>(null);
  const [throttlingError, setThrottlingError] = useState<any>(null);
  const [showContinueButton, setShowContinueButton] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  const draftsRef = useRef<Map<string, string>>(new Map());
  // Initialize with undefined to avoid circular dependency during initial render
  const prevConversationIdRef = useRef<string | undefined>(undefined);
  
  const {
    currentConversationId,
    currentMessages,
    streamedContentMap,
    setStreamedContentMap,
    setReasoningContentMap,
    setIsStreaming,
    removeStreamingConversation,
    addStreamingConversation,
    streamingConversations,
    updateProcessingState,
    addMessageToConversation,
    setUserHasScrolled,
  } = useChatContext();
  
  const { checkedKeys } = useFolderContext();
  const { activeSkillPrompts, currentProject } = useProject();
  const { isDarkMode } = useTheme();
  
  const isCurrentlyStreaming = streamingConversations.has(currentConversationId);
  const shouldSendAsFeedback = isCurrentlyStreaming && inputValue.trim().length > 0;
  
  // Memoized disabled state to prevent unnecessary re-renders
  const isDisabled = useMemo(() => 
    (!inputValue.trim() && attachedImages.length === 0) || isCurrentlyStreaming,
    [inputValue, attachedImages.length, isCurrentlyStreaming]
  );
  
  // Memoized button title for accessibility
  const buttonTitle = useMemo(() =>
    isCurrentlyStreaming
      ? "Waiting for AI response..."
      : currentMessages[currentMessages.length - 1]?.role === 'human'
        ? "AI response may have failed - click Send to retry"
        : "Send message",
    [isCurrentlyStreaming, currentMessages]
  );
  
  // Check if current model supports vision
  useEffect(() => {
    const checkVisionSupport = async () => {
      try {
        const response = await fetch('/api/model-capabilities');
        const capabilities = await response.json();
        setSupportsVision(capabilities.supports_vision || false);
      } catch (error) {
        console.error('Failed to check vision support:', error);
        setSupportsVision(false);
      }
    };
    checkVisionSupport();
  }, [currentConversationId]);
  
  // Listen for tool feedback ready events
  useEffect(() => {
    const handleFeedbackReady = (event: CustomEvent) => {
      if (event.detail?.conversationId === currentConversationId) {
        setCurrentToolId(event.detail.toolId || null);
      }
    };
    
    document.addEventListener('feedbackReady', handleFeedbackReady as EventListener);
    return () => document.removeEventListener('feedbackReady', handleFeedbackReady as EventListener);
  }, [currentConversationId]);
  
  // Listen for throttling errors from chatApi
  useEffect(() => {
    const handleThrottlingError = (event: CustomEvent) => {
      if (event.detail.conversation_id && event.detail.conversation_id !== currentConversationId) {
        return;
      }
      console.log('Throttling error received:', event.detail);
      setThrottlingError(event.detail);
    };
    
    document.addEventListener('throttlingError', handleThrottlingError as EventListener);
    return () => document.removeEventListener('throttlingError', handleThrottlingError as EventListener);
  }, [currentConversationId]);
  
  // Clear throttling error when streaming starts
  useEffect(() => {
    if (streamingConversations.has(currentConversationId)) {
      setThrottlingError(null);
    }
  }, [streamingConversations, currentConversationId]);
  
  // Check if the last message suggests continuation is needed
  useEffect(() => {
    const lastMessage = currentMessages[currentMessages.length - 1];
    if (lastMessage?.role === 'assistant' && lastMessage.content) {
      const isIncomplete = detectIncompleteResponse(lastMessage.content);
      setShowContinueButton(isIncomplete && !streamingConversations.has(currentConversationId));
    } else {
      setShowContinueButton(false);
    }
  }, [currentMessages, streamingConversations, currentConversationId]);
  
  // Save/restore draft text and clear images when conversation changes
  useEffect(() => {
    const prevId = prevConversationIdRef.current || currentConversationId;
    
    // Skip the first run where prevId is undefined
    if (prevConversationIdRef.current === undefined) {
      prevConversationIdRef.current = currentConversationId;
      return;
    }

    // Save the current editor content as a draft for the conversation we're leaving
    if (prevId && prevId !== currentConversationId && editorRef.current) {
      const currentContent = editorRef.current.innerHTML;
      if (currentContent && currentContent.trim()) {
        draftsRef.current.set(prevId, currentContent);
      } else {
        draftsRef.current.delete(prevId);
      }
    }

    // Clear images for the new conversation
    setAttachedImages([]);

    // Restore draft for the conversation we're switching to, or clear
    if (editorRef.current) {
      const savedDraft = draftsRef.current.get(currentConversationId);
      editorRef.current.innerHTML = savedDraft || '';
      // Sync inputValue state with restored content
      const { text } = serializeEditorContent();
      setInputValue(text);
    }

    prevConversationIdRef.current = currentConversationId;
  }, [currentConversationId]);
  
  // Process image files (from file input or drag-drop)
  const processImageFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    
    const maxSize = 5 * 1024 * 1024; // 5MB limit
    const maxImages = 5;
    
    if (attachedImages.length + files.length > maxImages) {
      message.warning(`Maximum ${maxImages} images allowed`);
      return;
    }
    
    const validImageTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    const newImages: ImageAttachment[] = [];
    
    for (const file of Array.from(files)) {
      if (!validImageTypes.includes(file.type)) {
        message.error(`Unsupported image type: ${file.type}`);
        continue;
      }
      
      if (file.size > maxSize) {
        message.error(`Image ${file.name} exceeds 5MB limit`);
        continue;
      }
      
      try {
        const base64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result as string);
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });
        
        const imageAttachment: ImageAttachment = {
          id: uuidv4(),
          filename: file.name,
          data: base64.split(',')[1],
          mediaType: file.type
        };
        
        newImages.push(imageAttachment);
      } catch (error) {
        message.error(`Failed to process ${file.name}`);
      }
    }
    
    if (newImages.length > 0) {
      setAttachedImages(prev => [...prev, ...newImages]);
      
      // Insert images at cursor position in the editor
      if (editorRef.current) {
        const selection = window.getSelection();
        let range: Range;
        
        // Check if selection is within our editor
        if (selection && selection.rangeCount > 0) {
          const currentRange = selection.getRangeAt(0);
          if (editorRef.current.contains(currentRange.commonAncestorContainer)) {
            range = currentRange;
          } else {
            // Selection not in editor, append at end
            range = document.createRange();
            range.selectNodeContents(editorRef.current);
            range.collapse(false);
          }
        } else {
          // No selection, append at end
          range = document.createRange();
          range.selectNodeContents(editorRef.current);
          range.collapse(false);
        }
        
        // Insert each image
        for (const img of newImages) {
          const imgElement = document.createElement('img');
          imgElement.src = `data:${img.mediaType};base64,${img.data}`;
          imgElement.dataset.imageId = img.id;
          imgElement.style.cssText = 'max-height: 100px; max-width: 150px; border-radius: 6px; margin: 0 4px; vertical-align: middle; cursor: pointer;';
          imgElement.contentEditable = 'false';
          imgElement.draggable = false;
          
          range.insertNode(imgElement);
          range.setStartAfter(imgElement);
          range.setEndAfter(imgElement);
        }
        
        // Restore focus and cursor position
        selection?.removeAllRanges();
        selection?.addRange(range);
        editorRef.current.focus();
      }
    }
  }, [attachedImages.length, supportsVision]);
  
  const handleImageSelect = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    await processImageFiles(event.target.files);
    // Reset file input so same file can be selected again
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [processImageFiles]);
  
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (supportsVision && e.dataTransfer.types.includes('Files')) {
      setIsDraggingOver(true);
    }
  }, [supportsVision]);
  
  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(false);
    
    if (!supportsVision) {
      message.warning('Current model does not support image attachments');
      return;
    }
    await processImageFiles(e.dataTransfer.files);
  }, [supportsVision, processImageFiles]);
  
  // Serialize editor content to text with image markers and extract image order
  const serializeEditorContent = useCallback((): { text: string; orderedImages: ImageAttachment[] } => {
    if (!editorRef.current) return { text: '', orderedImages: [] };
    
    const orderedImages: ImageAttachment[] = [];
    let text = '';
    
    const processNode = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent || '';
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as HTMLElement;
        
        if (element.tagName === 'IMG') {
          const imageId = element.dataset.imageId;
          const image = attachedImages.find(img => img.id === imageId);
          if (image) {
            orderedImages.push(image);
            text += `<image${orderedImages.length}>`;
          }
        } else if (element.tagName === 'BR') {
          text += '\n';
        } else if (element.tagName === 'DIV' || element.tagName === 'P') {
          // Block elements add newlines
          if (text.length > 0 && !text.endsWith('\n')) {
            text += '\n';
          }
          element.childNodes.forEach(processNode);
          if (!text.endsWith('\n')) {
            text += '\n';
          }
        } else {
          element.childNodes.forEach(processNode);
        }
      }
    };
    
    editorRef.current.childNodes.forEach(processNode);
    
    // Trim trailing newlines
    text = text.replace(/\n+$/, '');
    
    return { text, orderedImages };
  }, [attachedImages]);
  
  // Update inputValue when editor content changes
  const handleInput = useCallback(() => {
    const { text } = serializeEditorContent();
    setInputValue(text);
  }, [serializeEditorContent]);
  
  // Send feedback to running tools
  const sendToolFeedback = useCallback(async () => {
    if (!inputValue.trim() || isSendingFeedback) return;
    
    const feedbackText = inputValue.trim();
    setIsSendingFeedback(true);
    
    try {
      // Add feedback message to conversation
      const feedbackMessage: Message = {
        role: 'human',
        content: feedbackText,
        _timestamp: Date.now(),
        _isFeedback: true,
        _feedbackStatus: 'pending'
      };
      addMessageToConversation(feedbackMessage, currentConversationId);
      
      // Use the global WebSocket if available
      const feedbackWebSocket = (window as any).feedbackWebSocket;
      if (feedbackWebSocket && (window as any).feedbackWebSocketReady) {
        const toolId = currentToolId || 'streaming_tool';
        feedbackWebSocket.sendFeedback(toolId, feedbackText);
        console.log('🔄 FEEDBACK:', feedbackText);
        
        // Clear the input
        if (editorRef.current) editorRef.current.innerHTML = '';
        setInputValue('');
        draftsRef.current.delete(currentConversationId);
        
        message.success({
          content: (
            <span>
              ✅ Feedback queued
              <br /><span style={{ fontSize: '12px', opacity: 0.8 }}>The model will incorporate this at the next opportunity</span>
            </span>
          ),
          duration: 2,
          key: 'feedback-sent'
        });
      } else {
        console.error('🔄 FEEDBACK: WebSocket not ready');
        message.warning({
          content: 'Feedback system unavailable - tools will continue without feedback',
          duration: 3,
          key: 'feedback-unavailable'
        });
      }
    } catch (error) {
      console.error('🔄 FEEDBACK: Error sending feedback:', error);
      message.error('Failed to send feedback');
    } finally {
      setIsSendingFeedback(false);
    }
  }, [inputValue, isSendingFeedback, currentConversationId, currentToolId, addMessageToConversation]);
  
  const handleSend = useCallback(async () => {
    // If streaming, send as feedback instead
    if (shouldSendAsFeedback) {
      await sendToolFeedback();
      return;
    }
    
    const { text, orderedImages } = serializeEditorContent();
    
    if (!text.trim() || isSubmitting) return;
    
    // Don't allow sending while streaming
    if (isCurrentlyStreaming) {
      console.warn('Cannot send while streaming - use feedback instead');
      return;
    }
    
    setIsSubmitting(true);
    
    // CRITICAL: Capture conversation ID before any async operations
    // This ensures responses go to the correct conversation even if user switches
    const targetConversationId = currentConversationId;
    
    // Reset scroll state so auto-scroll works for new messages
    setUserHasScrolled(false);
    
    try {
      // Add user message
      const userMessage = {
        role: 'human' as const,
        content: text,
        _timestamp: Date.now(),
        images: orderedImages.length > 0 ? orderedImages : undefined
      };
      addMessageToConversation(userMessage, targetConversationId);
      
      // Clear editor
      if (editorRef.current) editorRef.current.innerHTML = '';
      setInputValue('');
      setAttachedImages([]);
      draftsRef.current.delete(targetConversationId);
      
      // Start streaming
      addStreamingConversation(targetConversationId);
      
      // Include the new user message in the messages sent to API
      const messagesToSend = [...currentMessages, userMessage].filter(m => !m.muted);
      
      // Send with active skill prompts included
      await sendPayload(
        messagesToSend,
        text,
        Array.from(checkedKeys).map(String),
        targetConversationId,
        activeSkillPrompts,
        orderedImages, // Include images in order they appear
        streamedContentMap,
        setStreamedContentMap,
        setIsStreaming,
        removeStreamingConversation,
        addMessageToConversation,
        streamingConversations.has(targetConversationId),
        (state) => updateProcessingState(targetConversationId, state),
        setReasoningContentMap,
        currentProject // Pass current project so backend knows working directory
      );
    } catch (error) {
      console.error('Error sending message:', error);
    } finally {
      setIsSubmitting(false);
    }
  }, [
    shouldSendAsFeedback, sendToolFeedback, serializeEditorContent, isSubmitting,
    isCurrentlyStreaming, currentConversationId, setUserHasScrolled, addMessageToConversation,
    addStreamingConversation, currentMessages, checkedKeys, activeSkillPrompts,
    streamedContentMap, setStreamedContentMap, setIsStreaming, removeStreamingConversation,
    streamingConversations, updateProcessingState, setReasoningContentMap
  ]);
  
  // Handle keyboard events - must be after sendToolFeedback and handleSend
  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (shouldSendAsFeedback) {
        sendToolFeedback();
      } else {
        handleSend();
      }
    }
  }, [shouldSendAsFeedback, sendToolFeedback, handleSend]);
  
  return (
    <div 
      style={{ 
        padding: '8px 20px 6px 20px', 
        borderTop: isDarkMode ? '1px solid #333' : '1px solid #e0e0e0' 
      }}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      onDragLeave={() => setIsDraggingOver(false)}
    >
      {/* Throttling error display */}
      {throttlingError && (
        <ThrottlingErrorDisplay
          error={throttlingError}
          onDismiss={() => setThrottlingError(null)}
        />
      )}
      
      {/* Continue button for truncated responses */}
      {showContinueButton && (
        <div style={{ marginBottom: '8px', textAlign: 'center' }}>
          <Button 
            type="default" 
            onClick={() => {
              const continuePrompt = "Please continue your previous response.";
              if (editorRef.current) {
                editorRef.current.textContent = continuePrompt;
                setInputValue(continuePrompt);
              }
              setShowContinueButton(false);
              handleSend();
            }} 
            style={{ background: isDarkMode ? '#111d2c' : '#f0f8ff', borderColor: '#1890ff', color: '#1890ff' }} 
            disabled={isCurrentlyStreaming}
          >
            ↗️ Continue Response
          </Button>
        </div>
      )}
      
      <div style={{ 
        background: isDraggingOver 
          ? (isDarkMode ? 'rgba(24, 144, 255, 0.15)' : 'rgba(24, 144, 255, 0.1)')
          : (isDarkMode ? '#252525' : '#ffffff'), 
        borderRadius: '12px', 
        padding: '10px 12px',
        border: isDraggingOver 
          ? '2px dashed #1890ff' 
          : isCurrentlyStreaming
            ? (isDarkMode ? '1px solid #49aa19' : '1px solid #52c41a')
            : (isDarkMode ? '1px solid #333' : '1px solid #e0e0e0'),
        transition: 'all 0.2s'
      }}>
        {/* Rich text editor with inline images */}
        <div
          ref={editorRef}
          contentEditable
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          onPaste={async (e) => {
            // Handle pasted images
            const items = e.clipboardData?.items;
            if (!items) return;
            
            const imageFiles: File[] = [];
            for (const item of Array.from(items)) {
              if (item.type.startsWith('image/')) {
                const file = item.getAsFile();
                if (file) imageFiles.push(file);
              }
            }
            
            if (imageFiles.length > 0) {
              e.preventDefault();
              const dt = new DataTransfer();
              imageFiles.forEach(f => dt.items.add(f));
              await processImageFiles(dt.files);
            }
          }}
          style={{ 
            minHeight: '50px',
            maxHeight: '150px',
            overflowY: 'auto',
            outline: 'none',
            border: 'none', 
            color: isDarkMode ? '#e0e0e0' : '#1f1f1f', 
            fontSize: '14px',
            lineHeight: '1.5',
            wordBreak: 'break-word',
          }}
          data-placeholder={isCurrentlyStreaming ? "Provide feedback for running tools... (Enter to send)" : "Message..."}
          suppressContentEditableWarning
        />
        
        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          style={{ display: 'none' }}
          onChange={handleImageSelect}
        />
        
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px' }}>
          <div style={{ display: 'flex', gap: '8px' }}>
            {/* Image upload button */}
            {!isCurrentlyStreaming && supportsVision && (
              <Button
                icon={<PictureOutlined />}
                onClick={() => fileInputRef.current?.click()}
                title="Attach image"
                disabled={attachedImages.length >= 5}
                style={{
                  backgroundColor: 'transparent',
                  borderColor: isDarkMode ? '#424242' : '#d9d9d9'
                }}
              />
            )}
            
            {/* Stop button when streaming */}
            {isCurrentlyStreaming && (
              <StopStreamButton
                conversationId={currentConversationId}
                size="middle"
              />
            )}
          </div>
          
          {/* Feedback button when streaming with input */}
          {shouldSendAsFeedback ? (
            <Button
              type="default"
              onClick={sendToolFeedback}
              disabled={!inputValue.trim() || isSendingFeedback}
              icon={<SendOutlined />}
              loading={isSendingFeedback}
              style={{
                backgroundColor: isDarkMode ? '#162312' : '#f6ffed',
                borderColor: isDarkMode ? '#49aa19' : '#52c41a',
                color: isDarkMode ? '#95de64' : '#52c41a'
              }}
            >
              Send Feedback
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              loading={isSubmitting}
              disabled={isDisabled}
              title={buttonTitle}
            >
              Send
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};
