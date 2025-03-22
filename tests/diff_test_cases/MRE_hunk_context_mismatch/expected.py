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
        # Skip items with zero quantity
        if item.quantity <= 0:
            continue
        subtotal += item.price * item.quantity
    
    # Calculate tax
    tax_rate = get_tax_rate(order.shipping_address)
    tax = subtotal * tax_rate
    
    # Calculate total
    total = subtotal + tax
    
    # Calculate shipping cost
    shipping = calculate_shipping(order)
    total += shipping
    
    # Apply any discounts
    if order.has_discount:
        discount = calculate_discount(order, subtotal)
        total -= discount
    
    # Update order object
    order.subtotal = subtotal
    order.tax = tax
    order.shipping = shipping
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

def get_tax_rate(address):
    """Get tax rate based on shipping address"""
    # Default tax rate
    return 0.08  # 8% tax rate

def calculate_shipping(order):
    """Calculate shipping cost based on order weight and destination"""
    base_rate = 5.00
    return base_rate
