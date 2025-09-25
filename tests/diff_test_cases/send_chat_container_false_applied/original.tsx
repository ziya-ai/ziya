import React, { memo, useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Send, Square, Paperclip, X } from 'lucide-react';
import { useChatContext } from '@/contexts/ChatContext';
import { useFolderContext } from '@/contexts/FolderContext';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/lib/utils';
import { FileAttachment } from '@/types/chat';
import { AttachmentPreview } from './AttachmentPreview';

interface SendChatContainerProps {
    fixedHeight?: boolean;
    className?: string;
}

export const SendChatContainer: React.FC<SendChatContainerProps> = memo(({ fixedHeight = false, className }) => {
    const {
        sendMessage,
        isStreaming,
        stopStreaming,
        addMessageToConversation,
        currentConversationId,
        removeStreamingConversation,
        updateProcessingState,
        setUserHasScrolled,
        getProcessingState
    } = useChatContext();

    const { checkedKeys } = useFolderContext();
    const { toast } = useToast();
    
    const [message, setMessage] = useState('');
    const [attachments, setAttachments] = useState<FileAttachment[]>([]);
    const [isDragOver, setIsDragOver] = useState(false);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const adjustTextareaHeight = useCallback(() => {
        const textarea = textareaRef.current;
        if (textarea) {
            textarea.style.height = 'auto';
            const scrollHeight = textarea.scrollHeight;
            const maxHeight = fixedHeight ? 120 : 200;
            textarea.style.height = `${Math.min(scrollHeight, maxHeight)}px`;
        }
    }, [fixedHeight]);

    useEffect(() => {
        adjustTextareaHeight();
    }, [message, adjustTextareaHeight]);

    const handleSubmit = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        
        if (!message.trim() && attachments.length === 0) return;
        if (isStreaming) return;

        const messageContent = message.trim();
        const messageAttachments = [...attachments];
        
        // Clear the form immediately
        setMessage('');
        setAttachments([]);
        
        // Reset textarea height
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
        }

        try {
            // Create the human message object
            const newHumanMessage = {
                id: Date.now().toString(),
                content: messageContent,
                role: 'human' as const,
                timestamp: new Date().toISOString(),
                attachments: messageAttachments,
                conversationId: currentConversationId
            };

            // Add the human message immediately
            addMessageToConversation(newHumanMessage, currentConversationId);

            // Clear streamed content and add the human message immediately
            setStreamedContentMap(new Map());
            
            // Send the message
            await sendMessage(messageContent, messageAttachments, checkedKeys);
        } catch (error) {
            console.error('Error sending message:', error);
            toast({
                title: "Error",
                description: "Failed to send message. Please try again.",
                variant: "destructive",
            });
        }
    }, [message, attachments, isStreaming, sendMessage, checkedKeys, addMessageToConversation, currentConversationId, toast]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit(e as any);
        }
    }, [handleSubmit]);

    const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || []);
        const newAttachments: FileAttachment[] = files.map(file => ({
            id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
            name: file.name,
            size: file.size,
            type: file.type,
            file: file
        }));
        
        setAttachments(prev => [...prev, ...newAttachments]);
        
        // Reset file input
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    }, []);

    const removeAttachment = useCallback((id: string) => {
        setAttachments(prev => prev.filter(att => att.id !== id));
    }, []);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
        
        const files = Array.from(e.dataTransfer.files);
        const newAttachments: FileAttachment[] = files.map(file => ({
            id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
            name: file.name,
            size: file.size,
            type: file.type,
            file: file
        }));
        
        setAttachments(prev => [...prev, ...newAttachments]);
    }, []);

    return (
        <div className={cn("border-t bg-background p-4", className)}>
            <form onSubmit={handleSubmit} className="space-y-3">
                {/* Attachments Preview */}
                {attachments.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                        {attachments.map((attachment) => (
                            <AttachmentPreview
                                key={attachment.id}
                                attachment={attachment}
                                onRemove={removeAttachment}
                            />
                        ))}
                    </div>
                )}

                {/* Input Area */}
                <div 
                    className={cn(
                        "relative flex items-end gap-2 rounded-lg border p-3 transition-colors",
                        isDragOver && "border-primary bg-primary/5"
                    )}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                >
                    <Textarea
                        ref={textareaRef}
                        value={message}
                        onChange={(e) => setMessage(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Type your message..."
                        className="min-h-[40px] resize-none border-0 p-0 shadow-none focus-visible:ring-0"
                        disabled={isStreaming}
                        style={{ height: 'auto' }}
                    />
                    
                    <div className="flex items-center gap-1">
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => fileInputRef.current?.click()}
                            disabled={isStreaming}
                            className="h-8 w-8 p-0"
                        >
                            <Paperclip className="h-4 w-4" />
                        </Button>
                        
                        {isStreaming ? (
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={stopStreaming}
                                className="h-8 w-8 p-0"
                            >
                                <Square className="h-4 w-4" />
                            </Button>
                        ) : (
                            <Button
                                type="submit"
                                variant="ghost"
                                size="sm"
                                disabled={!message.trim() && attachments.length === 0}
                                className="h-8 w-8 p-0"
                            >
                                <Send className="h-4 w-4" />
                            </Button>
                        )}
                    </div>
                </div>
            </form>

            {/* Hidden file input */}
            <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={handleFileSelect}
                accept="*/*"
            />
        </div>
    );
});

SendChatContainer.displayName = 'SendChatContainer';
