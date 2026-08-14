from pricing import apply_tax


def checkout(items):
    """items: list of (name, price). Returns the total with tax applied."""
    subtotal = sum(price for _, price in items)
    return apply_tax(subtotal)
