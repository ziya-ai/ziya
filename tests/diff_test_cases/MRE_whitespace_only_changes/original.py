def calculate_total(items):
    """
    Calculate the total price of all items.
    
    Args:
        items: List of items with 'price' attribute
        
    Returns:
        Total price
    """
    total = 0
    for item in items:
        total += item.price
    
    
    return total

def apply_discount(total, discount_percent):
    """Apply percentage discount to total"""
    if discount_percent < 0 or discount_percent > 100:
        raise ValueError("Discount must be between 0 and 100")
    
    discount = total * (discount_percent / 100)
    return total - discount
