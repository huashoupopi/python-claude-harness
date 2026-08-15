"""Line-item pricing: bulk discount first, then tax on the discounted subtotal."""

TAX_RATE = 0.08
BULK_THRESHOLD = 10
BULK_DISCOUNT = 0.05


def line_total(unit_price, quantity):
    """Subtotal for one line, before tax."""
    subtotal = unit_price * quantity
    if quantity >= BULK_THRESHOLD:
        subtotal *= 1 - BULK_DISCOUNT
    return subtotal


def with_tax(amount):
    return round(amount * (1 + TAX_RATE), 2)


def order_total(items):
    """items: list of (unit_price, quantity)."""
    subtotal = sum(line_total(price, qty) for price, qty in items)
    return with_tax(subtotal)
