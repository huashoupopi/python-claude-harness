def reverse_words(s):
    """Reverse the order of words: "a b c" -> "c b a"."""
    return " ".join(reversed(s.split()))
