import React, {createContext, ReactNode, useContext, useEffect, useState} from 'react';
import {Folders} from "../utils/types";
import {convertToTreeData} from "../utils/folderUtil";
import {TreeDataNode} from "antd";

interface FolderContextType {
  folders: Folders | undefined;
  treeData: TreeDataNode[];
  checkedKeys: React.Key[];
  setCheckedKeys: React.Dispatch<React.SetStateAction<React.Key[]>>;
  searchValue: string;
  setSearchValue: React.Dispatch<React.SetStateAction<string>>;
  expandedKeys: React.Key[];
  setExpandedKeys: React.Dispatch<React.SetStateAction<React.Key[]>>;
}

const FolderContext = createContext<FolderContextType | undefined>(undefined);

export const FolderProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [folders, setFolders] = useState<Folders>();
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [checkedKeys, setCheckedKeys] = useState<React.Key[]>(() => {
    const saved = localStorage.getItem('ZIYA_CHECKED_FOLDERS');
    return saved ? JSON.parse(saved) : [];
  });
  const [searchValue, setSearchValue] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => {
    const saved = localStorage.getItem('ZIYA_EXPANDED_FOLDERS');
    return saved ? JSON.parse(saved) : [];
  });

  // Save expanded folders whenever they change
  useEffect(() => {
    localStorage.setItem('ZIYA_EXPANDED_FOLDERS', JSON.stringify(expandedKeys));
  }, [expandedKeys]);

  // Save checked folders whenever they change
  useEffect(() => {
    localStorage.setItem('ZIYA_CHECKED_FOLDERS', JSON.stringify(checkedKeys));
  }, [checkedKeys]);

  useEffect(() => {
    const fetchFolders = async () => {
      try {
        const response = await fetch('/api/folders');
        const data = await response.json();
        setFolders(data);
        setTreeData(convertToTreeData(data));
      } catch (error) {
        console.error('Error fetching folders:', error);
      }
    };
    fetchFolders();
  }, []);

  return (
    <FolderContext.Provider value={{
      folders,
      treeData,
      checkedKeys,
      setCheckedKeys,
      searchValue,
      setSearchValue,
      expandedKeys,
      setExpandedKeys
    }}>
      {children}
    </FolderContext.Provider>
  );
};

export const useFolderContext = () => {
  const context = useContext(FolderContext);
  if (context === undefined) {
    throw new Error('useFolderContext must be used within a FolderProvider');
  }
  return context;
};
