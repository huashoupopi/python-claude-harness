"""Format a ratio as a percentage string with one decimal."""


def percent(part, whole):
    return f"{part / whole * 100:.1f}%"
