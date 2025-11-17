"""
Document extraction utilities for PDF, DOC/DOCX, and XLS/XLSX files.

This module provides functions to extract text content from various document formats
so they can be meaningfully included in the context for the LLM.
"""

import os
import io
from typing import Optional, Dict, Any, List
import logging

from app.utils.logging_utils import logger

# Track which libraries are available
_LIBRARIES_CHECKED = False
_AVAILABLE_LIBRARIES = {
    'pypdf2': False,
    'pdfplumber': False,
    'python_docx': False,
    'openpyxl': False,
    'xlrd': False,
    'pandas': False,
    'python_pptx': False
}

def _check_libraries():
    """Check which document processing libraries are available."""
    global _LIBRARIES_CHECKED, _AVAILABLE_LIBRARIES
    
    if _LIBRARIES_CHECKED:
        return
    
    # Check PDF libraries
    try:
        import pypdf
        _AVAILABLE_LIBRARIES['pypdf'] = True
    except ImportError:
        pass
    
    try:
        import pdfplumber
        _AVAILABLE_LIBRARIES['pdfplumber'] = True
    except ImportError:
        pass
    
    # Check Word document libraries
    try:
        import docx
        _AVAILABLE_LIBRARIES['python_docx'] = True
    except ImportError:
        pass
    
    # Check Excel libraries
    try:
        import openpyxl
        _AVAILABLE_LIBRARIES['openpyxl'] = True
    except ImportError:
        pass
    
    try:
        import xlrd
        _AVAILABLE_LIBRARIES['xlrd'] = True
    except ImportError:
        pass
    
    try:
        import pandas
        _AVAILABLE_LIBRARIES['pandas'] = True
    except ImportError:
        pass
    
    try:
        import pptx
        _AVAILABLE_LIBRARIES['python_pptx'] = True
    except ImportError:
        pass
    
    _LIBRARIES_CHECKED = True
    
    # Log available libraries
    available = [lib for lib, avail in _AVAILABLE_LIBRARIES.items() if avail]
    if available:
        logger.info(f"Document extraction libraries available: {', '.join(available)}")
    else:
        logger.warning("No document extraction libraries found. Install with: pip install pypdf pdfplumber python-docx openpyxl pandas python-pptx")

def is_document_file(file_path: str) -> bool:
    """
    Check if a file is a supported document type.
    
    Args:
        file_path: Path to the file
        
    Returns:
        True if the file is a supported document type
    """
    _check_libraries()
    
    # If no libraries are available, don't treat any files as documents
    if not any(_AVAILABLE_LIBRARIES.values()):
        return False
    
    if not os.path.exists(file_path):
        return False
    
    ext = os.path.splitext(file_path)[1].lower()
    supported_extensions = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}
    
    return ext in supported_extensions

def is_tool_backed_file(file_path: str) -> bool:
    """
    Check if a file has specialized tool support (like pcap files).
    These files should be marked with -1 tokens to indicate tool availability.
    """
    ext = os.path.splitext(file_path)[1].lower()
    tool_backed_extensions = {'.pcap', '.pcapng', '.cap', '.dmp'}
    return ext in tool_backed_extensions

def extract_pdf_text(file_path: str) -> Optional[str]:
    """
    Extract text from a PDF file.
    
    Args:
        file_path: Path to the PDF file
        
    Returns:
        Extracted text or None if extraction failed
    """
    _check_libraries()
    
    # Try pdfplumber first (better text extraction)
    if _AVAILABLE_LIBRARIES['pdfplumber']:
        try:
            logger.debug(f"Using pdfplumber to extract from: {file_path}")
            import pdfplumber
            
            text_content = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_content.append(text)
                        logger.debug(f"Extracted {len(text)} chars from page {len(text_content)}")
            
            logger.debug(f"Total pages processed: {len(text_content)}")
            return '\n\n'.join(text_content) if text_content else None
            
        except Exception as e:
            logger.warning(f"pdfplumber failed for {file_path}: {e}")
    
    # Fallback to pypdf
    if _AVAILABLE_LIBRARIES['pypdf']:
        try:
            import pypdf
            
            text_content = []
            with open(file_path, 'rb') as file:
                pdf_reader = pypdf.PdfReader(file)
                for page in pdf_reader.pages:
                    text = page.extract_text()
                    if text:
                        text_content.append(text)
            
            return '\n\n'.join(text_content) if text_content else None
            
        except Exception as e:
            logger.warning(f"pypdf failed for {file_path}: {e}")
    
    logger.error(f"No PDF libraries available to extract text from {file_path}")
    return None

def extract_docx_text(file_path: str) -> Optional[str]:
    """
    Extract text from a DOCX file.
    
    Args:
        file_path: Path to the DOCX file
        
    Returns:
        Extracted text or None if extraction failed
    """
    _check_libraries()
    
    if not _AVAILABLE_LIBRARIES['python_docx']:
        logger.error(f"python-docx library not available to extract text from {file_path}")
        return None
    
    try:
        import docx
        
        doc = docx.Document(file_path)
        text_content = []
        
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_content.append(paragraph.text)
        
        # Also extract text from tables
        for table in doc.tables:
            for row in table.rows:
                row_text = []
                for cell in row.cells:
                    if cell.text.strip():
                        row_text.append(cell.text.strip())
                if row_text:
                    text_content.append(' | '.join(row_text))
        
        return '\n\n'.join(text_content) if text_content else None
        
    except Exception as e:
        logger.error(f"Failed to extract text from DOCX {file_path}: {e}")
        return None

def extract_excel_text(file_path: str) -> Optional[str]:
    """
    Extract text from an Excel file (XLS or XLSX).
    
    Args:
        file_path: Path to the Excel file
        
    Returns:
        Extracted text or None if extraction failed
    """
    _check_libraries()
    
    # Try pandas first (handles both XLS and XLSX)
    if _AVAILABLE_LIBRARIES['pandas']:
        try:
            import pandas as pd
            
            # Read all sheets
            excel_file = pd.ExcelFile(file_path)
            text_content = []
            
            for sheet_name in excel_file.sheet_names:
                df = pd.read_excel(file_path, sheet_name=sheet_name)
                
                # Convert DataFrame to text representation
                sheet_text = f"Sheet: {sheet_name}\n"
                sheet_text += df.to_string(index=False, na_rep='')
                text_content.append(sheet_text)
            
            return '\n\n'.join(text_content) if text_content else None
            
        except Exception as e:
            logger.error(f"Failed to extract text from Excel {file_path}: {e}")
            return None
    
    logger.error(f"No Excel libraries available to extract text from {file_path}")
    return None

def extract_pptx_text(file_path: str) -> Optional[str]:
    """
    Extract text from a PowerPoint file (PPT or PPTX).
    
    Args:
        file_path: Path to the PowerPoint file
        
    Returns:
        Extracted text or None if extraction failed
    """
    _check_libraries()
    
    if not _AVAILABLE_LIBRARIES['python_pptx']:
        logger.error(f"python-pptx library not available to extract text from {file_path}")
        return None
    
    try:
        import pptx
        
        presentation = pptx.Presentation(file_path)
        text_content = []
        
        for slide_num, slide in enumerate(presentation.slides, 1):
            slide_text = f"Slide {slide_num}:"
            slide_content = []
            
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_content.append(shape.text.strip())
            
            if slide_content:
                slide_text += "\n" + "\n".join(slide_content)
                text_content.append(slide_text)
        
        return '\n\n'.join(text_content) if text_content else None
        
    except Exception as e:
        logger.error(f"Failed to extract text from PowerPoint {file_path}: {e}")
        return None

def extract_document_text(file_path: str) -> Optional[str]:
    """
    Extract text from a document file based on its extension.
    
    Args:
        file_path: Path to the document file
        
    Returns:
        Extracted text or None if extraction failed
    """
    # Check if we have any document processing libraries
    if not any(_AVAILABLE_LIBRARIES.values()):
        logger.warning(f"Cannot extract text from {file_path}: document extraction libraries not installed")
        return None
    
    logger.debug(f"Attempting to extract text from document: {file_path}")
    
    if not os.path.exists(file_path):
        logger.error(f"Document file not found: {file_path}")
        return None
    
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == '.pdf':
        logger.debug(f"Processing PDF file: {file_path}")
        return extract_pdf_text(file_path)
    elif ext in ['.doc', '.docx']:
        logger.debug(f"Processing Word document: {file_path}")
        return extract_docx_text(file_path)
    elif ext in ['.xls', '.xlsx']:
        logger.debug(f"Processing Excel file: {file_path}")
        return extract_excel_text(file_path)
    elif ext in ['.ppt', '.pptx']:
        return extract_pptx_text(file_path)
    else:
        logger.warning(f"Unsupported document type: {ext}")
        return None
