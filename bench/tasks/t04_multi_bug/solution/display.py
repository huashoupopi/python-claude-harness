def truncate(s, n):
    """Return s unchanged if len(s) <= n, otherwise s[:n] + "..."."""
    return s if len(s) <= n else s[:n] + "..."
