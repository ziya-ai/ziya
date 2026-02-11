/**
 * DirectoryBrowserModal - Select a directory to create a new project
 * 
 * Reuses the browse-directory API from MUIFileExplorer pattern
 * but simplified for single directory selection only.
 */
import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  Box,
  LinearProgress,
  Breadcrumbs,
  Link
} from '@mui/material';
import FolderIcon from '@mui/icons-material/Folder';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import HomeIcon from '@mui/icons-material/Home';

interface DirectoryEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
}

interface DirectoryBrowserModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (path: string) => Promise<void>;
}

export const DirectoryBrowserModal: React.FC<DirectoryBrowserModalProps> = ({
  open,
  onClose,
  onSelect
}) => {
  const [browsePath, setBrowsePath] = useState<string>('~');
  const [pathInput, setPathInput] = useState<string>('~');
  const [browseEntries, setBrowseEntries] = useState<DirectoryEntry[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  // Browse a directory
  const browseDirectory = async (path: string) => {
    setBrowseLoading(true);
    try {
      const response = await fetch(`/api/browse-directory?path=${encodeURIComponent(path)}`);
      if (!response.ok) {
        const errorData = await response.json();
        console.error('Browse error:', errorData);
        return;
      }
      const data = await response.json();
      setBrowsePath(data.current_path);
      setPathInput(data.current_path);
      
      // Filter to only show directories
      const directories = data.entries.filter((e: DirectoryEntry) => e.is_dir);
      setBrowseEntries(directories);
      
      // Auto-select the current directory when browsing
      setSelectedPath(data.current_path);
    } catch (error) {
      console.error('Failed to browse directory:', error);
    } finally {
      setBrowseLoading(false);
    }
  };

  // Navigate up one level
  const handleNavigateUp = () => {
    const parentPath = browsePath.split('/').slice(0, -1).join('/') || '/';
    browseDirectory(parentPath);
  };

  // Handle path input submission
  const handlePathInputSubmit = () => {
    if (pathInput.trim()) {
      browseDirectory(pathInput.trim());
    }
  };

  const handlePathInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handlePathInputSubmit();
    }
  };

  // Handle directory selection
  const handleSelectDirectory = (path: string) => {
    setSelectedPath(path);
  };

  // Handle creating project from selected directory
  const handleCreateProject = async () => {
    if (!selectedPath) return;
    
    setIsCreating(true);
    try {
      await onSelect(selectedPath);
      onClose();
    } catch (error) {
      console.error('Failed to create project:', error);
    } finally {
      setIsCreating(false);
    }
  };

  // Parse breadcrumb from current path
  const getBreadcrumbs = () => {
    const parts = browsePath.split('/').filter(Boolean);
    return parts;
  };

  // Navigate to breadcrumb segment
  const navigateToBreadcrumb = (index: number) => {
    const parts = browsePath.split('/').filter(Boolean);
    const newPath = '/' + parts.slice(0, index + 1).join('/');
    browseDirectory(newPath);
  };

  // Initialize on open
  useEffect(() => {
    if (open) {
      browseDirectory('~');
      setSelectedPath(null);
    }
  }, [open]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{ 
        sx: { 
          height: '70vh', 
          maxHeight: 600,
          bgcolor: '#1a1a1a',
          backgroundImage: 'none'
        } 
      }}
    >
      <DialogTitle sx={{ pb: 1, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6" sx={{ fontSize: '16px', fontWeight: 500 }}>
          Select Project Directory
        </Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '12px' }}>
          Choose a folder to open as a new project
        </Typography>
      </DialogTitle>
      
      <DialogContent sx={{ p: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Breadcrumb navigation */}
        <Box sx={{ 
          px: 2, 
          py: 1.5, 
          bgcolor: '#141414', 
          borderBottom: 1, 
          borderColor: 'divider',
          overflow: 'auto',
          '&::-webkit-scrollbar': { height: 6 },
          '&::-webkit-scrollbar-track': { background: '#0a0a0a' },
          '&::-webkit-scrollbar-thumb': { background: '#333', borderRadius: 3 }
        }}>
          <Breadcrumbs 
            separator="/" 
            sx={{ 
              fontSize: '12px',
              '& .MuiBreadcrumbs-separator': { color: '#555' }
            }}
          >
            <Link
              component="button"
              onClick={() => browseDirectory('~')}
              sx={{ 
                color: 'primary.main', 
                textDecoration: 'none',
                cursor: 'pointer',
                '&:hover': { textDecoration: 'underline' }
              }}
            >
              <HomeIcon sx={{ fontSize: 14, verticalAlign: 'middle', mr: 0.5 }} />
              ~
            </Link>
            {getBreadcrumbs().map((part, idx) => (
              <Link
                key={idx}
                component="button"
                onClick={() => navigateToBreadcrumb(idx)}
                sx={{ 
                  color: idx === getBreadcrumbs().length - 1 ? '#fff' : 'primary.main',
                  textDecoration: 'none',
                  cursor: 'pointer',
                  fontWeight: idx === getBreadcrumbs().length - 1 ? 500 : 400,
                  '&:hover': { textDecoration: 'underline' }
                }}
              >
                {part}
              </Link>
            ))}
          </Breadcrumbs>
        </Box>

        {/* Path input bar */}
        <Box sx={{ px: 2, py: 1.5, borderBottom: 1, borderColor: 'divider', display: 'flex', gap: 1, alignItems: 'center' }}>
          <Typography variant="body2" sx={{ fontWeight: 500, flexShrink: 0, fontSize: '12px' }}>
            Path:
          </Typography>
          <TextField
            size="small"
            fullWidth
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            onKeyDown={handlePathInputKeyDown}
            placeholder="/path/to/directory"
            sx={{
              '& .MuiOutlinedInput-root': {
                fontFamily: 'monospace',
                fontSize: '0.875rem',
                height: '32px'
              }
            }}
          />
          <Button
            variant="contained"
            size="small"
            onClick={handlePathInputSubmit}
            disabled={browseLoading}
            sx={{ flexShrink: 0 }}
          >
            Go
          </Button>
        </Box>

        {/* Directory listing */}
        <Box sx={{ flex: 1, overflow: 'auto' }}>
          {browseLoading ? (
            <LinearProgress />
          ) : (
            <List dense disablePadding>
              {/* Parent directory entry */}
              {browsePath && browsePath !== '/' && browsePath !== '~' && (
                <ListItemButton 
                  onClick={handleNavigateUp} 
                  sx={{ borderBottom: 1, borderColor: 'divider' }}
                >
                  <ListItemIcon sx={{ minWidth: 36 }}>
                    <ArrowUpwardIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText 
                    primary=".." 
                    secondary="Parent directory"
                    primaryTypographyProps={{ fontStyle: 'italic', color: 'text.secondary' }}
                  />
                </ListItemButton>
              )}

              {browseEntries.map((entry) => (
                <ListItemButton
                  key={entry.path}
                  selected={selectedPath === entry.path}
                  onClick={() => handleSelectDirectory(entry.path)}
                  onDoubleClick={() => browseDirectory(entry.path)}
                  sx={{ 
                    pr: 2,
                    '&.Mui-selected': {
                      bgcolor: 'rgba(59, 130, 246, 0.15)',
                      borderLeft: '3px solid',
                      borderColor: 'primary.main',
                      '&:hover': {
                        bgcolor: 'rgba(59, 130, 246, 0.25)',
                      }
                    }
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 36 }}>
                    <FolderIcon fontSize="small" sx={{ color: 'primary.main' }} />
                  </ListItemIcon>
                  <ListItemText
                    primary={entry.name}
                    secondary="Click to select • Double-click to open"
                    primaryTypographyProps={{
                      fontWeight: selectedPath === entry.path ? 600 : 400,
                      color: selectedPath === entry.path ? 'primary.main' : 'text.primary'
                    }}
                    secondaryTypographyProps={{
                      sx: { fontSize: '0.7rem', opacity: 0.7 }
                    }}
                  />
                </ListItemButton>
              ))}

              {browseEntries.length === 0 && !browseLoading && (
                <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
                  No directories found
                </Typography>
              )}
            </List>
          )}
        </Box>

        {/* Current selection display */}
        {selectedPath && (
          <Box sx={{ 
            px: 2, 
            py: 1.5, 
            bgcolor: 'rgba(59, 130, 246, 0.1)',
            borderTop: 1,
            borderBottom: 1,
            borderColor: 'rgba(59, 130, 246, 0.3)'
          }}>
            <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '11px' }}>
              Selected:
            </Typography>
            <Typography sx={{ 
              fontSize: '12px', 
              color: 'primary.main', 
              fontFamily: 'monospace',
              mt: 0.5 
            }}>
              {selectedPath}
            </Typography>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
        <Button 
          onClick={onClose}
          disabled={isCreating}
          sx={{ color: 'text.secondary' }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleCreateProject}
          disabled={!selectedPath || isCreating}
          sx={{
            bgcolor: '#2563eb',
            '&:hover': { bgcolor: '#1d4ed8' }
          }}
        >
          {isCreating ? 'Creating...' : 'Open as Project'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
