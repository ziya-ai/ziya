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
            num_value = float(value)
            # Additional validation for number type
            if num_value > 1e10 or num_value < -1e10:
                return None  # Number out of reasonable range
            return num_value
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
            date_obj = datetime.datetime.strptime(value, '%Y-%m-%d').date()
            # Additional validation for date type
            today = datetime.date.today()
            if date_obj > today + datetime.timedelta(days=365*10):
                return None  # Date too far in the future
            return date_obj
        except ValueError:
            return None
    
    if input_type == 'boolean':
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            value = value.lower()
            if value in ('true', 'yes', '1', 'on'):
                return True
            if value in ('false', 'no', '0', 'off'):
                return False
        if isinstance(value, int):
            if value == 1:
                return True
            if value == 0:
                return False
        return None
    
    if input_type == 'email':
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        if len(value) == 0:
            return None
        if '@' not in value or '.' not in value:
            return None
        email = value.strip().lower()
        # Additional validation for email
        if len(email.split('@')[0]) == 0:
            return None  # Missing username part
        return email
    
    # Unknown input type
    return None
