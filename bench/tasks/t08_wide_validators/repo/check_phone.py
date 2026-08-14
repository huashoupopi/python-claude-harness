def is_phone(v):
    """True if the value is exactly 11 digits."""
    return len(v) == 10 and v.isdigit()
