import React, { useEffect, useRef, memo, useState, useCallback, useMemo, useLayoutEffect } from "react";
import { useChatContext } from '../context/ChatContext';
import { sendPayload } from "../apis/chatApi";
import { Message } from "../utils/types";
import { convertKeysToStrings } from "../utils/types";
import { useFolderContext } from "../context/FolderContext";
import { Button, Input, message } from 'antd';
import { SendOutlined } from "@ant-design/icons";
import { useQuestionContext } from '../context/QuestionContext';
import { ThrottlingErrorDisplay } from './ThrottlingErrorDisplay';

const { TextArea } = Input;

const isQuestionEmpty = (input: string) => input.trim().length === 0;

interface SendChatContainerProps {
    fixed?: boolean;
    empty?: boolean;
}

export const SendChatContainer: React.FC<SendChatContainerProps> = memo(({ fixed = false, empty = false }) => {
    // Remove heavy performance monitoring during input

    const {
        isStreaming,
        setIsStreaming,
        addMessageToConversation,
        streamedContentMap,
        setStreamedContentMap,
        setReasoningContentMap,
        currentMessages,
        currentConversationId,
        streamingConversations,
        addStreamingConversation,
        removeStreamingConversation,
        updateProcessingState,
        setUserHasScrolled
        getProcessingState
    } = useChatContext();

    const { checkedKeys } = useFolderContext();
