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
import { useServerStatus } from '../context/ServerStatusContext';
import { v4 as uuidv4 } from 'uuid';

interface SendChatContainerProps {
  fixed?: boolean;
}

export const SendChatContainer: React.FC<SendChatContainerProps> = ({ fixed }) => {
  const [inputValue, setInputValue] = useState('');
  const [submittingConversationId, setSubmittingConversationId] = useState<string | null>(null);
  const [attachedImages, setAttachedImages] = useState<ImageAttachment[]>([]);
  const [supportsVision, setSupportsVision] = useState(false);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [isSendingFeedback, setIsSendingFeedback] = useState(false);
  const [currentToolId, setCurrentToolId] = useState<string | null>(null);
  const [feedbackStatus, setFeedbackStatus] = useState<'idle' | 'pending' | 'queued' | 'delivered'>('idle');
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
  const { isServerReachable } = useServerStatus();
  const { isDarkMode } = useTheme();
  
  const isCurrentlyStreaming = streamingConversations.has(currentConversationId);
  // isSubmitting is true only when the CURRENT conversation has a send in flight.
  // A send running for a different conversation (user switched tabs) must not block.
  const isSubmitting = submittingConversationId === currentConversationId;
  const shouldSendAsFeedback = isCurrentlyStreaming && inputValue.trim().length > 0;
  
  // Disabled when: no content, OR this conversation is streaming, OR server is unreachable
  const isDisabled = useMemo(() => 
    (!inputValue.trim() && attachedImages.length === 0) || isCurrentlyStreaming || !isServerReachable,
    [inputValue, attachedImages.length, isCurrentlyStreaming, isServerReachable]
  );
  
  // Memoized button title for accessibility
  const buttonTitle = useMemo(() =>
    !isServerReachable
      ? "Server is unreachable"
      : isCurrentlyStreaming
      ? "Waiting for AI response..."
      : currentMessages[currentMessages.length - 1]?.role === 'human'
        ? "AI response may have failed - click Send to retry"
        : "Send message",
    [isCurrentlyStreaming, currentMessages, isServerReachable]
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
  
  // Listen for feedback delivery confirmation (SSE event from backend)
  useEffect(() => {
    const handleDelivered = (event: CustomEvent) => {
      if (event.detail?.conversationId === currentConversationId) {
        console.log('📝 FEEDBACK: Delivery confirmed via SSE:', event.detail.message);
        setFeedbackStatus('delivered');
        // Auto-clear after 4s
        setTimeout(() => setFeedbackStatus('idle'), 4000);
      }
    };
    document.addEventListener('feedbackDelivered', handleDelivered as EventListener);
    return () => document.removeEventListener('feedbackDelivered', handleDelivered as EventListener);
  }, [currentConversationId]);

  // Listen for WebSocket-level acknowledgment (feedback reached server queue)
  useEffect(() => {
    const feedbackWs = (window as any).feedbackWebSocket;
    if (!feedbackWs?.ws) return;

    const wsHandler = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'feedback_status' && data.status === 'delivered') {
          console.log('📝 FEEDBACK: WebSocket ack received:', data.message);
          // Upgrade from queued → delivered (monitor captured it)
          setFeedbackStatus('delivered');
          setTimeout(() => setFeedbackStatus('idle'), 4000);
        }
      } catch { /* ignore non-JSON */ }
    };

    const ws = feedbackWs.ws as WebSocket;
    ws.addEventListener('message', wsHandler);
    return () => ws.removeEventListener('message', wsHandler);
  }, [isCurrentlyStreaming, currentConversationId]);

  // Reset feedback status when streaming ends
  useEffect(() => {
    if (!isCurrentlyStreaming) setFeedbackStatus('idle');
  }, [isCurrentlyStreaming]);

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
  
  // Remove an inline image element from the editor and clean up state
  const removeImageElement = useCallback((imgElement: HTMLElement) => {
    const imageId = imgElement.dataset.imageId;
    if (imageId) {
      setAttachedImages(prev => prev.filter(img => img.id !== imageId));
    }
    imgElement.remove();
    // Re-sync input value after DOM change
    requestAnimationFrame(() => {
      handleInput();
    });
  }, [handleInput]);

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
      
      setFeedbackStatus('pending');
      // Use the global WebSocket if available
      const feedbackWebSocket = (window as any).feedbackWebSocket;
      if (feedbackWebSocket && (window as any).feedbackWebSocketReady) {
        const toolId = currentToolId || 'streaming_tool';
        feedbackWebSocket.sendFeedback(toolId, feedbackText);
        console.log('🔄 FEEDBACK:', feedbackText);
        setFeedbackStatus('queued');
        
        // Clear the input
        if (editorRef.current) editorRef.current.innerHTML = '';
        setInputValue('');
        draftsRef.current.delete(currentConversationId);
        
        message.info({
          content: (
            <span>📤 Feedback sent — waiting for delivery confirmation…</span>
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
      return;
    }
    
    // CRITICAL: Capture conversation ID before any async operations
    // This ensures responses go to the correct conversation even if user switches
    const targetConversationId = currentConversationId;
    
    setSubmittingConversationId(targetConversationId);
    
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
        undefined, // throttlingRecoveryDataRef - not used here
        currentProject // Pass current project so backend knows working directory
      );
    } catch (error) {
      console.error('Error sending message:', error);
    } finally {
      setSubmittingConversationId(null);
    }
  }, [
    shouldSendAsFeedback, sendToolFeedback, serializeEditorContent, isSubmitting,
    isCurrentlyStreaming, currentConversationId, setUserHasScrolled, addMessageToConversation,
    addStreamingConversation, currentMessages, checkedKeys, activeSkillPrompts,
    streamedContentMap, setStreamedContentMap, setIsStreaming, removeStreamingConversation,
    streamingConversations, updateProcessingState, setReasoningContentMap, currentProject
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
      return;
    }

    // Handle Backspace / Delete over inline images (contentEditable='false' elements
    // are not natively removable via keyboard in most browsers)
    if (e.key === 'Backspace' || e.key === 'Delete') {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return;

      // Case 1: Non-collapsed selection that contains (or is) an image
      if (!sel.isCollapsed) {
        const range = sel.getRangeAt(0);
        const fragment = range.cloneContents();
        const imgs = fragment.querySelectorAll('img[data-image-id]');
        if (imgs.length > 0) {
          // Let the browser delete the range, but also clean up our state
          // We need to find the actual DOM images (not the cloned ones)
          const editor = editorRef.current;
          if (editor) {
            const imageIds = new Set(Array.from(imgs).map(img => (img as HTMLElement).dataset.imageId));
            // After the browser processes the deletion, sync state
            requestAnimationFrame(() => {
              setAttachedImages(prev => prev.filter(img => !imageIds.has(img.id)));
              handleInput();
            });
          }
        }
        return; // let browser handle the range deletion
      }

      // Case 2: Collapsed cursor — check adjacent node
      const { anchorNode, anchorOffset } = sel;
      if (!anchorNode) return;

      let targetImg: HTMLElement | null = null;

      if (anchorNode.nodeType === Node.ELEMENT_NODE) {
        // Cursor is between child nodes of a container (e.g. the editor div itself)
        const children = anchorNode.childNodes;
        if (e.key === 'Backspace' && anchorOffset > 0) {
          const prev = children[anchorOffset - 1] as HTMLElement;
          if (prev?.tagName === 'IMG') targetImg = prev;
        } else if (e.key === 'Delete' && anchorOffset < children.length) {
          const next = children[anchorOffset] as HTMLElement;
          if (next?.tagName === 'IMG') targetImg = next;
        }
      } else if (anchorNode.nodeType === Node.TEXT_NODE) {
        // Cursor is inside a text node — check siblings
        if (e.key === 'Backspace' && anchorOffset === 0) {
          const prev = anchorNode.previousSibling as HTMLElement;
          if (prev?.tagName === 'IMG') targetImg = prev;
        } else if (e.key === 'Delete' && anchorOffset === (anchorNode.textContent?.length || 0)) {
          const next = anchorNode.nextSibling as HTMLElement;
          if (next?.tagName === 'IMG') targetImg = next;
        }
      }

      if (targetImg && targetImg.dataset?.imageId) {
        e.preventDefault();
        removeImageElement(targetImg);
      }
    }
  }, [shouldSendAsFeedback, sendToolFeedback, handleSend, removeImageElement, handleInput]);
  
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
          
          {/* Feedback delivery status indicator */}
          {feedbackStatus !== 'idle' && (
            <div style={{
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              color: feedbackStatus === 'delivered'
                ? (isDarkMode ? '#95de64' : '#52c41a')
                : (isDarkMode ? '#faad14' : '#d48806'),
              transition: 'opacity 0.3s',
              opacity: feedbackStatus === 'idle' ? 0 : 1,
            }}>
              <span style={{
                display: 'inline-block',
                animation: feedbackStatus !== 'delivered' ? 'pulse 1.2s ease-in-out infinite' : 'none',
              }}>
                {feedbackStatus === 'pending' ? '⏳ Sending…' : feedbackStatus === 'queued' ? '📤 Queued — awaiting model…' : '✅ Delivered to model'}
              </span>
            </div>
          )}

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
