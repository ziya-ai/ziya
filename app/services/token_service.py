"""
Token counting service for contexts and skills.
"""
from pathlib import Path
from typing import List, Dict

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


class TokenService:
    """Service for calculating token counts."""
    
    def __init__(self, model: str = "cl100k_base"):
        if _TIKTOKEN_AVAILABLE:
            self.encoding = tiktoken.get_encoding(model)
        else:
            self.encoding = None
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in a string."""
        if self.encoding is not None:
            try:
                return len(self.encoding.encode(text))
            except Exception:
                pass
        # Fallback to rough estimate if tiktoken unavailable or encoding fails
        return len(text) // 4
    
    def count_tokens_for_file(self, base_path: str, relative_path: str) -> int:
        """Count tokens for a single file."""
        filepath = Path(base_path) / relative_path
        if not filepath.exists():
            return 0
        try:
            content = filepath.read_text(encoding='utf-8', errors='ignore')
            return self.count_tokens(content)
        except Exception:
            return 0
    
    def count_tokens_for_files(self, base_path: str, files: List[str]) -> int:
        """Count total tokens for multiple files."""
        total = 0
        for f in files:
            total += self.count_tokens_for_file(base_path, f)
        return total
    
    def count_tokens_per_file(
        self, 
        base_path: str, 
        files: List[str]
    ) -> Dict[str, int]:
        """Count tokens per file, returning a mapping."""
        result = {}
        for f in files:
            result[f] = self.count_tokens_for_file(base_path, f)
        return result
