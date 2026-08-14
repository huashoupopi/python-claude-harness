def initials(name):
    """"ada lovelace" -> "A.L."."""
    return "".join(p[0].upper() + "." for p in name.split())
