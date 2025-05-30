def calculate_average(numbers):
    """
    Calculate the average of a list of numbers.
    
    Args:
        numbers: List of numbers
        
    Returns:
        Average value or None if the list is empty
    """
    if not numbers:
        return None
    
    total = sum(numbers)
    count = len(numbers)
    
    return total / count

def calculate_median(numbers):
    """
    Calculate the median of a list of numbers.
    
    Args:
        numbers: List of numbers
        
    Returns:
        Median value or None if the list is empty
    """
    if not numbers:
        return None
    
    sorted_numbers = sorted(numbers)
    count = len(sorted_numbers)
    
    if count % 2 == 0:
        # Even number of elements
        middle1 = sorted_numbers[count // 2 - 1]
        middle2 = sorted_numbers[count // 2]
        return (middle1 + middle2) / 2
    else:
        # Odd number of elements
        return sorted_numbers[count // 2]
