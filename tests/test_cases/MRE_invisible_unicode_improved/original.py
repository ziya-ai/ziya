def unicode_function():
    # This function has invisible unicode characters
    print("Hello​ World")  # Contains a zero-width space between Hello and World
    
    # This line has a zero-width non-joiner
    value = 100‌ + 50
    
    # This line has a zero-width joiner
    result = "test‍ing"
    
    return result
