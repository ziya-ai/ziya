def factorial(n, memo=None):
    """
    Calculate factorial with memoization.
    
    Args:
        n: The number to calculate factorial for
        memo: Memoization dictionary to cache results
        
    Returns:
        The factorial of n
    """
    if memo is None:
        memo = {}
        
    if n in memo:
        return memo[n]
        
    if n <= 1:
        return 1
    else:
        result = n * factorial(n - 1, memo)
        memo[n] = result
        return result

def fibonacci(n, memo=None):
    """
    Calculate the nth Fibonacci number with memoization.
    
    Args:
        n: The position in the Fibonacci sequence
        memo: Memoization dictionary to cache results
        
    Returns:
        The nth Fibonacci number
    """
    if memo is None:
        memo = {}
        
    if n in memo:
        return memo[n]
        
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    else:
        result = fibonacci(n - 1, memo) + fibonacci(n - 2, memo)
        memo[n] = result
        return result

def sum_to_n(n):
    """
    Calculate the sum of numbers from 1 to n using formula.
    
    Args:
        n: The upper limit
        
    Returns:
        The sum from 1 to n
    """
    # Use the mathematical formula instead of recursion
    return n * (n + 1) // 2
