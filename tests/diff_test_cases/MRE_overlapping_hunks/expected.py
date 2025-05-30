def process_data(data):
    """
    Process the input data and return results
    """
    # Initialize variables
    result = []
    count = 0
    
    # Process each item
    for item in data:
        # Skip empty items
        # Also skip None values
        # This is a duplicate comment that will cause conflicts
        if item is None:
            continue
        if not item:
            continue
            
        # Transform the item
        transformed = transform_item(item)
        
        # Add to results if valid
        # Check validity before adding
        # This is another duplicate comment that will cause conflicts
        if is_valid(transformed):
            result.append(transformed)
            count += 1
    
    # Return the processed data
    return {
        "items": result,
        "processed": len(data),
        "count": count
    }

def transform_item(item):
    """Transform an individual item"""
    return item.upper()

def is_valid(item):
    """Check if an item is valid"""
    return len(item) > 0
