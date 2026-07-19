export const fetchDefaultIncludedFolders = async (projectPath?: string): Promise<string[]> => {
    const url = projectPath
        ? `/api/default-included-folders?project_path=${encodeURIComponent(projectPath)}`
        : '/api/default-included-folders';
    const response = await fetch(url);
    if (!response.ok) return [];
    const data = await response.json();
    return Array.isArray(data.defaultIncludedFolders) ? data.defaultIncludedFolders : [];
};
