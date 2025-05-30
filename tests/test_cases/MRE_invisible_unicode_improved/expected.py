def unicode_function():
    # This function has invisible unicode characters
    print("Hello World")  # Zero-width space removed
    
    # This line has a zero-width non-joiner
    value = 200 + 50  # Changed value and removed zero-width non-joiner
    
    # This line has a zero-width joiner
    result = "test‍ing"
    
    return result
