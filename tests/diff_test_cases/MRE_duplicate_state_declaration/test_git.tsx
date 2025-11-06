import React, { createContext, ReactNode, useContext, useEffect, useState } from 'react';
import { Folders } from "../utils/types";
import { convertToTreeData } from "../utils/folderUtil";
import { useChatContext } from "./ChatContext";

interface TreeDataNode {
  title: string;
  key: string;
  children?: TreeDataNode[];
  isLeaf?: boolean;
  selectable?: boolean;
}

interface FolderContextType {
  folders: Folders | undefined;
  treeData: TreeDataNode[];
  checkedKeys: React.Key[];
  expandedKeys: React.Key[];
  searchValue: string;
  setFolders: (folders: Folders) => void;
  setCheckedKeys: (keys: React.Key[]) => void;
  setExpandedKeys: (keys: React.Key[]) => void;
  setSearchValue: (value: string) => void;
  getFolderTokenCount: (path: string, folderData?: Folders) => number;
}

export const FolderContext = createContext<FolderContextType | undefined>(undefined);

export const useFolderContext = () => {
  const context = useContext(FolderContext);
  if (!context) {
    throw new Error('useFolderContext must be used within a FolderProvider');
  }
  return context;
};

export const FolderProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [folders, setFolders] = useState<Folders>();
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [checkedKeys, setCheckedKeys] = useState<React.Key[]>(() => {
    const saved = localStorage.getItem('ZIYA_CHECKED_FOLDERS');
    return saved ? JSON.parse(saved) : [];
  });

  const { currentFolderId, folderFileSelections, folders: chatFolders } = useChatContext();
  const [searchValue, setSearchValue] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => {
    const saved = localStorage.getItem('ZIYA_EXPANDED_FOLDERS');
    return saved ? JSON.parse(saved) : [];
  });

  const getFolderTokenCount = (path: string, folderData: Folders | undefined): number => {
    if (!folderData) {
      // console.warn(`getFolderTokenCount: folderData is undefined for path "${path}"`);
      return 0;
    }

    let current: Folders | undefined = folderData;
    const parts = path.split('/');

    for (const part of parts) {
      if (!current) {
        break;
      }
      const node = current[part];
      if (node) {
        if (parts.indexOf(part) === parts.length - 1) { // Last part of the path
          return node.token_count || 0;
        }
        current = node.children;
      } else {
        // console.warn(`getFolderTokenCount: Path segment "${part}" not found in current node for path "${path}".`);
        return 0; // Path segment not found
      }
    }

    return 0;
  };

  // Save expanded folders whenever they change
  useEffect(() => {
    try {
      localStorage.setItem('ZIYA_EXPANDED_FOLDERS', JSON.stringify(expandedKeys));
    } catch (error) {
      console.error('Failed to save expanded folders to localStorage:', error);
    }
  }, [expandedKeys]);

  // Save checked folders whenever they change
  useEffect(() => {
    try {
      localStorage.setItem('ZIYA_CHECKED_FOLDERS', JSON.stringify(checkedKeys));
    } catch (error) {
      console.error('Failed to save checked folders to localStorage:', error);
    }
  }, [checkedKeys]);

  // Update checked keys when folder changes if folder has specific file selections
  useEffect(() => {
    if (currentFolderId) {
      const folder = chatFolders.find(f => f.id === currentFolderId);
      if (folder && !folder.useGlobalContext) {
        const fileSelections = folderFileSelections[folder.id];
        if (fileSelections) {
          setCheckedKeys(fileSelections);
        }
      }
    }
  }, [currentFolderId, folderFileSelections, chatFolders]);

  // Update tree data whenever folders change
  useEffect(() => {
    if (folders) {
      const newTreeData = convertToTreeData(folders);
      setTreeData(newTreeData);
    }
  }, [folders]);

  return (
    <FolderContext.Provider
      value={{
        folders,
        treeData,
        checkedKeys,
        expandedKeys,
        searchValue,
        setFolders,
        setCheckedKeys,
        setExpandedKeys,
        setSearchValue,
        getFolderTokenCount,
      }}
    >
      {children}
    </FolderContext.Provider>
  );
};
