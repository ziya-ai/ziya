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

def calculate_mode(numbers):
    """
    Calculate the mode (most common value) of a list of numbers.
    
    Args:
        numbers: List of numbers
        
    Returns:
        Mode value or None if the list is empty
    """
    if not numbers:
        return None
    
    # Count occurrences of each number
    counts = {}
    for num in numbers:
        counts[num] = counts.get(num, 0) + 1
    
    # Find the number with the highest count
    max_count = 0
    mode = None
    
    for num, count in counts.items():
        if count > max_count:
            max_count = count
            mode = num
    
    return mode
