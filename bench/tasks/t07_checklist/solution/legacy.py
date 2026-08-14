from units import to_celsius


def old_convert(f):
    """Deprecated: kept for backwards compatibility, delegates to units.to_celsius."""
    return to_celsius(f)
