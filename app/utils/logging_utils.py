import logging
import os

# Create and configure the logger
formatter = logging.Formatter("\033[35mZIYA\033[0m:     %(message)s")
handler = logging.StreamHandler()
handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get('ZIYA_LOG_LEVEL', 'INFO').upper())
logger.addHandler(handler)
