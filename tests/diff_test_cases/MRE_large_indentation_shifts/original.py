def process_data(data):
    """Process the input data and return results."""
    results = []
    
    if data is not None:
        if isinstance(data, list):
            for item in data:
                if item is not None:
                    if isinstance(item, dict):
                        if 'value' in item:
                            value = item['value']
                            if isinstance(value, (int, float)):
                                if value > 0:
                                    if value < 100:
                                        # Process values between 0 and 100
                                        processed = value * 2
                                        if processed > 50:
                                            if processed < 150:
                                                # Only keep results in a specific range
                                                results.append({
                                                    'original': value,
                                                    'processed': processed,
                                                    'status': 'success'
                                                })
                                            else:
                                                results.append({
                                                    'original': value,
                                                    'status': 'too_large'
                                                })
                                        else:
                                            results.append({
                                                'original': value,
                                                'status': 'too_small'
                                            })
                                    else:
                                        results.append({
                                            'original': value,
                                            'status': 'out_of_range_high'
                                        })
                                else:
                                    results.append({
                                        'original': value,
                                        'status': 'out_of_range_low'
                                    })
                            else:
                                results.append({
                                    'original': value,
                                    'status': 'invalid_type'
                                })
                        else:
                            results.append({
                                'status': 'missing_value'
                            })
                    else:
                        results.append({
                            'status': 'not_dict'
                        })
                else:
                    results.append({
                        'status': 'null_item'
                    })
        else:
            results.append({
                'status': 'not_list'
            })
    else:
        results.append({
            'status': 'null_data'
        })
    
    return results
