export const fetchDefaultIncludedFolders = async (): Promise<string[]> => {
    const response = await fetch('/api/default-included-folders');
    const data = await response.json();
    return data.defaultIncludedFolders
};