import os.path
import os
from typing import List, Optional
from app.utils.logging_utils import logger
from app.utils.document_extractor import is_document_file, extract_document_text, is_tool_backed_file
 
# Define binary extensions once at module level

EXTERNAL_PREFIX = '[external]'

def resolve_external_path(file_path: str, base_dir: str) -> str:
    """Resolve a file path that may carry the [external] prefix.

    External paths are absolute paths outside the project root that were
    added via the "Add External Path" feature.  In the UI tree they are
    stored with a ``[external]`` prefix (e.g. ``[external]/home/user/foo.py``).

    Returns the absolute filesystem path suitable for ``open()`` / ``os.path.*``.
    """
    s = str(file_path)
    if s.startswith(EXTERNAL_PREFIX):
        real = s[len(EXTERNAL_PREFIX):]
        if real and not real.startswith('/'):
            real = '/' + real
        return real
    return os.path.join(base_dir, s)

BINARY_EXTENSIONS = {
    '.pyc', '.pyo', '.pyd', '.ico', '.png', '.jpg', '.jpeg', '.gif',
    '.core', '.bin', '.exe', '.dll', '.so', '.dylib', '.class',
    '.woff', '.woff2', '.ttf', '.eot', '.zip', '.key', '.crt', '.p12', '.pfx',
    '.der', '.pem',
    '.webp',  # raster image, handled via IMAGE_EXTENSIONS
}

# Raster image formats supported by LLM vision APIs (Claude, Nova, GPT-4V).
# SVGs are intentionally excluded — they're sent as text/XML code context.
IMAGE_EXTENSIONS = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
}

# In-process cache: (abspath) -> (mtime, size, base64_data, media_type)
_image_cache: dict = {}
_IMAGE_CACHE_MAX = 64  # max entries before full eviction


def read_image_as_base64(file_path: str):
    """Read a raster image file and return (base64_data, media_type), or None
    if the file isn't a supported image format.  Results are cached by
    (path, mtime, size) so unchanged files aren't re-read each turn."""
    ext = os.path.splitext(file_path)[1].lower()
    media_type = IMAGE_EXTENSIONS.get(ext)
    if not media_type:
        return None

    abspath = os.path.abspath(file_path)
    try:
        st = os.stat(abspath)
    except OSError:
        return None

    cache_key = abspath
    cached = _image_cache.get(cache_key)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2], cached[3]

    try:
        import base64
        with open(abspath, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        if len(_image_cache) >= _IMAGE_CACHE_MAX:
            _image_cache.clear()
        _image_cache[cache_key] = (st.st_mtime, st.st_size, data, media_type)
        return data, media_type
    except OSError:
        return None
 
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

        # Check extension for known binary types (case-insensitive)
        file_lower = file_path.lower()
        if any(file_lower.endswith(ext) for ext in BINARY_EXTENSIONS):
            logger.debug(f"Skipping binary file by extension: {file_path}")
            return True
            
        # Try to detect if file is binary by reading first few bytes
        with open(file_path, 'rb') as file:
            chunk = file.read(1024)
            # Check for null bytes (common in binary files)
            if b'\x00' in chunk:
                logger.debug(f"Skipping binary file by content: {file_path}")
                return True
            # Check for high ratio of non-printable characters
            non_printable = sum(1 for byte in chunk if byte < 32 and byte not in (9, 10, 13))
            if len(chunk) > 0 and non_printable / len(chunk) > 0.3:
                logger.debug(f"Skipping binary file by character ratio: {file_path}")
                return True
        return False
    except Exception as e:
        # Only log at debug level for unusual errors
        logger.debug(f"Could not determine file type for {file_path}, assuming binary: {e}")
        return True

def is_processable_file(file_path: str) -> bool:
    """
    Check if a file can be processed (either text or extractable document).
    Note: Tool-backed files (like pcap) return True here but are handled specially
    with -1 token counts to indicate tool availability.
    """
    result = not is_binary_file(file_path) or is_document_file(file_path)
    return result

def get_tool_backed_file_context(file_path: str) -> str:
    """
    Generate a context note for tool-backed files like PCAPs.
    
    Args:
        file_path: Path to the tool-backed file
        
    Returns:
        Context note explaining the file and available tools
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext in {'.pcap', '.pcapng', '.cap', '.dmp'}:
        return f"""Tool-Backed File: {os.path.basename(file_path)}
Location: {file_path}
Type: PCAP network capture file
Available Tool: mcp_analyze_pcap
Operations: summary, conversations, dns_queries, dns_responses, filter, search, tcp_health, flow_stats, connectivity_map, flow_health, search_advanced, http, packet_details, tunneling, ipv6_extensions, tls, icmp
Note: This file has been included in the context. Use the analyze_pcap tool to extract information from it. The tool can analyze protocols, flows, health metrics, and provide detailed packet information."""
    
    return f"Tool-Backed File: {file_path} (specialized tools available)"

def read_file_content(file_path: str) -> Optional[str]:
    """
    Read content from a file, handling both text and document files.
    
    Args:
        file_path: Path to the file
        
    Returns:
        File content as string, or None if reading failed
    """
    try:
        # Check if this file has specialized tool support - return context note instead
        if is_tool_backed_file(file_path):
            logger.info(f"File {file_path} has specialized tool support - adding context note")
            return get_tool_backed_file_context(file_path)
        
        # File doesn't exist yet (e.g. a delegate output target not yet created)
        if not os.path.exists(file_path):
            logger.debug(f"File not found (may be a future output target): {file_path}")
            return None

        # Check if it's a document file first
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
