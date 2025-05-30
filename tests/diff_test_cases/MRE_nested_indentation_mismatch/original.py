def outer_function():
    """Outer function with nested blocks"""
    
    def inner_function():
        """Inner function"""
        # Level 1 indentation
        if True:
            # Level 2 indentation
            for i in range(10):
                # Level 3 indentation
                if i % 2 == 0:
                    # Level 4 indentation
                    print(f"Even: {i}")
                else:
                    # Level 4 indentation
                    print(f"Odd: {i}")
        
        # Back to level 1
        return "Done"
    
    # Call the inner function
    result = inner_function()
    return result
