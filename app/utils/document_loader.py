import os
from typing import List
from app.utils.logging_utils import logger

from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_core.documents import Document
 
 
class DocumentLoader:
    @staticmethod
    def load_document(file_path: str) -> List[Document]:
        """
        Load a document based on its file extension.
        
        Args:
            file_path (str): Path to the document
            
        Returns:
            List[Document]: List of Document objects containing the document's content
        """
        # Skip silently if the path points to a folder
        if os.path.isdir(file_path):
            return []

        # Skip binary files by extension
        binary_extensions = {
            '.pyc', '.pyo', '.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg',
            '.core', '.bin', '.exe', '.dll', '.so', '.dylib', '.class', 
            '.pyd', '.woff', '.woff2', '.ttf', '.eot'
        }
        if any(file_path.endswith(ext) for ext in binary_extensions):
            logger.debug(f"Skipping binary file by extension: {file_path}")
            return []

        _, file_extension = os.path.splitext(file_path.lower())
        
        try:
            if file_extension == '.pdf':
                loader = PyPDFLoader(file_path)
                return loader.load()
            else:
                # Default to text loader for all other file types
                loader = TextLoader(file_path)
                return loader.load()
                
        except Exception as e:
            logger.error(f"Error loading file {file_path}: {str(e)}", exc_info=True)
            return []
