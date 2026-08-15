"""Format a ratio as a percentage string with one decimal."""


def percent(part, whole):
    if not whole:
        return "0.0%"
    return f"{part / whole * 100:.1f}%"
