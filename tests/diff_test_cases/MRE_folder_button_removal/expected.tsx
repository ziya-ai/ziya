import React from 'react';
import { Tree } from 'antd';
import { useDispatch, useSelector } from 'react-redux';
import { setSelectedFile, setSelectedFolder } from '../redux/actions';
import { RootState } from '../redux/store';
import { FileTreeNode } from '../types';
import { TokenCountDisplay } from "./TokenCountDisplay";
import union from 'lodash/union';
import { debounce } from 'lodash';
import { ChatHistory } from "./ChatHistory";
import { ModelConfigButton } from './ModelConfigButton';
import { ReloadOutlined, FolderOutlined, MessageOutlined } from '@ant-design/icons';
import { convertToTreeData } from '../utils/folderUtil';
import { MUIChatHistory } from './MUIChatHistory';

// Rest of the file content would be here...

export const FolderTree = React.memo(({ isPanelCollapsed }: FolderTreeProps) => {
    // Component implementation...
    
    // Somewhere in the render method:
    return (
        <div>
            {/* Other component content */}
            <div>
                <div className="chat-history-header">
                    <span>
                        <MessageOutlined style={{ marginRight: 8 }} />
                        Chat History
                    </span>
                </div>
            </div>
            <MUIChatHistory />
        </div>
    );
});
