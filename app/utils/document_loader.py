import os
from typing import List
 
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
            print(f"Skipping file {file_path} due to error: {e}")
            return []