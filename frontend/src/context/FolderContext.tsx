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
	if (!response.ok) {
          throw new Error(`Failed to fetch folders: ${response.status}`);
        }
        const data = await response.json();

        // Log the raw folder structure
        console.debug('Raw folder structure:', {
          componentsPath: data?.frontend?.src?.components,
          d3Files: Object.keys(data?.frontend?.src?.components?.children || {})
            .filter(f => f.includes('D3') || f === 'Debug.tsx')
        });

        // Store the complete folder structure
        setFolders(data);

        // Get all available file paths recursively
        const getAllPaths = (obj: any, prefix: string = ''): string[] => {
          return Object.entries(obj).flatMap(([key, value]: [string, any]) => {
            const path = prefix ? `${prefix}/${key}` : key;
            return value.children ? [...getAllPaths(value.children, path), path] : [path];
          });
        };

        const availablePaths = getAllPaths(data);
        console.debug('Available paths:', {
          total: availablePaths.length,
          d3Files: availablePaths.filter(p => p.includes('D3') || p.includes('Debug.tsx')),
          componentFiles: availablePaths.filter(p => p.includes('components/'))
        });

        setTreeData(convertToTreeData(data));

        // Update checked keys to include all available files and maintain selections
        setCheckedKeys(prev => {
          const currentChecked = new Set(prev as string[]);

	  // Get the parent directory of any checked directory
          const getParentDir = (path: string) => {
            const parts = path.split('/');
            return parts.slice(0, -1).join('/');
          };

          // If a directory is checked, include all its files
          const newChecked = new Set<string>();
          for (const path of availablePaths) {
            const parentDir = getParentDir(path);
            if (currentChecked.has(path) || currentChecked.has(parentDir)) {
              newChecked.add(path);
            }
          }

          console.log('Syncing checked keys:', {
            before: currentChecked.size,
            after: newChecked.size,
            added: [...newChecked].filter(k => !currentChecked.has(k)),
            removed: [...currentChecked].filter(k => !newChecked.has(k))
          });

	  // Debug log for D3 files
          const d3Files = [...newChecked].filter(k => k.includes('D3') || k.includes('Debug.tsx'));
          if (d3Files.length > 0) {
            console.log('D3 files in checked keys:', d3Files);
          }

          // Return the updated set of checked keys
          return [...newChecked];
        });
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
