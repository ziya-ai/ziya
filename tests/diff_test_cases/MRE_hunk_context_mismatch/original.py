def process_order(order):
    """
    Process an order and calculate totals
    
    Args:
        order: Order object with items and customer info
        
    Returns:
        Processed order with totals
    """
    # Calculate subtotal
    subtotal = 0
    for item in order.items:
        subtotal += item.price * item.quantity
    
    # Calculate tax
    tax_rate = 0.08  # 8% tax rate
    tax = subtotal * tax_rate
    
    # Calculate total
    total = subtotal + tax
    
    # Apply any discounts
    if order.has_discount:
        discount = calculate_discount(order, subtotal)
        total -= discount
    
    # Update order object
    order.subtotal = subtotal
    order.tax = tax
    order.total = total
    
    return order

def calculate_discount(order, subtotal):
    """Calculate discount amount based on order"""
    if order.discount_type == "percentage":
        return subtotal * (order.discount_value / 100)
    elif order.discount_type == "fixed":
        return min(order.discount_value, subtotal)
    else:
        return 0
