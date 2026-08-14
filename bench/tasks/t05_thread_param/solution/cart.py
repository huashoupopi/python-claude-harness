from config import DEFAULT_TAX_RATE
from pricing import apply_tax


def checkout(items, tax_rate=DEFAULT_TAX_RATE):
    """items: list of (name, price). Returns the total with tax applied."""
    subtotal = sum(price for _, price in items)
    return apply_tax(subtotal, tax_rate)
