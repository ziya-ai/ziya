"""
Language handler module for diff application system.

This module provides a pluggable architecture for language-specific operations
in the diff application system.
"""

from .base import LanguageHandler, LanguageHandlerRegistry
from .generic import GenericTextHandler
from .python import PythonHandler
from .javascript import JavaScriptHandler
from .typescript import TypeScriptHandler
from .java import JavaHandler
from .cpp import CppHandler
from .rust import RustHandler

# Register handlers in priority order (most specific first)
LanguageHandlerRegistry.register(PythonHandler)
LanguageHandlerRegistry.register(TypeScriptHandler)  # TypeScript before JavaScript
LanguageHandlerRegistry.register(JavaScriptHandler)
LanguageHandlerRegistry.register(JavaHandler)
LanguageHandlerRegistry.register(CppHandler)
LanguageHandlerRegistry.register(RustHandler)

# Register the generic handler last as the fallback
LanguageHandlerRegistry.register(GenericTextHandler)

__all__ = [
    'LanguageHandler', 
    'LanguageHandlerRegistry', 
    'GenericTextHandler',
    'PythonHandler',
    'JavaScriptHandler',
    'TypeScriptHandler',
    'JavaHandler',
    'CppHandler',
    'RustHandler'
]
