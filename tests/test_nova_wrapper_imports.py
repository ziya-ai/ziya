"""
Test for Nova wrapper imports.

This test verifies that the Nova wrapper imports all necessary types.
"""

import pytest
import importlib
import sys


def test_nova_wrapper_imports_directly():
    """Test that the Nova wrapper imports can be resolved directly."""
    # Import the module
    from app.agents.nova_wrapper import NovaWrapper
    
    # Get the module
    module = sys.modules['app.agents.nova_wrapper']
    
    # Check that ChatGeneration is imported
    assert hasattr(module, 'ChatGeneration'), "ChatGeneration not imported"
    
    # Check that AsyncIterator is imported
    assert hasattr(module, 'AsyncIterator'), "AsyncIterator not imported"


def test_nova_wrapper_import_resolution():
    """Test that all imports in the Nova wrapper can be resolved."""
    # Import the module
    import app.agents.nova_wrapper
    
    # Reload the module to ensure fresh imports
    importlib.reload(app.agents.nova_wrapper)
    
    # The import should succeed without errors
    assert True, "Nova wrapper imports could not be resolved"


def test_nova_wrapper_type_annotations_resolution():
    """Test that all type annotations in the Nova wrapper can be resolved."""
    # Import the module
    from app.agents.nova_wrapper import NovaWrapper
    
    # Get the _astream method
    astream_method = getattr(NovaWrapper, '_astream')
    
    # Get the annotations
    annotations = astream_method.__annotations__
    
    # Check that the return annotation is present
    assert 'return' in annotations, "Return annotation not present"
    
    # The return annotation should be AsyncIterator[ChatGeneration]
    return_annotation = annotations['return']
    assert str(return_annotation).startswith('typing.AsyncIterator'), \
        f"Return annotation is not AsyncIterator: {return_annotation}"
