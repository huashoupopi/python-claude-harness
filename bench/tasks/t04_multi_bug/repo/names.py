def initials(name):
    """"ada lovelace" -> "A.L."."""
    return ".".join(p[0] for p in name.split())
