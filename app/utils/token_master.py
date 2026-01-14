"""
Single source of truth for token counting across the entire system.
Replaces fragmented counting in directory_util, token_calibrator, and frontend.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
import threading


@dataclass
class TokenBreakdown:
    """Complete token breakdown for a request."""
    files: int              # Selected file content
    chat_history: int       # Previous messages
    system_prompt: int      # Base system prompt
    mcp_tools: int         # Tool definitions
    ast_context: int       # AST enhancement
    images: int            # Multi-modal image tokens
    
    @property
    def total_input(self) -> int:
        """Total input tokens (what Bedrock actually sees)."""
        return sum([
            self.files,
            self.chat_history, 
            self.system_prompt,
            self.mcp_tools,
            self.ast_context,
            self.images
        ])
    
    @property
    def cacheable(self) -> int:
        """Tokens eligible for caching (files + system + tools)."""
        return self.files + self.system_prompt + self.mcp_tools
    
    @property
    def throttle_risk_tokens(self) -> int:
        """Tokens that count toward throttling (ALL tokens, cached or not)."""
        return self.total_input


class TokenMaster:
    """
    Centralized token counting that:
    1. Uses calibrated estimates for speed
    2. Tracks actual usage from Bedrock
    3. Provides accurate breakdowns to frontend
    4. Warns about throttle risk vs billing cost separately
    """
    
    def __init__(self):
        self.lock = threading.Lock()
        self._calibrator = None
    
    @property
    def calibrator(self):
        if self._calibrator is None:
            from app.utils.token_calibrator import get_token_calibrator
            self._calibrator = get_token_calibrator()
        return self._calibrator
    
    def estimate_request_tokens(
        self,
        files: List[str],
        chat_history: List[Dict],
        system_prompt: str,
        mcp_tools: List[Dict],
        ast_context: Optional[str] = None,
        images: List[Dict] = None
    ) -> TokenBreakdown:
        """
        Estimate tokens for a complete request BEFORE sending to Bedrock.
        This is what frontend should display.
        """
        breakdown = TokenBreakdown(
            files=self._estimate_files(files),
            chat_history=self._estimate_text(
                '\n'.join(msg.get('content', '') for msg in chat_history)
            ),
            system_prompt=self._estimate_text(system_prompt),
            mcp_tools=self._estimate_text(
                json.dumps([t.get('input_schema', {}) for t in mcp_tools])
            ),
            ast_context=self._estimate_text(ast_context) if ast_context else 0,
            images=self._estimate_images(images) if images else 0
        )
        
        return breakdown
    
    def _estimate_files(self, file_paths: List[str]) -> int:
        """Estimate tokens for file content using calibrated data."""
        total = 0
        for path in file_paths:
            content = self._read_file_cached(path)
            total += self.calibrator.estimate_tokens(content, path)
        return total
    
    def _estimate_text(self, text: str) -> int:
        """Estimate tokens for arbitrary text."""
        if not text:
            return 0
        return self.calibrator.estimate_tokens(text)
    
    def _estimate_images(self, images: List[Dict]) -> int:
        """
        Estimate tokens for images.
        Claude uses ~1-2k tokens per image depending on size.
        """
        if not images:
            return 0
        
        total = 0
        for img in images:
            # Rough heuristic based on dimensions
            width = img.get('width', 1024)
            height = img.get('height', 1024)
            pixels = width * height
            
            # Claude uses ~1500 tokens per 1MP image
            megapixels = pixels / 1_000_000
            total += int(1500 * megapixels)
        
        return total
    
    def record_actual_usage(
        self,
        breakdown: TokenBreakdown,
        actual_metrics: Dict[str, int],
        conversation_id: str
    ):
        """
        Record actual usage from Bedrock to improve future estimates.
        This is called AFTER each request with real metrics.
        """
        actual_input = actual_metrics.get('inputTokenCount', 0)
        actual_cached = actual_metrics.get('cacheReadInputTokenCount', 0)
        actual_total = actual_input + actual_cached
        
        # Calculate error
        estimated = breakdown.total_input
        error_pct = abs(estimated - actual_total) / actual_total * 100 if actual_total > 0 else 0
        
        # If error > 15%, log warning
        if error_pct > 15:
            logger.warning(
                f"📊 ESTIMATION ERROR: {error_pct:.1f}% off "
                f"(estimated: {estimated:,}, actual: {actual_total:,})"
            )
        
        # Feed back to calibrator for learning
        # (existing calibration logic here)
