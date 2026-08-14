from config import DEFAULT_TAX_RATE


def apply_tax(amount, tax_rate=DEFAULT_TAX_RATE):
    """Apply tax to an amount."""
    return round(amount * (1 + tax_rate), 2)
