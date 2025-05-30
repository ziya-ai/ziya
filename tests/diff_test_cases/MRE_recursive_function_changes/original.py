def factorial(n):
    """
    Calculate factorial recursively.
    
    Args:
        n: The number to calculate factorial for
        
    Returns:
        The factorial of n
    """
    if n <= 1:
        return 1
    else:
        return n * factorial(n - 1)

def fibonacci(n):
    """
    Calculate the nth Fibonacci number recursively.
    
    Args:
        n: The position in the Fibonacci sequence
        
    Returns:
        The nth Fibonacci number
    """
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fibonacci(n - 1) + fibonacci(n - 2)

def sum_to_n(n):
    """
    Calculate the sum of numbers from 1 to n recursively.
    
    Args:
        n: The upper limit
        
    Returns:
        The sum from 1 to n
    """
    if n <= 1:
        return n
    else:
        return n + sum_to_n(n - 1)
