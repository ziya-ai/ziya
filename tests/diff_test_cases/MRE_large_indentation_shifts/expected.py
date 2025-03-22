def process_data(data):
    """Process the input data and return results."""
    results = []
    
    # Handle null data
    if data is None:
        results.append({'status': 'null_data'})
        return results
        
    # Check if data is a list
    if not isinstance(data, list):
        results.append({'status': 'not_list'})
        return results
    
    # Process each item in the list
    for item in data:
        # Handle null items
        if item is None:
            results.append({'status': 'null_item'})
            continue
            
        # Check if item is a dictionary
        if not isinstance(item, dict):
            results.append({'status': 'not_dict'})
            continue
            
        # Check if value key exists
        if 'value' not in item:
            results.append({'status': 'missing_value'})
            continue
            
        value = item['value']
        
        # Check value type
        if not isinstance(value, (int, float)):
            results.append({
                'original': value,
                'status': 'invalid_type'
            })
            continue
            
        # Check value range
        if value <= 0:
            results.append({
                'original': value,
                'status': 'out_of_range_low'
            })
            continue
            
        if value >= 100:
            results.append({
                'original': value,
                'status': 'out_of_range_high'
            })
            continue
            
        # Process the value
        processed = value * 2
        
        # Check processed value range
        if processed <= 50:
            results.append({
                'original': value,
                'status': 'too_small'
            })
        elif processed >= 150:
            results.append({
                'original': value,
                'status': 'too_large'
            })
        else:
            # Success case
            results.append({
                'original': value,
                'processed': processed,
                'status': 'success'
            })
    
    return results
