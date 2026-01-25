import logging
import os
from enum import Enum
from typing import Optional

class ZiyaMode(Enum):
    """Execution mode for Ziya application."""
    SERVER = "server"    # Full server mode with verbose logging
    CHAT = "chat"        # Interactive CLI mode with minimal logging


class ModeAwareLogger:
    """
    Logger that respects execution mode (server vs chat).
    
    In CHAT mode:
    - Suppresses DEBUG and most INFO logs
    - Only shows ERROR and WARNING to user
    - Internal operations remain silent
    
    In SERVER mode:
    - Shows all configured log levels
    - Full verbosity for debugging
    """
    
    def __init__(self, name: str, mode: Optional[ZiyaMode] = None):
        self._logger = logging.getLogger(name)
        self._mode = mode or self._detect_mode()
        self._configured = False
        self._last_checked_mode = None
    
    def _ensure_configured(self):
        """Lazy configuration - check mode each time to handle late ZIYA_MODE setting."""
        current_mode = self._detect_mode()
        
        # Reconfigure if mode changed or not yet configured
        if not self._configured or current_mode != self._last_checked_mode:
            self._logger.handlers.clear()
            self._logger.propagate = False
            
            if current_mode == ZiyaMode.CHAT:
                self._setup_chat_logging()
            else:
                self._setup_server_logging()
            
            self._configured = True
            self._last_checked_mode = current_mode
            self._mode = current_mode
    
    def _detect_mode(self) -> ZiyaMode:
        """Detect execution mode from environment."""
        mode_str = os.environ.get('ZIYA_MODE', 'server').lower()
        try:
            return ZiyaMode(mode_str)
        except ValueError:
            return ZiyaMode.SERVER
    
    def _setup_chat_logging(self):
        """Configure minimal logging for chat mode."""
        # Only show WARNING and above to console
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.setLevel(logging.WARNING)
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.WARNING)
    
    def _setup_server_logging(self):
        """Configure full logging for server mode."""
        formatter = logging.Formatter("\033[35mZIYA\033[0m: %(levelname)-8s %(message)s")
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)
        
        # Set level from environment or default to INFO
        level = os.environ.get('ZIYA_LOG_LEVEL', 'INFO').upper()
        self._logger.setLevel(level)
    
    # Delegate all logging methods to internal logger
    def debug(self, msg, *args, **kwargs):
        self._ensure_configured()
        self._logger.debug(msg, *args, **kwargs)
    
    def info(self, msg, *args, **kwargs):
        self._ensure_configured()
        self._logger.info(msg, *args, **kwargs)
    
    def warning(self, msg, *args, **kwargs):
        self._ensure_configured()
        self._logger.warning(msg, *args, **kwargs)
    
    def error(self, msg, *args, **kwargs):
        self._ensure_configured()
        self._logger.error(msg, *args, **kwargs)
    
    def critical(self, msg, *args, **kwargs):
        self._ensure_configured()
        self._logger.critical(msg, *args, **kwargs)
    
    @property
    def mode(self) -> ZiyaMode:
        """Get current execution mode."""
        return self._mode


def get_logger():
    """Get a mode-aware logger instance."""
    return ModeAwareLogger(__name__)


def get_mode_aware_logger(name: str) -> ModeAwareLogger:
    """
    Get a mode-aware logger for a specific module.
    
    Usage:
        logger = get_mode_aware_logger(__name__)
        logger.info("This will be suppressed in chat mode")
        logger.error("This will always show")
    """
    return ModeAwareLogger(name)

def configure_third_party_logging():
   """Suppress verbose logging from third-party libraries"""
   # Suppress asyncio errors
   logging.getLogger('asyncio').setLevel(logging.CRITICAL)
   
   # Suppress uvicorn access logs unless debug mode
   mode = os.environ.get('ZIYA_MODE', 'server').lower()
   log_level = os.environ.get('ZIYA_LOG_LEVEL', 'INFO').upper()
   
   if mode == 'chat' or log_level != 'DEBUG':
       logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
       logging.getLogger('uvicorn.error').setLevel(logging.WARNING)
   
   # Suppress boto3/botocore debug logs
   logging.getLogger('boto3').setLevel(logging.WARNING)
   logging.getLogger('botocore').setLevel(logging.WARNING)
   logging.getLogger('urllib3').setLevel(logging.WARNING)
   
   # In chat mode, suppress all INFO logs from app modules
   if mode == 'chat':
       logging.getLogger('app').setLevel(logging.WARNING)
       logging.getLogger('app.mcp').setLevel(logging.WARNING)
       logging.getLogger('app.agents').setLevel(logging.WARNING)
       logging.getLogger('app.streaming_tool_executor').setLevel(logging.WARNING)
       # But allow ERROR messages to pass through
       for handler in logging.root.handlers:
           handler.setLevel(logging.WARNING)

logger = get_logger()
configure_third_party_logging()
