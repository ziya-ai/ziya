import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Button, Tooltip, Space, Dropdown, Menu, Spin } from 'antd';
import { CopyOutlined, CheckOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { Typography } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { RedoOutlined, SoundOutlined, MutedOutlined, PictureOutlined, CodeOutlined, EyeOutlined } from "@ant-design/icons";
import { DocumentChip, ImageChip } from './FileChip';
import ModelChangeNotification from './ModelChangeNotification';
import { useSetQuestion } from '../context/QuestionContext';

const { Text } = Typography;

export interface ConversationProps {
  messages: Message[];
  onRetry?: (messageId: string) => void;
  onEdit?: (messageId: string, content: string) => void;
  onDelete?: (messageId: string) => void;
}
