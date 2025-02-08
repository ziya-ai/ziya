import logging
import os

# state test 

def get_logger():
   # Create and configure the logger
   logger = logging.getLogger(__name__)

   # Remove any existing handlers
   logger.handlers.clear()

   formatter = logging.Formatter("\033[35mZIYA\033[0m:     %(message)s")
   handler = logging.StreamHandler()
   handler.setFormatter(formatter)
   logger.addHandler(handler)

   # Set level from environment or default to INFO
   logger.setLevel(os.environ.get('ZIYA_LOG_LEVEL', 'INFO').upper())
   return logger

logger = get_logger()
