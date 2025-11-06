import React from 'react';
import {
  Description,
  PictureAsPdf,
  TableChart,
  Slideshow,
  Code,
  Image,
  VideoFile,
  AudioFile,
  Archive,
  InsertDriveFile,
  Folder,
  FolderOpen,
  Javascript,
  DataObject,
  Language
} from '@mui/icons-material';

export const getFileIcon = (fileName: string, isOpen?: boolean): JSX.Element => {
  const extension = fileName.toLowerCase().split('.').pop() || '';

  // Document types
  if (extension === 'pdf') {
    return <PictureAsPdf sx={{ color: '#d32f2f', fontSize: 16, mr: 0.5 }} />;
  }

  if (['doc', 'docx'].includes(extension)) {
    return <Description sx={{ color: '#1976d2', fontSize: 16, mr: 0.5 }} />;
  }

  if (['xls', 'xlsx'].includes(extension)) {
    return <TableChart sx={{ color: '#388e3c', fontSize: 16, mr: 0.5 }} />;
  }

  if (['ppt', 'pptx'].includes(extension)) {
    return <Slideshow sx={{ color: '#f57c00', fontSize: 16, mr: 0.5 }} />;
  }

  // Code files
  if (['js', 'jsx', 'ts', 'tsx'].includes(extension)) {
    return <Javascript sx={{ color: '#f7df1e', fontSize: 16, mr: 0.5 }} />;
  }

  if (['py', 'java', 'cpp', 'c', 'h', 'cs', 'php', 'rb', 'go', 'rs'].includes(extension)) {
    return <Code sx={{ color: '#7b1fa2', fontSize: 16, mr: 0.5 }} />;
  }

  if (['json', 'xml', 'yaml', 'yml'].includes(extension)) {
    return <DataObject sx={{ color: '#455a64', fontSize: 16, mr: 0.5 }} />;
  }

  // Markdown and text files
  if (['md', 'txt', 'readme'].includes(extension)) {
    return <Description sx={{ color: '#616161', fontSize: 16, mr: 0.5 }} />;
  }

  // Image files
  if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp'].includes(extension)) {
    return <Image sx={{ color: '#e91e63', fontSize: 16, mr: 0.5 }} />;
  }

  // Video files
  if (['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv'].includes(extension)) {
    return <VideoFile sx={{ color: '#3f51b5', fontSize: 16, mr: 0.5 }} />;
  }

  // Audio files
  if (['mp3', 'wav', 'flac', 'aac', 'ogg', 'wma'].includes(extension)) {
    return <AudioFile sx={{ color: '#ff5722', fontSize: 16, mr: 0.5 }} />;
  }

  // Archive files
  if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2'].includes(extension)) {
    return <Archive sx={{ color: '#795548', fontSize: 16, mr: 0.5 }} />;
  }

  // Network capture files
  if (['pcap', 'pcapng', 'cap', 'dmp'].includes(extension)) {
    return <Language sx={{ color: '#00bcd4', fontSize: 16, mr: 0.5 }} />;
  }

  // Default file icon
  return <InsertDriveFile sx={{ color: '#9e9e9e', fontSize: 16, mr: 0.5 }} />;
};

export const getFolderIcon = (isOpen: boolean): JSX.Element => {
  return isOpen ?
    <FolderOpen sx={{ color: '#ff9800', fontSize: 16, mr: 0.5 }} /> :
    <Folder sx={{ color: '#ff9800', fontSize: 16, mr: 0.5 }} />;
};
