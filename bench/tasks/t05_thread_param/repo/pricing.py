from config import DEFAULT_TAX_RATE


def apply_tax(amount):
    """Apply tax to an amount."""
    return round(amount * (1 + DEFAULT_TAX_RATE), 2)
