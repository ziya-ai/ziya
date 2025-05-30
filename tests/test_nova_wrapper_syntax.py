"""
Syntax test for Nova wrapper.

This test simply imports the Nova wrapper to check for syntax errors.
"""

def test_nova_wrapper_imports():
    """Test that the Nova wrapper can be imported without syntax errors."""
    try:
        from app.agents.nova_wrapper import NovaWrapper
        assert True
    except SyntaxError as e:
        assert False, f"Syntax error in Nova wrapper: {str(e)}"
    except NameError as e:
        assert False, f"Name error in Nova wrapper: {str(e)}"
    except ImportError as e:
        assert False, f"Import error in Nova wrapper: {str(e)}"
    except Exception as e:
        assert False, f"Error importing Nova wrapper: {str(e)}"
