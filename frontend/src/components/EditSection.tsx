import React, { useState, useRef, useEffect } from "react";
import { useChatContext } from '../context/ChatContext';
import { sendPayload } from "../apis/chatApi";
import { Message, ImageAttachment } from "../utils/types";
import { useFolderContext } from "../context/FolderContext";
import { useProject } from "../context/ProjectContext";
import { Button, Tooltip, Input, Space, message, Image as AntImage } from "antd";
import { convertKeysToStrings } from '../utils/types';
import { modelCapabilitiesService } from '../services/modelCapabilitiesService';
import {
    EditOutlined, CheckOutlined, CloseOutlined, SaveOutlined,
    PictureOutlined, PaperClipOutlined, DeleteOutlined
} from "@ant-design/icons";

interface EditSectionProps {
    index: number;
    isInline?: boolean;
}

export const EditSection: React.FC<EditSectionProps> = ({ index, isInline = false }) => {
    const {
        currentMessages,
        currentConversationId,
        addMessageToConversation,
        setIsStreaming,
        setConversations,
        streamingConversations,
        addStreamingConversation,
        streamedContentMap,
        setStreamedContentMap,
        removeStreamingConversation,
        editingMessageIndex,
        setEditingMessageIndex
    } = useChatContext();
    const { currentProject } = useProject();

    const [editedMessage, setEditedMessage] = useState(currentMessages[index].content);
    const { checkedKeys } = useFolderContext();
    const [attachedImages, setAttachedImages] = useState<ImageAttachment[]>([]);
    const [supportsVision, setSupportsVision] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const { TextArea } = Input;
    const isEditing = editingMessageIndex === index;

    // Focus the textarea when editing starts
    useEffect(() => {
        if (isEditing && textareaRef.current) {
            setTimeout(() => {
                if (textareaRef.current) {
                    textareaRef.current.focus();
                }
            }, 100);
        }
    }, [isEditing]);

    // Check vision support on mount
    // This is cached globally - only fetches once per app load
    useEffect(() => {
        modelCapabilitiesService.getCapabilities()
            .then(cap => setSupportsVision(cap.supports_vision || false))
            .catch(() => setSupportsVision(false));
    }, []);

    // Initialize attachedImages when editing starts
    useEffect(() => {
        if (isEditing) {
            const messageImages = currentMessages[index].images || [];
            setAttachedImages(messageImages);
        }
    }, [isEditing, index, currentMessages]);

    // Process image files (from file input or drag-drop)
    const processImageFiles = async (files: FileList | null) => {
        if (!files || files.length === 0) return;

        const maxSize = 5 * 1024 * 1024; // 5MB limit
        const maxImages = 5;

        if (attachedImages.length + files.length > maxImages) {
            message.warning(`Maximum ${maxImages} images per message`);
            return;
        }

        for (let i = 0; i < files.length; i++) {
            const file = files[i];

            if (!file.type.startsWith('image/')) {
                message.error(`${file.name} is not an image file`);
                continue;
            }

            if (file.size > maxSize) {
                message.error(`${file.name} is too large (max 5MB)`);
                continue;
            }

            try {
                const reader = new FileReader();
                const imageData = await new Promise<string>((resolve, reject) => {
                    reader.onload = () => resolve(reader.result as string);
                    reader.onerror = reject;
                    reader.readAsDataURL(file);
                });

                const matches = imageData.match(/^data:(.+);base64,(.+)$/);
                if (!matches) {
                    throw new Error('Invalid image data format');
                }

                const [, mediaType, data] = matches;

                const img = new Image();
                await new Promise((resolve, reject) => {
                    img.onload = resolve;
                    img.onerror = reject;
                    img.src = imageData;
                });

                const attachment: ImageAttachment = {
                    data,
                    mediaType: mediaType as ImageAttachment['mediaType'],
                    filename: file.name,
                    size: file.size,
                    width: img.width,
                    height: img.height
                };

                setAttachedImages(prev => [...prev, attachment]);
                message.success(`Added ${file.name}`);
            } catch (error) {
                console.error('Error reading image:', error);
                message.error(`Failed to load ${file.name}`);
            }
        }

        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    };

    // Handle image selection from file input
    const handleImageSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
        await processImageFiles(event.target.files);
    };

    // Handle drag and drop
    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
    };

    const handleDrop = async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();

        if (!supportsVision) {
            message.warning('Current model does not support image attachments');
            return;
        }

        const files = e.dataTransfer.files;
        if (files && files.length > 0) {
            await processImageFiles(files);
        }
    };

    const removeImage = (imgIndex: number) => {
        setAttachedImages(prev => prev.filter((_, i) => i !== imgIndex));
    };

    const triggerFileInput = () => {
        if (!supportsVision) {
            message.warning('Current model does not support image attachments');
            return;
        }
        fileInputRef.current?.click();
    };

    const handleEdit = () => {
        setEditingMessageIndex(index);
    };

    const handleSave = () => {
        // Update the conversation in the context with the edited message
        setConversations(prev => prev.map(conv => {
            if (conv.id === currentConversationId) {
                const updatedMessages = conv.messages.map((msg, i) => {
                    if (i === index) {
                        return {
                            ...msg,
                            content: editedMessage,
                            images: attachedImages.length > 0 ? attachedImages : undefined,
                            _timestamp: Date.now()  // Update timestamp to mark as modified
                        };
                    }
                    return msg;
                });
                return { ...conv, messages: updatedMessages, _version: Date.now() };
            }
            return conv;
        }));
        setEditingMessageIndex(null);
    };

    const handleCancel = () => {
        setEditingMessageIndex(null);
        setEditedMessage(currentMessages[index].content);
    };

    const handleSubmit = async () => {
        setEditingMessageIndex(null);
        setAttachedImages([]);

        // Clear any existing streamed content
        setStreamedContentMap(new Map());

        // Create truncated message array up to and including edited message
        const truncatedMessages = currentMessages.slice(0, index + 1);

        // Update the edited message
        truncatedMessages[index] = {
            ...truncatedMessages[index],
            content: editedMessage,
            images: attachedImages.length > 0 ? attachedImages : undefined,
            _timestamp: Date.now(),
            // Add a marker to indicate this message was edited and truncated
            _edited: true,
            _truncatedAfter: true
        };

        // Set conversation to just the truncated messages
        setConversations(prev => prev.map(conv =>
            conv.id === currentConversationId
                ? { ...conv, messages: truncatedMessages, _version: Date.now(), _editInProgress: true }
                : conv
        ));

        addStreamingConversation(currentConversationId);
        try {
            const result = await sendPayload(
                truncatedMessages,
                editedMessage,
                convertKeysToStrings(checkedKeys),
                currentConversationId,
                undefined,
                undefined, // images
                streamedContentMap,
                setStreamedContentMap,
                setIsStreaming,
                removeStreamingConversation,
                addMessageToConversation,
                streamingConversations.has(currentConversationId),
                undefined, // setProcessingState - not available in EditSection
                undefined, // setReasoningContentMap
                undefined, // throttlingRecoveryDataRef
                currentProject
            );
            // sendPayload already adds the message to conversation, so we just need to clear the flag
            if (result) {
                // Clear the edit in progress flag after the response is complete
                // Note: The message is already added by sendPayload, so we don't add it again
                setConversations(prev => prev.map(conv =>
                    conv.id === currentConversationId
                        ? { ...conv, _editInProgress: false }
                        : conv
                ));
            }
        } catch (error) {
            console.error('Error sending message:', error);
            removeStreamingConversation(currentConversationId);
        } finally {
            setIsStreaming(false);
        }
    };

    return (
        <div>
            {/* If this is the inline version and we're editing this message, don't render anything */}
            {isInline && isEditing && (
                null
            )}

            {/* If we're editing and this is NOT the inline version, show full edit interface */}
            {isEditing && !isInline && (
                <div style={{ width: '100%' }}>
                    {/* Header row with sender and buttons */}
                    <div style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: '8px',
                        width: '100%'
                    }}>
                        <div className="message-sender">You:</div>
                        <Space>
                            <Tooltip title="Attach image">
                                <Button icon={<PaperClipOutlined />} onClick={triggerFileInput} size="small" disabled={!supportsVision || attachedImages.length >= 5} />
                            </Tooltip>
                            <Tooltip title="Cancel editing">
                                <Button icon={<CloseOutlined />} onClick={handleCancel} size="small">
                                    Cancel
                                </Button>
                            </Tooltip>
                            <Tooltip title="Save changes to context">
                                <Button icon={<SaveOutlined />} onClick={handleSave} size="small">
                                    Save
                                </Button>
                            </Tooltip>
                            <Tooltip title="Send to model, remove newer responses">
                                <Button icon={<CheckOutlined />} onClick={handleSubmit} size="small" type="primary">
                                    Submit
                                </Button>
                            </Tooltip>
                        </Space>
                    </div>

                    {/* Display attached images */}
                    {attachedImages.length > 0 && (
                        <div style={{
                            marginBottom: '12px',
                            display: 'flex',
                            gap: '8px',
                            flexWrap: 'wrap'
                        }}>
                            {attachedImages.map((img, imgIndex) => (
                                <div key={imgIndex} style={{
                                    position: 'relative',
                                    display: 'inline-block'
                                }}>
                                    <AntImage
                                        src={`data:${img.mediaType};base64,${img.data}`}
                                        alt={img.filename || 'Attached image'}
                                        width={120}
                                        height={120}
                                        style={{
                                            objectFit: 'cover',
                                            borderRadius: '4px',
                                            border: '1px solid #d9d9d9'
                                        }}
                                        preview={{ mask: <PictureOutlined /> }}
                                    />
                                    <Button
                                        type="primary"
                                        danger
                                        size="small"
                                        icon={<DeleteOutlined />}
                                        onClick={() => removeImage(imgIndex)}
                                        style={{
                                            position: 'absolute',
                                            top: 4,
                                            right: 4,
                                            minWidth: '24px',
                                            height: '24px',
                                            padding: '0 4px'
                                        }}
                                    />
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Full-width textarea */}
                    <TextArea
                        ref={textareaRef}
                        autoFocus
                        style={{
                            width: '100%',
                            minHeight: '100px',
                            resize: 'vertical'
                        }}
                        value={editedMessage}
                        onChange={(e) => setEditedMessage(e.target.value)}
                        autoSize={{
                            minRows: 3,
                            maxRows: 20
                        }}
                        placeholder="Edit your message..."
                        onDragOver={handleDragOver}
                        onDrop={handleDrop}
                    />

                    {/* Hidden file input for image selection */}
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/*"
                        multiple
                        style={{ display: 'none' }}
                        onChange={handleImageSelect}
                    />
                </div>
            )}

            {/* Show edit button if not editing and this is inline, OR if not editing and not inline */}
            {!isEditing && (isInline || !isEditing) && (
                <Tooltip title="Edit">
                    <Button icon={<EditOutlined />} onClick={handleEdit} />
                </Tooltip>
            )}
        </div>
    );
};

