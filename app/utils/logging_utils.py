import logging
import os

# This module provides a consistent logging interface for the application

def get_logger():
   # Create and configure the logger
   logger = logging.getLogger(__name__)

   # Remove any existing handlers
   logger.handlers.clear()
   
   # Prevent propagation to the root logger to avoid duplicate logs
   logger.propagate = False

   formatter = logging.Formatter("\033[35mZIYA\033[0m: %(levelname)-8s %(message)s")
   handler = logging.StreamHandler()
   handler.setFormatter(formatter)
   logger.addHandler(handler)

   # Set level from environment or default to INFO
   logger.setLevel(os.environ.get('ZIYA_LOG_LEVEL', 'INFO').upper())
   return logger

def configure_third_party_logging():
   """Suppress verbose logging from third-party libraries"""
   # Suppress asyncio errors
   logging.getLogger('asyncio').setLevel(logging.CRITICAL)
   
   # Suppress uvicorn access logs unless debug mode
   if os.environ.get('ZIYA_LOG_LEVEL', 'INFO').upper() != 'DEBUG':
       logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
       logging.getLogger('uvicorn.error').setLevel(logging.WARNING)
   
   # Suppress boto3/botocore debug logs
   logging.getLogger('boto3').setLevel(logging.WARNING)
   logging.getLogger('botocore').setLevel(logging.WARNING)
   logging.getLogger('urllib3').setLevel(logging.WARNING)

logger = get_logger()
configure_third_party_logging()
