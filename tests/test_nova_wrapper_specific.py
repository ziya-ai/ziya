"""
Test specifically for the ChatGenerationChunk issue in Nova wrapper.

This test focuses on the specific issue with the _astream method return type annotation.
"""

import inspect
import pytest


def test_nova_wrapper_astream_return_type():
    """Test specifically the _astream method return type annotation."""
    import ast
    import os
    
    # Get the path to the nova_wrapper.py file
    nova_wrapper_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                    "app", "agents", "nova_wrapper.py")
    
    # Read the file
    with open(nova_wrapper_path, "r") as f:
        code = f.read()
    
    # Find the _astream method using string search
    if "async def _astream" not in code:
        pytest.fail("_astream method not found in the file")
    
    # Find the return type annotation
    import re
    match = re.search(r"async def _astream\([^)]*\)\s*->\s*([^:]+):", code)
    assert match is not None, "_astream method return type annotation not found"
    
    return_type = match.group(1).strip()
    print(f"Found return type: {return_type}")
    
    # Check if it contains ChatGenerationChunk
    assert "ChatGenerationChunk" not in return_type, \
        f"_astream method has invalid return type annotation: {return_type}"
    
    # It should be AsyncIterator[ChatGeneration] or similar
    assert "AsyncIterator" in return_type, \
        f"_astream method should return AsyncIterator, but got: {return_type}"
    
    assert "ChatGeneration" in return_type, \
        f"_astream method should return AsyncIterator[ChatGeneration], but got: {return_type}"


def test_nova_wrapper_imports():
    """Test that the Nova wrapper imports all necessary types."""
    import os
    
    # Get the path to the nova_wrapper.py file
    nova_wrapper_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                    "app", "agents", "nova_wrapper.py")
    
    # Read the file
    with open(nova_wrapper_path, "r") as f:
        code = f.read()
    
    # Check imports using string search
    assert "from typing import" in code, "typing module not imported"
    assert "AsyncIterator" in code, "AsyncIterator not imported or used"
    assert "ChatGeneration" in code, "ChatGeneration not imported or used"
    
    # Check specific imports
    import re
    typing_imports = re.findall(r"from typing import ([^\\]+)", code)
    all_typing_imports = " ".join(typing_imports)
    
    assert "AsyncIterator" in all_typing_imports, "AsyncIterator not imported from typing"
    
    langchain_imports = re.findall(r"from langchain_core\.outputs import ([^\\]+)", code)
    all_langchain_imports = " ".join(langchain_imports)
    
    assert "ChatGeneration" in all_langchain_imports, "ChatGeneration not imported from langchain_core.outputs"
