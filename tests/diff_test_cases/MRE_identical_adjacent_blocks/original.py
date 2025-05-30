def process_user_input(input_type, value):
    """
    Process different types of user input with validation.
    
    Args:
        input_type: The type of input ('text', 'number', 'date', etc.)
        value: The input value to process
        
    Returns:
        Processed value or None if invalid
    """
    if input_type == 'text':
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        if len(value) == 0:
            return None
        return value.strip()
    
    if input_type == 'number':
        if value is None:
            return None
        if not isinstance(value, (int, float, str)):
            return None
        try:
            return float(value)
        except ValueError:
            return None
    
    if input_type == 'date':
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        if len(value) == 0:
            return None
        try:
            import datetime
            return datetime.datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return None
    
    if input_type == 'boolean':
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            value = value.lower()
            if value in ('true', 'yes', '1'):
                return True
            if value in ('false', 'no', '0'):
                return False
        return None
    
    if input_type == 'email':
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        if len(value) == 0:
            return None
        if '@' not in value:
            return None
        return value.strip().lower()
    
    # Unknown input type
    return None
