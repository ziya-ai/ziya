"""
Background worker that refines document token estimates.
Runs in a separate thread, processing queued documents.
"""
import threading
import time
import os
from typing import Optional
from app.utils.logging_utils import logger


class DocumentTokenRefiner:
    """Background worker that extracts and counts tokens for document files."""
    
    def __init__(self, interval: int = 60):
        self.interval = interval  # Check every 60 seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
    
    def start(self):
        """Start the background worker."""
        with self.lock:
            if self.running:
                return
            
            self.running = True
            self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="DocumentTokenRefiner")
            self.thread.start()
            logger.info("📄 Document token refiner started")
    
    def stop(self):
        """Stop the background worker."""
        with self.lock:
            self.running = False
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
            logger.info("📄 Document token refiner stopped")
    
    def _worker_loop(self):
        """Main worker loop."""
        while self.running:
            try:
                self._process_batch()
            except Exception as e:
                logger.error(f"Document token refiner error: {e}")
            
            # Sleep but check running flag periodically for responsive shutdown
            for _ in range(self.interval):
                if not self.running:
                    break
                time.sleep(1)
    
    def _process_batch(self):
        """Process a batch of documents needing extraction."""
        from app.utils.token_calibrator import get_token_calibrator
        from app.utils.directory_util import get_accurate_token_count
        
        calibrator = get_token_calibrator()
        files = calibrator.get_files_needing_extraction(limit=5)
        
        if not files:
            return
        
        logger.info(f"📄 Refining token estimates for {len(files)} documents...")
        
        for file_path in files:
            if not self.running:
                break
            
            try:
                # Verify file still exists
                if not os.path.exists(file_path) or not os.path.isfile(file_path):
                    logger.debug(f"Skipping non-existent file: {file_path}")
                    continue
                
                # Get accurate count (this will update the cache internally)
                accurate_tokens = get_accurate_token_count(file_path)
                
                if accurate_tokens > 0:
                    cached = calibrator.get_cached_document_tokens(file_path)
                    if cached:
                        est = cached.get('estimated_tokens', 0)
                        logger.info(f"📄 ✓ {os.path.basename(file_path)}: "
                                  f"{est:,} (est) → {accurate_tokens:,} (actual) tokens")
                
            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")


# Global singleton
_refiner_instance: Optional[DocumentTokenRefiner] = None
_refiner_lock = threading.Lock()


def get_document_refiner() -> DocumentTokenRefiner:
    """Get or create the global document refiner singleton."""
    global _refiner_instance
    
    if _refiner_instance is None:
        with _refiner_lock:
            if _refiner_instance is None:
                _refiner_instance = DocumentTokenRefiner()
    
    return _refiner_instance
