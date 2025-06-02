import os.path
from typing import List, Optional
from app.utils.logging_utils import logger
from app.utils.document_extractor import is_document_file, extract_document_text
 
# Define binary extensions once at module level
BINARY_EXTENSIONS = {
    '.pyc', '.pyo', '.pyd', '.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg',
    '.core', '.bin', '.exe', '.dll', '.so', '.dylib', '.class',
    '.woff', '.woff2', '.ttf', '.eot', '.zip'
}
 
def is_binary_file(file_path: str) -> bool:
    """Check if a file is binary based on extension or content.
    
    Args:
        file_path (str): Path to the file to check
    Returns:
        True if the file is binary, False if it's text
    """
    try:
        # First, try using python-magic if available
        if os.path.isdir(file_path):
            return False

        # Check extension for known binary types
        if any(file_path.endswith(ext) for ext in BINARY_EXTENSIONS):
            return True
            
        # Try to detect if file is binary by reading first few bytes
        with open(file_path, 'rb') as file:
            return b'\x00' in file.read(1024)
    except Exception as e:
        # Only log at debug level for unusual errors
        logger.warning(f"Could not determine file type for {file_path}, assuming binary")
        return True

def is_processable_file(file_path: str) -> bool:
    """Check if a file can be processed (either text or extractable document)."""
    result = not is_binary_file(file_path) or is_document_file(file_path)
    return result

def read_file_content(file_path: str) -> Optional[str]:
    """
    Read content from a file, handling both text and document files.
    
    Args:
        file_path: Path to the file
        
    Returns:
        File content as string, or None if reading failed
    """
    try:
        # Check if it's a document file first
        from app.utils.document_extractor import is_document_file, extract_document_text
        if is_document_file(file_path):
            content = extract_document_text(file_path)
            if content is not None:
                return content
            # If document extraction failed, don't try to read as text
            logger.warning(f"Document extraction failed for {file_path}")
            return None
        
        # Regular text file
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read file {file_path}: {e}")
        return None
