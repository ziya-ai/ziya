import os
import sys

from app.utils.logging_utils import logger


def validate_langchain_vars():
    langchain_vars = [var for var in os.environ if var.startswith("LANGCHAIN_")]
    if langchain_vars:
        logger.error("Langchain environment variables are set:")
        for var in langchain_vars:
            logger.error(f"- {var}")
        logger.error(
            "To prevent accidentally sending confidential code to a 3rd party, please unset these variables before "
            "running Ziya.")
        sys.exit(1)
