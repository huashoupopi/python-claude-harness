def is_url(v):
    """True if the value starts with http:// or https://."""
    return v.startswith(("http://", "https://"))
