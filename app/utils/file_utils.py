import os.path
from app.utils.logging_utils import logger
 
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
        bool: True if the file is binary, False otherwise
    """
    try:
        # Check if path is a directory first
        if os.path.isdir(file_path):
            return False

        # Check extension for known binary types
        if any(file_path.endswith(ext) for ext in BINARY_EXTENSIONS):
            logger.debug(f"Detected binary file by extension: {file_path}")
            return True
            
        # Try to detect if file is binary by reading first few bytes
        with open(file_path, 'rb') as file:
            return b'\x00' in file.read(1024)
    except Exception as e:
        logger.debug(f"Unable to process file {file_path}: {str(e)}")
        return False
