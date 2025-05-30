"""
Test suite for Nova wrapper type annotations.

This test suite verifies that all type annotations in the Nova wrapper are valid.
"""

import inspect
import sys
import pytest
from typing import get_type_hints


def test_nova_wrapper_module_imports():
    """Test that the Nova wrapper module can be imported without errors."""
    try:
        import app.agents.nova_wrapper
        assert True
    except Exception as e:
        assert False, f"Error importing Nova wrapper module: {str(e)}"


def test_nova_wrapper_class_definition():
    """Test that the NovaWrapper class can be defined without errors."""
    try:
        from app.agents.nova_wrapper import NovaWrapper
        assert True
    except Exception as e:
        assert False, f"Error in NovaWrapper class definition: {str(e)}"


def test_nova_wrapper_method_signatures():
    """Test that all method signatures in NovaWrapper are valid."""
    from app.agents.nova_wrapper import NovaWrapper
    
    # Get all methods of the NovaWrapper class
    methods = [method for method in dir(NovaWrapper) if callable(getattr(NovaWrapper, method)) and not method.startswith('__')]
    
    # Check each method's signature
    for method_name in methods:
        try:
            method = getattr(NovaWrapper, method_name)
            inspect.signature(method)
            assert True
        except Exception as e:
            assert False, f"Error in method signature for {method_name}: {str(e)}"


def test_nova_wrapper_type_annotations():
    """Test that all type annotations in NovaWrapper are valid."""
    from app.agents.nova_wrapper import NovaWrapper
    
    # Get all methods of the NovaWrapper class
    methods = [method for method in dir(NovaWrapper) if callable(getattr(NovaWrapper, method)) and not method.startswith('__')]
    
    # Check each method's type annotations
    for method_name in methods:
        try:
            method = getattr(NovaWrapper, method_name)
            get_type_hints(method)
            assert True
        except Exception as e:
            assert False, f"Error in type annotations for {method_name}: {str(e)}"


def test_nova_wrapper_astream_signature():
    """Test specifically the _astream method signature."""
    from app.agents.nova_wrapper import NovaWrapper
    
    try:
        method = getattr(NovaWrapper, '_astream')
        signature = inspect.signature(method)
        assert True
    except Exception as e:
        assert False, f"Error in _astream method signature: {str(e)}"


def test_nova_wrapper_astream_type_annotations():
    """Test specifically the _astream method type annotations."""
    from app.agents.nova_wrapper import NovaWrapper
    
    try:
        method = getattr(NovaWrapper, '_astream')
        type_hints = get_type_hints(method)
        assert 'return' in type_hints, "Return type annotation missing"
        assert True
    except Exception as e:
        assert False, f"Error in _astream method type annotations: {str(e)}"


def test_nova_wrapper_complete_validation():
    """Test that all classes and type annotations in Nova wrapper are valid."""
    import inspect
    import sys
    
    # Import the module
    import app.agents.nova_wrapper
    
    # Get all attributes of the module
    module = sys.modules['app.agents.nova_wrapper']
    
    # Check all classes and functions
    for name, obj in inspect.getmembers(module):
        if inspect.isclass(obj):
            # Check class definition
            try:
                # Check all methods in the class
                for method_name, method in inspect.getmembers(obj, predicate=inspect.isfunction):
                    if not method_name.startswith('__'):
                        # Check method signature
                        try:
                            signature = inspect.signature(method)
                        except Exception as e:
                            assert False, f"Error in method signature for {name}.{method_name}: {str(e)}"
                        
                        # Check method type annotations
                        try:
                            type_hints = get_type_hints(method)
                        except Exception as e:
                            assert False, f"Error in type annotations for {name}.{method_name}: {str(e)}"
            except Exception as e:
                assert False, f"Error in class definition for {name}: {str(e)}"
        elif inspect.isfunction(obj):
            # Check function signature
            try:
                signature = inspect.signature(obj)
            except Exception as e:
                assert False, f"Error in function signature for {name}: {str(e)}"
            
            # Check function type annotations
            try:
                type_hints = get_type_hints(obj)
            except Exception as e:
                assert False, f"Error in type annotations for {name}: {str(e)}"
